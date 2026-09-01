# CodingAgent 任务清单

本文档记录后续 vibe coding session 的实际 backlog。任务应保持具体，尽量带验收标准，避免只写模糊方向。

## P0：近期面试准备

### 1. Package Typing 标记

状态：已完成。

任务：

增加 `src/coding_agent/py.typed`，并确保包级 mypy 可以正常运行。

验收标准：

- `uv --cache-dir .uv-cache run mypy` 通过。
- `uv --cache-dir .uv-cache run mypy src` 通过。
- 不改变现有运行行为。

### 2. CI 工作流

状态：已完成。

任务：

增加 GitHub Actions，用于 lint、类型检查和测试。

验收标准：

- Workflow 运行 `ruff check`。
- Workflow 运行 `mypy`。
- Workflow 运行 `pytest`。
- Workflow 使用 Python 3.12。
- Workflow 不依赖真实 DeepSeek 密钥。

### 3. Mock LLM Runtime 测试

状态：已完成。

任务：

使用 fake model adapter 增加确定性的 runtime 测试。

验收标准：

- 覆盖纯文本回答。
- 覆盖一次工具调用。
- 覆盖工具被拒绝。
- 覆盖模型错误。
- 覆盖取消。
- 现有测试继续通过。

### 4. 威胁模型文档

状态：已完成。

任务：

增加 `docs/THREAT_MODEL.md`。

验收标准：

- 覆盖仓库 prompt injection。
- 覆盖敏感文件泄露。
- 覆盖宿主机写入风险。
- 覆盖 shell 执行风险。
- 覆盖沙箱逃逸假设。
- 覆盖审批和审计边界。

## P1：Runtime 成熟化

### 5. Model Gateway

状态：已完成。

任务：

引入 provider registry，同时保持现有 DeepSeek 行为不变。

验收标准：

- DeepSeek adapter 继续可用。
- Runtime 继续依赖供应商无关接口。
- 测试覆盖 provider selection。

完成记录：

- 新增 `coding_agent.ai.gateway`，集中创建模型 adapter。
- 配置新增 `model_provider`，默认仍为 `deepseek`。
- CLI 新增 `--provider`，并改为通过 gateway 创建 adapter。
- 新增 `tests/test_model_gateway.py`，覆盖 DeepSeek selection、未知 provider、缺少 key、环境变量和显式覆盖。

### 6. 真正的 Streaming Delta

状态：已完成。

任务：

让 assistant 文本在模型返回时增量输出，而不是最终一次性输出。

验收标准：

- CLI 可以渲染增量输出。
- 最终 session checkpoint 仍保存完整 assistant 内容。
- 测试覆盖流式 chunk。

完成记录：

- Runtime 收到模型 `TextDelta` 后立即发出 `message_delta` 事件。
- Assistant 完整文本仍在 runtime 内累积，并写入模型消息历史和后续 checkpoint。
- 已覆盖纯文本多 chunk、工具调用前文本增量和部分文本输出后 retry 边界。

### 7. Context Manager

状态：已完成。

任务：

增加负责 token budget 和自动 compact 的 context manager。

验收标准：

- 长历史通过摘要保留，而不是简单截断。
- 最近工具结果和重要文件得到保留。
- 测试覆盖 compact 边界。

完成记录：

- 新增 `coding_agent.runtime.context`，实现 token 估算、compact 阈值、近期尾部保留和 tool call/result 配对保护。
- `ChatSession` 在每轮模型调用前执行 context manager，compact 发生时发出 `context_compacted` 事件。
- Compact 会写入 session event 和 trace，并保存到后续 checkpoint。
- 当前摘要是确定性抽取摘要，不依赖真实模型 API。
- 测试覆盖不触发 compact、长历史 compact、tool 配对边界、摘要脱敏和 checkpoint 持久化。

### 8. Token Usage Accounting

状态：已完成。

任务：

增加 session 级 token usage 账本，在 CLI 中实时展示 provider usage、当前上下文 token、窗口占比和 compact 节省量。

验收标准：

- Provider 返回 usage 时，session 累计 token 消耗使用真实 provider 数值。
- 当前上下文 token 有 provider usage 锚点时只估算锚点后的新增消息；无锚点或 compact 后退回整体估算。
- CLI 运行中和 `/status` 都能展示累计消耗、上下文 token、窗口占比和最近 compact 节省量。
- 测试覆盖账本、runtime usage 事件、session summary 和 CLI 展示。

