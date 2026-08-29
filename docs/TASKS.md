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

状态：未开始。

任务：

引入 provider registry，同时保持现有 DeepSeek 行为不变。

验收标准：

- DeepSeek adapter 继续可用。
- Runtime 继续依赖供应商无关接口。
- 测试覆盖 provider selection。

### 6. 真正的 Streaming Delta

状态：未开始。

任务：

让 assistant 文本在模型返回时增量输出，而不是最终一次性输出。

验收标准：

- CLI 可以渲染增量输出。
- 最终 session checkpoint 仍保存完整 assistant 内容。
- 测试覆盖流式 chunk。

### 7. Context Manager

状态：未开始。

任务：

增加负责 token budget 和自动 compact 的 context manager。

验收标准：

- 长历史通过摘要保留，而不是简单截断。
- 最近工具结果和重要文件得到保留。
- 测试覆盖 compact 边界。

### 8. Plan Mode

状态：未开始。

任务：

增加 plan mode，让模型在使用 shell 或 patch 工具前必须先产出计划。

验收标准：

- 使用 sandbox 或 patch 工具前需要计划审批。
- 非交互模式默认拒绝未审批执行。
- 测试覆盖批准和拒绝路径。

## P2：企业平台能力

### 9. FastAPI 服务

状态：未开始。

任务：

通过 API 暴露现有 `CodingAgent` runtime。

验收标准：

- CLI 和 API 复用同一套 runtime。
- API 可以创建 session。
- API 可以发送消息。
- API 可以流式返回事件。
- API 可以取消 run。

### 10. PostgreSQL 存储

状态：未开始。

任务：

在现有 store protocol 后面增加 PostgreSQL 持久化实现。

验收标准：

- Sessions、runs、events、approvals、artifacts 有 schema。
- 存在 Alembic migrations。
- JSONL 仍可作为本地模式使用。
- JSONL 和 PostgreSQL store 都通过 repository contract tests。

### 11. 审批 UI

状态：未开始。

任务：

创建最小 Web patch 审批流程。

验收标准：

- 展示 changed files。
- 展示脱敏 diff preview。
- 支持 approve 和 reject。
- 写入 audit record。

## P3：知识检索和多 Agent

### 12. Milvus 语义索引

状态：未开始。

任务：

增加代码 chunk embedding 和 Milvus search。

验收标准：

- Code chunk 包含 path、symbol、language、hash 元数据。
- Semantic search 可以作为工具被调用。
- 测试使用 fake embedding 或本地 stub。

### 13. Memory Store 和 Recall

状态：未开始。

任务：

实现项目记忆和用户记忆。

验收标准：

- Memory record 经过审核后再 promotion。
- Recall 可以把相关 memory 注入上下文。
- Memory 包含 source session、confidence、status 和 timestamps。

### 14. MCP 集成

状态：未开始。

任务：

增加 MCP server 配置、连接管理器和 MCP 工具包装。

验收标准：

- MCP 工具通过 schema 注册。
- MCP 调用经过 policy 和 trace。
- 工具结果受输出预算和脱敏策略约束。

### 15. Worktree 隔离的多 Agent

状态：未开始。

任务：

增加 planner、coder、reviewer、verifier agents，并结合 worktree 隔离。

验收标准：

- Child agents 不能直接修改主工作区。
- Worktree 输出以 patch proposal 返回。
- Parent trace 包含 child run 关系。
