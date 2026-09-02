from __future__ import annotations

import asyncio
import subprocess
import tarfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from coding_agent.db import initialize_database, tables
from coding_agent.sandbox.contracts import SandboxLimits, SandboxRequest, SandboxResult
from coding_agent.sandbox.docker import _FILES_MARKER, _PATCH_MARKER, _split_output, _wrapper_script
from coding_agent.sandbox.patches import MySqlPatchStore, PatchRegistry
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
    assert policy.is_protected(tmp_path / ".env.local")
    assert policy.is_protected(tmp_path / "nested" / "secrets" / "db.txt")
    assert policy.is_protected(tmp_path / ".ssh" / "id_rsa")
    assert policy.is_protected(tmp_path / ".coding-agent" / "session.jsonl")
    assert policy.is_protected(tmp_path / "private.pem")
    assert policy.is_protected(tmp_path / "private-key.txt")
    assert policy.is_protected(tmp_path / "credentials.json")
    assert policy.is_protected(tmp_path / "token.txt")
    assert not policy.is_protected(tmp_path / "src" / "main.py")


def test_workspace_path_policy_allows_secret_like_source_names(tmp_path: Path) -> None:
    policy = WorkspacePathPolicy(tmp_path)

    allowed = [
        "src/coding_agent/runtime/token_usage.py",
        "tests/test_token_usage.py",
        "src/auth/token_validator.py",
        "src/security/secret_scanner.py",
        "docs/token_usage.md",
    ]

    for relative in allowed:
        assert not policy.is_protected(tmp_path / relative), relative


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


def test_snapshot_keeps_secret_like_source_names_without_leaking_sensitive_files(
    tmp_path: Path,
) -> None:
    source_files = {
        "src/coding_agent/runtime/token_usage.py": "TOKEN_USAGE = True\n",
        "tests/test_token_usage.py": "def test_token_usage():\n    assert True\n",
        "src/auth/token_validator.py": "def validate_token():\n    return True\n",
    }
    sensitive_files = {
        ".env": "TOKEN=secret\n",
        "secrets/db.txt": "password\n",
        ".ssh/id_rsa": "private-key\n",
        "private.key": "private-key\n",
        "credentials.json": "{}\n",
    }
    for relative, content in {**source_files, **sensitive_files}.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def create_and_check() -> None:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            assert set(source_files) <= set(snapshot.files)
            assert not set(sensitive_files) & set(snapshot.files)
            with tarfile.open(snapshot.archive) as archive:
                names = set(archive.getnames())
            assert set(source_files) <= names
            assert not set(sensitive_files) & names
        finally:
            await snapshots.cleanup(snapshot)

    asyncio.run(create_and_check())


def test_current_project_source_names_are_not_treated_as_sensitive() -> None:
    root = Path(__file__).resolve().parents[1]
    policy = WorkspacePathPolicy(root)

    for relative in (
        "src/coding_agent/runtime/token_usage.py",
        "src/coding_agent/tools/plan.py",
        "tests/test_plan_mode.py",
    ):
        path = root / relative
        assert path.exists(), relative
        assert not policy.is_protected(path), relative


def test_patch_registry_applies_matching_sandbox_patch(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    _initialise_git(tmp_path, "sample.txt")

    async def register_and_apply() -> None:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            registry = PatchRegistry(tmp_path)
            patch_id = await registry.add(_PATCH, ["sample.txt"], snapshot)
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
        asyncio.run(registry.add(patch, files, snapshot))
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
            patch_id = await registry.add(_PATCH, ["sample.txt"], snapshot)
            assert patch_id is not None
            target.write_bytes(b"concurrent change\n")
            applied, summary, _ = await registry.apply(patch_id)
            return applied, summary
        finally:
            await snapshots.cleanup(snapshot)

    applied, summary = asyncio.run(register_then_change())
    assert not applied
    assert "workspace changed" in summary


def test_mysql_patch_store_persists_and_applies_from_new_registry(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    _initialise_git(tmp_path, "sample.txt")
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'patches.db').as_posix()}",
        future=True,
    )
    initialize_database(engine)

    async def register_and_apply_after_restart() -> None:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            first = PatchRegistry(tmp_path, store=MySqlPatchStore(engine))
            patch_id = await first.add(
                _PATCH,
                ["sample.txt"],
                snapshot,
                session_id="session-patch",
                run_id="run-patch",
            )
            assert patch_id is not None

            restarted = PatchRegistry(tmp_path, store=MySqlPatchStore(engine))
            details = await restarted.approval_details(patch_id)
            assert details["status"] == "pending"
            assert details["changed_files"] == ["sample.txt"]
            assert "after" in str(details["diff_preview"])

            applied, summary, files = await restarted.apply(patch_id, applied_by="test")
            assert applied, summary
            assert files == ["sample.txt"]

            record = await restarted.store.get(patch_id)
            assert record.status == "applied"
            assert record.applied_by == "test"
        finally:
            await snapshots.cleanup(snapshot)

    try:
        asyncio.run(register_and_apply_after_restart())
        assert target.read_text(encoding="utf-8") == "after\n"
    finally:
        engine.dispose()


def test_mysql_patch_store_invalidates_workspace_drift(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    _initialise_git(tmp_path, "sample.txt")
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'drift.db').as_posix()}",
        future=True,
    )
    initialize_database(engine)

    async def register_then_drift() -> tuple[bool, str, str]:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            registry = PatchRegistry(tmp_path, store=MySqlPatchStore(engine))
            patch_id = await registry.add(
                _PATCH,
                ["sample.txt"],
                snapshot,
                session_id="session-drift",
                run_id="run-drift",
            )
            assert patch_id is not None
            target.write_text("concurrent change\n", encoding="utf-8")
            applied, summary, _ = await PatchRegistry(
                tmp_path, store=MySqlPatchStore(engine)
            ).apply(patch_id)
            record = await registry.store.get(patch_id)
            return applied, summary, record.status
        finally:
            await snapshots.cleanup(snapshot)

    try:
        applied, summary, status = asyncio.run(register_then_drift())
        assert not applied
        assert "workspace changed" in summary
        assert status == "invalidated"
    finally:
        engine.dispose()


def test_patch_registry_does_not_apply_same_patch_twice(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    _initialise_git(tmp_path, "sample.txt")

    async def register_and_apply_twice() -> tuple[bool, str]:
        snapshots = SnapshotService(tmp_path)
        snapshot = await snapshots.create()
        try:
            registry = PatchRegistry(tmp_path)
            patch_id = await registry.add(_PATCH, ["sample.txt"], snapshot)
            assert patch_id is not None
            first, first_summary, _ = await registry.apply(patch_id)
            assert first, first_summary
            second, second_summary, _ = await registry.apply(patch_id)
            return second, second_summary
        finally:
            await snapshots.cleanup(snapshot)

    applied, summary = asyncio.run(register_and_apply_twice())
    assert not applied
    assert "applied" in summary
    assert target.read_text(encoding="utf-8") == "after\n"


def test_schema_contains_persistent_patches_table(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'schema.db').as_posix()}",
        future=True,
    )
    try:
        initialize_database(engine)
        columns = {column["name"] for column in inspect(engine).get_columns("patches")}

        assert "patches" in inspect(engine).get_table_names()
        assert {
            "patch_id",
            "status",
            "patch_text",
            "patch_sha256",
            "changed_files",
            "snapshot_files",
            "diff_preview",
            "invalidated_reason",
        }.issubset(columns)
        assert tables.patches.name == "patches"
    finally:
        engine.dispose()


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
