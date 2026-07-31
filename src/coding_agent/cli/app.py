"""Typer 命令行展示层。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from coding_agent.agent.coding_agent import ChatSession, CodingAgent
from coding_agent.ai.deepseek import DeepSeekAdapter
from coding_agent.config import AgentConfig
from coding_agent.runtime.events import AgentEvent
from coding_agent.runtime.loop import ApprovalProvider
from coding_agent.sessions.lock import SessionLockedError, acquire_session_lock
from coding_agent.sessions.store import JsonlSessionStore

app = typer.Typer(help="安全优先的本地编程 Agent。", no_args_is_help=True)


class TerminalApproval(ApprovalProvider):
    async def request(self, tool_name: str, reason: str, params: dict[str, object]) -> bool:
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
    model: str | None,
    allow_write: bool,
    allow_shell: bool,
    non_interactive: bool,
    sandbox_image: str | None = None,
) -> CodingAgent:
    overrides: dict[str, object] = {
        "model": model or "deepseek-chat",
        "allow_write": allow_write,
        "allow_shell": allow_shell,
        "non_interactive": non_interactive,
    }
    if sandbox_image is not None:
        overrides["sandbox_image"] = sandbox_image
    config = AgentConfig.from_environment(
        workspace,
        **overrides,
    )
    if config.deepseek_api_key is None:
        raise typer.BadParameter("未设置 DEEPSEEK_API_KEY，无法运行真实 Agent")
    adapter = DeepSeekAdapter(
        config.model, config.deepseek_api_key.get_secret_value(), config.deepseek_base_url
    )
    return CodingAgent(config, adapter, None if non_interactive else TerminalApproval())


@app.command()
def run(
    task: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_write: Annotated[bool, typer.Option("--allow-write")] = False,
    allow_shell: Annotated[bool, typer.Option("--allow-shell")] = False,
    sandbox_image: Annotated[str | None, typer.Option("--sandbox-image")] = None,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
) -> None:
    """执行一次任务。"""
    asyncio.run(
        _run_once(
            _build_agent(
                workspace, model, allow_write, allow_shell, non_interactive, sandbox_image
            ),
            task,
        )
    )


@app.command()
def chat(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_write: Annotated[bool, typer.Option("--allow-write")] = False,
    allow_shell: Annotated[bool, typer.Option("--allow-shell")] = False,
    sandbox_image: Annotated[str | None, typer.Option("--sandbox-image")] = None,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
    resume: Annotated[str | None, typer.Option("--resume")] = None,
    force_unlock: Annotated[bool, typer.Option("--force-unlock")] = False,
) -> None:
    """启动独占的连续聊天会话。"""
    agent = _build_agent(
        workspace, model, allow_write, allow_shell, non_interactive, sandbox_image
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
                typer.echo("\n会话已保存，再见。")
                return
            command = raw.strip()
            if not command:
                continue
            if command == "/exit":
                typer.echo("会话已保存，再见。")
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
    elif event.type == "tool_finished":
        tool_result = event.payload.get("result")
        if isinstance(tool_result, dict) and tool_result.get("status") != "success":
            artifact = event.payload.get("artifact")
            suffix = f"；详情：{artifact}" if artifact else ""
            typer.echo(
                f"\n工具未成功：{event.payload.get('tool')}，"
                f"{tool_result.get('summary', '未知原因')}{suffix}"
            )
    elif event.type in {"run_failed", "run_cancelled"}:
        typer.echo(f"\n运行结束：{event.payload.get('reason', event.type)}")


def _help_text() -> str:
    return (
        "/help      显示帮助\n/status    显示会话和运行指标\n/sessions  列出可恢复会话\n"
        "/history   显示当前可读会话记录\n/new       创建新会话\n/clear     清空当前模型上下文\n"
        "/cancel    提示取消方式\n/exit      保存并退出"
    )


def _status_text(session: ChatSession) -> str:
    summary = session.summary
    return (
        f"会话 ID：{summary.session_id}\n模型：{summary.model_name}\n"
        f"上下文消息：{summary.message_count}\n"
        f"运行：{summary.run_count}，工具：{summary.tool_count}，审批：{summary.approval_count}\n"
        f"最近状态：{summary.last_status}；失败：{summary.failed_count}；取消：{summary.cancelled_count}\n"
        f"累计运行时间：{summary.total_duration_ms:.0f} ms\n"
        f"授权：write={'是' if session._agent.config.allow_write else '否'}，"
        f"Docker 沙箱={'是' if session._agent.config.allow_shell else '否'}"
    )


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
    path = agent.data_root / "transcripts" / f"{session_id}.md"
    if not path.exists():
        typer.echo("当前会话暂无可读记录。")
        return
    typer.echo(await asyncio.to_thread(path.read_text, encoding="utf-8"))


@app.command()
def resume(
    session_id: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
) -> None:
    """显示会话事件历史。"""
    events = asyncio.run(JsonlSessionStore(workspace / ".coding-agent").load(session_id))
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
) -> None:
    """显示会话最后一条事件。"""
    events = asyncio.run(JsonlSessionStore(workspace / ".coding-agent").load(session_id))
    if not events:
        raise typer.Exit(code=1)
    typer.echo(events[-1].model_dump_json())
