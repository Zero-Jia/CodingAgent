# Session 交接说明

本文档是每次 vibe coding session 结束前必须更新的交接文件。它的目的，是让新的 Codex session 不依赖上一轮聊天记录，也能快速恢复项目上下文。

## 新 Session 开场 Prompt

新开 Codex session 时，直接使用这段 prompt：

```text
这是我的 CodingAgent 项目。请先完整阅读 README.md、CodingAgent.md、docs/SESSION_HANDOFF.md、docs/ROADMAP.md、pyproject.toml 和 src/coding_agent 下的核心模块。

先不要修改代码。请先总结：

1. 当前项目已经实现了什么；
2. 上一个 session 的交接状态是什么；
3. 本轮最合理的下一步是什么；
4. 你准备修改哪些文件，为什么。
```

## Session 结束 Prompt

每次结束 Codex session 前，直接使用这段 prompt：

```text
请更新 docs/SESSION_HANDOFF.md，记录本轮完成的内容、修改过的文件、验证命令和结果、未完成事项、下一轮建议。不要夸大未实现能力。
```

## 当前项目状态

当前项目是一个安全优先的本地 coding agent MVP。

已经实现：

- `src/coding_agent` 下的 Python 包结构。
- Typer CLI，支持一次性 `run` 和连续 `chat` 模式。
- 最小 FastAPI/SSE 服务入口，复用 `CodingAgent` 和 `ChatSession`。
- Model Gateway provider registry，当前已实现 `deepseek` provider。
- DeepSeek 模型适配器。
- 供应商无关的模型、消息、工具调用、usage 和事件契约。
- 事件驱动 runtime，支持工具执行、审批流、取消、trace 写入和 artifact 写入。
- 显式 Plan Mode，支持 `--plan`，要求模型在使用沙箱或 patch 工具前先提交计划并获批。
- API 本地最小 Web 审批入口，支持查看 pending approval、approve/reject、MySQL-backed approval queue、MySQL-backed pending patch package 和脱敏 JSONL 审计。
- 确定性 context manager，支持 token 预算触发、自动 compact、近期尾部保留和 tool call/result 边界保护。
- Session 级 token usage 账本，支持 provider usage 累计统计、当前上下文 token、窗口占比和 compact 节省量展示。
- 只读仓库工具：`read`、`search`、`git_diff`。
- Docker 沙箱工具：`sandbox_shell` 和 `verify`。
- 沙箱执行前 command risk detector：高置信危险命令直接拒绝，可疑命令强制交互式复核。
- 通过 `apply_patch` 实现 patch-only 宿主机写回。
- 工作区快照过滤，排除敏感文件、内部文件、符号链接、大文件、虚拟环境、缓存和 `.git`。
- Patch 校验，覆盖敏感路径、二进制 patch、子模块、文件模式变化、符号链接、可执行权限变化、重命名、复制、changed-file 不一致和宿主并发修改。
- JSONL session、checkpoint、transcript、summary、trace、artifact 和 application log。
- SQLAlchemy/MySQL 会话存储底座和 Alembic 初始迁移。
- CLI/API MySQL session store 运行时配置切换，默认仍使用 JSONL，显式 `database_url` 时使用 `MySqlSessionStore`。
- 配置 MySQL 后，API approval queue 会持久化审批请求、状态查询和 approve/reject 决议；等待中的 runtime 可以观察数据库决议并继续执行。
- 配置 MySQL 后，pending patch package 会持久化 patch text、changed files、snapshot hashes、diff preview 和状态；新 runtime/registry 可以读取旧 pending patch，但应用前仍会重新校验。
- 连续 chat session 的独占锁。
- `src/coding_agent/py.typed` 包类型标记。
- GitHub Actions 最小 CI workflow，运行 ruff、mypy 和 pytest。
- 最小 memory 协议，目前是 Noop 实现。
- 最小 eval report 契约。
- 覆盖 runtime、policy、tools、sandbox、patch validation 和 sessions 的测试。
- `docs/THREAT_MODEL.md` 威胁模型文档。

尚未实现：

- 认证授权。
- 生产级 Web UI。
- Milvus。
- Redis。
- MCP。
- Skills。
- Hooks。
- 真实 memory 检索。
- 模型辅助上下文摘要。
- 多 agent 编排。
- Worktree 隔离。
- coverage、pre-commit、release workflow、安全扫描和完整 CI/CD 发布流水线。

## 最近一次 Session

本轮完成：

