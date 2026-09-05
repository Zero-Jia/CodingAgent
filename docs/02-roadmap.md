# 开发路线图

按秋招面试价值排序，**优先做高辨识度能力**（Memory / MCP / 多 Agent），工程化和生产化后续补齐。

## Phase B：高辨识度能力（秋招核心，当前阶段）

### B1：Memory 系统（推荐先做）

| 编号 | 任务 | 目标 |
|---|---|---|
| B1-1 | MySQL memory metadata schema + Alembic migration | 记忆结构化存储 |
| B1-2 | Milvus memory vector collection | 记忆向量召回 |
| B1-3 | Memory extraction（从 session 提取候选记忆） | 记忆生成 |
| B1-4 | 人工审核 promotion 流程 | 记忆质量控制 |
| B1-5 | Memory recall 注入 runtime context | 记忆使用 |
| B1-6 | 记忆过期与置信度管理 | 记忆治理 |

### B2：MCP 集成

| 编号 | 任务 | 目标 |
|---|---|---|
| B2-1 | MCP server 配置与连接管理器 | MCP 基础接入 |
| B2-2 | MCP 工具动态发现与 schema 注册 | 工具生态 |
| B2-3 | MCP 工具包装（policy + trace + 输出预算） | 安全集成 |
| B2-4 | MCP 调用审计与脱敏 | 可观测 |

### B3：多 Agent + Worktree 隔离

| 编号 | 任务 | 目标 |
|---|---|---|
| B3-1 | Agent 角色定义（Planner / Coder / Reviewer / Verifier） | 角色分工 |
| B3-2 | Git worktree manager | 隔离并行 |
| B3-3 | Child agent 编排与 trace tree | 协作调度 |
| B3-4 | Worktree patch proposal 汇总 | 安全回写 |
| B3-5 | 失败恢复与冲突处理 | 鲁棒性 |

---

## Phase C：Runtime 成熟化（高辨识度完成后）

| 编号 | 任务 | 目标 |
|---|---|---|
| C1 | OpenAI / Anthropic / OpenAI-compatible provider | 多模型接入 |
| C2 | 模型辅助 compact summary | 上下文质量 |
| C3 | 工具输出分级摘要与大输出落盘 | 上下文治理 |
| C4 | CLI slash command registry | 交互体验 |
| C5 | Redis 分布式 active run / session lock | 多 worker 协调 |

---

## Phase A：工程化基础（可选，面试前补齐）

| 编号 | 任务 | 目标 |
|---|---|---|
| A1 | coverage 报告（pytest-cov） | 可量化质量 |
| A2 | pre-commit 配置 | 提交规范 |
| A3 | demo 脚本 + 示例 transcript | 面试展示 |
| A4 | EVALS.md 文档 | 评测说明 |
| A5 | mock LLM 全场景测试覆盖 | 测试完备性 |

---

## Phase D：生产化加固（长期）

| 编号 | 任务 | 目标 |
|---|---|---|
| D1 | 认证授权 + RBAC | 权限控制 |
| D2 | 组织级 policy + 多租户隔离 | 企业治理 |
| D3 | 完整 audit log schema | 合规审计 |
| D4 | OpenTelemetry + Prometheus + Grafana | 可观测 |
| D5 | 镜像 digest pinning + SBOM + 漏洞扫描 | 供应链安全 |
| D6 | 生产级 Web UI + 审批控制台 | 产品化 |

---

## 推进原则

- **Phase B 优先**：Memory → MCP → 多 Agent，按顺序推进（Memory 复用 Milvus/MySQL 基础设施，边际成本最低）
- 每个任务必须包含测试、文档更新和验证记录
- 任务粒度控制：单任务应在 1-2 个 session 内可完成
- 不破坏现有安全内核（Docker sandbox + patch-only 写回）
- 涉及破坏性改动（DB schema 变更、API 契约变更）需在 `04-progress-log.md` 写明 migration 路径
- 每完成一个任务必须更新 `03-task-backlog.md` 与 `04-progress-log.md`
