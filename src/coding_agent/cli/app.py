"""Typer 命令行展示层。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from coding_agent.agent.coding_agent import ChatSession, CodingAgent
from coding_agent.ai.gateway import ModelProviderError, create_model_adapter
from coding_agent.config import AgentConfig
from coding_agent.db import check_database_url, database_health_text
from coding_agent.memory import (
    MemoryExtractor,
    MemoryReviewService,
    ModelExtractor,
    NoopMemoryStore,
    ReviewError,
    create_memory_sync_service,
    persist_candidates,
)
from coding_agent.runtime.events import AgentEvent
from coding_agent.runtime.loop import ApprovalProvider
from coding_agent.semantic.contracts import SemanticIndexError
from coding_agent.semantic.service import create_semantic_service
from coding_agent.sessions.factory import (
    StorageConfigError,
    create_memory_store,
    create_session_store,
)
from coding_agent.sessions.lock import SessionLockedError, acquire_session_lock
from coding_agent.sessions.store import SessionEvent, SessionStore

app = typer.Typer(help="安全优先的本地编程 Agent。", no_args_is_help=True)


class TerminalApproval(ApprovalProvider):
    async def request(
        self,
        tool_name: str,
        reason: str,
        params: dict[str, object],
        *,
        session_id: str = "",
        run_id: str = "",
    ) -> bool:
        objective = params.get("objective")
        if isinstance(objective, str) and objective:
            typer.echo("目标：\n" + objective)
        plan = params.get("plan")
        if isinstance(plan, str) and plan:
            typer.echo("计划：\n" + plan)
        steps = params.get("steps")
        if isinstance(steps, list) and steps:
            typer.echo("步骤：" + ", ".join(str(item) for item in steps))
        files = params.get("files")
        if isinstance(files, list) and files:
            typer.echo("预计修改文件：" + ", ".join(str(item) for item in files))
        verification = params.get("verification_commands")
        if isinstance(verification, list) and verification:
            typer.echo("验证命令：" + ", ".join(str(item) for item in verification))
        risks = params.get("risks")
        if isinstance(risks, list) and risks:
            typer.echo("风险：" + ", ".join(str(item) for item in risks))
        revision_of = params.get("revision_of")
        if isinstance(revision_of, str) and revision_of:
            typer.echo("修订自计划：" + revision_of)
        failure_summary = params.get("failure_summary")
        if isinstance(failure_summary, str) and failure_summary:
            typer.echo("上次失败：" + failure_summary)
        changed_approach = params.get("changed_approach")
        if isinstance(changed_approach, str) and changed_approach:
            typer.echo("调整方案：" + changed_approach)
        command = params.get("command")
        if isinstance(command, str):
            typer.echo("沙箱命令：\n" + command)
        changed_files = params.get("changed_files")
        if isinstance(changed_files, list):
            typer.echo("待回写文件：" + ", ".join(str(item) for item in changed_files))
        preview = params.get("diff_preview")
        if isinstance(preview, str):
            typer.echo("待回写补丁：\n" + preview)
        return typer.confirm(f"批准 {tool_name} 操作吗？原因：{reason}", default=False)


def _build_agent(
    workspace: Path,
    provider: str | None,
    model: str | None,
    allow_write: bool,
    allow_shell: bool,
    plan_mode: bool,
    non_interactive: bool,
    sandbox_image: str | None = None,
    storage_backend: str | None = None,
    database_url: str | None = None,
    database_create_schema: bool = False,
) -> CodingAgent:
    overrides: dict[str, object] = {
        "allow_write": allow_write,
        "allow_shell": allow_shell,
        "plan_mode": plan_mode,
        "non_interactive": non_interactive,
    }
    if provider is not None:
        overrides["model_provider"] = provider
    if model is not None:
        overrides["model"] = model
    if sandbox_image is not None:
        overrides["sandbox_image"] = sandbox_image
    if storage_backend is not None:
        overrides["storage_backend"] = storage_backend
    if database_url is not None:
        overrides["database_url"] = database_url
    if database_create_schema:
        overrides["database_create_schema"] = True
    config = AgentConfig.from_environment(
        workspace,
        **overrides,
    )
    try:
        adapter = create_model_adapter(config)
    except ModelProviderError as error:
        raise typer.BadParameter(str(error), param_hint="--provider") from error
    try:
        return CodingAgent(config, adapter, None if non_interactive else TerminalApproval())
    except StorageConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--database-url") from error


def _build_session_store(
    workspace: Path,
    storage_backend: str | None,
    database_url: str | None,
    database_create_schema: bool,
) -> SessionStore:
    overrides: dict[str, object] = {}
    if storage_backend is not None:
        overrides["storage_backend"] = storage_backend
    if database_url is not None:
        overrides["database_url"] = database_url
    if database_create_schema:
        overrides["database_create_schema"] = True
    config = AgentConfig.from_environment(workspace, **overrides)
    try:
        return create_session_store(config, config.workspace / ".coding-agent")
    except StorageConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--database-url") from error


@app.command("db-check")
def db_check(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL to check.")
    ] = None,
) -> None:
    """检查数据库 URL、认证和连通性。"""
    overrides: dict[str, object] = {}
    if database_url is not None:
        overrides["database_url"] = database_url
    config = AgentConfig.from_environment(workspace, **overrides)
    if config.database_url is None:
        raise typer.BadParameter(
            "database URL is required. Set CODING_AGENT_DATABASE_URL or pass --database-url.",
            param_hint="--database-url",
        )
    health = check_database_url(config.database_url.get_secret_value())
    typer.echo(database_health_text(health))
    if not health.ok:
        raise typer.Exit(code=1)


@app.command("index-workspace")
def index_workspace(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
) -> None:
    """使用 DashScope embedding 将当前工作区代码索引到 Milvus。"""
    config = AgentConfig.from_environment(workspace)
    try:
        stats = asyncio.run(create_semantic_service(config).build_index())
    except SemanticIndexError as error:
        raise typer.BadParameter(str(error), param_hint="semantic config") from error
    typer.echo(
        "\n".join(
            [
                f"后端：{stats.backend}",
                f"Collection：{stats.collection}",
                f"索引文件：{stats.indexed_files}",
                f"索引 chunk：{stats.indexed_chunks}",
                f"跳过文件：{stats.skipped_files}",
            ]
        )
    )


@app.command("semantic-search")
def semantic_search_command(
    query: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    top_k: Annotated[int | None, typer.Option("--top-k", min=1, max=50)] = None,
) -> None:
    """查询 Milvus 中的真实工作区语义索引。"""
    config = AgentConfig.from_environment(workspace)
    try:
        service = create_semantic_service(config)
        hits = asyncio.run(service.search(query, top_k=top_k))
    except SemanticIndexError as error:
        raise typer.BadParameter(str(error), param_hint="semantic config") from error
    typer.echo(service.format_hits(hits))


@app.command("extract-memories")
def extract_memories(
    session_id: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    no_model: Annotated[
        bool,
        typer.Option(
            "--no-model",
            help="Only run the deterministic rule extractor; skip the model extractor.",
        ),
    ] = False,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for memories.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables at startup for local development and tests.",
        ),
    ] = False,
) -> None:
    """从已持久化的 session 对话中提取候选记忆并写入 memory store。"""
    overrides: dict[str, object] = {}
    if provider is not None:
        overrides["model_provider"] = provider
    if model is not None:
        overrides["model"] = model
    if storage_backend is not None:
        overrides["storage_backend"] = storage_backend
    if database_url is not None:
        overrides["database_url"] = database_url
    if database_create_schema:
        overrides["database_create_schema"] = True
    config = AgentConfig.from_environment(workspace, **overrides)
    data_root = config.workspace / ".coding-agent"

    try:
        sessions = create_session_store(config, data_root)
    except StorageConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--database-url") from error

    checkpoint = asyncio.run(sessions.load_checkpoint(session_id))
    if checkpoint is None:
        typer.echo(f"未找到 session {session_id} 的 checkpoint，无法提取记忆。", err=True)
        raise typer.Exit(code=1)

    model_extractor = None
    if not no_model:
        try:
            adapter = create_model_adapter(config)
            model_extractor = ModelExtractor(adapter)
        except ModelProviderError:
            typer.echo(
                "提示：模型未配置，仅运行规则提取（使用 --no-model 可消除此提示）。",
                err=True,
            )

    extractor = MemoryExtractor(model=model_extractor, ttl_days=config.memory_ttl_days)
    candidates = asyncio.run(
        extractor.extract(
            checkpoint.messages,
            session_id=session_id,
            run_id="",
            user_id="",
            project_id=str(config.workspace.resolve()),
        )
    )

    try:
        memory_store = create_memory_store(config, data_root)
    except StorageConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--database-url") from error

    if isinstance(memory_store, NoopMemoryStore):
        typer.echo(
            "存储后端为 jsonl，记忆持久化需要 mysql（--database-url）；本次仅输出候选预览："
        )
        for record in candidates:
            typer.echo(
                f"- [{record.category}] conf={record.confidence:.2f} {record.content}"
            )
        typer.echo(f"候选总数：{len(candidates)}（未持久化）")
        return

    new, skipped = asyncio.run(persist_candidates(memory_store, candidates))
    typer.echo(
        "\n".join(
            [
                f"提取候选：{len(candidates)}",
                f"新增：{new}",
                f"跳过（已存在）：{skipped}",
            ]
        )
    )


@app.command("review-memories")
def review_memories(
    reviewer: Annotated[
        str, typer.Option("--reviewer", help="审核人标识，写入记忆的 reviewer 字段用于审计。")
    ],
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    user_id: Annotated[
        str, typer.Option("--user-id", help="记忆归属用户 ID。")
    ] = "",
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="记忆归属项目 ID；默认取 workspace 绝对路径。"),
    ] = None,
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for memories.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables at startup for local development and tests.",
        ),
    ] = False,
) -> None:
    """逐条审核候选记忆：promote 为 promoted 或 reject 为 rejected。"""
    overrides: dict[str, object] = {}
    if storage_backend is not None:
        overrides["storage_backend"] = storage_backend
    if database_url is not None:
        overrides["database_url"] = database_url
    if database_create_schema:
        overrides["database_create_schema"] = True
    config = AgentConfig.from_environment(workspace, **overrides)
    data_root = config.workspace / ".coding-agent"

    try:
        memory_store = create_memory_store(config, data_root)
    except StorageConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--database-url") from error

    if isinstance(memory_store, NoopMemoryStore):
        typer.echo(
            "存储后端为 jsonl，记忆审核需要 mysql（--database-url）；无法进行审核。",
            err=True,
        )
        raise typer.Exit(code=1)

    effective_project_id = project_id or str(config.workspace.resolve())
    service = MemoryReviewService(memory_store)
    candidates = asyncio.run(
        service.list_candidates(user_id=user_id, project_id=effective_project_id)
    )

    if not candidates:
        typer.echo("没有待审核的候选记忆。")
        return

    typer.echo(f"待审核候选：{len(candidates)} 条\n")

    promoted = 0
    rejected = 0
    skipped = 0
    for index, record in enumerate(candidates, start=1):
        typer.echo("─" * 60)
        typer.echo(
            f"[{index}/{len(candidates)}] id={record.memory_id[:12]}  "
            f"category={record.category}  confidence={record.confidence:.2f}"
        )
        typer.echo(f"  scope={record.scope}  source_session={record.source_session_id}")
        typer.echo(f"  content: {record.content}")
        choice = typer.prompt(
            "操作 [p]romote / [r]eject / [s]kip / [q]uit",
            default="s",
        ).strip().lower()

        if choice == "q":
            typer.echo("\n已退出审核。")
            break
        if choice == "s":
            skipped += 1
            continue
        if choice not in ("p", "r"):
            typer.echo(f"  未知操作 {choice!r}，按 skip 处理。")
            skipped += 1
            continue

        note = typer.prompt("审核备注（可留空）", default="").strip()
        try:
            if choice == "p":
                asyncio.run(
                    service.promote(
                        memory_id=record.memory_id,
                        reviewer=reviewer,
                        review_note=note,
                    )
                )
                promoted += 1
                typer.echo("  → promoted")
            else:
                asyncio.run(
                    service.reject(
                        memory_id=record.memory_id,
                        reviewer=reviewer,
                        review_note=note,
                    )
                )
                rejected += 1
                typer.echo("  → rejected")
        except ReviewError as error:
            typer.echo(f"  审核失败：{error}", err=True)
            skipped += 1

    typer.echo("\n" + "─" * 60)
    typer.echo(
        f"审核完成：promoted={promoted}  rejected={rejected}  skipped={skipped}"
    )


@app.command("sync-memories")
def sync_memories(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    user_id: Annotated[
        str, typer.Option("--user-id", help="记忆归属用户 ID。")
    ] = "",
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="记忆归属项目 ID；默认取 workspace 绝对路径。"),
    ] = None,
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for memories.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables at startup for local development and tests.",
        ),
    ] = False,
) -> None:
    """把 promoted 记忆批量写入 Milvus 记忆向量索引，供 recall 语义召回。"""
    overrides: dict[str, object] = {}
    if storage_backend is not None:
        overrides["storage_backend"] = storage_backend
    if database_url is not None:
        overrides["database_url"] = database_url
    if database_create_schema:
        overrides["database_create_schema"] = True
    config = AgentConfig.from_environment(workspace, **overrides)
    data_root = config.workspace / ".coding-agent"

    try:
        memory_store = create_memory_store(config, data_root)
    except StorageConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--database-url") from error

    if isinstance(memory_store, NoopMemoryStore):
        typer.echo(
            "存储后端为 jsonl，记忆同步需要 mysql（--database-url）；无法同步。",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        service = create_memory_sync_service(config, memory_store)
    except SemanticIndexError as error:
        raise typer.BadParameter(str(error), param_hint="semantic config") from error

    effective_project_id = project_id or str(config.workspace.resolve())
    try:
        stats = asyncio.run(
            service.sync(user_id=user_id, project_id=effective_project_id)
        )
    except Exception as error:  # 同步是离线批处理：失败直接退出，让调用方感知
        typer.echo(f"同步失败：{error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(
        "\n".join(
            [
                f"已同步 promoted 记忆：{stats.synced}",
                f"后端：{stats.backend}",
                f"Collection：{stats.collection}",
            ]
        )
    )


@app.command()
def run(
    task: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_write: Annotated[bool, typer.Option("--allow-write")] = False,
    allow_shell: Annotated[bool, typer.Option("--allow-shell")] = False,
    plan_mode: Annotated[bool, typer.Option("--plan")] = False,
    sandbox_image: Annotated[str | None, typer.Option("--sandbox-image")] = None,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for sessions.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables at startup for local development and tests.",
        ),
    ] = False,
) -> None:
    """执行一次任务。"""
    asyncio.run(
        _run_once(
            _build_agent(
                workspace,
                provider,
                model,
                allow_write,
                allow_shell,
                plan_mode,
                non_interactive,
                sandbox_image,
                storage_backend,
                database_url,
                database_create_schema,
            ),
            task,
        )
    )


@app.command()
def chat(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_write: Annotated[bool, typer.Option("--allow-write")] = False,
    allow_shell: Annotated[bool, typer.Option("--allow-shell")] = False,
    plan_mode: Annotated[bool, typer.Option("--plan")] = False,
    sandbox_image: Annotated[str | None, typer.Option("--sandbox-image")] = None,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
    resume: Annotated[str | None, typer.Option("--resume")] = None,
    force_unlock: Annotated[bool, typer.Option("--force-unlock")] = False,
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for sessions.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables at startup for local development and tests.",
        ),
    ] = False,
) -> None:
    """启动独占的连续聊天会话。"""
    agent = _build_agent(
        workspace,
        provider,
        model,
        allow_write,
        allow_shell,
        plan_mode,
        non_interactive,
        sandbox_image,
        storage_backend,
        database_url,
        database_create_schema,
    )
    asyncio.run(_chat_loop(agent, resume, force_unlock))


async def _run_once(agent: CodingAgent, task: str) -> None:
    session_id: str | None = None
    async for event in agent.run(task):
        session_id = event.session_id
        _render_event(event)
    if session_id:
        typer.echo(f"\n会话 ID：{session_id}")


async def _chat_loop(agent: CodingAgent, resume_id: str | None, force_unlock: bool) -> None:
    session = await agent.start_chat(resume_id)
    try:
        lease = acquire_session_lock(agent.data_root, session.session_id, force_unlock=force_unlock)
    except SessionLockedError as error:
        raise typer.BadParameter(str(error), param_hint="--resume") from error
    typer.echo(f"连续会话已就绪。会话 ID：{session.session_id}")
    typer.echo("输入问题开始；/help 查看命令，/exit 保存并退出。")
    try:
        while True:
            try:
                raw = await asyncio.to_thread(input, "\n你> ")
            except (EOFError, KeyboardInterrupt):
                typer.echo("\n" + _exit_text(session))
                return
            command = raw.strip()
            if not command:
                continue
            if command == "/exit":
                typer.echo(_exit_text(session))
                return
            if command == "/help":
                typer.echo(_help_text())
                continue
            if command == "/status":
                typer.echo(_status_text(session))
                continue
            if command == "/sessions":
                await _print_sessions(agent)
                continue
            if command == "/history":
                await _print_history(agent, session.session_id)
                continue
            if command == "/new":
                lease.release()
                session = await agent.start_chat()
                lease = acquire_session_lock(agent.data_root, session.session_id)
                typer.echo(f"已创建新会话：{session.session_id}")
                continue
            if command == "/cancel":
                typer.echo("当前为空闲状态。运行中请按 Ctrl+C 取消当前回合。")
                continue
            if command == "/clear":
                if typer.confirm("确定清空当前模型上下文吗？事件日志会保留。", default=False):
                    await session.clear_context()
                    typer.echo("上下文已清空。")
                continue
            try:
                async for event in session.send(raw):
                    _render_event(event)
            except KeyboardInterrupt:
                session.cancel_current_turn()
                typer.echo("\n已请求取消当前回合。")
    finally:
        lease.release()


def _render_event(event: AgentEvent) -> None:
    if event.type == "message_delta":
        typer.echo(str(event.payload.get("text", "")), nl=False)
    elif event.type == "run_finished":
        typer.echo("")
    elif event.type == "approval_requested":
        typer.echo(f"\n需要审批：{event.payload.get('tool')}。{event.payload.get('reason')}")
    elif event.type == "approval_resolved":
        approval_label = "已批准" if event.payload.get("approved") else "已拒绝"
        typer.echo(f"\n审批结果：{event.payload.get('tool')} {approval_label}")
    elif event.type == "plan_approved":
        plan_id = event.payload.get("current_plan_id", "")
        revision_count = event.payload.get("revision_count", 0)
        typer.echo(f"\n计划已批准：{plan_id}，修订次数：{revision_count}")
    elif event.type == "plan_rejected":
        typer.echo("\n计划已拒绝：高风险工具仍保持阻塞")
    elif event.type == "plan_failed":
        reason = event.payload.get("last_failure_summary", "")
        typer.echo(f"\n计划已失效：{reason}")
    elif event.type == "plan_revision_required":
        reason = event.payload.get("reason", "")
        typer.echo(f"\n需要修订计划：{reason}")
    elif event.type == "tool_finished":
        tool_result = event.payload.get("result")
        if isinstance(tool_result, dict) and tool_result.get("status") != "success":
            artifact = event.payload.get("artifact")
            suffix = f"；详情：{artifact}" if artifact else ""
            typer.echo(
                f"\n工具未成功：{event.payload.get('tool')}，"
                f"{tool_result.get('summary', '未知原因')}{suffix}"
            )
    elif event.type == "context_compacted":
        before = _payload_int(event.payload, "before_tokens")
        after = _payload_int(event.payload, "after_tokens")
        saved = max(0, before - after)
        typer.echo(
            f"\n上下文已压缩：{before:,} -> {after:,} token，节省 {saved:,} token"
        )
    elif event.type == "token_usage_updated":
        source = str(event.payload.get("source", "context_estimate"))
        if source in {"provider_usage", "context_compaction", "context_cleared"}:
            typer.echo("\n" + _token_usage_line(event.payload))
    elif event.type in {"run_failed", "run_cancelled"}:
        typer.echo(f"\n运行结束：{event.payload.get('reason', event.type)}")


def _help_text() -> str:
    return (
        "/help      显示帮助\n/status    显示会话和运行指标\n/sessions  列出可恢复会话\n"
        "/history   显示当前可读会话记录\n/new       创建新会话\n/clear     清空当前模型上下文\n"
        "/cancel    提示取消方式\n/exit      保存并退出"
    )


def _exit_text(session: ChatSession) -> str:
    workspace = _quote_cli_arg(str(session._agent.config.workspace))
    return (
        "会话已保存，再见。\n"
        "下次继续本会话可运行：\n"
        f"uv --cache-dir .uv-cache run agent chat --workspace {workspace} "
        f"--resume {session.session_id}"
    )


def _quote_cli_arg(value: str) -> str:
    if value and not any(char.isspace() for char in value) and '"' not in value:
        return value
    return '"' + value.replace('"', '\\"') + '"'


def _status_text(session: ChatSession) -> str:
    summary = session.summary
    snapshot = session.token_snapshot()
    plan_line = ""
    if session._agent.config.plan_mode:
        plan_status = getattr(summary, "last_plan_status", "") or "draft_required"
        plan_id = getattr(summary, "last_plan_id", "")
        revision_count = getattr(summary, "plan_revision_count", 0)
        plan_line = (
            f"Plan 状态：{plan_status}"
            f"{'；ID：' + plan_id if plan_id else ''}"
            f"；修订次数：{revision_count}\n"
        )
        last_failure = getattr(summary, "last_plan_failure", "")
        if last_failure:
            plan_line += f"最近计划失败：{last_failure}\n"
    return (
        f"会话 ID：{summary.session_id}\n模型：{summary.model_name}\n"
        f"上下文消息：{summary.message_count}\n"
        f"运行：{summary.run_count}，工具：{summary.tool_count}，审批：{summary.approval_count}\n"
        f"最近状态：{summary.last_status}；失败：{summary.failed_count}；取消：{summary.cancelled_count}\n"
        f"Token 消耗：{snapshot.session_total_tokens:,}（输入 "
        f"{snapshot.session_prompt_tokens:,}，输出 {snapshot.session_completion_tokens:,}）\n"
        f"当前上下文：{snapshot.current_context_tokens:,} / "
        f"{snapshot.context_window_tokens:,} token"
        f"（{_format_ratio(snapshot.context_usage_ratio)}，"
        f"{_context_source_label(snapshot.current_context_source)}）\n"
        f"最近压缩：节省 {snapshot.last_compacted_tokens_saved:,} token"
        f"（累计 {snapshot.total_compacted_tokens_saved:,}）\n"
        f"累计运行时间：{summary.total_duration_ms:.0f} ms\n"
        f"Plan Mode：{'是' if session._agent.config.plan_mode else '否'}\n"
        f"{plan_line}"
        f"授权：write={'是' if session._agent.config.allow_write else '否'}，"
        f"Docker 沙箱={'是' if session._agent.config.allow_shell else '否'}"
    )


def _token_usage_line(payload: dict[str, object]) -> str:
    total = _payload_int(payload, "session_total_tokens")
    prompt = _payload_int(payload, "session_prompt_tokens")
    completion = _payload_int(payload, "session_completion_tokens")
    current = _payload_int(payload, "current_context_tokens")
    window = _payload_int(payload, "context_window_tokens")
    ratio = _payload_float(payload, "context_usage_ratio")
    source = str(payload.get("current_context_source", "estimated"))
    saved = _payload_int(payload, "last_compacted_tokens_saved")
    compact = f"；最近压缩节省 {saved:,}" if saved else ""
    return (
        f"Token：本会话 {total:,}（输入 {prompt:,}，输出 {completion:,}）；"
        f"上下文 {current:,} / {window:,}（{_format_ratio(ratio)}，"
        f"{_context_source_label(source)}）{compact}"
    )


def _payload_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _payload_float(payload: dict[str, object], key: str) -> float:
    value = payload.get(key, 0.0)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _format_ratio(ratio: float) -> str:
    return f"{ratio * 100:.1f}%"


def _context_source_label(source: str) -> str:
    if source == "anchored":
        return "provider usage 锚定"
    return "估算"


async def _print_sessions(agent: CodingAgent) -> None:
    summaries = await agent.sessions.list_summaries()
    if not summaries:
        typer.echo("没有可恢复会话。")
        return
    for summary in summaries:
        typer.echo(
            f"{summary.session_id}  {summary.updated_at.isoformat()}  "
            f"{summary.last_status}  {summary.last_user_message_preview}"
        )


async def _print_history(agent: CodingAgent, session_id: str) -> None:
    transcript = await agent.sessions.load_transcript(session_id)
    if not transcript:
        typer.echo("当前会话暂无可读记录。")
        return
    typer.echo(transcript)


@app.command()
def resume(
    session_id: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for sessions.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables before reading sessions.",
        ),
    ] = False,
) -> None:
    """显示会话事件历史。"""
    events = asyncio.run(
        _load_events(
            _build_session_store(
                workspace, storage_backend, database_url, database_create_schema
            ),
            session_id,
        )
    )
    if not events:
        raise typer.Exit(code=1)
    for event in events:
        typer.echo(event.model_dump_json())


@app.command()
def status(
    session_id: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    storage_backend: Annotated[
        str | None, typer.Option("--storage", help="Session storage backend: jsonl or mysql.")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option("--database-url", help="SQLAlchemy database URL for sessions.")
    ] = None,
    database_create_schema: Annotated[
        bool,
        typer.Option(
            "--database-create-schema",
            help="Create database tables before reading sessions.",
        ),
    ] = False,
) -> None:
    """显示会话最后一条事件。"""
    events = asyncio.run(
        _load_events(
            _build_session_store(
                workspace, storage_backend, database_url, database_create_schema
            ),
            session_id,
        )
    )
    if not events:
        raise typer.Exit(code=1)
    typer.echo(events[-1].model_dump_json())


async def _load_events(store: SessionStore, session_id: str) -> list[SessionEvent]:
    return await store.load(session_id)