- 完成 pending patch 持久化审批包的小边界任务，不实现 Redis、完整 Web UI、RBAC 或完整 audit log schema。
- 参考 `D:\Software\MewCode` 的 `PermissionRequest`/`PermissionResponse`、remote pending permission future、diff 截断和 session 恢复容错思路；没有引入其宿主机直写、本地 shell 权限模型或 `ALLOW_ALWAYS` 规则写入。
- 新增 `PatchStore` 抽象、`InMemoryPatchStore` 和 `MySqlPatchStore`。
- `PatchRegistry` 默认仍使用进程内 store；配置 MySQL session store 后，runtime 使用 `MySqlPatchStore` 将 patch package 写入 `patches` 表。
- `PendingPatch` 记录 patch text、patch sha256、changed files、snapshot file hashes、diff preview、状态、session/run 关联、创建/更新时间和应用元数据。
- `sandbox_shell` 创建 patch 时写入 session/run 上下文；`apply_patch` 的审批详情支持异步读取持久化 patch package。
- `apply_patch` 应用前将 patch 从 `pending` claim 为 `applying`，再重新执行结构校验、敏感路径校验、changed files 一致性校验、文件 hash 校验和 `git apply --check`；成功后标记 `applied`，失败后标记 `invalidated`。
- 审批拒绝 `apply_patch` 时，关联 pending patch 会标记为 `rejected`，避免后续误用。
- 新增 Alembic migration `0003_add_persistent_patch_packages`，创建 `patches` 表。
- 新增 sandbox 和 API 回归测试，覆盖跨 registry 恢复应用、工作区漂移失效、重复应用保护、schema、审批详情读取和批准后写回。

本轮修改文件：

