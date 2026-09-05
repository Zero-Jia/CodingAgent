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

### Session 2026-09-05（Milvus memory vector collection）

- **目标**：完成 B1-2，为 promoted 记忆建立独立 Milvus collection，提供语义召回能力。复用 `semantic/milvus.py` 模式，不实现 recall 注入 runtime（B1-5）、过期管理（B1-6）
- **完成任务**：
  - [B1-2] Milvus memory vector collection — 改动文件：
    - `src/coding_agent/memory/contracts.py`：新增 `MemoryVectorHit`（memory_id/content/score）+ `MemoryVectorIndex` Protocol（`ensure_collection`/`upsert`/`search`）；search 按 user_id + project_id 过滤，返回余弦相似度降序命中
    - `src/coding_agent/memory/vector.py`（新）：`MilvusMemoryVectorIndex`（schema: memory_id VARCHAR PK / user_id / project_id / scope / category / content VARCHAR(8192) / embedding FLOAT_VECTOR COSINE AUTOINDEX；filter 表达式转义双引号与反斜杠）+ `InMemoryMemoryVectorIndex`（纯 Python 余弦相似度，测试用）；复用 `semantic/milvus.py` 的 `_entity`/`_score`/`_embedding_dimension`/`_safe_error` 辅助函数模式
    - `src/coding_agent/memory/__init__.py`：导出 `MemoryVectorHit`/`MemoryVectorIndex`/`MilvusMemoryVectorIndex`/`InMemoryMemoryVectorIndex`
    - `src/coding_agent/config.py`：新增 `milvus_memory_collection` 字段（默认 `coding_agent_memories`）+ 环境变量 `CODING_AGENT_MILVUS_MEMORY_COLLECTION`
    - `tests/test_memory_vector.py`（新）：8 个测试（upsert+search 基本召回、按 user+project 过滤、余弦相似度降序、top_k 截断、同 memory_id 幂等覆盖、长度不匹配抛错、维度不匹配抛错、空库返回空）
- **关键决策**：
  - **独立 collection 而非复用 code_chunks**：记忆与代码语义空间不同（记忆是自然语言偏好/约定，代码是符号语义），分开 collection 避免向量空间混淆，且记忆需要 user/project 过滤维度
  - **schema 字段精简**：只存召回必需字段（memory_id/user_id/project_id/scope/category/content/embedding），不冗余存储 confidence/status 等（这些在 MySQL memories 表，通过 memory_id 关联）
  - **只索引 promoted 记忆由调用方保证**：index 层不校验状态，B1-5/CLI 在 upsert 前从 store 取 promoted 记录；保持 index 层无状态依赖
  - **filter 转义**：user_id/project_id 可能含特殊字符，`_escape_filter` 转义双引号与反斜杠，避免 Milvus filter 注入
- **遇到的问题**：
  - 并行 Edit 同一文件时偶发部分 edit 不生效（MemoryVectorIndex 未使用导入未被移除），改为串行 edit 后解决
- **验证结果**：
  - ruff check：All checks passed
  - mypy（全量 src）：Success, 62 source files
  - pytest：190 passed, 1 skipped（基线 182/1 → +8 新增 vector 测试）
- **未完成/遗留**：
  - 未实现"把 promoted 记忆批量写入向量索引"的 service/CLI（B1-5 或单独小任务可加：遍历 store.list_promoted → embed → index.upsert）
  - 未实现 recall 注入 runtime（B1-5）：向量索引能力已就绪，B1-5 可直接消费 `MemoryVectorIndex.search`
  - Milvus 真实集成 smoke test 未写（仅 InMemory 覆盖）；按语义索引惯例后续可加 `RUN_REAL_MEMORY_VECTOR_TESTS=1` 门控
- **下一步建议**：B1-5（Memory recall 注入 runtime context）—— 向量索引（B1-2）与审核（B1-4）均已就绪，可在任务开始时召回高置信 promoted 记忆（语义召回 + metadata 保底）注入系统上下文，闭合 Memory 端到端链路

---

### Session 2026-09-05（Memory 审核流程 + Milvus 向量索引）—— B1-4

