from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pytest import MonkeyPatch
from typer.testing import CliRunner

from coding_agent.cli.app import _build_session_store, _exit_text, app
from coding_agent.db.diagnostics import DatabaseHealth
from coding_agent.sessions.mysql import MySqlSessionStore

runner = CliRunner()


def test_exit_text_includes_resume_command_with_quoted_workspace() -> None:
    session = SimpleNamespace(
        session_id="session-123",
        _agent=SimpleNamespace(config=SimpleNamespace(workspace=Path("D:/Work Dir/Repo"))),
    )

    text = _exit_text(session)

    assert "会话已保存，再见。" in text
    assert "uv --cache-dir .uv-cache run agent chat" in text
    assert '--workspace "D:\\Work Dir\\Repo"' in text
    assert "--resume session-123" in text


def test_cli_session_store_uses_database_url(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'cli-sessions.db').as_posix()}"

    store = _build_session_store(
        tmp_path,
        storage_backend=None,
        database_url=database_url,
        database_create_schema=True,
    )

    assert isinstance(store, MySqlSessionStore)
    store.engine.dispose()


def test_db_check_reports_success_without_model_configuration(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'health.db').as_posix()}"

    result = runner.invoke(
        app,
        [
            "db-check",
            "--workspace",
            str(tmp_path),
            "--database-url",
            database_url,
        ],
    )

    assert result.exit_code == 0
    assert "状态：连接成功" in result.output
    assert "sqlite+pysqlite" in result.output


def test_db_check_uses_database_url_environment(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_AGENT_DATABASE_URL", "sqlite+pysqlite:///:memory:")

    result = runner.invoke(app, ["db-check", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "状态：连接成功" in result.output


def test_db_check_explains_mysql_access_denied(tmp_path: Path) -> None:
    health = DatabaseHealth(
        status="error",
        redacted_url="mysql+pymysql://***:***@localhost:3306/coding_agent",
        drivername="mysql+pymysql",
        username="coding_agent",
        host="localhost",
        port=3306,
        database="coding_agent",
        error="(1045, \"Access denied for user 'coding_agent'@'localhost'\")",
        hints=(
            "当前数据库 URL 使用的是 `coding_agent` 用户；确认该用户确实存在，且密码正确。",
            "如果你本机只确认 root 可登录，本地验证可临时使用 "
            "`mysql+pymysql://root:<root-password>@localhost:3306/coding_agent?charset=utf8mb4`。",
        ),
    )

    with patch("coding_agent.cli.app.check_database_url", return_value=health):
        result = runner.invoke(
            app,
            [
                "db-check",
                "--workspace",
                str(tmp_path),
                "--database-url",
                "mysql+pymysql://coding_agent:wrong@localhost:3306/coding_agent",
            ],
        )

    assert result.exit_code == 1
    assert "状态：连接失败" in result.output
    assert "Access denied" in result.output
    assert "root:<root-password>" in result.output