- `README.md`
- `CodingAgent.md`
- `migrations/versions/0003_add_persistent_patch_packages.py`
- `src/coding_agent/agent/coding_agent.py`
- `src/coding_agent/db/tables.py`
- `src/coding_agent/runtime/loop.py`
- `src/coding_agent/sandbox/patches.py`
- `src/coding_agent/tools/contracts.py`
- `src/coding_agent/tools/sandbox.py`
- `tests/test_api.py`
- `tests/test_sandbox.py`
- `tests/test_session_store_contract.py`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache run ruff check src\coding_agent\sandbox\patches.py src\coding_agent\tools\sandbox.py src\coding_agent\runtime\loop.py src\coding_agent\agent\coding_agent.py src\coding_agent\db\tables.py tests\test_sandbox.py tests\test_api.py tests\test_session_store_contract.py migrations\versions\0003_add_persistent_patch_packages.py`：通过。
- `uv --cache-dir .uv-cache run mypy src\coding_agent\sandbox\patches.py src\coding_agent\tools\sandbox.py src\coding_agent\runtime\loop.py src\coding_agent\agent\coding_agent.py tests\test_sandbox.py tests\test_api.py`：通过。
- `uv --cache-dir .uv-cache run pytest tests\test_sandbox.py --basetemp .codex-test-tmp-patch-sandbox -p no:cacheprovider`：18 passed，1 skipped。
- `uv --cache-dir .uv-cache run pytest tests\test_session_store_contract.py --basetemp .codex-test-tmp-patch-schema -p no:cacheprovider`：8 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_api.py --basetemp .codex-test-tmp-patch-api -p no:cacheprovider`：16 passed。
- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过，50 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，50 source files。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-patch-final -p no:cacheprovider`：129 passed，1 skipped。

本轮未完成事项：

- 未用真实 DeepSeek API 启动完整聊天回合。
- 未对本机真实 MySQL 执行 `0003` migration；自动化通过 SQLite Alembic migration 和 SQLAlchemy MySQL dialect 编译测试覆盖结构兼容性。
- `patches` 表已经保存 patch package，但完整审计查询、操作者身份、RBAC 和多用户归属仍未实现。
- Session lock 和 active run registry 仍是本地文件/进程内状态，不支持多 worker 分布式协调。
- `applying` 状态如果进程在实际 `git apply` 前后崩溃，当前没有自动租约恢复，需要后续结合 worker lease/Redis 或数据库锁超时策略处理。
- 审批页面仍是最小本地页面，尚未实现认证、CORS 白名单、生产级前端或审计筛选。

上轮完成：

- 完成持久化 approval queue 的小边界任务，不实现 Redis、完整 Web UI 或 pending patch 持久化。
- 参考 `D:\Software\MewCode` 的 `PermissionRequest`/`PermissionResponse`、remote pending permission future 和权限测试思路；只借鉴“挂起审批、由 UI/API 决议、拒绝后把结果回给模型”的事件模型，没有引入其宿主机直写或本地 shell 权限模型。
- 新增 `ApprovalStore` 协议、`InMemoryApprovalStore` 和 `MySqlApprovalStore`。
- `ApprovalRegistry` 保留本地 future map 以唤醒当前 runtime，并把 store 作为审批状态源；MySQL 模式下等待中的 request 会轮询数据库最终决议，因此另一个 API registry 可以通过同一数据库 approve/reject 并让原运行继续。
- JSONL 本地模式继续使用进程内 approval queue 和 `.coding-agent/approvals/audit.jsonl`，保持默认本地行为不回退。
- `create_app()` 在检测到 `MySqlSessionStore` 时自动装配 `MySqlApprovalStore`，否则使用进程内 store。
- `approvals` 表新增 `schema_version`、`reason`、`expires_at`、`resolution_reason` 和 `resolved_by` 字段。
- 新增 Alembic migration `0002_add_persistent_approval_queue_fields`，从 `0001_create_platform_storage` 平滑升级。
- API 测试新增跨 registry 持久化审批用例：第一个 registry 创建 pending approval，第二个 registry 从同一数据库查询并 approve，原 pending request 被数据库决议唤醒。
- README、架构、路线图、任务和验证文档已同步当前能力边界。

本轮修改文件：

- `README.md`
- `migrations/versions/0002_add_persistent_approval_queue_fields.py`
- `src/coding_agent/api/app.py`
- `src/coding_agent/api/approvals.py`
- `src/coding_agent/db/tables.py`
- `tests/test_api.py`
- `tests/test_session_store_contract.py`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache run ruff check src\coding_agent\api\approvals.py src\coding_agent\api\app.py src\coding_agent\db\tables.py tests\test_api.py tests\test_session_store_contract.py migrations\versions\0002_add_persistent_approval_queue_fields.py`：通过。
- `uv --cache-dir .uv-cache run mypy src\coding_agent\api\approvals.py src\coding_agent\api\app.py tests\test_api.py`：通过。
- `uv --cache-dir .uv-cache run pytest tests\test_api.py --basetemp .codex-test-tmp-approval-api -p no:cacheprovider`：15 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_session_store_contract.py --basetemp .codex-test-tmp-approval-schema -p no:cacheprovider`：8 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_storage_config.py --basetemp .codex-test-tmp-approval-storage -p no:cacheprovider`：9 passed。
- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过，50 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，50 source files。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-approval-final -p no:cacheprovider`：124 passed，1 skipped。

本轮未完成事项：

- 未用真实 DeepSeek API 启动完整聊天回合。
- 未对本机真实 MySQL 执行 `0002` migration；自动化通过 SQLite Alembic migration 和 SQLAlchemy MySQL dialect 编译测试覆盖结构兼容性。
- MySQL 模式持久化的是 approval request 和 decision，不持久化 runtime 内存中的 pending patch；服务重启后不能直接应用旧 patch。
- Session lock 和 active run registry 仍是本地文件/进程内状态，不支持多 worker 分布式协调。
- 审批审计仍写本地 JSONL，尚未实现完整数据库 audit log schema。
- 审批页面仍是最小本地页面，尚未实现认证、RBAC、CORS 白名单、操作者身份、多用户或生产级前端。

上轮完成：

- 完成数据库目标替换为 MySQL，保留 JSONL 默认本地模式和 SQLAlchemy 抽象。
- 参考 `D:\Software\MewCode` 的配置、runtime 装配和 session manager 分层方式；只借鉴入口装配分层，没有复制其本地 session 文件格式或宿主机权限模型。
- 旧数据库驱动依赖已替换为 `pymysql[rsa]`，MySQL URL 使用 `mysql+pymysql://...`；`cryptography` 作为锁定依赖支持 MySQL 8 默认的 `caching_sha2_password` 认证。
- 关系型 session store 已重命名为 `MySqlSessionStore`，模块为 `sessions/mysql.py`。
- `AgentConfig.storage_backend` 现在只接受 `jsonl` 或 `mysql`；显式 `database_url` 会选择 MySQL 后端，旧后端名会被拒绝。
- 数据库 engine 增加 MySQL 连接超时和连接回收配置，避免数据库不可达时长时间卡住，并降低 MySQL `wait_timeout` 后死连接风险。
- SQLAlchemy table 和 Alembic 初始迁移移除了 MySQL 不兼容的 `TEXT server_default`，并为表声明 InnoDB/utf8mb4。
- Alembic `migrations/env.py` 已接入数据库 URL 解析，优先读取 `-x database_url=...`，其次读取 `CODING_AGENT_DATABASE_URL`，最后读取 `alembic.ini` 的 `sqlalchemy.url`。
- 修复只设置 `CODING_AGENT_DATABASE_URL` 时执行 `uv --cache-dir .uv-cache run alembic upgrade head` 报 `KeyError: 'url'` 的问题。
- 修复连接 MySQL 8 账号时 PyMySQL 缺少 `cryptography` 导致 `sha256_password` / `caching_sha2_password` 认证失败的问题。
- 新增 `agent db-check` CLI 诊断命令，可在不调用模型的情况下检查数据库 URL、驱动、认证、目标库和权限，并对 1045、1049、服务不可达、缺少 `cryptography` 等常见问题给出可执行建议。
- Alembic 在线迁移连接失败时会输出同一套脱敏诊断信息，避免用户只能看到 SQLAlchemy/PyMySQL 堆栈。
- 新增 MySQL dialect DDL 编译回归测试，防止后续重新引入 TEXT 默认值等 MySQL 兼容问题。
- README 已补充 MySQL 登录、PATH、数据库账号初始化、root 本地验证 URL、`agent db-check`、MySQL 8 认证依赖和 Alembic URL 优先级说明；架构、路线图、任务、决策、威胁模型和验证文档已同步改为 MySQL 方向。

本轮修改文件：