- **目标**：完成 B1-4，为 B1-3 产出的候选记忆提供人工审核入口（promote 为 promoted / reject 为 rejected），闭合"提取→审核→promoted"链路。不实现 Milvus 向量召回（B1-2）、recall 注入（B1-5）、过期管理（B1-6）
- **完成任务**：
  - [B1-4] 人工审核 promotion 流程 — 改动文件：
    - `src/coding_agent/memory/review.py`（新）：`MemoryReviewService`（`list_candidates`/`promote`/`reject`/`review`）+ `ReviewError`。审核只允许 `candidate -> promoted/rejected`；`review` 校验：目标状态合法、reviewer 非空、记忆存在且当前状态为 candidate；调用 `store.update_status` 后 `get` 返回更新后记录
    - `src/coding_agent/memory/__init__.py`：导出 `MemoryReviewService`、`ReviewError`
    - `src/coding_agent/cli/app.py`：新增 `review-memories --reviewer [--user-id] [--project-id] [--storage/--database-url/--database-create-schema]` 命令；逐条展示 candidate（category/confidence/content/source_session），交互 `[p]romote/[r]eject/[s]kip/[q]uit` + 审核备注；jsonl 后端提示需 mysql 并退出；统计 promoted/rejected/skipped
    - `tests/test_memory_review.py`（新）：9 个测试（list_candidates 只返回 candidate；promote/reject 状态流转+reviewer/note/reviewed_at 写入；重复审核已 promoted 抛错；审核不存在记忆抛错；非法目标状态抛错；空 reviewer 抛错；promoted 后从 candidate 列表消失；NoopStore promote 抛 ReviewError）
- **关键决策**：
  - **抽离 `MemoryReviewService` 服务层**：审核状态流转与校验逻辑放在 `memory/review.py`，CLI 只负责交互展示与收集决定，符合决策 5（CLI 保持薄客户端）
  - **审核只允许 candidate 出发**：`candidate -> promoted` 或 `candidate -> rejected`，防止对已审核记忆重复操作或跳过审核直接改状态；已 promoted/rejected 的记忆再次审核抛 `ReviewError`
  - **reviewer 非空校验**：保证审计可追溯，空字符串或纯空格均拒绝
  - **复用现有 `MemoryStore.update_status`**：B1-1 已实现该方法（写 status/reviewer/review_note/reviewed_at/updated_at），B1-4 无需改 schema 或 store，无 Alembic migration
- **遇到的问题**：
  - mypy strict 要求测试文件中所有函数有类型注解，`_create_engine`/`_ensure_session` 需补 `-> Engine` 和 `engine: Engine`（与 `test_memory_store_contract.py` 保持一致）
- **验证结果**：
  - ruff check：All checks passed
  - mypy（全量 src）：Success, 61 source files
  - pytest：182 passed, 1 skipped（基线 173/1 → +9 新增 review 测试）
- **未完成/遗留**：
  - 未实现 Milvus memory collection（B1-2）：recall 仍只能基于 metadata search（SQL LIKE）
  - 未实现 recall 注入 runtime（B1-5）：promoted 记忆尚未被 runtime 消费
  - 未实现记忆过期与置信度管理（B1-6）
  - CLI 审核是逐条交互，无批量 promote-all 快捷操作（后续按需加）
- **下一步建议**：B1-2（Milvus memory vector collection，复用 `semantic/milvus.py` 模式）或 B1-5（Memory recall 注入 runtime context）。建议先做 B1-2，为 B1-5 recall 提供语义召回能力；若优先闭合端到端链路，也可先做 B1-5（基于现有 metadata search 保底）

---

### Session 2026-09-05（Memory extraction 混合式提取）

- **目标**：完成 B1-3，实现"规则优先、模型补位"的候选记忆提取，从 session checkpoint 对话提取候选记忆写入 `MemoryStore`，不实现 Milvus 向量召回（B1-2）、审核 CLI（B1-4）、recall 注入（B1-5）
- **完成任务**：
  - [B1-3] Memory extraction — 改动文件：
    - `src/coding_agent/memory/extraction.py`：`RuleExtractor`（user 消息线索词中英匹配，confidence=0.8，category=preference，返回候选+命中下标集合）；`ModelExtractor`（规则未命中部分=非线索 user + assistant 消息，跳过 system/tool，喂 `ModelAdapter` + 提取 prompt，解析 JSON，confidence 默认 0.5，容错 markdown fence/非法 JSON/ModelError 跳过不崩）；`MemoryExtractor` 编排（规则→模型→按 memory_id 去重合并，规则版高置信优先）；`persist_candidates`（`store.get()` 幂等去重，返回 new/skipped）；`MemoryCategory` 常量（preference/convention/decision/fix/fact）
    - `src/coding_agent/memory/contracts.py`：新增 `MemoryCategory` 受控词表
    - `src/coding_agent/memory/__init__.py`：导出 `MemoryExtractor`/`ModelExtractor`/`RuleExtractor`/`persist_candidates`/`MemoryCategory`
    - `src/coding_agent/sessions/factory.py`：`create_memory_store(config, data_root)`（jsonl→NoopMemoryStore，mysql→MySqlMemoryStore，镜像 `create_session_store` 范式）
    - `src/coding_agent/cli/app.py`：`extract-memories --session-id --workspace [--no-model] [--provider/--model/--storage/--database-url/--database-create-schema]`；mysql 持久化打印计数，jsonl 打印候选预览
    - `tests/test_memory_extraction.py`：22 个测试（规则命中/不命中/确定性/归一化/非 user 跳过；模型 JSON 解析/空数组/非法 JSON/markdown fence/ModelError 跳过/confidence 越界/未知类别/空 transcript；编排合并去重/--no-model/跨 run 确定性；persist 幂等/Noop 不崩/端到端；factory jsonl+mysql；CLI 冒烟 sqlite+checkpoint）
