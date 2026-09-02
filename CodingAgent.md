# CodingAgent 最终形态设计说明

本文档描述的是以当前 `CodingAgent` 项目为基础，参考 `D:\Software\MewCode` 的完整 agent 运行时能力，并按照企业级 coding agent 的工程标准继续演进后的目标形态。

当前项目已经具备一个安全优先的本地 coding agent 内核：Python 包结构清晰，支持 DeepSeek 模型调用、事件驱动运行时、只读工具、Docker 无网络沙箱、补丁注册与审批回写、默认 JSONL 会话与追踪存储、可配置 SQLAlchemy/MySQL 会话存储、MySQL-backed API approval queue、脱敏 artifact、基础测试和严格类型检查。后续完整形态不是简单扩展一个 CLI 工具，而是将其升级为一个可审计、可扩展、可部署、支持团队协作和多模型接入的企业级 agent 平台。

## 1. 项目整体定位

`CodingAgent` 的最终定位是：

> 一个安全优先、可审计、支持沙箱执行、补丁审批、多模型接入、项目记忆、工具生态和多 agent 协作的企业级 coding agent runtime。

它面向的场景包括：

- 本地或企业私有代码仓库的自动阅读、分析、修改和验证。
- 在受控沙箱中执行测试、构建、静态检查和脚本。
- 通过审批流将模型产生的修改安全写回宿主仓库。
- 为团队提供可追踪的 coding agent 会话、运行记录、成本统计和审计日志。
- 通过 MCP、Skills、Hooks 等机制接入企业内部工具链。
- 通过记忆系统沉淀项目约定、历史修复、用户偏好和架构知识。
- 通过多 agent 和 worktree 隔离支持并行探索、计划、实现和验证。

项目的核心差异化不在于“能不能让大模型调用 shell”，而在于是否能将大模型的代码修改限制在可审计、可回滚、可验证的工程边界内。

## 2. 当前项目基础

当前 `CodingAgent` 项目采用 `src/coding_agent` 包结构，已经拆分出较清晰的领域模块：

- `ai`：模型供应商无关协议，以及 DeepSeek 适配器。
- `runtime`：事件驱动 agent loop，负责模型流、工具调用、审批、trace、artifact 写入。
- `agent`：装配层，负责组合模型、工具、策略、沙箱、会话和追踪。
- `tools`：模型可调用工具协议，以及 read、search、git_diff、sandbox_shell、verify、apply_patch。
- `sandbox`：Docker 沙箱契约、快照生成、一次性容器执行、patch 注册和校验。
- `policy`：工具权限、路径边界、写入审批和 shell 审批。
- `workspace`：工作区检查、文件搜索、敏感路径过滤。
- `sessions`：JSONL 会话事件、checkpoint、transcript、会话索引、MySQL store 和独占锁。
- `db`：SQLAlchemy Core schema、engine helper 和 Alembic migration。
- `tracing`：结构化 trace、artifact、应用日志和脱敏。
- `memory`：记忆协议预留，目前是 Noop 实现。
- `evals`：最小评测场景和报告结构。
- `cli`：Typer 命令行入口，支持一次性任务和连续聊天会话。

当前项目最强的工程基础是安全执行链路：

1. 模型不能直接写宿主机文件。
2. 模型不能直接执行宿主机 shell。
3. 命令只能在一次性 Docker 容器中运行。
4. 沙箱不挂载宿主目录，而是接收过滤后的工作区快照。
5. 沙箱网络关闭、只读根文件系统、非 root 用户、capability 全部删除。
6. 沙箱变更以 Git diff 形式返回。
7. 写回必须通过 `apply_patch`，并经过路径、敏感文件、文件哈希、patch 结构和 `git apply --check` 校验。
8. 会话、trace、artifact 会被脱敏持久化，便于审计和排障。

这条链路是项目区别于普通 agent demo 的核心价值。

## 3. 与 MewCode 的对比

`MewCode` 是一个功能更完整的终端 AI coding assistant。它已经具备 Textual TUI、多 provider 模型接入、MCP、Skills、Hooks、上下文压缩、长期记忆、sub-agent、team、background task、git worktree、remote WebSocket UI 和大量测试。

相比之下，当前 `CodingAgent` 的能力更窄，但安全边界更鲜明。

### 3.1 CodingAgent 已有优势