完成记录：

- 新增 `coding_agent.runtime.token_usage`，实现 `SessionTokenState` 和 `TokenSnapshot`。
- Runtime 将模型 usage 提升为 `model_usage_reported`；`ChatSession` 聚合后发出 `token_usage_updated`。
- Session summary 持久化累计输入、输出、总 token、当前上下文 token、窗口占比和 compact 节省量。
- CLI 在实时流和 `/status` 中展示 token 指标。
- 参考了 `D:\Software\MewCode` 中 `record_usage_anchor/current_tokens` 的“真实锚点 + 增量估算”思路。

### 9. Plan Mode

状态：已完成。

任务：

增加 plan mode，让模型在使用 shell 或 patch 工具前必须先产出计划。

验收标准：

- 使用 sandbox 或 patch 工具前需要计划审批。
- 非交互模式默认拒绝未审批执行。
- 测试覆盖批准和拒绝路径。

完成记录：

- 新增 `submit_plan` 工具，Plan Mode 开启时由 runtime 暴露给模型。
- 新增 `plan_mode` 配置和 CLI `--plan` 开关。
- Runtime 在 `sandbox_shell`、`verify` 和 `apply_patch` 前强制检查本轮是否已有获批计划。
- 计划审批不绕过原有 `--allow-shell`、`--allow-write` 和具体工具审批。
- 非交互模式下 `submit_plan` 默认拒绝，不能靠预授权绕过计划门禁。
- 新增 `tests/test_plan_mode.py` 覆盖未计划拦截、计划批准、计划拒绝、非交互拒绝和 CLI 状态展示。
- 新增 Plan Mode 恢复状态机：高风险工具失败后当前计划自动失效，继续执行前必须提交包含失败摘要和调整方案的修订计划。
- 新增 plan 事件和 session summary 字段，记录计划状态、计划 ID、修订次数和最近失败。
- `tests/test_plan_mode.py` 增加失败后阻塞、修订计划放行、缺少失败上下文拒绝等回归测试。

### 9a. Sandbox Command Risk Detector

状态：已完成。

任务：

在 `sandbox_shell` 和 `verify` 进入 Docker 前增加高置信命令风险检测。

验收标准：

- 明显危险命令在进入 Docker 沙箱前被拒绝。
- `--allow-shell`、Plan Mode 计划批准和非交互预授权不能绕过危险命令拒绝。
- 可疑命令要求交互式复核；非交互模式下拒绝。
- 常见验证命令和定向清理命令不被误伤。
- 覆盖复合命令、大小写和空白变化。

完成记录：

- 新增 `coding_agent.policy.command_risk`，包含命令风险等级、风险结果和 detector。
- `PolicyEngine` 在 `sandbox_shell` 和 `verify` 授权判断前执行风险检测。
- 高置信危险命令返回 `policy_denied`，可疑命令强制人工确认或在非交互模式拒绝。
- 新增 `tests/test_command_risk.py`，并扩展 policy/runtime 回归测试。

## P2：企业平台能力

### 10. FastAPI 服务

状态：已完成。

任务：

通过 API 暴露现有 `CodingAgent` runtime。

验收标准：

- CLI 和 API 复用同一套 runtime。
- API 可以创建 session。
- API 可以发送消息。
- API 可以流式返回事件。
- API 可以取消 run。

完成记录：

- 新增 `coding_agent.api.app`，提供 FastAPI app factory 和 `ApiSessionManager`。
- API 复用 `CodingAgent` 与 `ChatSession`，不复制 runtime，也不新增宿主机 shell 或直接写入能力。
- 新增 `GET /health`、`POST /v1/sessions`、`GET /v1/sessions`、`GET /v1/sessions/{session_id}`、`POST /v1/sessions/{session_id}/messages/stream`、`POST /v1/runs/{run_id}/cancel` 和 `POST /v1/sessions/{session_id}/cancel`。
- 消息接口使用 Server-Sent Events 原样返回 `AgentEvent`，便于后续前端 UI 渲染流式文本、工具事件、审批事件、token 事件和完成/失败/取消事件。
- API 层复用现有 session lock，并用进程内 active run registry 防止同一会话并发发送和支持取消。
- 新增 `tests/test_api.py`，使用 fake model 覆盖创建/列出 session、SSE 事件流、未知 session、非法 session id、空消息、取消活跃运行和取消不存在的 run。
- 当前未实现认证授权、Web 审批响应接口、持久化 approval queue 和跨进程 active run registry；这些仍属于后续平台化任务。

