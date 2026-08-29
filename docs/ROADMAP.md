# CodingAgent 路线图

本文档用于约束后续 vibe coding session 的工作范围。每个阶段都应该拆成小任务，每个任务都要包含测试、文档更新和验证记录。

## 当前基线

当前已经实现：

- 基于 `src/coding_agent` 的 Python 3.12 包结构。
- Typer CLI。
- DeepSeek 模型适配器。
- 供应商无关的模型和工具契约。
- 事件驱动 runtime。
- `read`、`search`、`git_diff` 只读工具。
- Docker 无网络沙箱命令工具。
- 单独的 `verify` 工具，沙箱变更会被丢弃。
- 基于 `apply_patch` 的宿主机写回。
- 工作区敏感路径过滤。
- JSONL session、checkpoint、transcript、trace、artifact 和 application log。
- 最小 eval report 契约。
- 面向 policy、tools、sandbox、patch validation 和 sessions 的基础测试。
- `ruff`、`mypy`、`pytest` 配置。

当前尚未实现：

- FastAPI 或 Web UI。
- PostgreSQL、Milvus、Redis。
- MCP。
- Skills。
- Hooks。
- 真实 memory 检索。
- 上下文压缩。
- 多 agent 编排。
- Worktree 隔离。
- CI/CD。

## Phase 1：面试级工程基础

目标：让当前 MVP 可信、可复现、可解释。

任务：

- 增加 `src/coding_agent/py.typed`。
- 确保包级 `uv --cache-dir .uv-cache run mypy` 通过，而不只是 `mypy src` 通过。
- 增加 GitHub Actions，运行 `pytest`、`ruff`、`mypy`。
- 增加 pre-commit 配置。
- 增加 coverage 报告。
- 增加 mock LLM runtime 集成测试。
- 如果安全说明需要更完整，增加 `docs/THREAT_MODEL.md`。
- 如果评测体系开始扩展，增加 `docs/EVALS.md`。
- 增加常用面试 demo 脚本或示例 transcript。

验收标准：

- 新环境 clone 后能按文档运行验证命令。
- 每个 pull request 都能触发 CI。
- 自动化测试不依赖真实 DeepSeek API。
- 文档明确区分已实现能力和未来规划。

## Phase 2：Agent Runtime 成熟化

目标：让 agent 能支撑更长、更安全、更可预测的任务。

任务：

- 增加 model gateway 和 provider registry。
- 保持现有 DeepSeek 行为不变，同时迁移到 gateway 后面。
- 增加 OpenAI-compatible adapter。
- 实现真正的增量 streaming 输出。
- 增加带 token budget 的 context manager。
- 增加自动 compact。
- 增加工具输出摘要策略。
- 增加 plan mode：先计划，审批后执行。
- 增加 CLI slash command registry。
- 增加 sandbox 执行前的 command risk detector。

验收标准：

- 现有测试继续通过。
- Runtime 具备 mock 测试，覆盖纯文本回答、工具调用、工具拒绝、模型错误、取消和多轮历史。
- Plan mode 在计划批准前阻止 shell 和 patch 操作。
- Context compaction 有确定性测试覆盖。

## Phase 3：平台化和企业数据层

目标：把本地 runtime 扩展成服务化平台。

任务：

- 基于现有 `CodingAgent` API 增加 FastAPI app。
- 增加 WebSocket 或 SSE 事件流。
- 增加 PostgreSQL schema，覆盖 sessions、runs、turns、events、tools、approvals、patches、artifacts、model usage 和 audit logs。
- 增加 SQLAlchemy repository。
- 增加 Alembic migration。
- 增加 Redis，用于 task state、locks 和 pub/sub。
- 增加持久化 approval queue。
- 增加最小 Web patch 审批页面。

验收标准：

- CLI 和 API 复用同一套 runtime。
- JSONL 仍可作为本地开发模式。
- PostgreSQL 实现通过 repository contract tests。
- 审批状态在进程重启后仍然存在。

## Phase 4：知识检索和记忆

目标：让 agent 能理解大型仓库，并复用项目知识。

任务：

- 增加 workspace indexer。
- 增加 Tree-sitter 代码 chunking。
- 增加符号元数据提取。
- 增加 embedding pipeline。
- 增加 Milvus collections，用于代码 chunk 和 memory。
- 增加 semantic search 工具。
- 增加候选 memory extraction。
- 增加人工审核后的 memory promotion。
- 增加 memory recall 注入 runtime context。

验收标准：

- Agent 可以通过语义检索定位相关文件，而不是只靠全文搜索。
- Memory record 包含来源、作用域、置信度、时间戳和状态。
- Recall 行为使用 fake embedding 或本地 stub 做确定性测试。

## Phase 5：多 Agent 和 Worktree 隔离

目标：支持并行任务拆解，同时不破坏主工作区。

任务：

- 增加 planner、explorer、coder、reviewer、verifier、summarizer 角色定义。
- 增加 background task manager。
- 增加 child agent trace tree。
- 增加 git worktree manager。
- 让实现类 agent 在隔离 worktree 中运行。
- 将 worktree 产出的修改汇总为 patch proposal，并继续走现有 patch approval 链路。

验收标准：

- Child agents 不能直接修改主工作区。
- Worktree 变更以可审查 patch 形式返回。
- 失败的 child task 不会破坏 parent state。
- Trace 能展示 parent-child 关系。

## Phase 6：生产化加固

目标：达到企业私有化部署标准。

任务：

- 增加 RBAC。
- 增加组织级 policy。
- 增加租户隔离。
- 增加 OpenTelemetry。
- 增加 Prometheus metrics。
- 增加 Grafana dashboard 示例。
- 增加镜像 digest 固定。
- 增加 SBOM 生成。
- 增加依赖漏洞扫描。
- 增加 backup/restore 文档。
- 增加 disaster recovery 说明。

验收标准：

- 部署文档包含明确安全控制。
- 所有高权限动作都可审计。
- Runtime、sandbox、model 和 storage 的失败都有可观察信号。

## Vibe Coding 规则

每个实现 session 都应遵守：

> 一个 session，只做一个边界清晰的能力；必须包含测试、文档更新和验证记录。

避免使用类似“把项目改成企业级”这种过宽的 prompt。推荐使用具体 prompt：

```text
本轮只增加 mock LLM runtime 集成测试，不重构无关模块。请覆盖纯文本回答、一次工具调用、工具被拒绝、模型错误和取消。运行 pytest、ruff、mypy。结束前更新 docs/SESSION_HANDOFF.md。
```
