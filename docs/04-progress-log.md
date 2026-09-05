# 开发进度日志

> 每个 session 开发结束追加一条记录，**最新在最上面**。

## 记录模板（复制使用）

```
### Session YYYY-MM-DD（任务名称）
- **目标**：本 session 要做什么
- **完成任务**：
  - [编号] 任务名 — 改动文件
- **未完成/遗留**：
  - 原因 + 下次继续点
- **关键决策**：如果有架构/技术选型决策记录在此
- **遇到的问题**：踩坑记录，避免下次重复
- **验证结果**：
  - ruff / mypy / pytest 结果
- **下一步建议**：下个 session 优先做什么
```

---

### Session 2026-09-02（真实 DashScope/Milvus 语义索引第一版）

- **目标**：完成代码语义索引第一版，不实现 Redis、memory recall、多 agent、Tree-sitter 或生产级 Web UI
- **完成任务**：
  - [P3-1] Milvus 语义索引 — 改动文件：
    - `.env.example`、`.env`、`README.md`、`pyproject.toml`、`uv.lock`
    - `src/coding_agent/config.py`：新增 `.env` 轻量加载、DashScope embedding 配置
    - `src/coding_agent/semantic/`：contracts、chunking、embeddings、milvus、service、store
    - `src/coding_agent/tools/semantic.py`：`semantic_search` 只读工具
    - `src/coding_agent/agent/coding_agent.py`、`src/coding_agent/cli/app.py`：装配 + CLI 命令
    - `tests/test_semantic_index.py`、`tests/test_semantic_tool.py`
- **关键决策**：
  - 生产路径使用真实 DashScope `qwen3.7-text-embedding` + PyMilvus
  - 自动化测试使用 fake embedding + in-memory vector index 隔离外部服务
  - 真实 smoke test 通过 `RUN_REAL_SEMANTIC_TESTS=1` 显式启用
- **验证结果**：
  - ruff check：通过
  - mypy：通过，58 source files
  - mypy src：通过，58 source files
  - pytest：136 passed，2 skipped
- **未完成/遗留**：
  - 尚未运行真实 DashScope + 本机 Milvus smoke test
  - 语义索引目前是全量重建/upsert，未实现增量 reindex、过期 chunk 清理、Tree-sitter 结构化 chunk
  - `semantic_search` 不会自动重建索引，代码变化后需手动重新运行 `agent index-workspace`
- **下一步建议**：进入 Phase B，优先做 Memory 系统（B1），复用现有 Milvus 和 MySQL 基础设施

---

### Session 2026-09-01（Pending Patch 持久化审批包）

- **目标**：让配置 MySQL 后的沙箱待回写 patch 作为持久化 package 保存，应用前重新校验
- **完成任务**：
  - [P2-6] Pending Patch 持久化 — 改动文件：
    - `README.md`、`CodingAgent.md`
    - `migrations/versions/0003_add_persistent_patch_packages.py`
    - `src/coding_agent/sandbox/patches.py`：`PatchStore` 抽象、`InMemoryPatchStore`、`MySqlPatchStore`
    - `src/coding_agent/db/tables.py`：`patches` 表
    - `src/coding_agent/runtime/loop.py`、`src/coding_agent/tools/sandbox.py`、`src/coding_agent/tools/contracts.py`
    - `src/coding_agent/agent/coding_agent.py`
    - `tests/test_sandbox.py`、`tests/test_api.py`、`tests/test_session_store_contract.py`
- **关键决策**：
  - `apply_patch` 应用前将 patch 从 `pending` claim 为 `applying`，再重新执行结构、路径、hash 和 `git apply --check` 校验
  - 成功标记 `applied`，失败标记 `invalidated`，审批拒绝标记 `rejected`
- **验证结果**：
  - ruff check：通过
  - mypy：通过，50 source files
  - pytest：129 passed，1 skipped

---

### Session 2026-08-31（持久化 Approval Queue）

- **目标**：让配置 MySQL 后的 API approval queue 持久化审批请求和决议
- **完成任务**：
  - [P2-5] 持久化 Approval Queue — 改动文件：
    - `migrations/versions/0002_add_persistent_approval_queue_fields.py`
    - `src/coding_agent/api/approvals.py`：`ApprovalStore` 协议、`InMemoryApprovalStore`、`MySqlApprovalStore`
    - `src/coding_agent/api/app.py`、`src/coding_agent/db/tables.py`
    - `tests/test_api.py`、`tests/test_session_store_contract.py`
