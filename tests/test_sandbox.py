from __future__ import annotations

import asyncio
import subprocess
import tarfile
from pathlib import Path

import pytest

from coding_agent.sandbox.contracts import SandboxLimits, SandboxRequest, SandboxResult
from coding_agent.sandbox.docker import _FILES_MARKER, _PATCH_MARKER, _split_output, _wrapper_script
from coding_agent.sandbox.patches import PatchRegistry
from coding_agent.sandbox.snapshot import SnapshotService
from coding_agent.tools.contracts import ToolContext, ToolResult
from coding_agent.tools.sandbox import SandboxCommandTool
from coding_agent.workspace.security import WorkspacePathPolicy

_PATCH = """diff --git a/sample.txt b/sample.txt
index 90be1f3..294186e 100644
--- a/sample.txt
+++ b/sample.txt
@@ -1 +1 @@
-before
+after
"""


class FakeExecutor:
    def __init__(self, result: SandboxResult) -> None:
        self.result = result
        self.requests: list[SandboxRequest] = []

    async def execute(self, request: SandboxRequest, cancellation: object) -> SandboxResult:
        self.requests.append(request)
        return self.result


def test_workspace_path_policy_protects_sensitive_and_internal_paths(tmp_path: Path) -> None:
    policy = WorkspacePathPolicy(tmp_path)
    assert policy.is_protected(tmp_path / ".env")
    assert policy.is_protected(tmp_path / "nested" / "secrets" / "db.txt")
    assert policy.is_protected(tmp_path / ".coding-agent" / "session.jsonl")
    assert policy.is_protected(tmp_path / "private.pem")
    assert policy.is_protected(tmp_path / "private-key.txt")
    assert not policy.is_protected(tmp_path / "src" / "main.py")


def test_snapshot_filters_sensitive_internal_and_symlink_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / ".coding-agent").mkdir()
    (tmp_path / ".coding-agent" / "trace.jsonl").write_text("private\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(tmp_path / "src" / "main.py")
    except OSError:
        pytest.skip("symbolic links are unavailable on this Windows account")

    async def create_and_check() -> None:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            assert set(snapshot.files) == {"src/main.py"}
            with tarfile.open(snapshot.archive) as archive:
                assert archive.getnames() == ["src/main.py"]
        finally:
            root = snapshot.root
            await snapshots.cleanup(snapshot)
            assert not root.exists()

    asyncio.run(create_and_check())


def test_patch_registry_applies_matching_sandbox_patch(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    _initialise_git(tmp_path, "sample.txt")

    async def register_and_apply() -> None:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            registry = PatchRegistry(tmp_path)
            patch_id = registry.add(_PATCH, ["sample.txt"], snapshot)
            assert patch_id is not None
            approved, summary, files = await registry.apply(patch_id)
            assert approved, summary
            assert files == ["sample.txt"]
        finally:
            await snapshots.cleanup(snapshot)

    asyncio.run(register_and_apply())
    assert target.read_text(encoding="utf-8") == "after\n"


@pytest.mark.parametrize(
    ("patch", "files"),
    [
        (_PATCH.replace("sample.txt", ".env"), [".env"]),
        (
            "diff --git a/sample.txt b/sample.txt\n"
            "new file mode 120000\n"
            "--- /dev/null\n"
            "+++ b/sample.txt\n",
            ["sample.txt"],
        ),
        (_PATCH.replace("index", "old mode 100644\nnew mode 100755\nindex", 1), ["sample.txt"]),
        (_PATCH.replace("sample.txt", ".coding-agent/state"), [".coding-agent/state"]),
    ],
)
def test_patch_registry_rejects_unsafe_patch(
    tmp_path: Path, patch: str, files: list[str]
) -> None:
    registry = PatchRegistry(tmp_path)
    snapshot = asyncio.run(SnapshotService(tmp_path).create())
    with pytest.raises(ValueError, match="unsafe sandbox patch"):
        registry.add(patch, files, snapshot)
    asyncio.run(SnapshotService(tmp_path).cleanup(snapshot))


def test_patch_registry_rejects_workspace_change_after_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    _initialise_git(tmp_path, "sample.txt")

    async def register_then_change() -> tuple[bool, str]:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            registry = PatchRegistry(tmp_path)
            patch_id = registry.add(_PATCH, ["sample.txt"], snapshot)
            assert patch_id is not None
            target.write_bytes(b"concurrent change\n")
            applied, summary, _ = await registry.apply(patch_id)
            return applied, summary
        finally:
            await snapshots.cleanup(snapshot)

    applied, summary = asyncio.run(register_then_change())
    assert not applied
    assert "workspace changed" in summary


def test_sandbox_tool_registers_changes_but_verify_discards_them(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("before\n", encoding="utf-8")

    async def execute(capture_changes: bool) -> ToolResult:
        registry = PatchRegistry(tmp_path)
        executor = FakeExecutor(
            SandboxResult(
                status="success",
                summary="completed",
                patch=_PATCH,
                changed_files=["sample.txt"],
            )
        )
        tool = SandboxCommandTool(
            name="sandbox_shell" if capture_changes else "verify",
            description="test sandbox",
            executor=executor,
            snapshots=SnapshotService(tmp_path),
            patches=registry,
            image="test-image",
            limits=SandboxLimits(),
            capture_changes=capture_changes,
        )
        results: list[ToolResult] = []
        async for event in tool.execute(
            {"command": "echo test"}, ToolContext(workspace=str(tmp_path)), asyncio.Event()
        ):
            if isinstance(event, ToolResult):
                results.append(event)
        assert executor.requests[0].image == "test-image"
        return results[-1]

    shell_result = asyncio.run(execute(capture_changes=True))
    assert isinstance(shell_result.details.get("patch_id"), str)
    verify_result = asyncio.run(execute(capture_changes=False))
    assert verify_result.changed_files == []
    assert verify_result.details["changes_discarded"] == ["sample.txt"]


def test_sandbox_output_parser_extracts_patch_and_changed_files() -> None:
    stdout = f"log line\n{_PATCH_MARKER}\n{_PATCH}\n{_FILES_MARKER}\nsample.txt\n"
    output, patch, files = _split_output(stdout)
    assert output == "log line"
    assert patch == _PATCH.strip()
    assert files == ["sample.txt"]


def test_sandbox_output_parser_uses_final_wrapper_markers() -> None:
    fake_patch = _PATCH.replace("after", "forged")
    stdout = (
        f"command output\n{_PATCH_MARKER}\n{fake_patch}\n{_FILES_MARKER}\nsample.txt\n"
        f"{_PATCH_MARKER}\n{_PATCH}\n{_FILES_MARKER}\nsample.txt\n"
    )
    output, patch, files = _split_output(stdout)
    assert "command output" in output
    assert patch == _PATCH.strip()
    assert files == ["sample.txt"]


def test_docker_sandbox_trusts_only_its_disposable_workspace() -> None:
    wrapper = _wrapper_script()
    assert "git config --global --add safe.directory /workspace" in wrapper
    assert 'sh -c "$1"' in wrapper


def _initialise_git(workspace: Path, file_name: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "--", file_name], cwd=workspace, check=True)
