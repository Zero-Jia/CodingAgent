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

补丁只保留在当前 Agent 运行时内，退出或恢复会话后必须重新运行沙箱命令。回写目标必须是 Git 工作树，既有文件应由 Git 跟踪。当前版本有意拒绝二进制补丁、子模块、符号链接、可执行权限变更、重命名和复制；这些情况需要人工处理。任何补丁若触及敏感或内部目录，或快照后的原文件已被其他进程改动，都会拒绝写回。

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

## FastAPI 服务

当前提供最小 FastAPI 服务入口，用于后续 Web UI、审批控制台和后台 worker 复用同一套 runtime：

- `GET /health`：健康检查和当前 workspace/model 摘要。
- `POST /v1/sessions`：创建或恢复会话。
- `GET /v1/sessions`：列出本工作区会话摘要。
- `GET /v1/sessions/{session_id}`：读取单个会话摘要。
- `POST /v1/sessions/{session_id}/messages/stream`：发送消息并以 Server-Sent Events 返回 `AgentEvent`。
- `POST /v1/runs/{run_id}/cancel` 和 `POST /v1/sessions/{session_id}/cancel`：取消活跃运行。

API 层只封装 `CodingAgent` 和 `ChatSession`，不复制 agent loop，也不新增宿主机写入或宿主机 shell 能力。服务进程会复用现有 session lock，避免与 CLI 或另一个 API 进程同时写入同一会话。当前尚未实现 Web UI、认证、持久化审批队列和多进程分布式锁；生产部署前需要补齐这些能力。

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
- `logs/application.jsonl`：应用诊断日志

终端默认只显示 Agent 的最终回答、审批和失败/取消提示；工具完整输出不会直接显示。API 密钥、Authorization 头、密码/令牌字段和可识别的密钥赋值都会被脱敏。

应用启动、会话恢复、运行开始和完成会写入 `logs/application.jsonl`。每次有文本输出的工具调用都会将脱敏全文保存到 `artifacts/<session_id>/<run_id>/`；trace、transcript、session 事件和 checkpoint 仅记录摘要、字符数与 artifact 相对路径。待回写补丁不会持久化到这些目录。

## 扩展路线

`CodingAgent` 是 FastAPI 和未来 UI 的入口，因此两者无需复制运行循环。`SessionStore`、`TraceStore`、`ArtifactStore` 与 `MemoryStore` 是稳定协议。未来 MySQL 后端可映射为 `sessions`、`runs`、`events` 和 `artifacts` 表，而不改动运行时；`MemoryRecord` 已为后续经人工审核的记忆检索实现预留接口。下一阶段可将 Docker CLI 执行器替换为远程隔离运行时，或为补丁引入持久化的人工审批队列；多 Agent、Web/TUI/IDE 客户端和真实 MySQL 尚未实现。

## 评测

`coding_agent.evals` 提供可重复的场景和报告契约。测试覆盖未授权写入/Shell 拒绝、敏感文件拒绝、快照过滤、危险补丁拒绝、补丁回写的并发改动检测、验证变更丢弃以及会话存储的基本行为。报告会明确区分自动化测量指标与需要人工审核的项目，绝不伪造“代码质量评分”。