- 沙箱执行更安全：不直接挂载宿主仓库，而是复制过滤后的快照进入容器。
- 写回模型更严格：模型只能通过 patch registry 申请写回。
- 安全叙事更适合企业：快照过滤、敏感路径拒绝、哈希校验、审批、trace、artifact 构成完整闭环。
- 代码规模较小，模块边界清楚，适合作为秋招项目继续快速扩展。

### 3.2 MewCode 已有而 CodingAgent 欠缺的能力

- 多模型 provider：Anthropic、OpenAI Responses、OpenAI-compatible。
- 更成熟的上下文工程：自动 compact、工具输出预算、compact 后恢复最近读取文件。
- 更强的交互体验：TUI、plan mode、permission dialog、slash command、remote UI。
- 工具体系扩展：deferred tool search、MCP 工具封装、skill 加载与安装。
- 自动化生命周期：Hooks、session lifecycle、tool lifecycle、message lifecycle。
- 多 agent 运行：sub-agent、background task、team mailbox、trace tree。
- Git worktree 隔离：不同任务可以在独立 worktree 中并行修改。
- 长期记忆系统：项目记忆、用户记忆、索引、召回、后台整理。
- 更丰富测试：权限、上下文、MCP、memory、sub-agent、team、worktree、hook 等都有覆盖。

### 3.3 不应直接照搬 MewCode 的部分

MewCode 功能丰富，但它更偏本地终端产品。对于企业级安全项目，不能直接照搬以下做法：

- 本地直写文件工具不能成为默认路径。
- 本地 shell 或 OS sandbox 不能替代容器级隔离。
- Windows 下 OS sandbox 能力有限，必须保留 Docker/远程隔离运行时。
- 嵌入式单文件 remote UI 不适合作为长期企业前端架构。
- 大型单体 TUI/agent 文件会增加维护成本，应该保留当前项目的清晰分层。

最终形态应当吸收 MewCode 的 agent 生命周期、工具生态和产品体验，但保留 `CodingAgent` 现有的安全内核。

## 4. 企业级项目标准下的当前不足

按照企业级 coding agent 标准，当前项目还存在以下不足：

### 4.1 模型层不足

当前只有 DeepSeek 适配器，且配置入口偏简单。最终需要支持：

- DeepSeek
- OpenAI
- Anthropic
- OpenAI-compatible 私有模型服务
- 本地模型网关
- 按任务选择模型
- fallback / retry / rate limit / cost accounting

### 4.2 上下文管理不足

当前已有确定性 context manager，支持 token 估算、自动 compact、近期尾部原文保留和
tool call/result 配对保护。CLI 已能基于 provider usage 显示 session 累计 token 消耗、
当前上下文 token、窗口占比和 compact 节省量。它尚未实现模型辅助摘要、上下文窗口
自动探测和检索增强。
最终需要支持：

- 上下文窗口检测
- 模型辅助 compact summary
- 工具输出分级摘要
- 大输出落盘
- compact 后恢复关键文件
- 当前任务计划和决策链保留
- 长会话恢复

### 4.3 记忆系统不足

当前 `memory` 只有协议和 `NoopMemoryStore`。最终需要实现：

- 用户记忆
- 项目记忆
- 仓库约定记忆
- 历史修复记忆
- 人工确认后的长期记忆
- 向量召回
- 记忆过期和置信度管理

### 4.4 工具体系不足

当前工具固定，缺少插件化发现和企业内部系统接入。最终需要支持：

- MCP 工具接入
- 工具注册表
- 工具权限分级
- 工具 schema 校验
- 工具调用审计
- deferred tool loading
- 企业内部工具，例如 Jira、GitHub/GitLab、CI、知识库、制品库。

### 4.5 交互层不足

当前是 Typer CLI。最终需要形成多入口：

- CLI
- Web UI
- WebSocket/SSE 实时事件流
- IDE 插件入口
- REST API
- 后台任务 worker
- 审批控制台

### 4.6 协作能力不足

当前是单 agent 单任务运行。最终需要支持：

- planner agent
- coder agent
- reviewer agent
- verifier agent
- researcher agent
- 多 agent trace tree
- background task
- worktree 隔离
- 任务取消、恢复、重试、汇总

### 4.7 工程化基础不足

当前已有 `pytest`、`ruff`、`mypy` 配置，但还需要补齐：

- GitHub Actions
- pre-commit
- coverage
- Docker 镜像 CI
- 安全扫描
- 依赖漏洞扫描
- release workflow
- benchmark workflow
- E2E 测试
- mock LLM 测试
- 合同测试

### 4.8 数据层不足