- `README.md`
- `CodingAgent.md`
- `pyproject.toml`
- `uv.lock`
- `migrations/versions/0001_create_platform_storage.py`
- `migrations/env.py`
- `src/coding_agent/api/approvals.py`
- `src/coding_agent/config.py`
- `src/coding_agent/db/diagnostics.py`
- `src/coding_agent/db/engine.py`
- `src/coding_agent/db/tables.py`
- `src/coding_agent/sessions/__init__.py`
- `src/coding_agent/sessions/factory.py`
- `src/coding_agent/sessions/mysql.py`
- `tests/test_cli.py`
- `tests/test_session_store_contract.py`
- `tests/test_storage_config.py`
- `docs/ARCHITECTURE.md`
- `docs/DECISIONS.md`
- `docs/ROADMAP.md`
- `docs/SESSION_HANDOFF.md`
- `docs/TASKS.md`
- `docs/THREAT_MODEL.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache lock`：通过；移除旧数据库驱动，新增 `pymysql[rsa]`，并锁定 `cryptography`、`cffi` 和 `pycparser`。
- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy src`：通过，50 source files。
- `uv --cache-dir .uv-cache run pytest tests\test_storage_config.py --basetemp .codex-test-tmp-mysql-config -p no:cacheprovider`：8 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_session_store_contract.py --basetemp .codex-test-tmp-alembic-env -p no:cacheprovider`：8 passed。
- `uv --cache-dir .uv-cache run python -c "import cryptography, pymysql; print(cryptography.__version__); print(pymysql.__version__)"`：通过，`cryptography` 50.0.1 可导入。
- `uv --cache-dir .uv-cache run pytest tests\test_session_store_contract.py tests\test_storage_config.py --basetemp .codex-test-tmp-mysql-auth -p no:cacheprovider`：16 passed。
- `uv --cache-dir .uv-cache run agent db-check --database-url "sqlite+pysqlite:///:memory:"`：通过。
- `uv --cache-dir .uv-cache run pytest tests\test_api.py tests\test_cli.py --basetemp .codex-test-tmp-mysql-api-cli -p no:cacheprovider`：16 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_cli.py tests\test_storage_config.py --basetemp .codex-test-tmp-db-check -p no:cacheprovider`：14 passed。
- 使用本机 MySQL `root@localhost` 连接 `coding_agent` 数据库执行 `agent db-check`：通过，MySQL 8.0.41。
- 使用本机 MySQL `root@localhost` 执行 `uv --cache-dir .uv-cache run alembic upgrade head`：通过，`alembic_version` 为 `0001_create_platform_storage`，当前库 9 张表。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-db-check-final -p no:cacheprovider`：123 passed，1 skipped。
- 使用临时 SQLite `CODING_AGENT_DATABASE_URL` 执行 `uv --cache-dir .uv-cache run alembic upgrade head`：通过。

本轮未完成事项：

- 未用真实 DeepSeek API 启动完整聊天回合；数据库侧已用本机 MySQL 8.0.41 完成连通性和 Alembic 真实迁移验证。
- 生产环境仍应通过 Alembic migration 管理 schema，`database_create_schema` 仅用于本地开发和自动化测试。
- Session lock、active run registry 和 approval registry 仍是本地文件/进程内状态，不支持多 worker 分布式协调。
- 审批审计仍写本地 JSONL，尚未接入 MySQL `approvals` 表或完整 audit log schema。

上轮完成：

- 完成 P3 `MySQL 运行时配置切换和生产连接池设置` 的小边界任务。
- 参考 `D:\Software\MewCode` 的配置、runtime 装配和 session manager 分层方式；只借鉴入口装配分层，没有复制其本地 session 文件格式或宿主机权限模型。
- `AgentConfig` 新增 `storage_backend`、`database_url`、`database_pool_size`、`database_max_overflow`、`database_pool_pre_ping` 和 `database_create_schema`。
- 未设置数据库 URL 时仍使用 JSONL；显式设置 `CODING_AGENT_DATABASE_URL` 或 CLI `--database-url` 时，`AgentConfig` 会选择 MySQL 后端。
- 新增 `coding_agent.sessions.factory`，集中创建 `JsonlSessionStore` 或 `MySqlSessionStore`，并在数据库配置失败时输出脱敏后的 URL。
- `CodingAgent` 支持注入 `SessionStore`，默认通过统一 factory 装配，CLI/API 不再各自硬编码 JSONL session store。
- CLI `run/chat/resume/status` 增加 `--storage`、`--database-url` 和 `--database-create-schema`，其中 `resume/status` 不需要创建模型也能读取数据库后端。
- `SessionStore` 协议新增 `load_transcript`，`/history` 可通过当前 store 读取 JSONL 或 MySQL transcript。
- `create_database_engine()` 增加连接池参数、`pool_pre_ping` 和 `hide_parameters`；SQLite 测试 URL 会避开生产连接池参数。
- README 和架构/路线图/任务/验证文档已同步更新。

本轮修改文件：

