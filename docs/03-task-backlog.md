# 任务清单

> **状态约定**：`todo` / `doing` / `done` / `blocked` / `skip`
> `skip` = 经评估后决定不做（保留记录，备后续按需重启）。
> 完成任务请填"实际改动文件"列，便于回溯。

---

## Phase B：高辨识度能力（当前阶段）

### B1：Memory 系统

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| B1-1 | MySQL memory metadata schema + Alembic migration | done | `src/coding_agent/db/tables.py`、`migrations/versions/0004_add_memory_metadata.py`、`src/coding_agent/memory/contracts.py`、`src/coding_agent/memory/mysql.py`、`src/coding_agent/memory/__init__.py`、`tests/test_memory_store_contract.py` | 新增 `memories` 表（memory_id、user_id、project_id、scope、category、content、source_session_id FK、source_run_id、confidence、status、reviewer、review_note、created_at、updated_at、reviewed_at、expires_at）；扩展 `MemoryStore` Protocol（store/get/list_by_status/update_status/list_promoted/search）+ `MySqlMemoryStore` 实现 + `NoopMemoryStore`；15 个契约测试（含 schema、migration upgrade/downgrade、MySQL DDL 编译）。未实现 Milvus、extraction、recall |
| B1-2 | Milvus memory vector collection | todo | — | 复用现有 `semantic/milvus.py` 模式，新增 memory collection（embedding + memory_id metadata） |
| B1-3 | Memory extraction（从 session 提取候选记忆） | todo | — | 从 session events / trace 中提取项目约定、用户偏好、历史修复；候选状态待审核 |
| B1-4 | 人工审核 promotion 流程 | todo | — | 候选记忆 → 人工确认 → promoted；提供 CLI 审核命令 |
| B1-5 | Memory recall 注入 runtime context | todo | — | 任务开始时根据 query/workspace 召回高置信记忆，注入系统上下文 |
| B1-6 | 记忆过期与置信度管理 | todo | — | TTL、置信度衰减、去重合并 |

### B2：MCP 集成

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| B2-1 | MCP server 配置与连接管理器 | todo | — | 配置 MCP server 列表，管理 stdio / HTTP 连接生命周期 |
| B2-2 | MCP 工具动态发现与 schema 注册 | todo | — | 启动时拉取 MCP tools，注册到 tool registry |
| B2-3 | MCP 工具包装（policy + trace + 输出预算） | todo | — | MCP 调用经过 policy 决策、trace 记录、输出脱敏和预算限制 |
| B2-4 | MCP 调用审计与脱敏 | todo | — | MCP 工具结果写入 trace，敏感信息脱敏 |

### B3：多 Agent + Worktree 隔离

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| B3-1 | Agent 角色定义（Planner / Coder / Reviewer / Verifier） | todo | — | 每个角色有独立 system prompt 和工具子集 |
| B3-2 | Git worktree manager | todo | — | 创建/删除 worktree，隔离并行修改 |
| B3-3 | Child agent 编排与 trace tree | todo | — | Parent agent 调度 child，trace 记录父子关系 |
| B3-4 | Worktree patch proposal 汇总 | todo | — | Child worktree 变更汇总为 patch proposal，走现有 patch approval 链路 |
| B3-5 | 失败恢复与冲突处理 | todo | — | Child task 失败不破坏 parent state；冲突由 reviewer 处理 |

---

## Phase A：工程化基础（已完成部分 + 待补齐）

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| A1 | Package Typing 标记（py.typed） | done | `src/coding_agent/py.typed` | 已完成 |
| A2 | CI 工作流（ruff + mypy + pytest） | done | `.github/workflows/ci.yml` | 已完成 |
| A3 | Mock LLM Runtime 测试 | done | `tests/test_runtime.py` 等 | 已完成，覆盖纯文本、工具调用、拒绝、错误、取消 |
| A4 | 威胁模型文档 | done | `docs/07-threat-model.md` | 已完成 |
| A5 | coverage 报告 | todo | — | 增加 `pytest-cov`，CI 中生成 coverage 报告 |
| A6 | pre-commit 配置 | todo | — | 集成 ruff、mypy、end-of-file-fixer、trailing-whitespace |
| A7 | demo 脚本 + 示例 transcript | todo | — | 准备 2-3 个可复现 demo 场景 |
| A8 | EVALS.md 文档 | todo | — | 说明评测方法和已有结果 |

---

## Phase P1：Runtime 成熟化（已完成）

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| P1-1 | Model Gateway provider registry | done | `src/coding_agent/ai/gateway.py` | 已完成 |
| P1-2 | 真正的 Streaming Delta | done | `src/coding_agent/runtime/loop.py` | 已完成 |
| P1-3 | Context Manager（token budget + compact） | done | `src/coding_agent/runtime/context.py` | 已完成 |
| P1-4 | Token Usage Accounting | done | `src/coding_agent/runtime/token_usage.py` | 已完成 |
| P1-5 | Plan Mode | done | `src/coding_agent/runtime/plan.py`、`src/coding_agent/tools/plan.py` | 已完成，含失败后修订计划状态机 |
| P1-6 | Sandbox Command Risk Detector | done | `src/coding_agent/policy/command_risk.py` | 已完成 |

---

## Phase P2：企业平台能力（已完成）

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| P2-1 | FastAPI 服务 + SSE | done | `src/coding_agent/api/app.py` | 已完成 |
| P2-2 | MySQL 存储基础 schema | done | `src/coding_agent/db/tables.py`、`migrations/` | 已完成 |
| P2-3 | MySQL 运行时配置切换 | done | `src/coding_agent/sessions/factory.py` | 已完成 |
| P2-4 | 审批 UI（最小 Web 审批页） | done | `src/coding_agent/api/app.py`、`src/coding_agent/api/approvals.py` | 已完成 |
| P2-5 | 持久化 Approval Queue | done | `src/coding_agent/api/approvals.py`、`migrations/versions/0002_*.py` | 已完成 |
| P2-6 | Pending Patch 持久化审批包 | done | `src/coding_agent/sandbox/patches.py`、`migrations/versions/0003_*.py` | 已完成 |

---

## Phase P3：知识检索（已完成第一版）

| 编号 | 任务 | 状态 | 实际改动文件 | 备注 |
|---|---|---|---|---|
| P3-1 | Milvus 语义索引（第一版） | done | `src/coding_agent/semantic/` | 已完成，DashScope embedding + Milvus + workspace chunker |
| P3-2 | 增量 reindex + 过期 chunk 清理 | todo | — | 后续优化 |
| P3-3 | Tree-sitter 结构化 chunk | todo | — | 后续优化 |

---

## 阻塞项

| 编号 | 任务 | 阻塞原因 | 待解决 |
|---|---|---|---|
| — | — | — | — |