- **关键决策**：
  - `ApprovalRegistry` 保留本地 future map，以 store 作为审批状态源
  - MySQL 模式下等待中的 request 轮询数据库决议，支持其他 API registry 完成 approve/reject
- **验证结果**：
  - ruff check：通过
  - mypy：通过，50 source files
  - pytest：124 passed，1 skipped

---

### Session 2026-08-30（MySQL 数据库替换 + 运行时配置切换）

- **目标**：将数据库目标替换为 MySQL，保留 JSONL 默认本地模式和 SQLAlchemy 抽象
- **完成任务**：
  - [P2-2] MySQL 存储基础 schema — `src/coding_agent/db/`、`migrations/versions/0001_*.py`
  - [P2-3] MySQL 运行时配置切换 — `src/coding_agent/sessions/factory.py`、`config.py`
  - 新增 `agent db-check` CLI 诊断命令
- **关键决策**：
  - 使用 `pymysql[rsa]`，支持 MySQL 8 默认 `caching_sha2_password` 认证
  - 保留 JSONL 作为默认本地模式，显式 `database_url` 时切换 MySQL
- **验证结果**：
  - ruff check：通过
  - mypy src：通过，50 source files
  - pytest：123 passed，1 skipped
  - 本机 MySQL 8.0.41 真实迁移验证通过

---

### Session 2026-08-29（审批 UI + FastAPI 服务）

- **目标**：创建最小 Web patch 审批流程 + FastAPI/SSE 服务入口
- **完成任务**：
  - [P2-1] FastAPI 服务 — `src/coding_agent/api/app.py`
  - [P2-4] 审批 UI — `src/coding_agent/api/approvals.py`、`GET /approvals/ui`
- **关键决策**：
  - API 复用 `CodingAgent` 与 `ChatSession`，不复制 runtime
  - 审批页面使用 DOM `textContent` 渲染动态内容，避免 diff preview 作为 HTML 执行
- **验证结果**：
  - ruff check：通过
  - mypy：通过，48 source files
  - pytest：108 passed，1 skipped

---

### Session 2026-08-28（Command Risk Detector + Plan Mode 状态机）

- **目标**：沙箱执行前增加高置信命令风险检测；Plan Mode 升级为状态机
- **完成任务**：
  - [P1-6] Command Risk Detector — `src/coding_agent/policy/command_risk.py`
  - [P1-5] Plan Mode 状态机升级 — `src/coding_agent/runtime/plan.py`
- **关键决策**：
  - 高置信危险命令直接拒绝，即使 `--allow-shell` 或计划已获批也不能绕过
  - 计划失败后需提交包含 `revision_of`、`failure_summary`、`changed_approach` 的修订计划
- **验证结果**：
  - ruff check：通过
  - mypy：通过，41 source files
  - pytest：88 passed，1 skipped

---

### Session 2026-08-27（Plan Mode + Context Manager + Token Usage）

- **目标**：实现 Plan Mode、context manager、token usage 账本
- **完成任务**：
  - [P1-3] Context Manager — `src/coding_agent/runtime/context.py`
  - [P1-4] Token Usage Accounting — `src/coding_agent/runtime/token_usage.py`
  - [P1-5] Plan Mode 第一版 — `src/coding_agent/tools/plan.py`
- **验证结果**：
  - ruff / mypy / pytest 全通过

---

### Session 2026-08-26（Model Gateway + Streaming Delta）

- **目标**：引入 provider registry，实现真正的增量 streaming 输出
- **完成任务**：
  - [P1-1] Model Gateway — `src/coding_agent/ai/gateway.py`
  - [P1-2] Streaming Delta — `src/coding_agent/runtime/loop.py`
- **验证结果**：
  - ruff / mypy / pytest 全通过

---

### Session 2026-08-25（工程化基础 + 威胁模型）

- **目标**：补齐 CI、类型标记、mock LLM 测试、威胁模型文档
- **完成任务**：
  - [A1] py.typed — `src/coding_agent/py.typed`
  - [A2] GitHub Actions CI — `.github/workflows/ci.yml`
  - [A3] Mock LLM Runtime 测试
  - [A4] 威胁模型文档 — `docs/07-threat-model.md`
- **验证结果**：
  - ruff / mypy / pytest 全通过
