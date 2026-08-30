# CodingAgent 架构说明

本文档记录 `CodingAgent` 当前已经实现的真实架构。它和根目录下的 `CodingAgent.md` 分工不同：`CodingAgent.md` 主要描述长期最终形态，本文档只描述当前项目现状、模块职责和后续开发必须保留的设计边界。

## 当前定位

`CodingAgent` 是一个安全优先的本地 coding agent MVP，面向 Windows 本地开发环境。当前实现是一个 Python 包，提供 Typer 命令行入口、Model Gateway 与 DeepSeek 模型适配器、事件驱动运行时、Docker 沙箱执行、基于 patch 的宿主机写回、JSONL 会话持久化、provider usage token 统计、脱敏 trace 存储、GitHub Actions 最小 CI，以及围绕安全链路和 runtime 的基础测试。

当前项目还不是完整企业级平台。它尚未实现 Web UI、FastAPI 服务、PostgreSQL、Milvus、Redis、MCP、Skills、Hooks、真实 memory 检索、模型辅助上下文摘要、多 agent 编排和 worktree 隔离。

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
- CLI 只传递 provider 和 model 配置，不直接构造 `DeepSeekAdapter`。
- 工具调用通过统一的 `ToolCall` 对象表达。

后续工作：

- 增加 OpenAI Responses API 支持。
- 增加 Anthropic Messages API 支持。
- 增加 OpenAI-compatible 私有模型网关支持。
- 增加模型 fallback、限流处理、成本统计和 retry 策略。

### `coding_agent.runtime`

职责：供应商无关的 agent 执行循环。

当前文件：

- `loop.py`：执行单个 agent 回合，处理模型流、工具调用、审批、trace 和 artifact。
- `context.py`：负责 token 估算、确定性 compact、近期消息保留和 tool call/result 边界保护。
- `token_usage.py`：维护 session 级 token 账本，用 provider usage 作为真实消耗来源，并对锚点后的新增上下文做估算。
- `events.py`：CLI 以及未来 API/TUI 客户端消费的公开事件类型。

当前状态：

- 支持 run start、message output、reasoning output、tool start/update/finish、approval request/resolve、finish、failure 和 cancellation。
- Assistant 文本会按模型 `TextDelta` 增量发出 `message_delta`，同时完整内容仍保存到模型消息历史。
- Context manager 会在长历史接近预算时生成确定性 compact summary，并保留近期消息原文。
- Compact 会发出 `context_compacted` 事件，并写入 session event 和 trace。
- Runtime 会把模型返回的 usage 提升为 `model_usage_reported` 事件；`ChatSession` 聚合后发出 `token_usage_updated`，并更新 session summary。
- 支持最大 turn 数和最大工具调用数限制。
- 通过 `TraceStore` 写入 trace 事件。
- 在存在 artifact writer 时保存完整工具输出。

已知限制：

- 工具调用目前串行执行。
- Compact summary 目前是确定性抽取摘要，不调用模型生成高质量自然语言摘要。

后续工作：

- 增加模型辅助 compact summary。
- 增强工具输出摘要策略。
- 增加 plan mode。
- 对安全的只读工具做批量或并发执行。
- 增加结构化 retry 和 recovery 逻辑。

### `coding_agent.agent`

职责：装配层。

当前文件：

- `coding_agent.py`：组合模型、策略、工具、沙箱、会话、trace、artifact 和工作区上下文。

当前状态：

- `CodingAgent` 是 CLI 以及未来 API/TUI 复用的稳定 Python API。
- `ChatSession` 支持连续对话和 checkpoint 恢复。
- 系统提示词强调仓库内容不可信、命令只能进沙箱、修改只能通过 patch 回写、不能虚构结果。

后续工作：

- 将模型选择迁移到 model gateway。
- 为持久化存储增加依赖注入。
- 增加 planner、coder、reviewer、verifier 等 agent 角色编排。

### `coding_agent.tools`

职责：模型可调用工具的契约与实现。

当前文件：

- `contracts.py`：工具协议、上下文、更新事件和结果模型。
- `builtin.py`：`read`、`search`、`edit`、`write`、`shell`、`git_diff`。
- `sandbox.py`：`sandbox_shell`、`verify`、`apply_patch`。

当前状态：

- Runtime 当前暴露只读工具、沙箱工具和 patch 工具。
- 宿主机 `shell` 被 policy 明确拒绝。
- 直接 `edit` 和 `write` 已有实现，但不在默认 runtime 工具列表中。

后续工作：

- 增加 tool registry。
- 增加 MCP 工具包装。
- 增加 semantic search 工具。
- 增加工具输出预算管理。
- 增加工具审计元数据。

### `coding_agent.sandbox`

职责：隔离命令执行和受控宿主机写回。

当前文件：

