# Coding Agent

这是一个面向 Windows 的本地 Python 编程 Agent。项目以安全优先：模型只能读取受限工作区上下文；所有命令均在一次性、无网络的 Docker 沙箱中执行；宿主机写入只能通过经过验证且经授权的 Git 补丁回写。

## 快速开始

请使用 Python 3.12 和 uv。`uv.lock` 已固定运行时和开发依赖版本。

```powershell
uv sync --all-groups
$env:DEEPSEEK_API_KEY = "你的本地密钥"

# Docker Desktop 使用 Linux containers，并在项目根目录构建固定的沙箱镜像
docker build -f Dockerfile.sandbox -t coding-agent-sandbox:python-3.12 .

# 只读分析
uv run agent chat --workspace .

# 预先授权沙箱命令；补丁回写仍会单独询问
uv run agent chat --workspace . --allow-shell

# 预先授权沙箱命令与补丁回写
uv run agent chat --workspace . --allow-write --allow-shell

# 开启 Plan Mode；运行沙箱命令或回写补丁前必须先提交计划并获批，失败后需修订计划
uv run agent chat --workspace . --plan

# 显式选择模型 provider 和模型名；当前已实现 provider 为 deepseek
uv run agent chat --workspace . --provider deepseek --model deepseek-chat

# 启动最小 FastAPI 服务；默认从环境变量读取模型配置
uv run uvicorn coding_agent.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

`agent chat` 是连续会话模式：同一个终端中的每次提问都会复用会话上下文。输入 `/help` 查看命令，输入 `/exit` 保存并退出。同一会话只能由一个终端持有；异常退出后的失效锁可在确认原进程不在运行后使用 `--force-unlock` 清理。

恢复已退出的会话：

```powershell
uv run agent chat --workspace . --resume <会话ID>
uv run agent chat --workspace . --resume <会话ID> --force-unlock
```

仍可执行一次性任务：

```powershell
uv run agent run "检查仓库并建议最小修复" --workspace . --model deepseek-chat
```

不带 `--allow-write` 或 `--allow-shell` 时，交互式运行会分别请求确认。`--allow-shell` 只授权 Docker 沙箱内的命令，`--allow-write` 只授权将已验证补丁写回宿主机。使用 `--plan` 时，模型在调用 `sandbox_shell`、`verify` 或 `apply_patch` 前必须先调用 `submit_plan` 提交计划，并由用户批准；计划批准不绕过具体命令和补丁回写审批。若这些高风险工具返回失败、超时、取消、验证失败或策略拒绝，当前计划会自动失效，继续执行前必须提交包含失败摘要和调整方案的修订计划。使用 `--non-interactive` 时，未被对应选项预先授权或未获计划审批的操作会立即被拒绝。

## Docker 沙箱与补丁回写

运行沙箱前需要 Docker Desktop 已启动、处于 Linux containers 模式，并且本地存在 `coding-agent-sandbox:python-3.12` 镜像。默认 Dockerfile 会在构建阶段依据本项目的锁文件预装运行时和测试依赖，因此容器内可离线执行 `python -m pytest`。可以用 `--sandbox-image <镜像名>` 或 `CODING_AGENT_SANDBOX_IMAGE` 改用已审计的其他镜像。Agent 不会自动安装 Docker、拉取镜像或下载依赖。

每次 `sandbox_shell` 或 `verify` 调用都会执行以下流程：

1. 创建临时工作区快照。`.git`、`.coding-agent`、虚拟环境、缓存、`node_modules`、`.env*`、密钥/令牌目录和私钥格式均不会进入快照；符号链接和超过 8 MB 的单个文件也会排除。
2. 通过标准输入将快照传入一次性容器，绝不挂载宿主机目录。容器以非 root 用户运行，根文件系统只读，网络禁用，所有 capability 删除，并限制 CPU、内存、PID、临时文件系统和命令超时。
3. `sandbox_shell` 在容器内输出文本 Git diff 和变更文件清单。`verify` 的用途仅是运行验证，即使它改变了快照，变更也会被丢弃。
4. 模型必须调用 `apply_patch` 才能请求回写。终端会展示变更文件和脱敏后的 diff 预览；回写前会重新检查路径、文件哈希和 `git apply --check`。

JSONL 本地模式下，补丁只保留在当前 Agent 运行时内，退出或恢复会话后必须重新运行沙箱命令。配置 MySQL session store 后，沙箱生成的 pending patch 会作为 package 写入 `patches` 表，包含 patch text、changed files、snapshot hashes、diff preview 和状态；新 runtime 可以读取旧 pending patch，但应用前仍必须重新执行结构、路径、文件哈希和 `git apply --check` 校验。回写目标必须是 Git 工作树，既有文件应由 Git 跟踪。当前版本有意拒绝二进制补丁、子模块、符号链接、可执行权限变更、重命名和复制；这些情况需要人工处理。任何补丁若触及敏感或内部目录，或快照后的原文件已被其他进程改动，都会拒绝写回。

模型不再获得直接的 `shell`、`edit`、`write` 工具。`read`、`search` 和 `git_diff` 仍是受限的只读工具；其中 `git_diff` 禁用 Git 外部 diff 与 textconv。

## DeepSeek 配置

将 `.env.example` 中的变量名配置到本地进程环境（不要提交 `.env`）。可选变量：

```powershell
$env:DEEPSEEK_API_KEY = "你的本地密钥"
$env:CODING_AGENT_MODEL_PROVIDER = "deepseek"
$env:CODING_AGENT_MODEL = "deepseek-chat"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
$env:DEEPSEEK_MODEL = "deepseek-chat"
$env:CODING_AGENT_SANDBOX_IMAGE = "coding-agent-sandbox:python-3.12"
```

`CODING_AGENT_MODEL_PROVIDER` 决定使用哪个模型适配器，`CODING_AGENT_MODEL` 决定传给该 provider 的具体模型名。当前版本只实现 `deepseek` provider；不设置 provider 时默认使用 `deepseek`，不设置模型名时默认使用 `deepseek-chat`。`DEEPSEEK_MODEL` 仍作为 DeepSeek 专属模型名回退变量保留。

DeepSeek 适配器使用兼容 OpenAI 的 Chat Completions SSE 协议。所有 Agent 运行均使用真实模型配置。

## 存储配置

默认情况下，CLI 和 API 会继续把 session、checkpoint、summary 和 transcript 写入工作区内的 `.coding-agent/` JSONL 文件，适合本地开发和单机使用。

如果本机已经安装 MySQL，但 PowerShell 提示 `mysql` 不是可识别命令，通常只是 MySQL 客户端没有加入 `PATH`。可以先用完整路径进入 MySQL：

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p
```

