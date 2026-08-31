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
- 显式 Plan Mode，支持 `--plan`，要求模型在使用沙箱或 patch 工具前先提交计划并获批。
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

- 修复 snapshot 敏感过滤误伤普通源码文件的问题。
- 收窄 `WorkspacePathPolicy` 的敏感文件规则，改为精确目录、精确文件名、私钥命名和密钥后缀匹配。
- `token_usage.py`、`test_token_usage.py`、`token_validator.py`、`secret_scanner.py` 这类正常源码文件不再因为文件名包含 `token` 或 `secret` 被排除。
- `.env*`、`.ssh/`、`secrets/`、`credentials/`、`credentials.json`、`token.txt`、`private.key`、`*.pem` 等仍会被保护。
- 同步收窄 `dockerignore_patterns()`，避免 `search` 因 `*token*`、`*secret*` 这类宽泛 glob 漏掉正常源码。
- 新增 sandbox 回归测试，覆盖敏感文件仍被排除、类似敏感命名的源码被保留、snapshot tar 内包含正常源码。
- 参考了 `D:\Software\MewCode` 的 `PathSandbox` 思路：路径边界和受保护路径应尽量精确，不用宽泛关键词排除普通源码。

上轮完成：

- 完成 P1 `Plan Mode` 能力。
- 新增 `plan_mode` 配置和 CLI `--plan` 开关。
- 新增 `submit_plan` 工具，要求模型提交计划、预计修改文件、验证命令和风险。
- Runtime 在 Plan Mode 下对 `sandbox_shell`、`verify` 和 `apply_patch` 增加前置计划门禁。
- 计划审批只解锁本轮高风险工具门禁，不绕过原有 `--allow-shell`、`--allow-write` 和具体工具审批。
- 非交互模式下 `submit_plan` 默认拒绝，不能靠 `--allow-shell` 或 `--allow-write` 绕过计划门禁。
- CLI 审批提示会展示计划内容、预计修改文件、验证命令和风险，`/status` 会展示 Plan Mode 状态。
- 参考了 `D:\Software\MewCode` 中 permission mode、`ExitPlanMode` 和 plan approval 测试思路，但未引入其 TUI、计划文件、本地直写或 OS sandbox 机制。

本轮完成：

- 将 Plan Mode 从一次性获批开关升级为 runtime 状态机。
- 新增 `src/coding_agent/runtime/plan.py`，集中管理计划状态、计划 ID、修订次数和最近失败。
- `sandbox_shell`、`verify` 或 `apply_patch` 返回非 success 时，当前计划会自动进入 `failed` 状态。
- 计划失败后，后续高风险工具会被 `plan_revision_required` 拦截；模型必须重新调用 `submit_plan`，并提供 `revision_of`、`failure_summary` 和 `changed_approach`。
- 新增 `plan_submitted`、`plan_approved`、`plan_rejected`、`plan_failed` 和 `plan_revision_required` 事件。
- CLI 审批展示新增目标、步骤、修订来源、失败摘要和调整方案；`/status` 展示计划状态、计划 ID、修订次数和最近失败。
- `tests/test_plan_mode.py` 从 6 个用例扩展到 9 个用例，覆盖失败后阻塞、修订计划恢复和缺少失败上下文时拒绝修订。

本轮修改文件：

- `README.md`
- `src/coding_agent/runtime/plan.py`
- `src/coding_agent/runtime/loop.py`
- `src/coding_agent/runtime/events.py`
- `src/coding_agent/tools/plan.py`
- `src/coding_agent/agent/coding_agent.py`
- `src/coding_agent/cli/app.py`
- `src/coding_agent/sessions/store.py`
- `tests/test_plan_mode.py`
- `docs/ARCHITECTURE.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

上轮修改文件：

- `src/coding_agent/workspace/security.py`
- `tests/test_sandbox.py`
- `docs/ARCHITECTURE.md`
- `docs/THREAT_MODEL.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

最近一次验证结果：

- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过，40 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，40 source files。
- `uv --cache-dir .uv-cache run pytest tests/test_sandbox.py --basetemp .codex-test-tmp-security -p no:cacheprovider`：14 passed，1 skipped。
- `uv --cache-dir .uv-cache run pytest tests/test_plan_mode.py --basetemp .codex-test-tmp-plan-recovery-3 -p no:cacheprovider`：10 passed。
- 当前仓库 `SnapshotService` smoke test：确认 `src/coding_agent/runtime/token_usage.py` 和 `tests/test_plan_mode.py` 均进入 snapshot。
- 实际 Docker sandbox smoke test：`test -f src/coding_agent/runtime/token_usage.py && python -m pytest tests/test_plan_mode.py -q`，success，exit code 0，6 passed。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-plan-recovery-final2 -p no:cacheprovider`：56 passed，1 skipped。

注意事项：

- 普通 `uv run pytest` 在 Codex 沙箱账户下可能失败，因为它会尝试写入 `C:\Users\HP\AppData\Local\Temp\pytest-of-HP`。
- 如果旧 `.codex-test-tmp` 目录存在权限残留，继续改用新的项目内临时目录，例如 `.codex-test-tmp-security-final`。
- `git status` 可能因为 Git dubious ownership 失败，原因是仓库属于用户 Windows 账户，而 Codex 使用沙箱账户运行。除非用户明确要求，不要修改全局 Git 配置。
- GitHub Actions workflow 已在仓库中新增，但本轮只完成了本地验证，没有观察远端 Actions 实际运行结果。
- Token 消耗累计值只有在 provider 返回 usage 时才是精确值；当前上下文 token 在 provider 锚点之后仍会对新增尾部消息做估算。
- Context summary 当前是确定性抽取摘要，不调用模型；后续可增加模型辅助摘要，但必须保留 fake adapter 测试和审计事件。
- Plan Mode 当前是显式 `--plan` 开关，不是默认开启；计划审批只在当前 run 内有效。
- 后续新增敏感路径规则时，不要使用宽泛子串排除普通源码文件；必须同步增加误伤回归测试。

## 下一轮建议

建议下一轮任务：

从 `docs/TASKS.md` 中选择下一个边界清晰的任务。若继续按 P1 Runtime 成熟化推进，建议做 `CLI slash command registry` 或 `sandbox 执行前的 command risk detector`；若优先补工程化，也可以先增加 coverage 或 pre-commit。

建议 prompt：

```text
本轮只做 sandbox 执行前的 command risk detector，不重构无关模块。请在 PolicyEngine 或独立 risk detector 中识别明显危险命令，增加确定性测试，完成后运行 ruff、mypy、pytest，并更新 docs/SESSION_HANDOFF.md 和 docs/VERIFICATION.md。
```

验收标准：

- 明显危险命令在进入 Docker 沙箱前被拒绝或要求更高等级审批。
- 只读验证命令不受误伤。
- 复合命令、大小写和空白变化有测试覆盖。
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
