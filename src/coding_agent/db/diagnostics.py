"""Database connectivity diagnostics for CLI and migrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import make_url

from coding_agent.db.engine import create_database_engine


@dataclass(frozen=True)
class DatabaseHealth:
    status: Literal["ok", "error"]
    redacted_url: str
    drivername: str = ""
    username: str = ""
    host: str = ""
    port: int | None = None
    database: str = ""
    current_database: str = ""
    current_user: str = ""
    server_version: str = ""
    error: str = ""
    hints: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def check_database_url(database_url: str) -> DatabaseHealth:
    """Validate that the configured database URL can be reached."""
    try:
        url = make_url(database_url)
    except Exception as error:
        return DatabaseHealth(
            status="error",
            redacted_url="[invalid database url]",
            error=str(error),
            hints=(
                "检查数据库 URL 格式，应类似 "
                "mysql+pymysql://user:password@localhost:3306/coding_agent?charset=utf8mb4。",
            ),
        )

    engine = None
    redacted_url = _redact_url(database_url)
    try:
        engine = create_database_engine(database_url)
        with engine.connect() as connection:
            if url.drivername.startswith("mysql"):
                row = connection.execute(
                    text("SELECT DATABASE(), CURRENT_USER(), VERSION()")
                ).one()
                current_database = str(row[0] or "")
                current_user = str(row[1] or "")
                server_version = str(row[2] or "")
            else:
                connection.execute(text("SELECT 1")).one()
                current_database = url.database or ""
                current_user = url.username or ""
                server_version = url.drivername
    except Exception as error:
        message = _safe_error_message(error, database_url)
        return DatabaseHealth(
            status="error",
            redacted_url=redacted_url,
            drivername=url.drivername,
            username=url.username or "",
            host=url.host or "",
            port=url.port,
            database=url.database or "",
            error=message,
            hints=_database_error_hints(message, username=url.username or ""),
        )
    finally:
        if engine is not None:
            engine.dispose()

    return DatabaseHealth(
        status="ok",
        redacted_url=redacted_url,
        drivername=url.drivername,
        username=url.username or "",
        host=url.host or "",
        port=url.port,
        database=url.database or "",
        current_database=current_database,
        current_user=current_user,
        server_version=server_version,
    )


def database_health_text(health: DatabaseHealth) -> str:
    """Render a concise, secret-safe database health report."""
    lines = [
        f"数据库 URL：{health.redacted_url}",
        f"驱动：{health.drivername or 'unknown'}",
    ]
    if health.host:
        host = health.host if health.port is None else f"{health.host}:{health.port}"
        lines.append(f"主机：{host}")
    if health.database:
        lines.append(f"目标数据库：{health.database}")
    if health.ok:
        lines.extend(
            [
                "状态：连接成功",
                f"当前数据库：{health.current_database or '-'}",
                f"当前用户：{health.current_user or '-'}",
                f"MySQL/数据库版本：{health.server_version or '-'}",
            ]
        )
        return "\n".join(lines)

    lines.extend(["状态：连接失败", f"错误：{health.error}"])
    if health.hints:
        lines.append("建议：")
        lines.extend(f"- {hint}" for hint in health.hints)
    return "\n".join(lines)


def database_connection_error_message(database_url: str, error: BaseException) -> str:
    """Build a secret-safe migration/runtime error with actionable hints."""
    try:
        url = make_url(database_url)
    except Exception:
        return database_health_text(
            DatabaseHealth(
                status="error",
                redacted_url="[invalid database url]",
                error=str(error),
                hints=(
                    "检查数据库 URL 格式，应类似 "
                    "mysql+pymysql://user:password@localhost:3306/coding_agent?charset=utf8mb4。",
                ),
            )
        )
    message = _safe_error_message(error, database_url)
    health = DatabaseHealth(
        status="error",
        redacted_url=_redact_url(database_url),
        drivername=url.drivername,
        username=url.username or "",
        host=url.host or "",
        port=url.port,
        database=url.database or "",
        error=message,
        hints=_database_error_hints(message, username=url.username or ""),
    )
    return database_health_text(health)


def _database_error_hints(message: str, *, username: str) -> tuple[str, ...]:
    normalized = message.lower()
    if "cryptography" in normalized and (
        "caching_sha2_password" in normalized or "sha256_password" in normalized
    ):
        return (
            "运行 `uv --cache-dir .uv-cache sync` 安装锁文件中的 `cryptography` 依赖。",
            "项目依赖应保留 `pymysql[rsa]`，以兼容 MySQL 8 默认认证方式。",
        )
    if "1045" in normalized or "access denied for user" in normalized:
        user_hint = (
            f"当前数据库 URL 使用的是 `{username}` 用户；确认该用户确实存在，且密码正确。"
            if username
            else "确认数据库 URL 中包含正确的用户名和密码。"
        )
        return (
            user_hint,
            "如果你本机只确认 root 可登录，本地验证可临时使用 "
            "`mysql+pymysql://root:<root-password>@localhost:3306/coding_agent?charset=utf8mb4`。",
            "企业/长期用法建议用 root 登录 MySQL 后创建独立 `coding_agent` 用户，"
            "并执行 GRANT 授权。",
        )
    if "1049" in normalized or "unknown database" in normalized:
        return (
            "目标数据库不存在；用 root 登录 MySQL 后执行 "
            "`CREATE DATABASE coding_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`。",
        )
    if "1044" in normalized:
        return (
            "当前用户没有目标数据库权限；用 root 登录 MySQL 后执行 "
            "`GRANT ALL PRIVILEGES ON coding_agent.* TO 'coding_agent'@'localhost';`。",
        )
    if "2003" in normalized or "can't connect to mysql server" in normalized:
        return (
            "确认 MySQL 服务已启动；Windows 常见服务名是 `MySQL80`。",
            "确认 URL 中的 host/port 正确，默认通常是 `localhost:3306`。",
        )
    if "2005" in normalized or "unknown mysql server host" in normalized:
        return ("确认数据库 URL 中的主机名正确，本地 MySQL 通常使用 `localhost`。",)
    return (
        "先运行 `uv --cache-dir .uv-cache run agent db-check --database-url \"...\"` 做连接诊断。",
        "确认 MySQL 服务、用户名、密码、数据库名和授权都与数据库 URL 一致。",
    )


def _safe_error_message(error: BaseException, database_url: str) -> str:
    original = getattr(error, "orig", None)
    message = str(original if original is not None else error)
    password = make_url(database_url).password
    if password:
        message = message.replace(password, "***")
    return message.replace(database_url, _redact_url(database_url))


def _redact_url(database_url: str) -> str:
    try:
        url = make_url(database_url)
    except Exception:
        return "[invalid database url]"
    if url.drivername.startswith("sqlite"):
        return url.render_as_string(hide_password=True)
    host = url.host or ""
    if url.port is not None:
        host = f"{host}:{url.port}"
    database = f"/{url.database}" if url.database else ""
    credentials = "***:***@" if url.username or url.password else ""
    return f"{url.drivername}://{credentials}{host}{database}"
