# 项目总览

## 基本信息

- **项目名称**：CodingAgent
- **技术栈**：Python 3.12 + Typer + FastAPI + Docker + SQLAlchemy/MySQL + Milvus + DashScope
- **项目路径**：`d:\Software\CodingAgent`
- **当前阶段**：安全优先 Coding Agent MVP 已完成，向高辨识度能力（Memory / MCP / 多 Agent）演进

## 项目定位

> 一个安全优先、可审计、支持沙箱执行、补丁审批、多模型接入、项目记忆、工具生态和多 agent 协作的企业级 coding agent runtime。

核心差异化：模型不能直接写宿主机、不能直接执行宿主机 shell。所有修改必须经过 Docker 快照 → Git diff → patch registry → 审批 → 校验 → 回写的完整链路。

## 当前已实现能力（基线）

### 安全内核
- Docker 无网络沙箱（`--network none`、只读根文件系统、非 root、drop capabilities、`no-new-privileges`）
- 工作区快照过滤（排除 `.git`、`.coding-agent`、`.env*`、密钥、虚拟环境、缓存、符号链接、大文件）
- Patch-only 宿主机写回（拒绝二进制、子模块、符号链接、重命名、复制、可执行权限变更、敏感路径、并发修改）
- 审批流（CLI 终端 + Web 审批页 + MySQL 持久化 approval queue）
- 命令风险检测器（高置信危险命令直接拒绝，可疑命令强制交互复核）
- 敏感路径策略（精确目录/文件名/密钥后缀匹配，不误伤普通源码）

### 模型与 Runtime
- Model Gateway provider registry，当前实现 `deepseek`
- 供应商无关模型契约（`ModelAdapter`、`ToolCall`、流式事件、usage）
- 事件驱动 runtime（工具执行、审批、取消、trace、artifact）
- 真正的 assistant 文本增量 streaming（逐个转发 `TextDelta`）
- 确定性 context manager（token budget、自动 compact、近期尾部保留、tool call/result 配对保护）
- Session 级 token usage 账本（provider usage 累计 + 当前上下文 token + 窗口占比 + compact 节省量）
- Plan Mode 状态机（高风险工具前必须提交计划，失败后需修订计划）

### 工具与策略
- 只读工具：`read`、`search`、`git_diff`
- 沙箱工具：`sandbox_shell`、`verify`
- 写回工具：`apply_patch`
- 计划工具：`submit_plan`
- 语义检索工具：`semantic_search`（DashScope + Milvus）

### 存储与服务
- JSONL 本地存储（默认）：session、checkpoint、transcript、summary、trace、artifact、application log
- MySQL 可选存储：`MySqlSessionStore`、`MySqlApprovalStore`、`MySqlPatchStore`
- Alembic 迁移（3 个版本）
- FastAPI + SSE 服务入口（复用 `CodingAgent` 与 `ChatSession`）
- 最小 Web 审批页面

### 语义检索
- DashScope `qwen3.7-text-embedding` embedding provider
- Milvus 向量库（collection 初始化、维度校验、upsert、top-k search）
- Workspace code chunker（path、symbol、language、line range、file hash、content hash）
- CLI：`agent index-workspace`、`agent semantic-search`

### 工程化
- `ruff`、`mypy --strict`、`pytest` 配置
- `src/coding_agent/py.typed` 包类型标记
- GitHub Actions CI（ruff + mypy + pytest）
- 136 个测试通过，2 个 skipped

## 目标状态（企业级 Coding Agent）

- 长期记忆系统（MySQL metadata + Milvus 向量 + extraction + 人工审核 + recall 注入）
- MCP 工具生态（动态发现、schema 校验、policy + trace 集成）
- 多 Agent 编排（Planner / Coder / Reviewer / Verifier + git worktree 隔离）
- 可插拔模型网关（OpenAI / Anthropic / OpenAI-compatible）
- 模型辅助上下文摘要
- Redis 分布式锁与协调
- 生产级 Web UI 与审批控制台
- RBAC 与多租户隔离

## 关键文件索引

| 模块 | 入口文件 |
|---|---|
| Agent 装配 | `src/coding_agent/agent/coding_agent.py` |
| Runtime 循环 | `src/coding_agent/runtime/loop.py` |
| Runtime 计划 | `src/coding_agent/runtime/plan.py` |
| Runtime 上下文 | `src/coding_agent/runtime/context.py` |
| Runtime Token | `src/coding_agent/runtime/token_usage.py` |
| Runtime 事件 | `src/coding_agent/runtime/events.py` |
| 模型契约 | `src/coding_agent/ai/contracts.py` |
| 模型网关 | `src/coding_agent/ai/gateway.py` |
| DeepSeek 适配 | `src/coding_agent/ai/deepseek.py` |
| 工具契约 | `src/coding_agent/tools/contracts.py` |
| 内置工具 | `src/coding_agent/tools/builtin.py` |
| 沙箱工具 | `src/coding_agent/tools/sandbox.py` |
| 计划工具 | `src/coding_agent/tools/plan.py` |
| 语义工具 | `src/coding_agent/tools/semantic.py` |
| 沙箱契约 | `src/coding_agent/sandbox/contracts.py` |
| 沙箱快照 | `src/coding_agent/sandbox/snapshot.py` |
| Docker 执行 | `src/coding_agent/sandbox/docker.py` |
| Patch 注册 | `src/coding_agent/sandbox/patches.py` |
| 策略引擎 | `src/coding_agent/policy/engine.py` |
| 命令风险 | `src/coding_agent/policy/command_risk.py` |
| 工作区服务 | `src/coding_agent/workspace/service.py` |
| 路径安全 | `src/coding_agent/workspace/security.py` |
| 语义索引 | `src/coding_agent/semantic/service.py` |
| 语义 Milvus | `src/coding_agent/semantic/milvus.py` |
| Session 工厂 | `src/coding_agent/sessions/factory.py` |
| JSONL Store | `src/coding_agent/sessions/store.py` |
| MySQL Store | `src/coding_agent/sessions/mysql.py` |
| 会话锁 | `src/coding_agent/sessions/lock.py` |
| DB Schema | `src/coding_agent/db/tables.py` |
| DB Engine | `src/coding_agent/db/engine.py` |
| DB 诊断 | `src/coding_agent/db/diagnostics.py` |
| Trace 存储 | `src/coding_agent/tracing/store.py` |
| Memory 契约 | `src/coding_agent/memory/contracts.py` |
| API 入口 | `src/coding_agent/api/app.py` |
| API 审批 | `src/coding_agent/api/approvals.py` |
| CLI 入口 | `src/coding_agent/cli/app.py` |
| 配置 | `src/coding_agent/config.py` |
| 评测 | `src/coding_agent/evals/scenarios.py` |
| 测试 | `tests/` |
| 迁移 | `migrations/versions/` |

## 核心安全链路

```text
用户任务
  → 模型读取受限工作区（read/search/git_diff/semantic_search）
  → 模型提交计划（Plan Mode 下 submit_plan）
  → 命令进入 Docker 沙箱（sandbox_shell / verify）
      → 过滤后的工作区快照（stdin 传入，不挂载宿主）
      → 无网络、只读、非 root、drop capabilities
      → 沙箱内生成 Git diff
  → 注册 pending patch（patch registry）
  → 审批（CLI / Web / MySQL 持久化）
  → 校验（结构、路径、文件 hash、git apply --check）
  → 写回宿主工作区（apply_patch）
```