当前默认使用本地 JSONL 文件，适合 MVP；项目已提供 SQLAlchemy/MySQL 会话存储底座、MySQL-backed API approval queue、Alembic migration 和 CLI/API 运行时配置切换。仍缺少分布式锁、pending patch 持久化审批包和完整审计数据模型。最终需要：

- MySQL 存储结构化业务数据
- Milvus 存储代码和记忆向量
- Redis 存储锁、缓存、队列状态和 pub/sub
- 对象存储保存 artifact、trace 附件、沙箱输出
- Alembic 管理数据库迁移

## 5. 最终技术栈

### 5.1 核心语言与运行时

- Python 3.12+
- asyncio
- Pydantic
- Typer
- FastAPI
- WebSocket / Server-Sent Events
- httpx
- pytest
- pytest-asyncio
- ruff
- mypy
- uv

Python 作为主体语言，负责 agent runtime、模型适配、工具协议、安全策略、任务调度、API 服务和数据访问。

### 5.2 模型与 Agent 技术

- DeepSeek Chat Completions
- OpenAI Responses API
- Anthropic Messages API
- OpenAI-compatible adapter
- Tool calling
- Streaming events
- Prompt versioning
- Context compaction
- Retrieval-Augmented Generation
- Multi-agent orchestration
- MCP
- Skills
- Hooks

模型层不直接侵入业务逻辑，统一通过 `ModelAdapter` 暴露流式事件、文本增量、reasoning、tool call、usage 和错误信息。

### 5.3 数据与存储

- MySQL：会话、运行、事件、审批、用户、项目、权限、审计日志。
- Milvus：代码语义索引、长期记忆、项目知识、历史任务向量。
- Redis：任务队列、分布式锁、短期缓存、WebSocket pub/sub。
- S3 / MinIO：sandbox 输出、trace artifact、报告、补丁包。
- Alembic：数据库 schema 迁移。
- SQLAlchemy：数据访问层。

本地 JSONL 存储会保留为开发模式和单机模式，但企业部署默认使用 MySQL。

### 5.4 沙箱与执行环境

- Docker
- Linux container
- 无网络执行模式
- 非 root 用户
- 只读根文件系统
- capability drop
- CPU / memory / pid / timeout 限制
- tmpfs 临时工作区
- 可替换远程沙箱执行器

沙箱层是项目安全模型的核心，最终会支持本地 Docker、远程隔离执行集群和按语言预构建镜像。

### 5.5 代码理解与检索

- ripgrep
- Git diff
- Tree-sitter
- LSP
- 代码 chunking
- embedding model
- Milvus vector search
- MySQL metadata filter

最终的代码检索不只依赖全文搜索，而是结合符号、文件结构、调用关系、向量召回和最近修改上下文。

### 5.6 可观测性与运维

- OpenTelemetry
- Prometheus
- Grafana
- JSON structured logging
- trace id / span id
- token usage metrics
- cost metrics
- sandbox resource metrics
- approval audit log

所有 agent 决策、工具调用、审批、补丁、验证和失败都应可追踪。

### 5.7 安全与治理

- RBAC
- workspace policy
- tool policy
- path policy
- secret redaction
- audit log
- approval workflow
- dependency vulnerability scanning
- SBOM
- image digest pinning
- tenant isolation

企业环境中，agent 的能力边界必须是系统级约束，而不是只靠 prompt 约束。

## 6. 最终模块设计

### 6.1 Model Gateway

模型网关负责屏蔽不同大模型供应商差异。

职责：

- 统一消息格式。
- 统一 tool schema。
- 统一 streaming event。
- 统一 reasoning / thinking 输出。
- 统一 usage 和 cost 统计。
- 处理 retry、timeout、rate limit、fallback。
- 支持按任务类型选择模型。

最终接口仍保持当前 `ModelAdapter` 的思想，但扩展 provider registry：

- `DeepSeekAdapter`
- `OpenAIResponsesAdapter`
- `AnthropicAdapter`
- `OpenAICompatAdapter`
- `LocalModelAdapter`

### 6.2 Agent Runtime

Agent Runtime 是项目的核心执行循环。

职责：

- 接收用户任务。
- 构造系统上下文和任务上下文。
- 调用模型。
- 解析工具调用。
- 执行权限检查。
- 调度工具。
- 写入 trace。
- 保存会话。
- 处理取消、失败、重试和恢复。

最终 Runtime 需要从当前单 agent loop 演进为支持多执行模式：

