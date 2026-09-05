# 开发约定

## 目录结构

```
src/coding_agent/
  ai/            # 模型供应商抽象（contracts、gateway、deepseek）
  runtime/       # Agent 执行循环（loop、plan、context、token_usage、events）
  agent/         # 装配层（coding_agent.py）
  tools/         # 模型可调用工具（contracts、builtin、sandbox、plan、semantic）
  sandbox/       # 隔离执行（contracts、snapshot、docker、patches）
  policy/        # 策略引擎（engine、command_risk）
  workspace/     # 工作区服务（service、security）
  semantic/      # 语义索引（contracts、chunking、embeddings、milvus、service、store）
  sessions/      # 会话存储（factory、store、mysql、lock）
  db/            # 数据库（tables、engine、diagnostics）
  tracing/       # Trace 与 artifact（store）
  memory/        # 记忆系统（contracts）
  evals/         # 评测（scenarios）
  api/           # FastAPI 服务（app、approvals）
  cli/           # Typer 命令行（app）
  config.py      # 配置
tests/           # 测试
migrations/      # Alembic 迁移
docs/            # 本目录：跨 session 记忆库
```

## 命名规范

- 文件：`snake_case.py`
- 类：`PascalCase`
- 函数/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- Provider 适配器：`XxxAdapter`（如 `DeepSeekAdapter`）
- Store 实现：`XxxStore`（如 `MySqlSessionStore`、`InMemoryPatchStore`）
- Service 类：`XxxService`（如 `SnapshotService`）
- Tool 函数：`xxx_tool` 或直接动词（如 `read`、`search`、`apply_patch`）

## 新增能力 checklist

- [ ] 在对应模块实现核心逻辑
- [ ] 补充或更新测试
- [ ] 涉及 DB schema 变更：新增 Alembic migration
- [ ] 涉及模型 adapter：测试不依赖真实 API key
- [ ] 更新 `03-task-backlog.md` 任务状态
- [ ] 在 `04-progress-log.md` 顶部追加 session 记录
- [ ] 运行验证命令（见下）

## 验证命令

在受限 Windows 账户下运行时，使用项目内 cache 和临时目录：

```powershell
uv --cache-dir .uv-cache run ruff check
uv --cache-dir .uv-cache run mypy
uv --cache-dir .uv-cache run mypy src
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider
```

定向验证（只检查改动文件）：

```powershell
uv --cache-dir .uv-cache run ruff check <改动文件列表>
uv --cache-dir .uv-cache run mypy <改动文件列表>
uv --cache-dir .uv-cache run pytest <改动测试文件> --basetemp .codex-test-tmp-xxx -p no:cacheprovider
```

### 已知本地环境问题

**uv cache 权限**：直接 `uv run pytest` 可能失败（`Failed to initialize cache at C:\Users\HP\AppData\Local\uv\cache`），改用 `uv --cache-dir .uv-cache run ...`

**pytest 临时目录**：`pytest` 可能尝试创建 `C:\Users\HP\AppData\Local\Temp\pytest-of-HP`，改用 `--basetemp .codex-test-tmp`

**Git dubious ownership**：`git status` 可能失败（仓库属于用户 Windows 账户，Agent 使用沙箱账户）。除非用户明确要求，不要修改全局 Git 配置。

## 测试规范

- 自动化测试**不依赖真实 DeepSeek / DashScope / Milvus / MySQL 服务**
- 模型测试使用 fake adapter
- 语义索引测试使用 fake embedding + in-memory vector index
- 真实集成 smoke test 通过环境变量显式启用（如 `RUN_REAL_SEMANTIC_TESTS=1`）
- 数据库测试使用 SQLAlchemy SQLite URL 覆盖同一 store 行为

## 提交规范（Commit Message）

- 格式：`<type>: <描述>`
- type ∈ `feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf`
- 示例：
  - `feat: 新增 memory metadata schema 和 Alembic migration`
  - `fix: 修复 snapshot 敏感过滤误伤普通源码文件`
  - `docs: 更新 03-task-backlog B1-1 状态为 done`

## 依赖管理

- 新增依赖必须更新 `pyproject.toml` 并执行 `uv lock`
- 优先使用已存在依赖，避免引入功能重复的库
- 新增依赖需在 `04-progress-log.md` 说明用途

## 破坏性变更

- **DB schema 变更**：必须先写 Alembic migration，再改代码；生产环境通过 `alembic upgrade head` 管理
- **API 契约变更**：默认向后兼容，新字段以可选形式加入
- **安全模型变更**：不得引入宿主机直写或宿主机 shell；不得破坏 patch-only 写回链路
- **工具列表变更**：必须同步更新 policy engine 和文档

## 文档维护规则

- 每个 session 结束**必须**更新：
  - `03-task-backlog.md`：任务状态 + 实际改动文件
  - `04-progress-log.md`：顶部追加 session 记录
  - `05-agent-handoff.md`：当前状态 + 下一步建议
- **不要**删除已 `done` 的任务记录
- **不要**在不读 backlog 的情况下开始写代码
- **不要**夸大未实现能力
