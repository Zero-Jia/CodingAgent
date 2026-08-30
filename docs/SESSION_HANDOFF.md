# Session 交接说明

本文档是每次 vibe coding session 结束前必须更新的交接文件。它的目的，是让新的 Codex session 不依赖上一轮聊天记录，也能快速恢复项目上下文。

## 新 Session 开场 Prompt

新开 Codex session 时，直接使用这段 prompt：

```text
这是我的 CodingAgent 项目。请先完整阅读 README.md、CodingAgent.md、docs/SESSION_HANDOFF.md、docs/ROADMAP.md、pyproject.toml 和 src/coding_agent 下的核心模块。

先不要修改代码。请先总结：

1. 当前项目已经实现了什么；
2. 上一个 session 的交接状态是什么；
3. 本轮最合理的下一步是什么；
4. 你准备修改哪些文件，为什么。
```

## Session 结束 Prompt

每次结束 Codex session 前，直接使用这段 prompt：

```text
请更新 docs/SESSION_HANDOFF.md，记录本轮完成的内容、修改过的文件、验证命令和结果、未完成事项、下一轮建议。不要夸大未实现能力。
```

## 当前项目状态

当前项目是一个安全优先的本地 coding agent MVP。

已经实现：

- `src/coding_agent` 下的 Python 包结构。
- Typer CLI，支持一次性 `run` 和连续 `chat` 模式。
- Model Gateway provider registry，当前已实现 `deepseek` provider。
- DeepSeek 模型适配器。
- 供应商无关的模型、消息、工具调用、usage 和事件契约。
- 事件驱动 runtime，支持工具执行、审批流、取消、trace 写入和 artifact 写入。
- 确定性 context manager，支持 token 预算触发、自动 compact、近期尾部保留和 tool call/result 边界保护。
- Session 级 token usage 账本，支持 provider usage 累计统计、当前上下文 token、窗口占比和 compact 节省量展示。
- 只读仓库工具：`read`、`search`、`git_diff`。
- Docker 沙箱工具：`sandbox_shell` 和 `verify`。
- 通过 `apply_patch` 实现 patch-only 宿主机写回。
- 工作区快照过滤，排除敏感文件、内部文件、符号链接、大文件、虚拟环境、缓存和 `.git`。
- Patch 校验，覆盖敏感路径、二进制 patch、子模块、文件模式变化、符号链接、可执行权限变化、重命名、复制、changed-file 不一致和宿主并发修改。
- JSONL session、checkpoint、transcript、summary、trace、artifact 和 application log。
- 连续 chat session 的独占锁。
- `src/coding_agent/py.typed` 包类型标记。
- GitHub Actions 最小 CI workflow，运行 ruff、mypy 和 pytest。
- 最小 memory 协议，目前是 Noop 实现。
- 最小 eval report 契约。
- 覆盖 runtime、policy、tools、sandbox、patch validation 和 sessions 的测试。
- `docs/THREAT_MODEL.md` 威胁模型文档。

尚未实现：

- FastAPI 服务。
- Web UI。
- PostgreSQL。
- Milvus。
- Redis。
- MCP。
- Skills。
- Hooks。
- 真实 memory 检索。
- 模型辅助上下文摘要。
- 多 agent 编排。
- Worktree 隔离。
- coverage、pre-commit、release workflow、安全扫描和完整 CI/CD 发布流水线。

## 最近一次 Session

本轮完成：

- 完成 `Token Usage Accounting` 能力。
- Runtime 将模型 adapter 返回的 `UsageEvent` 提升为 `model_usage_reported`，并保证该事件在 assistant 消息写入历史之后发出。
- 新增 `coding_agent.runtime.token_usage`，用 provider usage 统计 session 累计输入、输出和总 token。
- 当前上下文 token 使用“provider usage 锚点 + 锚点后新增消息估算”；无锚点、恢复会话或 compact 后退回确定性字符估算。
- `ChatSession` 聚合 usage 后发出 `token_usage_updated`，并把 token 指标写入 session event 和 summary。
- CLI 在运行中收到 usage 后输出 token 状态，`/status` 显示累计消耗、当前上下文 token、窗口占比和最近 compact 节省量。
- CLI `/exit` 和 EOF/Ctrl+C 退出时会输出可直接用于恢复当前 session 的 `uv --cache-dir .uv-cache run agent chat --workspace ... --resume ...` 指令。
- 参考了 `D:\Software\MewCode` 中 `record_usage_anchor/current_tokens` 的真实锚点加增量估算思路。

