"""使用 Docker CLI 启动一次性、无宿主挂载的 Linux 沙箱。"""

from __future__ import annotations

import asyncio
import shutil

from coding_agent.sandbox.contracts import (
    SandboxExecutor,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)

_PATCH_MARKER = "__CODING_AGENT_PATCH_4deac1a2__"
_FILES_MARKER = "__CODING_AGENT_FILES_4deac1a2__"


def _wrapper_script() -> str:
    return (
        "set -eu\n"
        "tar -xf - -C /workspace\n"
        "cd /workspace\n"
        "git init -q\n"
        # /workspace is a root-owned tmpfs mount while the sandbox runs as
        # an unprivileged user. Trust only this disposable in-container path.
        "git config --global --add safe.directory /workspace\n"
        "git config user.email sandbox@local\n"
        "git config user.name sandbox\n"
        "git add -A\n"
        "git commit --allow-empty -qm baseline\n"
        "set +e\n"
        "sh -c \"$1\"\n"
        "status=$?\n"
        "set -e\n"
        "git add -N -- .\n"
        f"printf '\\n{_PATCH_MARKER}\\n'\n"
        "git diff --binary --no-ext-diff --no-renames HEAD\n"
        f"printf '\\n{_FILES_MARKER}\\n'\n"
        "git diff --no-renames --name-only HEAD\n"
        "exit $status\n"
    )


class DockerSandboxExecutor(SandboxExecutor):
    """Docker Desktop/Linux containers 的最小隔离实现。"""

    def __init__(self, docker_binary: str | None = None) -> None:
        self.docker_binary = docker_binary or shutil.which("docker")

    async def execute(self, request: SandboxRequest, cancellation: object) -> SandboxResult:
        if self.docker_binary is None:
            return SandboxResult(
                status="unavailable",
                summary="Docker was not found; install Docker Desktop and build the sandbox image",
            )
        limits = request.limits
        wrapper = _wrapper_script()
        command = [
            self.docker_binary,
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--init",
            "--user",
            "10001:10001",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            str(limits.pids_limit),
            "--memory",
            f"{limits.memory_mb}m",
            "--cpus",
            str(limits.cpu_count),
            "--ulimit",
            "nofile=256:256",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,mode=1777,size={limits.tmpfs_mb}m",
            "--tmpfs",
            f"/workspace:rw,nosuid,mode=1777,size={limits.tmpfs_mb}m",
            "--env",
            "HOME=/tmp",
            "--env",
            "XDG_CACHE_HOME=/tmp/.cache",
            "-w",
            "/workspace",
            request.image,
            "sh",
            "-c",
            wrapper,
            "sandbox-wrapper",
            request.command,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return SandboxResult(status="unavailable", summary=f"Docker could not start: {error}")
        archive = await asyncio.to_thread(request.snapshot.archive.read_bytes)
        communicate = asyncio.create_task(process.communicate(archive))
        cancelled = asyncio.create_task(_wait(cancellation))
        try:
            done, pending = await asyncio.wait(
                {communicate, cancelled},
                timeout=limits.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                await _stop_process(process, communicate)
                return SandboxResult(status="cancelled", summary="sandbox command cancelled")
            if communicate not in done:
                await _stop_process(process, communicate)
                return SandboxResult(status="timeout", summary="sandbox command timed out")
            stdout, stderr = communicate.result()
        finally:
            if not cancelled.done():
                cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
        output, patch, files = _split_output(stdout.decode(errors="replace"))
        stderr_text = stderr.decode(errors="replace")
        output = (output + stderr_text)[:12_000]
        if process.returncode == 125 and not patch:
            detail = stderr_text.strip().splitlines()[-1:] or ["unknown Docker error"]
            return SandboxResult(
                status="unavailable",
                summary=f"Docker could not create the sandbox: {detail[0][:500]}",
                output=output,
                exit_code=process.returncode,
            )
        status: SandboxStatus = "success" if process.returncode == 0 else "execution_error"
        return SandboxResult(
            status=status,
            summary="sandbox command completed",
            output=output,
            exit_code=process.returncode,
            patch=patch,
            changed_files=files,
        )


async def _wait(cancellation: object) -> bool:
    wait = getattr(cancellation, "wait", None)
    if wait is None:
        return False
    return bool(await wait())


async def _stop_process(
    process: asyncio.subprocess.Process, communicate: asyncio.Task[tuple[bytes, bytes]]
) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(asyncio.shield(communicate), timeout=3)
    except TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(asyncio.shield(communicate), timeout=3)
        except TimeoutError:
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)


def _split_output(stdout: str) -> tuple[str, str, list[str]]:
    if _PATCH_MARKER not in stdout or _FILES_MARKER not in stdout:
        return stdout, "", []
    # The command's stdout is untrusted and can contain marker-like text.  The
    # wrapper writes its own markers only after the command has completed.
    output, remainder = stdout.rsplit(_PATCH_MARKER, 1)
    patch, files = remainder.rsplit(_FILES_MARKER, 1)
    return output.rstrip(), patch.strip(), [line for line in files.splitlines() if line.strip()]