也可以只在当前 PowerShell 会话中临时加入 PATH：

```powershell
$env:Path += ";C:\Program Files\MySQL\MySQL Server 8.0\bin"
mysql -u root -p
```

输入 root 密码后，看到 `mysql>` 表示已经进入数据库。首次使用本项目的 MySQL 模式时，可以创建独立数据库和账号：

```sql
CREATE DATABASE coding_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'coding_agent'@'localhost' IDENTIFIED BY '<app-password>';
GRANT ALL PRIVILEGES ON coding_agent.* TO 'coding_agent'@'localhost';
FLUSH PRIVILEGES;
```

如果账号已存在，可只执行 `CREATE DATABASE` 和 `GRANT`。如果 MySQL 服务未启动，先在 Windows 服务中启动 `MySQL80`，或用管理员 PowerShell 执行 `Start-Service MySQL80`。

显式配置数据库 URL 后，运行时会切换为 SQLAlchemy/MySQL 会话存储：

```powershell
$env:CODING_AGENT_DATABASE_URL = "mysql+pymysql://coding_agent:<app-password>@localhost:3306/coding_agent?charset=utf8mb4"
uv run alembic upgrade head
uv run agent chat --workspace .

# 或者仅对单次命令显式传入
uv run agent chat --workspace . --database-url "mysql+pymysql://coding_agent:<app-password>@localhost:3306/coding_agent?charset=utf8mb4"
```

如果你本机确认能登录的是 root，但还没有创建 `coding_agent` 用户，本地验证也可以先用 root URL：

```powershell
$env:CODING_AGENT_DATABASE_URL = "mysql+pymysql://root:<root-password>@localhost:3306/coding_agent?charset=utf8mb4"
uv --cache-dir .uv-cache run agent db-check
uv --cache-dir .uv-cache run alembic upgrade head
uv --cache-dir .uv-cache run agent chat --workspace .
```

