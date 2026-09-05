# CodingAgent 架构说明

本文档记录 `CodingAgent` 当前已经实现的真实架构。它描述当前项目现状、模块职责和后续开发必须保留的设计边界。

## 当前定位

`CodingAgent` 是一个安全优先的本地 coding agent MVP，面向 Windows 本地开发环境。当前实现是一个 Python 包，提供 Typer 命令行入口、最小 FastAPI/SSE 服务入口、本地 Web 审批入口、Model Gateway 与 DeepSeek 模型适配器、事件驱动运行时、显式 Plan Mode、Docker 沙箱执行、沙箱执行前 command risk detector、基于 patch 的宿主机写回、默认 JSONL 会话持久化、可配置 SQLAlchemy/MySQL 会话存储、MySQL-backed API approval queue、MySQL-backed pending patch package、真实 DashScope/Milvus 语义代码索引、provider usage token 统计、脱敏 trace 和审批审计存储、GitHub Actions 最小 CI，以及围绕安全链路和 runtime 的基础测试。

当前项目还不是完整企业级平台。它尚未实现认证授权、生产级 Web UI、Redis 分布式 active run/session lock、MCP、真实 memory 检索、模型辅助上下文摘要、多 agent 编排和 worktree 隔离。

## 核心设计原则

项目最重要的架构原则是：

> 模型可以读取信息并提出修改，但不能直接获得宿主机写权限或宿主机 shell 权限。

所有宿主机修改都应继续经过以下链路：

1. 生成过滤后的工作区快照。
2. 在隔离 Docker 容器中执行命令。
3. 在沙箱内部生成 Git diff。
4. 注册 pending patch。
5. 经过人工或配置化审批。
6. 校验 patch 结构。
7. 拒绝敏感路径。
8. 校验宿主工作区文件哈希。
9. 执行 `git apply --check`。
10. 将 patch 应用回宿主工作区。

这是当前项目相对于普通本地 shell agent 的核心优势。

## 模块说明

### `coding_agent.ai`

职责：模型供应商抽象。

当前文件：

- `contracts.py`：模型、消息、工具调用、usage、流式事件等供应商无关契约。
- `gateway.py`：provider registry 和 adapter 工厂。
- `deepseek.py`：基于 OpenAI-compatible Chat Completions SSE 协议的 DeepSeek 适配器。

当前状态：

- Model Gateway 负责根据配置选择 provider 并创建 adapter。
- DeepSeek 是唯一已实现的真实模型供应商。
- Runtime 依赖 `ModelAdapter` 协议，而不是直接依赖 DeepSeek。

### `coding_agent.runtime`

职责：供应商无关的 agent 执行循环。

当前文件：

- `loop.py`：执行单个 agent 回合，处理模型流、工具调用、审批、trace 和 artifact。
- `plan.py`：维护 Plan Mode 状态机，记录当前计划状态、计划 ID、修订次数和最近一次失败。
- `context.py`：负责 token 估算、确定性 compact、近期消息保留和 tool call/result 边界保护。
- `token_usage.py`：维护 session 级 token 账本，用 provider usage 作为真实消耗来源。
- `events.py`：CLI 以及未来 API/TUI 客户端消费的公开事件类型。

当前状态：

- 支持 run start、message output、reasoning output、tool start/update/finish、approval request/resolve、finish、failure 和 cancellation。
- Assistant 文本会按模型 `TextDelta` 增量发出 `message_delta`。
- Context manager 会在长历史接近预算时生成确定性 compact summary，并保留近期消息原文。
- Plan Mode 开启时，runtime 会在 `sandbox_shell`、`verify` 和 `apply_patch` 前强制要求已有获批的 `submit_plan`。
- 已获批计划覆盖的高风险工具一旦返回非 success，runtime 会发出 `plan_failed`；后续高风险工具会先收到 `plan_revision_required` 并被拒绝，直到模型提交包含失败摘要和调整策略的修订计划并获批。

### `coding_agent.agent`

职责：装配层。

当前文件：

- `coding_agent.py`：组合模型、策略、工具、沙箱、会话、trace、artifact 和工作区上下文。

当前状态：