本轮修改文件：

- `src/coding_agent/runtime/context.py`
- `src/coding_agent/runtime/token_usage.py`
- `src/coding_agent/runtime/events.py`
- `src/coding_agent/runtime/loop.py`
- `src/coding_agent/agent/coding_agent.py`
- `src/coding_agent/sessions/store.py`
- `src/coding_agent/cli/app.py`
- `src/coding_agent/config.py`
- `tests/test_context_manager.py`
- `tests/test_token_usage.py`
- `tests/test_runtime.py`
- `tests/test_cli.py`
- `CodingAgent.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/SESSION_HANDOFF.md`
- `docs/TASKS.md`
- `docs/VERIFICATION.md`

最近一次验证结果：

- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过。
- `uv --cache-dir .uv-cache run mypy src`：通过。
- `uv --cache-dir .uv-cache run pytest tests/test_token_usage.py tests/test_runtime.py --basetemp .codex-test-tmp-token-targeted -p no:cacheprovider`：13 passed。
- `uv --cache-dir .uv-cache run pytest tests/test_cli.py --basetemp .codex-test-tmp-cli-exit -p no:cacheprovider`：1 passed。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-cli-exit-final -p no:cacheprovider`：43 passed，1 skipped。

注意事项：

- 普通 `uv run pytest` 在 Codex 沙箱账户下可能失败，因为它会尝试写入 `C:\Users\HP\AppData\Local\Temp\pytest-of-HP`。
- `git status` 可能因为 Git dubious ownership 失败，原因是仓库属于用户 Windows 账户，而 Codex 使用沙箱账户运行。除非用户明确要求，不要修改全局 Git 配置。
- GitHub Actions workflow 已在仓库中新增，但本轮只完成了本地验证，没有观察远端 Actions 实际运行结果。
- 本轮第一次使用 `--basetemp .codex-test-tmp` 运行 runtime 测试时，被旧临时目录权限问题拦截；改用新的 `.codex-test-tmp-stream` 后通过。
- Token 消耗累计值只有在 provider 返回 usage 时才是精确值；当前上下文 token 在 provider 锚点之后仍会对新增尾部消息做估算。
- Context summary 当前是确定性抽取摘要，不调用模型；后续可增加模型辅助摘要，但必须保留 fake adapter 测试和审计事件。

## 下一轮建议

建议下一轮任务：

从 `docs/TASKS.md` 中选择下一个边界清晰的任务。若继续按当前 backlog 推进，建议做 P1 的 `Plan Mode`；若优先补工程化，也可以先增加 coverage 或 pre-commit，但需要先把它们加入任务清单。

建议 prompt：

```text
本轮只做 Plan Mode，不重构无关模块。请增加计划模式，让模型在使用 sandbox 或 patch 工具前必须先产出计划并通过审批。增加确定性测试覆盖批准和拒绝路径，完成后运行 ruff、mypy、pytest，并更新 docs/SESSION_HANDOFF.md 和 docs/VERIFICATION.md。
```

验收标准：

- 使用 sandbox 或 patch 工具前需要计划审批。
- 非交互模式默认拒绝未审批执行。
- 批准和拒绝路径有测试覆盖。
- 现有安全边界不变。
- `uv --cache-dir .uv-cache run ruff check`、`uv --cache-dir .uv-cache run mypy`、`uv --cache-dir .uv-cache run mypy src` 和 `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider` 通过。

## 后续 Session 工作规则

- 除非任务明确要求，否则不要重写 `README.md` 或 `CodingAgent.md`。
- 不要用直接宿主机写入替代 Docker sandbox + patch-only 写回。
- PostgreSQL、Milvus、Redis、MCP、Skills、Hooks、Web UI 等能力在代码实现前，不能写成已经完成。
- 每个 session 只做一个边界清晰的能力。
- 代码修改必须补充或更新测试。
- 每次结束前更新本文档。
- 验证命令和结果记录到 `docs/VERIFICATION.md`。
- 优先做小而可审查的修改，避免大范围重构。