长期使用和企业部署不要让应用直连 root。推荐先用 root 登录 MySQL，创建上面的 `coding_agent` 专用用户并完成授权，然后把 URL 切回 `coding_agent:<app-password>`。

运行迁移或启动 Agent 前，可以先做数据库连通性诊断：

```powershell
uv --cache-dir .uv-cache run agent db-check
```

该命令不会调用模型，只检查数据库 URL、驱动、认证、目标库和权限。遇到 `Access denied for user 'coding_agent'@'localhost'` 时，说明 URL 中的用户或密码与 MySQL 实际账号不一致，或该用户没有被创建/授权。

MySQL 8 默认账号认证方式通常是 `caching_sha2_password`。项目依赖已通过 `pymysql[rsa]` 锁定该认证方式所需的 `cryptography` 包；如果看到 `cryptography package is required for sha256_password or caching_sha2_password auth methods`，先运行 `uv sync` 或直接重新执行 `uv run ...`，让 uv 按 `uv.lock` 安装缺失依赖。不要通过把 MySQL 用户改回旧的 `mysql_native_password` 来规避该问题，除非你的部署环境明确要求兼容旧客户端。

Alembic migration 会按以下优先级读取数据库 URL：

1. `uv run alembic -x database_url="mysql+pymysql://..." upgrade head`
2. 当前进程环境变量 `CODING_AGENT_DATABASE_URL`
3. `alembic.ini` 中的 `sqlalchemy.url`

推荐使用 `CODING_AGENT_DATABASE_URL`，不要把真实账号密码写入 `alembic.ini`。如果三处都没有配置，`alembic upgrade head` 会直接提示需要数据库 URL，而不是报 `KeyError: 'url'`。

可选配置：

```powershell
$env:CODING_AGENT_STORAGE_BACKEND = "mysql"
$env:CODING_AGENT_DATABASE_POOL_SIZE = "5"
$env:CODING_AGENT_DATABASE_MAX_OVERFLOW = "10"
$env:CODING_AGENT_DATABASE_POOL_PRE_PING = "true"
$env:CODING_AGENT_DATABASE_CONNECT_TIMEOUT_SECONDS = "5"
$env:CODING_AGENT_DATABASE_POOL_RECYCLE_SECONDS = "1800"
```

生产环境应先运行 Alembic migration，再启动 CLI/API。`CODING_AGENT_DATABASE_CREATE_SCHEMA=true` 或 `--database-create-schema` 只用于本地开发和自动化测试，避免生产启动时隐式改 schema。数据库配置错误会隐藏 URL 中的用户名和密码后再报错。

## FastAPI 服务

当前提供最小 FastAPI 服务入口，用于后续 Web UI、审批控制台和后台 worker 复用同一套 runtime：

- `GET /health`：健康检查和当前 workspace/model 摘要。
- `POST /v1/sessions`：创建或恢复会话。
- `GET /v1/sessions`：列出本工作区会话摘要。
- `GET /v1/sessions/{session_id}`：读取单个会话摘要。
- `POST /v1/sessions/{session_id}/messages/stream`：发送消息并以 Server-Sent Events 返回 `AgentEvent`。
- `POST /v1/runs/{run_id}/cancel` 和 `POST /v1/sessions/{session_id}/cancel`：取消活跃运行。
- `GET /approvals/ui`：打开本地最小审批页面，查看并处理待审批操作。
- `GET /approvals`、`GET /approvals/{approval_id}`、`POST /approvals/{approval_id}/approve`、`POST /approvals/{approval_id}/reject`：列出、查看、批准或拒绝待审批操作。

API 层只封装 `CodingAgent` 和 `ChatSession`，不复制 agent loop，也不新增宿主机写入或宿主机 shell 能力。服务进程会复用现有 session lock，避免与 CLI 或另一个 API 进程同时写入同一会话。API 的审批页面目前是本地最小实现：JSONL 本地模式下待审批项仍保存在当前进程内；配置 MySQL session store 后，审批请求、状态查询和 approve/reject 决议会持久化到 `approvals` 表，等待中的运行会轮询数据库决议并继续执行；pending patch package 会持久化到 `patches` 表，审批通过后仍由 `apply_patch` 重新校验再写回。审批审计仍以脱敏 JSONL 写入 `.coding-agent/approvals/audit.jsonl`。当前尚未实现认证、多进程 active run/session lock 协调和生产级 Web UI；生产部署前需要补齐这些能力，并且服务应只绑定可信本地地址或受认证代理保护。