- `single_turn`
- `chat_session`
- `plan_then_execute`
- `background_task`
- `multi_agent_task`
- `review_only`
- `verify_only`

### 6.3 Tool Registry

工具注册表负责管理所有模型可用工具。

基础工具：

- `read`
- `search`
- `git_diff`
- `sandbox_shell`
- `verify`
- `apply_patch`

扩展工具：

- `mcp_call`
- `semantic_search`
- `open_issue`
- `create_pr`
- `run_ci`
- `query_docs`
- `load_skill`
- `spawn_agent`
- `create_worktree`

工具注册表需要支持：

- schema 校验
- 权限标注
- 动态加载
- deferred loading
- 工具分组
- 工具审计
- 工具输出预算

### 6.4 Sandbox Execution

沙箱执行层继续保持当前项目的安全设计。

执行流程：

1. 生成过滤后的 workspace snapshot。
2. 排除 `.git`、`.coding-agent`、`.env`、密钥、虚拟环境、缓存、大文件和符号链接。
3. 将 snapshot 通过 stdin 传入一次性 Docker 容器。
4. 容器内初始化临时 Git 仓库。
5. 执行模型请求的命令。
6. 收集 stdout、stderr、exit code、Git diff 和 changed files。
7. 返回结构化 `SandboxResult`。
8. `verify` 丢弃变更。
9. `sandbox_shell` 将变更注册为 pending patch。

最终增强：

- 远程沙箱池
- 镜像 digest 固定
- SBOM
- 资源使用统计
- 语言镜像模板
- 沙箱缓存
- per-task network policy
- 禁止危险系统调用

### 6.5 Patch Approval

补丁审批是宿主机写入的唯一入口。

当前已经具备：

- patch id
- changed files
- diff preview
- binary patch 拒绝
- symlink / executable mode 拒绝
- rename / copy 拒绝
- submodule patch 拒绝
- 敏感路径拒绝
- 快照后文件哈希校验
- `git apply --check`

最终增强：

- patch 持久化审批队列
- Web 审批页面
- reviewer comment
- 多级审批
- patch risk score
- 自动最小验证建议
- 失败自动回滚
- 与 GitHub/GitLab PR 集成

### 6.6 Context Manager

上下文管理器负责让长任务可持续运行。

最终能力：

- token budget 估算
- provider context window 检测
- 历史消息裁剪
- 自动 compact
- compact summary 持久化
- 工具输出摘要
- 大输出 artifact 化
- 最近读取文件恢复
- 当前计划恢复
- 关键错误和决策恢复

当前项目已有 message history 和 artifact 存储，但还需要把裁剪升级为真正的 context lifecycle。

### 6.7 Memory System

记忆系统负责长期知识沉淀。

最终分层：

- Session memory：当前会话内短期上下文。
- Project memory：项目架构、命令、约定、坑点。
- User memory：用户偏好和常用流程。
- Organization memory：企业内部规范、代码规范、安全策略。
- Reference memory：外部文档、API 文档、设计说明。

数据流：

1. 从会话、trace、用户反馈和人工标注中提取候选记忆。
2. 人工或规则审核后写入 MySQL。
3. 生成 embedding 并写入 Milvus。
4. 任务开始时根据 query、workspace、文件、语言和历史任务召回。
5. 将高置信度记忆注入系统上下文。
6. 定期合并、去重、过期和降权。

### 6.8 Workspace Index

工作区索引负责理解代码仓库。

最终能力：

- 文件树索引
- Git 状态索引
- README / AGENTS / CONTRIBUTING 规则读取
- 语言识别
- 测试命令识别
- 依赖文件识别
- Tree-sitter AST 分块
- 函数、类、符号索引
- import / call graph
- embedding 生成
- Milvus 语义检索
- MySQL 元数据过滤

这样 agent 不需要每次都盲目全文搜索，而是能先定位相关模块和符号。

### 6.9 Policy Engine

策略引擎负责把安全要求变成强制执行。

当前已有：

- read/search/git_diff 自动允许
- write/sandbox/apply_patch 需要授权
- 非交互模式未授权即拒绝
- host shell 默认拒绝
- 敏感路径拒绝

最终增强：

- YAML/数据库策略配置
- project policy
- organization policy
- user role policy
- tool risk level
- command risk detector
- approval reason
- policy decision audit
- OPA/Rego 可选集成

### 6.10 Session, Trace and Audit

最终的数据记录需要支撑生产排障和合规审计。

记录对象：