- `README.md`
- `CodingAgent.md`
- `src/coding_agent/config.py`
- `src/coding_agent/db/engine.py`
- `src/coding_agent/agent/coding_agent.py`
- `src/coding_agent/cli/app.py`
- `src/coding_agent/sessions/__init__.py`
- `src/coding_agent/sessions/factory.py`
- `src/coding_agent/sessions/mysql.py`
- `src/coding_agent/sessions/store.py`
- `tests/test_cli.py`
- `tests/test_storage_config.py`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy src`：通过，49 source files。
- `uv --cache-dir .uv-cache run pytest tests\test_storage_config.py --basetemp .codex-test-tmp-storage-config -p no:cacheprovider`：7 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_session_store_contract.py --basetemp .codex-test-tmp-storage-contract -p no:cacheprovider`：6 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_api.py tests\test_cli.py --basetemp .codex-test-tmp-storage-api-cli -p no:cacheprovider`：16 passed。

本轮未完成事项：

- 未启动真实 MySQL 实例做端到端连接测试；本轮使用 SQLite SQLAlchemy URL 验证同一 store 行为和 API 装配。
- 生产环境仍应通过 Alembic migration 管理 schema，`database_create_schema` 仅用于本地开发和自动化测试。
- Session lock、active run registry 和 approval registry 仍是本地文件/进程内状态，不支持多 worker 分布式协调。
- 审批审计仍写本地 JSONL，尚未接入 MySQL `approvals` 表或完整 audit log schema。

上轮完成：

- 完成 P2 `审批 UI` 的本地最小 Web/API 审批流程。
- 参考 `D:\Software\MewCode` 的 remote permission future、permission dialog 和权限测试思路；只借鉴异步 pending approval 和 UI/测试结构，没有引入其宿主机直写或本地 shell 权限模型。
- 新增 `coding_agent.api.approvals`，包含 `ApprovalRecord`、进程内 `ApprovalRegistry`、approve/reject 幂等决议、超时/取消兜底、详情脱敏截断和 `JsonlApprovalAuditStore`。
- `ApprovalProvider.request` 增加 `session_id` 和 `run_id` 上下文，API 审批可以把待审批项关联到具体 session/run；CLI 终端审批行为保持不变。
- `create_app()` 会给 API agent 装配 `ApprovalRegistry`，需要审批的 Plan Mode、sandbox 或 patch 操作会挂起等待 Web 端决议。
- 新增 `GET /approvals/ui` 最小本地审批页面，使用 DOM `textContent` 渲染动态内容，避免 diff preview 中的 HTML/script 被执行。
- 新增 `GET /approvals`、`GET /approvals/{approval_id}`、`POST /approvals/{approval_id}/approve` 和 `POST /approvals/{approval_id}/reject`。
- 审批详情展示 tool、reason、session/run、changed files、脱敏 diff preview 或 details。
- 审批请求和决议会写入 `.coding-agent/approvals/audit.jsonl`。
- 取消 run/session 或 SSE 消息流断开时会取消对应 pending approval，避免工具永久挂起。
- 新增 API 回归测试，覆盖 approve 解挂、reject 返回 policy_denied 给模型、patch preview 脱敏、audit 写入、重复决议幂等、非法 status/ID、resolved 查询和 cancel 解挂。

本轮修改文件：

- `README.md`
- `src/coding_agent/api/app.py`
- `src/coding_agent/api/approvals.py`
- `src/coding_agent/runtime/loop.py`
- `src/coding_agent/cli/app.py`
- `tests/test_api.py`
- `tests/test_plan_mode.py`
- `docs/ARCHITECTURE.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache run ruff check src\coding_agent\api\app.py src\coding_agent\api\approvals.py src\coding_agent\runtime\loop.py src\coding_agent\cli\app.py tests\test_api.py tests\test_plan_mode.py`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过，48 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，48 source files。
- `uv --cache-dir .uv-cache run pytest tests\test_api.py --basetemp .codex-test-tmp-api-approval -p no:cacheprovider`：14 passed。
- `uv --cache-dir .uv-cache run pytest tests\test_plan_mode.py tests\test_runtime.py --basetemp .codex-test-tmp-approval-runtime -p no:cacheprovider`：19 passed。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-approval-final -p no:cacheprovider`：108 passed，1 skipped。

本轮未完成事项：

- 审批队列仍是 API 进程内状态，服务重启后 pending approval 不会恢复。
- 审批审计目前写入本地 JSONL，尚未接入 MySQL `approvals` 表或完整 audit log schema。
- 审批页面是最小本地页面，尚未实现认证、RBAC、CORS 白名单、操作者身份、多用户或生产级前端。
- 多 worker 部署下 active run、approval 和 session lock 仍需 Redis/MySQL 协调。
- Pending patch 仍只在单个 runtime 内存中有效，尚未持久化。

上轮完成：