## 架构

`ai` 提供供应商无关契约和 DeepSeek 适配器；`runtime` 只通过模型和工具协议运行事件循环；`workspace`、`policy`、`sandbox`、`tools`、`sessions`、`tracing` 和 `memory` 是独立服务。`sandbox` 包含快照、Docker 执行器和补丁注册表；`agent.CodingAgent` 负责装配并提供稳定 Python API；`cli` 和 `api` 仅负责输入输出、事件渲染和服务边界。

连续聊天会话在内存中保留模型消息历史，每条用户消息产生一个新的运行 ID；会话 ID 保持不变。工具调用和工具结果会进入消息历史，以便模型在下一次提问中理解刚完成的操作。

## 数据与安全

每个工作区的数据均保存在 `.coding-agent/` 下：

- `sessions/<session_id>.jsonl`：不可变的会话事件历史（`agent resume`、`agent status`）
- `checkpoints/<session_id>.json`：连续对话的可恢复模型上下文（`agent chat --resume`）
- `transcripts/<session_id>.md`：按完整用户/Agent 回答合并的可读会话记录
- `session-index.json`：`/sessions` 使用的会话摘要索引
- `locks/<session_id>.lock`：连续会话的独占锁
- `traces/<session_id>/<run_id>.jsonl`：关联运行过程的结构化追踪
- `artifacts/<session_id>/<run_id>/`：可选的脱敏产物
- `approvals/audit.jsonl`：本地 API 审批请求和决议的脱敏审计记录
- `logs/application.jsonl`：应用诊断日志

项目还提供可选的 SQLAlchemy/MySQL 会话、审批和 patch package 存储底座：`coding_agent.db` 定义 sessions、runs、session events、checkpoints、transcripts、approvals、artifacts、patches 和 model usage 表，`coding_agent.sessions.MySqlSessionStore` 实现与 JSONL store 相同的核心协议，`coding_agent.api.approvals.MySqlApprovalStore` 持久化 API 审批请求和决议，`coding_agent.sandbox.patches.MySqlPatchStore` 持久化 pending patch package，`migrations/` 提供 Alembic 迁移。当前 CLI/API 默认仍使用 `.coding-agent/` JSONL 本地模式；显式配置 `CODING_AGENT_DATABASE_URL` 或 `--database-url` 后会改用 MySQL store 保存 session、checkpoint、summary、transcript、API approval queue 和 pending patch package。分布式锁和完整 audit log schema 仍属于后续任务。

终端默认只显示 Agent 的最终回答、审批和失败/取消提示；工具完整输出不会直接显示。API 密钥、Authorization 头、密码/令牌字段和可识别的密钥赋值都会被脱敏。

应用启动、会话恢复、运行开始和完成会写入 `logs/application.jsonl`。每次有文本输出的工具调用都会将脱敏全文保存到 `artifacts/<session_id>/<run_id>/`；trace、transcript、session 事件和 checkpoint 仅记录摘要、字符数与 artifact 相对路径。MySQL 模式下待回写补丁持久化在数据库 `patches` 表，不写入 `.coding-agent/` 目录。

## 扩展路线

`CodingAgent` 是 FastAPI 和未来 UI 的入口，因此两者无需复制运行循环。`SessionStore`、`TraceStore`、`ArtifactStore` 与 `MemoryStore` 是稳定协议。当前已提供 MySQL 会话存储实现、MySQL-backed API approval queue、MySQL-backed pending patch package、Alembic 迁移和 CLI/API 运行时配置切换；`MemoryRecord` 已为后续经人工审核的记忆检索实现预留接口。下一阶段可将 Docker CLI 执行器替换为远程隔离运行时，或补齐 Redis active run/session lock；多 Agent、Web/TUI/IDE 客户端、Milvus 和 Redis 尚未实现。

## 评测

`coding_agent.evals` 提供可重复的场景和报告契约。测试覆盖未授权写入/Shell 拒绝、敏感文件拒绝、快照过滤、危险补丁拒绝、补丁回写的并发改动检测、验证变更丢弃以及会话存储的基本行为。报告会明确区分自动化测量指标与需要人工审核的项目，绝不伪造“代码质量评分”。