- **关键决策**：
  - **混合式而非纯规则**：规则只抓显式线索词（最强信号），模型在规则未命中部分自主判断（项目约定/历史修复/重要决策等隐含信号），契合 Agentic RAG 思路；两段都产 `status=candidate`，统一由 B1-4 人工审核把关，模型低置信候选不会直接污染 recall
  - **memory_id = sha256(content)[:32]**（content-only），使相同内容跨 session 自动合并；已知限制：`source_session_id` FK(ondelete CASCADE) 若源 session 行被显式删除会级联删记忆，留待 B1-6 收紧（可让 source_session_id 可空或解耦 FK）
  - **模型输入只含规则未命中部分**（非线索 user + assistant，跳过 system/tool），避免重复提取与 tool 大输出噪声，省 token
  - **置信度分层**：规则 0.8 / 模型默认 0.5，便于 B1-4 审核优先级与 B1-6 衰减
  - **`persist_candidates` 用 `store.get()` 去重**而非 try/except IntegrityError，可移植且不依赖 dialect 特定 upsert
  - **CLI 对 NoopMemoryStore 分支**：jsonl 后端打印候选预览而非调用 persist（Noop 的 get 返回 None 会误计 new）
- **遇到的问题**：
  - 初版 CLI 冒烟测试用 `JsonlSessionStore` 存 checkpoint，但 CLI 用 `--storage mysql` 建了另一个 store 读不到；改用 `MySqlSessionStore` 在 sqlite 上存 checkpoint（`save_checkpoint` 内部 `_ensure_session` 自动建 sessions 行，满足 FK）
  - `NoopMemoryStore` 在 `persist_candidates` 中 `get` 返回 None → 误计 new=1；这不是 bug（CLI 已对 Noop 分支），修测试断言为 new=1/skipped=0 并注释说明
  - ruff UP038 要求 `isinstance(x, (int,float))` → `isinstance(x, int | float)`；F402 警告 `event` 循环变量遮蔽 sqlalchemy `event` 导入，重命名为 `evt`
- **验证结果**：
  - ruff check：All checks passed
  - mypy（全量）：Success, 60 source files
  - pytest：173 passed, 1 skipped（基线 151/1 → +22 新增 extraction 测试；语义真实集成 smoke test 仍 skipped）
- **未完成/遗留**：
  - 未接入 runtime：提取是离线 CLI 命令，`MemoryExtractor` 未被 `runtime/` 引用；B1-5 recall 注入时再装配
  - 未实现审核 CLI（B1-4）：候选写入后无人工 promote/reject 入口
  - 未实现 Milvus memory collection（B1-2）：recall 仍只能基于 metadata search（SQL LIKE）
  - 模型提取的真实 DeepSeek smoke test 未写（仅 FakeModelAdapter 覆盖）；按语义索引惯例后续可加 `RUN_REAL_EXTRACTION_TESTS=1` 门控测试
- **下一步建议**：B1-4（人工审核 promotion 流程，CLI 审核命令）——本轮已产出大量 candidate，需要审核入口才能 promote 供 recall 使用；或 B1-2（Milvus memory collection）让 recall 支持语义召回。建议先做 B1-4（无外部依赖，闭合"提取→审核→promoted"链路）

---

### Session 2026-09-05（Memory metadata schema + Alembic migration）