- 完成 P2 `MySQL 存储` 的最小平台数据层。
- 新增 `coding_agent.db`，用 SQLAlchemy Core 定义 sessions、runs、session_events、checkpoints、transcripts、approvals、artifacts 和 model_usage 表。
- 新增 `MySqlSessionStore`，实现 `SessionStore` 的事件追加/读取、checkpoint 保存/恢复、summary 保存/列表和 transcript 追加。
- 新增 Alembic 初始迁移 `0001_create_platform_storage`，可创建 MySQL 目标 schema；合同测试中使用 SQLite 验证迁移结构。
- JSONL 仍作为 CLI/API 默认本地模式保留；本轮没有改变 `CodingAgent` 默认装配路径。
- 新增 repository contract tests，让 JSONL 和 SQLAlchemy store 共用核心行为测试，并覆盖 summary 更新不得因外键级联删除事件和 checkpoint。
- 参考 `D:\Software\MewCode` 的 session record/meta 分离、JSONL 容错和 resume/delete 测试思路；未照搬其本地直写模型。
- `pyproject.toml` 和 `uv.lock` 增加 SQLAlchemy、Alembic 和 MySQL 驱动依赖。

本轮修改文件：

- `README.md`
- `CodingAgent.md`
- `pyproject.toml`
- `uv.lock`
- `alembic.ini`
- `migrations/env.py`
- `migrations/versions/0001_create_platform_storage.py`
- `src/coding_agent/db/__init__.py`
- `src/coding_agent/db/engine.py`
- `src/coding_agent/db/tables.py`
- `src/coding_agent/sessions/__init__.py`
- `src/coding_agent/sessions/mysql.py`
- `src/coding_agent/sessions/store.py`
- `tests/test_session_store_contract.py`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache add "sqlalchemy==2.0.43" "alembic==1.16.5"`：通过；初次受限网络运行失败后，经授权重试成功。
- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过，47 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，47 source files。
- `uv --cache-dir .uv-cache run mypy src tests/test_session_store_contract.py`：通过，48 source files。
- `uv --cache-dir .uv-cache run pytest tests/test_session_store_contract.py --basetemp .codex-test-tmp-sqlalchemy-targeted-2 -p no:cacheprovider`：6 passed。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-sqlalchemy-final2 -p no:cacheprovider`：101 passed，1 skipped。

本轮未完成事项：

- 未将 CLI/API 默认 store 切换为数据库；仍需后续增加配置入口和运行时装配。
- 未实现 Redis 分布式锁、active run registry 或 pub/sub。
- 未实现持久化 approval queue、Web 审批接口或 patch 审批页面。
- MySQL schema 目前是平台存储底座；turns、tool_calls、patches、audit_logs 和多用户/多租户归属仍待规范化扩展。
- 未启动真实 MySQL 实例做端到端连接测试；本轮使用 SQLAlchemy contract tests 和 Alembic SQLite migration 测试验证结构和行为。

上轮完成：

- 参考 `D:\Software\MewCode` 的 remote WebSocket 事件桥接、浏览器 UI 事件消费和 background task cancel 思路，给当前项目增加最小 FastAPI/SSE 服务入口。
- 新增 `coding_agent.api.app`，提供 `create_app()` factory、`ApiSessionManager`、会话/run 内存索引、SSE 序列化和取消入口。
- API 复用现有 `CodingAgent` 与 `ChatSession`，不复制 runtime，不新增宿主机 shell、直接文件写入或绕过 patch-only 写回的能力。
- 新增 `GET /health`、`POST /v1/sessions`、`GET /v1/sessions`、`GET /v1/sessions/{session_id}`、`POST /v1/sessions/{session_id}/messages/stream`、`POST /v1/runs/{run_id}/cancel` 和 `POST /v1/sessions/{session_id}/cancel`。
- `messages/stream` 以 Server-Sent Events 原样返回 `AgentEvent`，便于后续前端 UI 渲染流式文本、工具事件、审批事件、token usage、完成、失败和取消。
- API 层复用现有 session lock，避免 CLI 或另一个 API 进程同时写入同一会话；同一进程内用 active session/run registry 防止并发发送并支持取消。
- 新增严格 session_id/run_id 校验，拒绝路径穿越式 ID。
- 新增 `tests/test_api.py`，使用 fake model 覆盖创建/列出 session、SSE 事件流、未知 session、非法 session id、空消息、取消活跃运行和取消不存在的 run。
- `pyproject.toml` 和 `uv.lock` 增加 FastAPI、Starlette 和 uvicorn 依赖。

本轮修改文件：

- `README.md`
- `pyproject.toml`
- `uv.lock`
- `src/coding_agent/api/__init__.py`
- `src/coding_agent/api/app.py`
- `tests/test_api.py`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮验证结果：

- `uv --cache-dir .uv-cache lock`：通过，新增 fastapi 0.116.1、starlette 0.47.3、uvicorn 0.35.0。
- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy src tests/test_api.py`：通过，44 source files。
- `uv --cache-dir .uv-cache run pytest tests/test_api.py --basetemp .codex-test-tmp-api -p no:cacheprovider`：7 passed。
- `uv --cache-dir .uv-cache run mypy`：通过，43 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，43 source files。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-fastapi-final -p no:cacheprovider`：95 passed，1 skipped。

本轮未完成事项：

- 没有实现前端 UI。
- 没有实现认证、CORS 策略、RBAC 或多租户。
- 没有实现 Web 审批响应接口或持久化 approval queue；未预授权的 shell/write/plan approval 仍会按 runtime 策略拒绝。
- Active run registry 仍是进程内状态；多 worker 部署还需要 Redis 或数据库锁。
- Pending patch 仍只在单个 runtime 内存中有效，尚未持久化。