- `CodingAgent` 是 CLI 以及未来 API/TUI 复用的稳定 Python API。
- `ChatSession` 支持连续对话和 checkpoint 恢复。
- 系统提示词强调仓库内容不可信、命令只能进沙箱、修改只能通过 patch 回写、不能虚构结果。

### `coding_agent.tools`

职责：模型可调用工具的契约与实现。

当前文件：

- `contracts.py`：工具协议、上下文、更新事件和结果模型。
- `builtin.py`：`read`、`search`、`edit`、`write`、`shell`、`git_diff`。
- `sandbox.py`：`sandbox_shell`、`verify`、`apply_patch`。
- `plan.py`：`submit_plan`，用于在 Plan Mode 下提交计划审批。
- `semantic.py`：`semantic_search`，只读语义检索工具。

当前状态：

- Runtime 当前暴露只读工具、沙箱工具、patch 工具和语义检索工具。
- 宿主机 `shell` 被 policy 明确拒绝。
- 直接 `edit` 和 `write` 已有实现，但不在默认 runtime 工具列表中。

### `coding_agent.sandbox`

职责：隔离命令执行和受控宿主机写回。

当前文件：

- `contracts.py`：sandbox request、result、limits、snapshot 等模型。
- `snapshot.py`：生成过滤后的工作区快照。
- `docker.py`：用无网络、强约束的一次性 Docker 容器执行命令。
- `patches.py`：注册、持久化、校验并应用沙箱生成的 patch。

当前状态：

- 快照会排除敏感文件、内部状态、虚拟环境、缓存、符号链接和大文件。
- Docker 通过 stdin 接收快照，不挂载宿主工作区。
- Docker 使用 `--network none`、`--read-only`、非 root 用户、`no-new-privileges`、删除 capabilities、PID/内存/CPU 限制和 tmpfs workspace。
- Patch 应用会拒绝二进制 patch、子模块、文件模式变化、符号链接、可执行权限变化、重命名、复制、敏感路径、changed-file 不一致以及宿主并发修改。
- `PatchRegistry` 通过 `PatchStore` 抽象保存待回写 patch。默认 JSONL/本地模式使用进程内 `InMemoryPatchStore`；配置 MySQL session store 时使用 `MySqlPatchStore`。
- `apply_patch` 在应用前会先把 patch 从 `pending` claim 为 `applying`，再重新执行结构、路径、hash 和 `git apply --check` 校验。

### `coding_agent.policy`

职责：统一工具和路径权限决策。

当前文件：

- `engine.py`：允许只读工具，授权控制沙箱和 patch 操作，拒绝宿主机 shell，拒绝敏感路径。
- `command_risk.py`：沙箱执行前的高置信命令风险检测器。

当前状态：

- 支持交互式审批和非交互模式拒绝。
- 使用 `WorkspacePathPolicy` 做路径安全检查。
- 在 `sandbox_shell` 和 `verify` 进入 Docker 前识别高置信危险命令并直接拒绝。
- 对可疑命令强制交互式复核；非交互模式下拒绝。

### `coding_agent.workspace`

职责：工作区检查和路径安全。

当前文件：

- `service.py`：识别项目标记、规则文件、语言、验证命令、Git 状态、文件读取和搜索。
- `security.py`：排除敏感路径和内部路径。

### `coding_agent.semantic`

职责：真实代码语义索引和检索。

当前文件：

- `contracts.py`：`CodeChunk`、`SemanticSearchHit`、`EmbeddingProvider`、`VectorIndex` 等协议和数据模型。
- `chunking.py`：按工作区安全策略扫描文本源码，生成带 path、language、symbol、line range、file hash 和 content hash 的 chunk。
- `embeddings.py`：通过 DashScope OpenAI-compatible `/embeddings` 接口调用 `qwen3.7-text-embedding`。
- `milvus.py`：基于 PyMilvus 创建 collection、校验向量维度、upsert chunk 和执行 top-k search。
- `store.py`：测试用的确定性内存向量索引。
- `service.py`：组合 chunker、embedding provider 和 Milvus store，提供 `build_index()` 和 `search()`。

当前状态：