### 11. PostgreSQL 存储

状态：已完成。

任务：

在现有 store protocol 后面增加 PostgreSQL 持久化实现。

验收标准：

- Sessions、runs、events、approvals、artifacts 有 schema。
- 存在 Alembic migrations。
- JSONL 仍可作为本地模式使用。
- JSONL 和 PostgreSQL store 都通过 repository contract tests。

完成记录：

- 新增 `coding_agent.db`，用 SQLAlchemy Core 定义平台存储基础表，覆盖 sessions、runs、events、approvals、artifacts 和 model usage 等本轮验收对象。
- 新增 `PostgresSessionStore`，实现 `SessionStore` 的事件、checkpoint、summary 和 transcript 核心行为。
- 新增 Alembic 初始迁移 `0001_create_platform_storage`，创建 sessions、session_events、checkpoints、transcripts、approvals、artifacts 和 model_usage 表。
- 新增 `tests/test_session_store_contract.py`，让 JSONL 与 SQLAlchemy store 共同通过 repository contract tests，并覆盖 summary 更新不得级联删除事件和 checkpoint。
- JSONL 仍是当前 CLI/API 默认本地模式；PostgreSQL 运行时配置切换、分布式锁、持久化 approval queue 和更细粒度 runs/turns/tools/patches/audit logs 属于后续任务。

### 12. 审批 UI

状态：已完成。

任务：

创建最小 Web patch 审批流程。

验收标准：

- 展示 changed files。
- 展示脱敏 diff preview。
- 支持 approve 和 reject。
- 写入 audit record。

完成记录：

- 新增 API 专用的进程内 `ApprovalRegistry`，通过现有 `ApprovalProvider` 协议挂起高风险操作并等待 Web 端 approve/reject。
- `ApprovalProvider.request` 增加 `session_id` 和 `run_id` 上下文，CLI 审批行为保持不变。
- 新增 `GET /approvals/ui` 最小本地审批页面，使用 DOM `textContent` 渲染动态内容，避免 diff preview 中的 HTML 被执行。
- 新增 `GET /approvals`、`GET /approvals/{approval_id}`、`POST /approvals/{approval_id}/approve` 和 `POST /approvals/{approval_id}/reject`。
- 审批详情会展示 tool、reason、session/run、changed files 和脱敏截断后的 details/diff preview。
- 审批请求和决议会写入 `.coding-agent/approvals/audit.jsonl`，当前仍是本地 JSONL 审计，不是 PostgreSQL 持久化审批队列。
- 取消 run/session 或消息流断开时会取消对应 pending approval，避免工具永久挂起。
- 新增 API 回归测试，覆盖 approve、reject、patch preview 脱敏、audit、幂等决议、非法状态/ID、resolved 列表查询和 cancel 解挂。

## P3：知识检索和多 Agent

### 13. Milvus 语义索引

状态：未开始。

任务：

增加代码 chunk embedding 和 Milvus search。

验收标准：

- Code chunk 包含 path、symbol、language、hash 元数据。
- Semantic search 可以作为工具被调用。
- 测试使用 fake embedding 或本地 stub。

### 14. Memory Store 和 Recall

状态：未开始。

任务：

实现项目记忆和用户记忆。

验收标准：

- Memory record 经过审核后再 promotion。
- Recall 可以把相关 memory 注入上下文。
- Memory 包含 source session、confidence、status 和 timestamps。

### 15. MCP 集成

状态：未开始。

任务：

增加 MCP server 配置、连接管理器和 MCP 工具包装。

验收标准：

- MCP 工具通过 schema 注册。
- MCP 调用经过 policy 和 trace。
- 工具结果受输出预算和脱敏策略约束。

### 16. Worktree 隔离的多 Agent

状态：未开始。

任务：

增加 planner、coder、reviewer、verifier agents，并结合 worktree 隔离。

验收标准：

- Child agents 不能直接修改主工作区。
- Worktree 输出以 patch proposal 返回。
- Parent trace 包含 child run 关系。
