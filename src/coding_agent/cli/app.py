"""轻量 Typer 展示层；业务逻辑由 CodingAgent 承担。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from coding_agent.agent.coding_agent import CodingAgent
from coding_agent.ai.deepseek import DeepSeekAdapter
from coding_agent.config import AgentConfig
from coding_agent.runtime.events import AgentEvent
from coding_agent.runtime.loop import ApprovalProvider
from coding_agent.sessions.store import JsonlSessionStore

app = typer.Typer(help="安全优先的本地编程 Agent。", no_args_is_help=True)


class TerminalApproval(ApprovalProvider):
    async def request(self, tool_name: str, reason: str, params: dict[str, object]) -> bool:
        return typer.confirm(f"批准 {tool_name} 操作吗？原因：{reason}", default=False)


def _build_agent(
    workspace: Path, model: str | None, allow_write: bool, allow_shell: bool, non_interactive: bool
) -> CodingAgent:
    config = AgentConfig.from_environment(
        workspace,
        model=model or "deepseek-chat",
        allow_write=allow_write,
        allow_shell=allow_shell,
        non_interactive=non_interactive,
    )
    if config.deepseek_api_key is None:
        raise typer.BadParameter("未设置 DEEPSEEK_API_KEY，无法运行真实 Agent")
    adapter = DeepSeekAdapter(
        config.model, config.deepseek_api_key.get_secret_value(), config.deepseek_base_url
    )
    approval = None if non_interactive else TerminalApproval()
    return CodingAgent(config, adapter, approval)


@app.command()
def run(
    task: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_write: Annotated[bool, typer.Option("--allow-write")] = False,
    allow_shell: Annotated[bool, typer.Option("--allow-shell")] = False,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
) -> None:
    """执行一次任务；需要真实 DeepSeek 配置。"""
    asyncio.run(
        _run_once(_build_agent(workspace, model, allow_write, allow_shell, non_interactive), task)
    )


@app.command()
def chat(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_write: Annotated[bool, typer.Option("--allow-write")] = False,
    allow_shell: Annotated[bool, typer.Option("--allow-shell")] = False,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
    resume: Annotated[str | None, typer.Option("--resume")] = None,
) -> None:
    """启动连续聊天；每条消息保留在同一个 session_id 的上下文中。"""
    asyncio.run(
        _chat_loop(
            _build_agent(workspace, model, allow_write, allow_shell, non_interactive), resume
        )
    )


async def _run_once(agent: CodingAgent, task: str) -> None:
    session: str | None = None
    async for event in agent.run(task):
        session = event.session_id
        _render_event(event)
    if session:
        typer.echo(f"\n会话 ID：{session}")


async def _chat_loop(agent: CodingAgent, resume_id: str | None) -> None:
    session = await agent.start_chat(resume_id)
    typer.echo(f"连续会话已就绪。会话 ID：{session.session_id}")
    typer.echo("输入问题开始；/help 查看命令，/exit 保存并退出。")
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
            typer.echo(
                "/help  显示帮助\n/status  显示会话状态\n/clear  清空当前上下文\n/exit  保存并退出"
            )
            continue
        if command == "/status":
            typer.echo(f"会话 ID：{session.session_id}；当前上下文消息数：{session.message_count}")
            continue
        if command == "/clear":
            if typer.confirm("确定清空当前会话上下文吗？事件日志会保留。", default=False):
                await session.clear_context()
                typer.echo("上下文已清空。")
            continue
        try:
            async for event in session.send(raw):
                _render_event(event)
        except KeyboardInterrupt:
            if session.cancel_current_turn():
                typer.echo("\n已请求取消当前回合。")
            else:
                typer.echo("\n当前没有可取消的回合。")


def _render_event(event: AgentEvent) -> None:
    if event.type == "message_delta":
        typer.echo(str(event.payload.get("text", "")), nl=False)
    elif event.type in {
        "tool_started",
        "tool_finished",
        "approval_requested",
        "approval_resolved",
        "run_failed",
        "run_cancelled",
    }:
        typer.echo(f"\n[{event.type}] {json.dumps(event.payload, ensure_ascii=False, default=str)}")


@app.command()
def resume(
    session_id: str,
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)] = Path(
        "."
    ),
) -> None:
    """显示历史事件；继续对话请使用 agent chat --resume <session_id>。"""
    events = asyncio.run(JsonlSessionStore(workspace / ".coding-agent").load(session_id))
    if not events:
        typer.echo("未找到会话")
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
    """显示会话最后一条持久化事件。"""
    events = asyncio.run(JsonlSessionStore(workspace / ".coding-agent").load(session_id))
    if not events:
        typer.echo("未找到会话")
        raise typer.Exit(code=1)
    typer.echo(events[-1].model_dump_json())
