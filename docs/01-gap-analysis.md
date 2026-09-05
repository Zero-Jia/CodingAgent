# 与企业级 Coding Agent 的差距分析

## 1. 记忆系统缺失（当前最高优先级）

- **现状**：`src/coding_agent/memory/contracts.py` 只有协议，实现是 `NoopMemoryStore`
- **差距**：
  - 无项目记忆（架构、命令、约定、坑点）
  - 无用户记忆（偏好、常用流程）
  - 无历史修复记忆
  - 无 memory extraction 机制
  - 无人工审核 promotion 流程
  - 无 recall 注入 runtime context
- **影响**：长任务跨 session 无法复用知识，每次都要重新理解仓库；用户偏好无法沉淀

## 2. 工具体系不可扩展

- **现状**：工具固定在 runtime 中，无插件化发现
- **差距**：
  - 无 MCP 工具接入
  - 无 tool registry
  - 无工具权限分级（当前只有 allow/deny 二元）
  - 无 deferred tool loading
  - 无企业内部工具（Jira、GitHub/GitLab、CI、知识库）
- **影响**：无法接入企业工具链，生态扩展性弱

## 3. 单 Agent 单任务，无协作能力

- **现状**：一个 agent 串行完成所有任务
- **差距**：
  - 无 Planner / Coder / Reviewer / Verifier 角色分工
  - 无 background task
  - 无 git worktree 隔离并行修改
  - 无 child agent trace tree
  - 无任务取消、恢复、重试、汇总
- **影响**：复杂任务无法拆解并行，工程协作能力弱，面试辨识度不足

## 4. 模型层单一

- **现状**：只有 DeepSeek 适配器
- **差距**：
  - 无 OpenAI / Anthropic / OpenAI-compatible 支持
  - 无 fallback / retry / rate limit
  - 无成本统计
  - 无按任务选择模型
- **影响**：供应商绑定，无法体现可插拔架构设计

## 5. 上下文管理不足

- **现状**：确定性 compact（抽取摘要，不调模型）
- **差距**：
  - 无模型辅助高质量 compact summary
  - 无工具输出分级摘要
  - 无大输出落盘策略
  - 无 compact 后恢复关键文件
  - 无上下文窗口自动探测
- **影响**：长任务上下文质量有上限

## 6. 协作与分布式能力不足

- **现状**：单进程 active run registry + 本地文件 session lock
- **差距**：
  - 无 Redis 分布式锁
  - 无多 worker 协调
  - 无 WebSocket 实时事件流（当前只有 SSE）
  - 无后台任务 worker
- **影响**：无法支撑多 worker API 部署

## 7. 生产化加固不足

- **现状**：无认证授权、无 RBAC、无多租户
- **差距**：
  - 无认证（API 默认无鉴权）
  - 无 RBAC
  - 无组织级 policy
  - 无完整 audit log schema（当前是 JSONL）
  - 无 OpenTelemetry / Prometheus
  - 无镜像 digest pinning / SBOM / 漏洞扫描
- **影响**：无法直接生产部署

## 8. 工程化短板

- **现状**：有 ruff / mypy / pytest / GitHub Actions
- **差距**：
  - 无 coverage 报告
  - 无 pre-commit
  - 无安全扫描
  - 无 E2E 测试
  - 无 mock LLM 测试覆盖（当前有部分 fake model 测试）
  - 无 demo 脚本 / 示例 transcript
- **影响**：面试时缺乏可量化的工程质量指标

## 差距优先级矩阵

| 维度 | 面试辨识度 | 工程量 | 优先级 |
|---|---|---|---|
| Memory 系统 | 高 | 中 | **P0（秋招优先）** |
| MCP 集成 | 高 | 中 | **P0（秋招优先）** |
| 多 Agent + Worktree | 高 | 高 | **P0（秋招优先）** |
| 多 Provider | 中 | 中 | P1 |
| 模型辅助 compact | 中 | 低 | P1 |
| Redis 分布式锁 | 中 | 中 | P1 |
| 工程化（coverage/pre-commit） | 中 | 低 | P2 |
| 生产化加固（RBAC/OTel） | 低 | 高 | P3 |

## 推进策略

秋招时间窗口有限，**优先做高辨识度能力**（Memory / MCP / 多 Agent），工程化和生产化后续补齐。理由：

1. 安全内核已经足够扎实，是项目的"基本盘"
2. Memory / MCP / 多 Agent 是面试中最能讲故事的高级能力
3. 这三块能复用现有基础设施（Milvus、MySQL、ModelGateway、Runtime），边际成本低
4. 工程化（coverage/pre-commit）是"锦上添花"，不影响核心叙事