上轮完成：

- 参考 `D:\Software\MewCode` 的 `DangerousCommandDetector` 和权限分层思路，给当前项目增加沙箱执行前 command risk detector。
- 新增 `coding_agent.policy.command_risk`，定义 `normal`、`suspicious`、`dangerous` 三类命令风险。
- `PolicyEngine` 在 `sandbox_shell` 和 `verify` 授权判断前运行风险检测。
- 高置信危险命令直接返回 `policy_denied`，即使配置了 `--allow-shell` 也不能绕过。
- 可疑命令强制交互式复核；非交互模式下直接拒绝。
- 覆盖 `rm -rf /`、`rm -rf .`、`rm -rf *`、`mkfs`、`dd of=/dev/*`、设备重定向、`chmod -R 777 /`、`chown -R ... /`、fork bomb、远程脚本管道执行、`git reset --hard`、`git clean -xdf` 和 `find . -delete` 等高置信风险。
- 常见验证命令和定向清理命令保持不误伤，例如 `python -m pytest`、`uv run pytest`、`npm test`、`git diff` 和 `rm -rf build/`。
- 新增 detector、policy 和 runtime 回归测试，确认危险命令不会进入工具执行，并会把 `policy_denied` 返回给模型继续下一轮。

本轮修改文件：

- `src/coding_agent/policy/command_risk.py`
- `src/coding_agent/policy/engine.py`
- `tests/test_command_risk.py`
- `tests/test_policy_and_tools.py`
- `tests/test_runtime.py`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/TASKS.md`
- `docs/THREAT_MODEL.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

本轮完成：

- 修复 snapshot 敏感过滤误伤普通源码文件的问题。
- 收窄 `WorkspacePathPolicy` 的敏感文件规则，改为精确目录、精确文件名、私钥命名和密钥后缀匹配。
- `token_usage.py`、`test_token_usage.py`、`token_validator.py`、`secret_scanner.py` 这类正常源码文件不再因为文件名包含 `token` 或 `secret` 被排除。
- `.env*`、`.ssh/`、`secrets/`、`credentials/`、`credentials.json`、`token.txt`、`private.key`、`*.pem` 等仍会被保护。
- 同步收窄 `dockerignore_patterns()`，避免 `search` 因 `*token*`、`*secret*` 这类宽泛 glob 漏掉正常源码。
- 新增 sandbox 回归测试，覆盖敏感文件仍被排除、类似敏感命名的源码被保留、snapshot tar 内包含正常源码。
- 参考了 `D:\Software\MewCode` 的 `PathSandbox` 思路：路径边界和受保护路径应尽量精确，不用宽泛关键词排除普通源码。

上轮完成：

- 完成 P1 `Plan Mode` 能力。
- 新增 `plan_mode` 配置和 CLI `--plan` 开关。
- 新增 `submit_plan` 工具，要求模型提交计划、预计修改文件、验证命令和风险。
- Runtime 在 Plan Mode 下对 `sandbox_shell`、`verify` 和 `apply_patch` 增加前置计划门禁。
- 计划审批只解锁本轮高风险工具门禁，不绕过原有 `--allow-shell`、`--allow-write` 和具体工具审批。
- 非交互模式下 `submit_plan` 默认拒绝，不能靠 `--allow-shell` 或 `--allow-write` 绕过计划门禁。
- CLI 审批提示会展示计划内容、预计修改文件、验证命令和风险，`/status` 会展示 Plan Mode 状态。
- 参考了 `D:\Software\MewCode` 中 permission mode、`ExitPlanMode` 和 plan approval 测试思路，但未引入其 TUI、计划文件、本地直写或 OS sandbox 机制。

本轮完成：

- 将 Plan Mode 从一次性获批开关升级为 runtime 状态机。
- 新增 `src/coding_agent/runtime/plan.py`，集中管理计划状态、计划 ID、修订次数和最近失败。
- `sandbox_shell`、`verify` 或 `apply_patch` 返回非 success 时，当前计划会自动进入 `failed` 状态。
- 计划失败后，后续高风险工具会被 `plan_revision_required` 拦截；模型必须重新调用 `submit_plan`，并提供 `revision_of`、`failure_summary` 和 `changed_approach`。
- 新增 `plan_submitted`、`plan_approved`、`plan_rejected`、`plan_failed` 和 `plan_revision_required` 事件。
- CLI 审批展示新增目标、步骤、修订来源、失败摘要和调整方案；`/status` 展示计划状态、计划 ID、修订次数和最近失败。
- `tests/test_plan_mode.py` 从 6 个用例扩展到 9 个用例，覆盖失败后阻塞、修订计划恢复和缺少失败上下文时拒绝修订。

本轮修改文件：