- `agent index-workspace` 会将当前安全工作区 chunk 写入真实 Milvus collection。
- `agent semantic-search` 会调用真实 DashScope embedding 查询 Milvus。
- `semantic_search` 作为只读工具在 `CODING_AGENT_SEMANTIC_BACKEND=milvus` 且 DashScope 配置有效时注册到 runtime。
- 检索结果包含 `stale` 标记；如果当前文件 hash 与索引时不同，模型必须重新读取文件后再编辑或引用。
- 自动化测试使用 fake embedding 和 `InMemoryVectorIndex` 隔离外部服务；真实 smoke test 通过 `RUN_REAL_SEMANTIC_TESTS=1` 显式启用。

### `coding_agent.sessions`

职责：本地会话持久化。

当前文件：

- `factory.py`：根据 `AgentConfig` 在 JSONL 和 SQLAlchemy/MySQL session store 之间选择。
- `store.py`：JSONL session events、checkpoints、summaries、transcripts。
- `mysql.py`：SQLAlchemy-backed session store。
- `lock.py`：会话锁，防止同一 chat session 被并发写入。

当前状态：

- 支持连续对话恢复。
- 保存脱敏 checkpoint、人类可读 transcript、会话摘要（运行次数、工具次数、provider usage 累计 token、当前上下文 token、上下文窗口占比和最近 compact 节省量）。
- `MySqlSessionStore` 实现与 JSONL store 相同的核心协议，有合同测试覆盖。
- 未配置数据库时 CLI/API 默认使用 JSONL；显式配置 `CODING_AGENT_DATABASE_URL` 后使用 `MySqlSessionStore`。

### `coding_agent.tracing`

职责：诊断、审计友好的 trace 和 artifact。

当前文件：

- `store.py`：JSONL trace store、artifact store、application log、脱敏和输出摘要。

### `coding_agent.memory`

职责：长期记忆边界。

当前文件：

- `contracts.py`：memory record 和 store protocol。

当前状态：目前只有 Noop 实现。

### `coding_agent.evals`

职责：可重复评测契约。

当前文件：

- `scenarios.py`：evaluation scenario、result、report 模型。

当前状态：仅提供最小结构。

### `coding_agent.api`

职责：HTTP/SSE 服务入口。

当前文件：

- `app.py`：FastAPI app factory、会话/run 内存索引、SSE 事件序列化和取消入口。
- `approvals.py`：API 审批队列、审批记录模型、进程内和 MySQL store、本地 JSONL 审计写入。

当前状态：

- `POST /v1/sessions`、`GET /v1/sessions`、`GET /v1/sessions/{session_id}`、`POST /v1/sessions/{session_id}/messages/stream`、`POST /v1/runs/{run_id}/cancel`、`POST /v1/sessions/{session_id}/cancel`。
- `GET /approvals/ui` 最小本地审批页面，`GET /approvals`、`GET /approvals/{approval_id}`、`POST /approvals/{approval_id}/approve`、`POST /approvals/{approval_id}/reject`。
- API 通过 `ApprovalProvider` 挂起需要人工确认的操作。
- 配置 MySQL 后，`ApprovalRegistry` 使用 `MySqlApprovalStore`，等待中的 runtime 轮询数据库决议。

### `coding_agent.db`

职责：平台化关系数据库 schema 和初始化工具。

当前文件：

- `tables.py`：SQLAlchemy Core 表定义，覆盖 sessions、runs、session_events、checkpoints、transcripts、approvals、artifacts、patches 和 model_usage。
- `engine.py`：数据库 engine 创建和 schema 初始化工具。
- `diagnostics.py`：数据库连通性诊断。

### `coding_agent.cli`

职责：本地命令行入口。

当前文件：

- `app.py`：Typer 命令，包括一次性 run、连续 chat、resume、status、sessions、history、clear 和审批提示。

## 质量门禁

在受限 Windows 账户下运行时，建议使用项目内 uv cache 和 pytest 临时目录：

```powershell
uv --cache-dir .uv-cache run ruff check
uv --cache-dir .uv-cache run mypy
uv --cache-dir .uv-cache run mypy src
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider
```

最近一次验证结果：

- `ruff check`：通过。
- `mypy`：通过。
- `mypy src`：通过。
- `pytest`：136 passed，2 skipped。
