"""暴露给模型的 Docker 沙箱命令和补丁回写工具。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from coding_agent.ai.contracts import ToolDefinition
from coding_agent.sandbox.contracts import SandboxExecutor, SandboxLimits, SandboxRequest
from coding_agent.sandbox.patches import PatchRegistry
from coding_agent.sandbox.snapshot import SnapshotService
from coding_agent.tools.contracts import Cancellation, ToolContext, ToolResult, ToolUpdate


class SandboxCommandTool:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        executor: SandboxExecutor,
        snapshots: SnapshotService,
        patches: PatchRegistry,
        image: str,
        limits: SandboxLimits,
        capture_changes: bool,
    ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["command"],
            },
            risk="shell",
        )
        self.executor = executor
        self.snapshots = snapshots
        self.patches = patches
        self.image = image
        self.limits = limits
        self.capture_changes = capture_changes

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        command = params.get("command")
        if not isinstance(command, str) or not command.strip():
            yield ToolResult(
                status="validation_failed", summary="command must be a non-empty string"
            )
            return
        requested_timeout = params.get("timeout_seconds", self.limits.timeout_seconds)
        if isinstance(requested_timeout, bool) or not isinstance(requested_timeout, int):
            yield ToolResult(
                status="validation_failed", summary="timeout_seconds must be an integer"
            )
            return
        limits = SandboxLimits(
            timeout_seconds=min(max(requested_timeout, 1), self.limits.timeout_seconds),
            memory_mb=self.limits.memory_mb,
            cpu_count=self.limits.cpu_count,
            pids_limit=self.limits.pids_limit,
            tmpfs_mb=self.limits.tmpfs_mb,
        )
        yield ToolUpdate(message="creating filtered workspace snapshot")
        try:
            snapshot = await self.snapshots.create()
        except OSError as error:
            yield ToolResult(status="execution_error", summary=f"snapshot creation failed: {error}")
            return
        try:
            yield ToolUpdate(message="running command in isolated Docker container")
            result = await self.executor.execute(
                SandboxRequest(command=command, snapshot=snapshot, image=self.image, limits=limits),
                cancellation,
            )
            patch_id: str | None = None
            details: dict[str, object] = {"sandbox": "docker", "network": "disabled"}
            if self.capture_changes and result.status == "success":
                try:
                    patch_id = self.patches.add(result.patch, result.changed_files, snapshot)
                except ValueError as error:
                    yield ToolResult(status="validation_failed", summary=str(error))
                    return
            elif result.changed_files:
                details["changes_discarded"] = result.changed_files
            if patch_id is not None:
                details.update(
                    {
                        "patch_id": patch_id,
                        "changed_files": result.changed_files,
                        "next_step": "call apply_patch with patch_id after reviewing the diff",
                    }
                )
            status = "execution_error" if result.status == "unavailable" else result.status
            yield ToolResult(
                status=status,
                summary=result.summary,
                output=result.output[: context.max_output_chars],
                exit_code=result.exit_code,
                changed_files=result.changed_files if self.capture_changes else [],
                details=details,
            )
        finally:
            await self.snapshots.cleanup(snapshot)


class ApplyPatchTool:
    def __init__(self, patches: PatchRegistry) -> None:
        self.patches = patches
        self.definition = ToolDefinition(
            name="apply_patch",
            description=(
                "Apply a validated patch previously produced by a sandbox command. "
                "This writes to the host workspace and requires approval unless pre-authorized."
            ),
            parameters={
                "type": "object",
                "properties": {"patch_id": {"type": "string"}},
                "required": ["patch_id"],
            },
            risk="write",
        )

    def approval_details(self, params: dict[str, object]) -> dict[str, object]:
        patch_id = params.get("patch_id")
        if not isinstance(patch_id, str):
            return params
        return self.patches.approval_details(patch_id)

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        patch_id = params.get("patch_id")
        if not isinstance(patch_id, str) or not patch_id:
            yield ToolResult(
                status="validation_failed", summary="patch_id must be a non-empty string"
            )
            return
        if cancellation.is_set():
            yield ToolResult(status="cancelled", summary="patch application cancelled")
            return
        applied, summary, files = await self.patches.apply(patch_id)
        yield ToolResult(
            status="success" if applied else "validation_failed",
            summary=summary,
            changed_files=files,
        )