- `README.md`
- `src/coding_agent/runtime/plan.py`
- `src/coding_agent/runtime/loop.py`
- `src/coding_agent/runtime/events.py`
- `src/coding_agent/tools/plan.py`
- `src/coding_agent/agent/coding_agent.py`
- `src/coding_agent/cli/app.py`
- `src/coding_agent/sessions/store.py`
- `tests/test_plan_mode.py`
- `docs/ARCHITECTURE.md`
- `docs/TASKS.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

上轮修改文件：

- `src/coding_agent/workspace/security.py`
- `tests/test_sandbox.py`
- `docs/ARCHITECTURE.md`
- `docs/THREAT_MODEL.md`
- `docs/SESSION_HANDOFF.md`
- `docs/VERIFICATION.md`

最近一次验证结果：

- `uv --cache-dir .uv-cache run ruff check`：通过。
- `uv --cache-dir .uv-cache run mypy`：通过，41 source files。
- `uv --cache-dir .uv-cache run mypy src`：通过，41 source files。
- `uv --cache-dir .uv-cache run pytest tests/test_command_risk.py tests/test_policy_and_tools.py tests/test_runtime.py --basetemp .codex-test-tmp-risk -p no:cacheprovider`：42 passed。
- `uv --cache-dir .uv-cache run pytest tests/test_sandbox.py --basetemp .codex-test-tmp-security -p no:cacheprovider`：14 passed，1 skipped。
- `uv --cache-dir .uv-cache run pytest tests/test_plan_mode.py --basetemp .codex-test-tmp-plan-recovery-3 -p no:cacheprovider`：10 passed。
- 当前仓库 `SnapshotService` smoke test：确认 `src/coding_agent/runtime/token_usage.py` 和 `tests/test_plan_mode.py` 均进入 snapshot。
- 实际 Docker sandbox smoke test：`test -f src/coding_agent/runtime/token_usage.py && python -m pytest tests/test_plan_mode.py -q`，success，exit code 0，6 passed。
- `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-risk-final2 -p no:cacheprovider`：88 passed，1 skipped。

注意事项：

- 普通 `uv run pytest` 在 Codex 沙箱账户下可能失败，因为它会尝试写入 `C:\Users\HP\AppData\Local\Temp\pytest-of-HP`。
- 如果旧 `.codex-test-tmp` 目录存在权限残留，继续改用新的项目内临时目录，例如 `.codex-test-tmp-security-final`。
- `git status` 可能因为 Git dubious ownership 失败，原因是仓库属于用户 Windows 账户，而 Codex 使用沙箱账户运行。除非用户明确要求，不要修改全局 Git 配置。
- GitHub Actions workflow 已在仓库中新增，但本轮只完成了本地验证，没有观察远端 Actions 实际运行结果。
- Token 消耗累计值只有在 provider 返回 usage 时才是精确值；当前上下文 token 在 provider 锚点之后仍会对新增尾部消息做估算。
- Context summary 当前是确定性抽取摘要，不调用模型；后续可增加模型辅助摘要，但必须保留 fake adapter 测试和审计事件。
- Plan Mode 当前是显式 `--plan` 开关，不是默认开启；计划审批只在当前 run 内有效。
- 后续新增敏感路径规则时，不要使用宽泛子串排除普通源码文件；必须同步增加误伤回归测试。

## 下一轮建议

建议下一轮任务：

从 `docs/TASKS.md` 中选择下一个边界清晰的任务。若继续推进平台化，建议先做 Redis-backed active run/session lock，为多 worker API 部署补齐运行态协调；若优先补工程化，可以先增加 coverage 或 pre-commit。

建议 prompt：

```text
本轮只做 Redis-backed active run/session lock，不实现完整 Web UI 或 RBAC。请让 API 多 worker 场景下同一 session/run 的活跃状态、取消信号和锁协调不再依赖单进程内存；保留当前 Docker sandbox + patch-only 写回模型，补充并发/取消测试、配置文档和验证记录。
```

验收标准：

- 未配置 Redis 时，当前单进程 active run registry 和本地 session lock 行为不回退。
- 配置 Redis 后，同一 session 在不同 API worker 中不能并发运行。
- 配置 Redis 后，取消 run/session 可以跨 worker 传递取消意图。
- 锁必须有 TTL、续约和崩溃后释放策略，避免永久死锁。
- Redis URL 和认证信息必须脱敏，不泄露凭据。
- 自动化测试不依赖真实 DeepSeek API。
- `uv --cache-dir .uv-cache run ruff check`、`uv --cache-dir .uv-cache run mypy`、`uv --cache-dir .uv-cache run mypy src` 和 `uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider` 通过。

## 后续 Session 工作规则

- 除非任务明确要求，否则不要重写 `README.md` 或 `CodingAgent.md`。
- 不要用直接宿主机写入替代 Docker sandbox + patch-only 写回。
- MySQL、Milvus、Redis、MCP、Skills、Hooks、Web UI 等能力在代码实现前，不能写成已经完成。
- 每个 session 只做一个边界清晰的能力。
- 代码修改必须补充或更新测试。
- 每次结束前更新本文档。
- 验证命令和结果记录到 `docs/VERIFICATION.md`。
- 优先做小而可审查的修改，避免大范围重构。