- `contracts.py`：sandbox request、result、limits、snapshot 等模型。
- `snapshot.py`：生成过滤后的工作区快照。
- `docker.py`：用无网络、强约束的一次性 Docker 容器执行命令。
- `patches.py`：注册、校验并应用沙箱生成的 patch。

当前状态：

- 快照会排除敏感文件、内部状态、虚拟环境、缓存、符号链接和大文件。
- Docker 通过 stdin 接收快照，不挂载宿主工作区。
- Docker 使用 `--network none`、`--read-only`、非 root 用户、`no-new-privileges`、删除 capabilities、PID/内存/CPU 限制和 tmpfs workspace。
- Patch 应用会拒绝二进制 patch、子模块、文件模式变化、符号链接、可执行权限变化、重命名、复制、敏感路径、changed-file 不一致以及宿主并发修改。

后续工作：

- 增加远程沙箱执行器。
- 增加镜像 digest 固定。
- 增加沙箱资源指标。
- 增加不同语言的沙箱镜像。
- 增加持久化审批队列。

### `coding_agent.policy`

职责：统一工具和路径权限决策。

当前文件：

- `engine.py`：允许只读工具，授权控制沙箱和 patch 操作，拒绝宿主机 shell，拒绝敏感路径。

当前状态：

- 支持交互式审批和非交互模式拒绝。
- 使用 `WorkspacePathPolicy` 做路径安全检查。

后续工作：

- 增加可配置策略文件。
- 增加命令风险检测。
- 增加基于角色的权限策略。
- 将 policy decision 写入数据库审计。

### `coding_agent.workspace`

职责：工作区检查和路径安全。

当前文件：

- `service.py`：识别项目标记、规则文件、语言、验证命令、Git 状态、文件读取和搜索。
- `security.py`：排除敏感路径和内部路径。

当前状态：

- 支持有边界的仓库上下文注入。
- 仓库文本默认视为不可信。
- 可用时优先使用 `rg` 搜索。

后续工作：

- 增加 Tree-sitter 索引。
- 增加 LSP 或静态符号提取。
- 增加代码 chunk 和 embedding。
- 增加基于 Milvus 的语义搜索。

### `coding_agent.sessions`

职责：本地会话持久化。

当前文件：

- `store.py`：JSONL session events、checkpoints、summaries、transcripts。
- `lock.py`：会话锁，防止同一 chat session 被并发写入。

当前状态：

- 支持连续对话恢复。
- 保存脱敏 checkpoint。
- 保存人类可读 transcript。
- 保存会话摘要，包括运行次数、工具次数、provider usage 累计 token、当前上下文 token、上下文窗口占比和最近 compact 节省量。

后续工作：

- 增加 PostgreSQL 实现。
- 增加数据库 migration。
- 将 run、turn、event 规范化存储。
- 增加多用户归属。

### `coding_agent.tracing`

职责：诊断、审计友好的 trace 和 artifact。

当前文件：

- `store.py`：JSONL trace store、artifact store、application log、脱敏和输出摘要。

当前状态：

- 按 session 和 run 保存 trace events。
- 将完整文本输出写入脱敏 artifact。
- 记录应用生命周期日志。

后续工作：

- 增加 OpenTelemetry span。
- 增加 Prometheus metrics。
- 增加成本看板。
- 增加审计导出。

### `coding_agent.memory`

职责：长期记忆边界。

当前文件：

- `contracts.py`：memory record 和 store protocol。

当前状态：

- 目前只有 Noop 实现。

后续工作：

- 增加 PostgreSQL memory metadata。
- 增加 Milvus 向量索引。
- 增加 memory extraction 和人工审核。
- 增加 memory recall 注入。
- 增加过期时间、置信度和来源追踪。

### `coding_agent.evals`

职责：可重复评测契约。

当前文件：

- `scenarios.py`：evaluation scenario、result、report 模型。

当前状态：

- 仅提供最小结构。
- 尚未运行完整 benchmark suite。

后续工作：

- 增加 mock LLM 场景。
- 增加 patch 质量检查。
- 增加安全拒绝任务集。
- 增加 benchmark 报告。

### `coding_agent.cli`

职责：本地命令行入口。

当前文件：

- `app.py`：Typer 命令，包括一次性 run、连续 chat、resume、status、sessions、history、clear 和审批提示。

当前状态：

- CLI 是唯一用户界面。
- 审批在终端中完成。
- `/status` 会显示当前 session 的累计 token 消耗、当前上下文 token、窗口占比和最近 compact 节省量。

后续工作：

- 保留 CLI 作为开发者入口。
- 在同一个 `CodingAgent` API 上增加 FastAPI/WebSocket 服务。
- 增加 Web 审批界面。

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
- `pytest`：28 passed，1 skipped。

普通 `uv run pytest` 在 Codex 沙箱账户下可能失败，因为它可能尝试写入 `C:\Users\HP\AppData\Local\Temp\pytest-of-HP`。