- session
- run
- turn
- model request summary
- model response summary
- tool call
- tool result
- approval request
- approval decision
- sandbox execution
- patch proposal
- patch application
- verification result
- artifact
- cost usage
- error event

本地开发可继续用 JSONL，企业部署写入 MySQL，并将大对象写入 S3/MinIO。

### 6.11 API and UI

最终系统不只提供 CLI。

API 层：

- `POST /sessions`
- `POST /sessions/{id}/messages`
- `GET /sessions/{id}/events`
- `POST /runs/{id}/cancel`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`
- `GET /traces/{run_id}`
- `GET /artifacts/{artifact_id}`

UI 层：

- 会话列表
- 实时聊天
- 工具调用时间线
- diff 审批
- trace 查看
- artifact 下载
- memory 管理
- 项目配置
- 模型和成本看板

CLI 继续保留，作为开发者本地入口和 CI/脚本入口。

### 6.12 MCP, Skills and Hooks

最终需要引入可扩展生态。

MCP：

- 连接企业内部工具。
- 动态发现工具 schema。
- 统一通过 `mcp_call` 执行。
- 工具结果纳入 trace 和权限系统。

Skills：

- 项目级技能。
- 用户级技能。
- 企业级技能。
- 支持 inline skill 和 forked skill。
- 技能可声明适用场景、所需工具和上下文。

Hooks：

- session start
- turn start
- pre tool use
- post tool use
- pre patch apply
- post patch apply
- run finished
- run failed

Hooks 可用于自动安全扫描、通知、CI 触发、审计上报和质量门禁。

### 6.13 Multi-Agent and Worktree

多 agent 是最终形态的重要展示点。

角色：

- Planner：拆解任务、生成计划。
- Explorer：阅读仓库、定位相关代码。
- Coder：实现修改。
- Reviewer：检查风险和回归。
- Verifier：运行测试和验证。
- Summarizer：生成最终报告和 PR 描述。

执行隔离：

- 每个实现类任务可以创建独立 git worktree。
- agent 只能在自己的 worktree 沙箱中修改。
- 主 agent 汇总 diff、trace 和验证结果。
- 冲突由人工或 reviewer agent 处理。

这部分可以显著提高项目面试辨识度，因为它体现了任务调度、隔离、并发、状态管理和工程协作。

### 6.14 Eval and Benchmark

企业级 agent 必须能被评估。

最终评测体系：

- 固定代码修复任务集
- 安全拒绝任务集
- 工具调用正确性任务集
- patch 最小性评测
- 测试通过率
- 失败恢复率
- 人工审核指标
- token 成本
- 平均运行时间
- sandbox 资源消耗

测试层级：

- 单元测试
- 集成测试
- Docker sandbox 测试
- mock LLM 测试
- MCP 合同测试
- API E2E 测试
- UI E2E 测试
- 回归 benchmark

## 7. 推荐数据库设计

MySQL 中建议包含以下核心表：

- `users`
- `organizations`
- `projects`
- `workspaces`
- `sessions`
- `runs`
- `turns`
- `events`
- `tool_calls`
- `approvals`
- `patches`
- `artifacts`
- `memories`
- `model_usage`
- `audit_logs`

Milvus 中建议包含以下 collection：

- `code_chunks`
- `project_memories`
- `user_memories`
- `run_summaries`
- `documentation_chunks`

Redis 中建议保存：

- active run 状态
- WebSocket pub/sub channel
- distributed lock
- short-lived model cache
- pending approval notification
- background task queue

## 8. 面试中可以强调的技术亮点

这个项目最终可以围绕以下亮点展开：

1. **安全优先的 agent 写回模型**
   模型不能直接写宿主机，所有修改必须通过 Docker 快照、Git diff、patch registry 和审批回写。

2. **企业级权限和审计**
   每次工具调用、审批、沙箱执行、补丁应用和验证都有结构化记录，可追踪、可复盘。

3. **可插拔模型网关**
   DeepSeek、OpenAI、Anthropic 和私有模型服务通过统一协议接入，运行时不绑定具体供应商。

4. **上下文生命周期管理**
   通过 token budget、compact、artifact、memory recall 让长任务持续运行。

5. **向量记忆和代码语义索引**
   用 MySQL 管结构化元数据，用 Milvus 管向量召回，实现项目级长期知识。

6. **多 agent 协作**
   planner、coder、reviewer、verifier 分工协作，并通过 worktree 隔离并行修改。

7. **可验证的工程质量**
   pytest、ruff、mypy、coverage、CI、benchmark 和 mock LLM 共同保证质量。

8. **可部署平台化**
   从本地 CLI 扩展到 FastAPI、Web UI、任务 worker、审批控制台和企业私有化部署。

## 9. 分阶段演进路线

### Phase 1：补齐面试基础工程

目标：让当前项目成为可信的安全 agent MVP。

任务：

- 修复 package 类型标记和 mypy 包级检查。
- 增加 GitHub Actions。
- 增加 pre-commit。
- 增加 coverage。
- 增加 `ARCHITECTURE.md`。
- 增加 `THREAT_MODEL.md`。
- 增加 `EVALS.md`。
- 增加 mock LLM 集成测试。
- 准备稳定 demo 脚本。

### Phase 2：补齐 agent 核心能力

目标：让项目具备长任务运行能力。

任务：

- 多 provider model gateway。
- 真正的 streaming delta 输出。
- context token budget。
- 自动 compact。
- 工具输出 artifact 策略增强。
- plan mode。
- slash command registry。
- command risk detector。

### Phase 3：补齐企业扩展能力

目标：让项目从单机工具变成可扩展平台。

任务：

- FastAPI 服务。
- WebSocket/SSE 事件流。
- MySQL 数据层扩展。
- Redis 任务队列和锁。
- Web 审批页。
- MCP 工具接入。
- Skills 系统。
- Hooks 系统。

### Phase 4：补齐智能化与协作能力

目标：形成明显区别于普通 demo 的高级 agent 系统。

任务：

- Milvus 向量库。
- 代码语义索引。
- memory extraction。
- memory recall。
- multi-agent orchestration。
- worktree isolation。
- reviewer/verifier agent。
- benchmark dashboard。

### Phase 5：生产化

目标：达到企业私有化部署标准。

任务：

- RBAC。
- 多租户。
- 组织级 policy。
- 审计报表。
- OpenTelemetry。
- Prometheus/Grafana。
- 镜像安全扫描。
- SBOM。
- 灰度发布。
- backup/restore。
- disaster recovery。

## 10. 最终项目结构建议

最终目录可以演进为：

```text
src/coding_agent/
  ai/
    contracts.py
    gateway.py
    deepseek.py
    openai.py
    anthropic.py
    openai_compat.py
  runtime/
    loop.py
    context.py
    token_usage.py
    events.py
    planner.py
    recovery.py
  agent/
    coding_agent.py
    orchestration.py
    roles.py
  tools/
    contracts.py
    registry.py
    builtin.py
    sandbox.py
    mcp.py
    skills.py
  sandbox/
    contracts.py
    snapshot.py
    docker.py
    remote.py
    patches.py
    images.py
  policy/
    engine.py
    rules.py
    dangerous.py
    rbac.py
  workspace/
    service.py
    security.py
    indexer.py
    symbols.py
    embeddings.py
  memory/
    contracts.py
    store.py
    recall.py
    extraction.py
    consolidation.py
  sessions/
    store.py
    mysql.py
    lock.py
  tracing/
    store.py
    telemetry.py
    metrics.py
  api/
    app.py
    routes_sessions.py
    routes_runs.py
    routes_approvals.py
  workers/
    runner.py
    queue.py
  evals/
    scenarios.py
    runner.py
    report.py
  cli/
    app.py
```

## 11. 最终形态总结

`CodingAgent` 的最终形态不是一个简单的“命令行套壳大模型”，而是一个围绕安全执行、受控写回、上下文管理、可观测性、插件生态和多 agent 协作构建的工程系统。

当前项目已经有了最重要的底座：安全沙箱、补丁审批和基础数据库存储。后续最应该优先补的是工程化质量门禁、多 provider、上下文压缩、真实 memory、MCP/Skills/Hooks、MySQL 数据层扩展、Milvus 数据层、Web 审批和多 agent worktree 隔离。

如果用于秋招面试，建议把项目主线讲成：

> 我没有直接做一个能执行命令的 agent，而是先解决 coding agent 在企业落地时最关键的安全问题：模型如何在不直接接触宿主机写权限的前提下完成代码修改、验证和回写。然后在这个安全内核之上扩展模型网关、上下文管理、记忆系统、工具生态、多 agent 协作和平台化部署。

这条主线清晰、工程含量高，也能自然展开到 Python、Docker、FastAPI、MySQL、Milvus、Redis、MCP、OpenTelemetry、CI/CD 和安全治理等技术点。