- **目标**：完成 B1-1，为 Memory 系统打地基。只实现 MySQL/SQLite metadata 层，不实现 Milvus 向量召回、自动 extraction 与 recall 注入
- **完成任务**：
  - [B1-1] MySQL memory metadata schema + Alembic migration — 改动文件：
    - `src/coding_agent/db/tables.py`：新增 `memories` 表（memory_id PK、schema_version、user_id、project_id、scope、category、content LONGTEXT、source_session_id FK→sessions、source_run_id、confidence、status、reviewer、review_note、created_at、updated_at、reviewed_at、expires_at；4 个索引：source_session_id、status、scope、user+project+status 联合）；加入 `schema_tables` 与 `__all__`
    - `migrations/versions/0004_add_memory_metadata.py`：Alembic upgrade/downgrade，`down_revision=0003_add_persistent_patch_packages`
    - `src/coding_agent/memory/contracts.py`：扩充 `MemoryStore` Protocol（store/get/list_by_status/update_status/list_promoted/search，关键字参数）；新增 `MemoryStatus`（candidate/promoted/rejected/expired）与 `MemoryScope`（session/project/user）常量；`MemoryRecord` 补 source_run_id/reviewer/review_note/reviewed_at；`NoopMemoryStore` 同步补全为降级实现
    - `src/coding_agent/memory/mysql.py`：`MySqlMemoryStore` 实现，SQLAlchemy Core + `asyncio.to_thread`，遵循 `MySqlSessionStore` 范式（dialect 无关，SQLite 可跑契约测试）
    - `src/coding_agent/memory/__init__.py`：导出新类型
    - `tests/test_memory_store_contract.py`：15 个契约测试（store/get 往返、list_by_status 排序与隔离、update_status promote+stamp、list_promoted scope 过滤、search 大小写不敏感+status 过滤、Noop 降级、schema 含 memories、alembic upgrade/downgrade、MySQL DDL 编译校验）
- **关键决策**：
  - `review_note` 为 Text 列，遵循现有约定「Text 列只给 `default=`（Python 端），不给 `server_default`」——MySQL 不允许 TEXT 带 DEFAULT，`test_schema_compiles_for_mysql_without_text_defaults` 强制此约束
  - `source_session_id` FK→sessions(ondelete CASCADE)；store 不主动 `_ensure_session`，由 extraction（B1-3）保证 session 已存在，FK 违背直接抛 IntegrityError，避免 memory 模块反向耦合 sessions 模块
  - `MemoryStore.search` 签名由位置参数改为关键字参数并新增 `status` 过滤（默认只检索 promoted）；现有代码无任何调用方，签名变更无破坏性
  - `update_status` 对不存在的 memory_id 静默无操作（UPDATE 影响 0 行不抛错），便于幂等重试
- **遇到的问题**：
  - 初版给 `review_note` 误加 `server_default=""`，触发 `test_schema_compiles_for_mysql_without_text_defaults` 失败；按现有 Text 列约定移除后通过
  - 测试断言 `after.updated_at >= before.updated_at` 用了固定假时间戳 12:00，而 `update_status` 用真实 now()（06:05），导致失败；改为校验 `updated_at` 已变化且等于 `reviewed_at`
  - ruff 全量扫描误报 `.codex-test-tmp-*` 临时目录里的测试夹具 `.py` 文件（非规范 basetemp 名未进 .gitignore）；清理临时目录后通过。后续必须严格用 `--basetemp .codex-test-tmp`（gitignored）
- **验证结果**：
  - ruff check：All checks passed
  - mypy（全量）：Success, 59 source files
  - mypy src：Success, 59 source files
  - pytest：151 passed, 1 skipped（基线 136/2 → +15 新增 memory 测试；语义真实集成 smoke test 仍 skipped）
- **未完成/遗留**：
  - 未接入 runtime：`MemoryStore` 尚未被 `agent/coding_agent.py` 或 `runtime/` 引用，B1-5 recall 注入时再装配
  - 未实现 CLI 审核命令（B1-4）
  - 未实现 Milvus memory collection（B1-2）
- **下一步建议**：B1-2（Milvus memory vector collection）或 B1-3（extraction）。B1-3 不依赖 Milvus，可直接基于本 session 的 `MySqlMemoryStore` 从 session events 提取候选记忆写入；B1-2 复用 `semantic/milvus.py` 模式新增 memory collection。建议先做 B1-3（无外部依赖、与用户 RAG 背景契合、能跑通端到端候选写入），B1-2 之后做

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
