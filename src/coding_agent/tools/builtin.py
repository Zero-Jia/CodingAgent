"""有边界的读取、编辑、写入、搜索和 PowerShell 工具；策略由运行时检查。"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from coding_agent.ai.contracts import ToolDefinition
from coding_agent.tools.contracts import Cancellation, ToolContext, ToolResult, ToolUpdate
from coding_agent.workspace.service import WorkspaceService


class BaseTool:
    definition: ToolDefinition


def _path(params: dict[str, object], context: ToolContext) -> Path:
    raw = params.get("path")
    if not isinstance(raw, str):
        raise ValueError("path must be a string")
    candidate = (Path(context.workspace) / raw).resolve()
    candidate.relative_to(Path(context.workspace).resolve())
    return candidate


def _integer(params: dict[str, object], name: str, default: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"{name} must be an integer")
    return int(value)


class ReadTool(BaseTool):
    definition = ToolDefinition(
        name="read",
        description="Read a bounded UTF-8 text file with optional lines.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
        },
        risk="read",
    )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        try:
            target = _path(params, context)
            data = await asyncio.to_thread(target.read_bytes)
            if b"\x00" in data[:8192]:
                yield ToolResult(status="validation_failed", summary="binary file refused")
                return
            lines = data.decode("utf-8", errors="replace").splitlines()
            start = max(1, _integer(params, "start_line", 1))
            end = min(len(lines), _integer(params, "end_line", len(lines)))
            output = "\n".join(
                f"{index}: {line}" for index, line in enumerate(lines[start - 1 : end], start)
            )
            yield ToolResult(
                status="success",
                summary=f"read {target.name}",
                output=output[: context.max_output_chars],
            )
        except (OSError, ValueError) as error:
            yield ToolResult(status="execution_error", summary=str(error))


class SearchTool(BaseTool):
    definition = ToolDefinition(
        name="search",
        description="Search text in workspace using rg when available.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk="read",
    )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        query = params.get("query")
        if not isinstance(query, str) or not query:
            yield ToolResult(status="validation_failed", summary="query must be a non-empty string")
            return
        output = await WorkspaceService(Path(context.workspace)).search(query)
        yield ToolResult(
            status="success", summary="search completed", output=output[: context.max_output_chars]
        )


class EditTool(BaseTool):
    definition = ToolDefinition(
        name="edit",
        description="Replace exactly one occurrence of old_text in a text file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        risk="write",
    )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        try:
            target = _path(params, context)
            old = params.get("old_text")
            new = params.get("new_text")
            if not isinstance(old, str) or not isinstance(new, str):
                raise ValueError("old_text and new_text must be strings")
            text = await asyncio.to_thread(target.read_text, encoding="utf-8")
            count = text.count(old)
            if count != 1:
                yield ToolResult(
                    status="validation_failed",
                    summary=f"old_text must match exactly once; matches={count}",
                )
                return
            if cancellation.is_set():
                yield ToolResult(status="cancelled", summary="edit cancelled before write")
                return
            await asyncio.to_thread(target.write_text, text.replace(old, new, 1), encoding="utf-8")
            yield ToolResult(
                status="success", summary=f"edited {target.name}", changed_files=[str(target)]
            )
        except (OSError, ValueError) as error:
            yield ToolResult(status="execution_error", summary=str(error))


class WriteTool(BaseTool):
    definition = ToolDefinition(
        name="write",
        description="Create or overwrite a workspace text file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        risk="write",
    )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        try:
            target = _path(params, context)
            content = params.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            if cancellation.is_set():
                yield ToolResult(status="cancelled", summary="write cancelled before write")
                return
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, content, encoding="utf-8")
            yield ToolResult(
                status="success", summary=f"wrote {target.name}", changed_files=[str(target)]
            )
        except (OSError, ValueError) as error:
            yield ToolResult(status="execution_error", summary=str(error))


class PowerShellTool(BaseTool):
    def __init__(self, name: str = "shell") -> None:
        self._process: asyncio.subprocess.Process | None = None
        self.definition = ToolDefinition(
            name=name,
            description="Run a bounded PowerShell command in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["command"],
            },
            risk="shell",
        )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        command = params.get("command")
        if not isinstance(command, str) or not command.strip():
            yield ToolResult(
                status="validation_failed", summary="command must be a non-empty string"
            )
            return
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            yield ToolResult(status="execution_error", summary="pwsh or powershell was not found")
            return
        timeout = min(max(_integer(params, "timeout_seconds", 30), 1), 120)
        process = await asyncio.create_subprocess_exec(
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            cwd=context.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process
        try:
            communicate = asyncio.create_task(process.communicate())
            cancelled = asyncio.create_task(cancellation.wait())
            done, pending = await asyncio.wait(
                {communicate, cancelled}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if cancelled in done:
                await self._stop_process(process)
                yield ToolResult(status="cancelled", summary="shell command cancelled")
                return
            if communicate not in done:
                await self._stop_process(process)
                yield ToolResult(status="timeout", summary=f"command exceeded {timeout}s")
                return
            stdout, stderr = communicate.result()
        finally:
            self._process = None
        output = (stdout.decode(errors="replace") + stderr.decode(errors="replace"))[
            : context.max_output_chars
        ]
        status: Literal["success", "execution_error"] = (
            "success" if process.returncode == 0 else "execution_error"
        )
        yield ToolResult(
            status=status, summary="command completed", output=output, exit_code=process.returncode
        )

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()


class GitDiffTool(PowerShellTool):
    def __init__(self) -> None:
        super().__init__("git_diff")

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        async for result in super().execute(
            {
                "command": (
                    "git --no-pager -c core.pager=cat diff --no-ext-diff --no-textconv --stat; "
                    "git --no-pager -c core.pager=cat diff --no-ext-diff --no-textconv -- ."
                )
            },
            context,
            cancellation,
        ):
            yield result
