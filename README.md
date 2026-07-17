# Coding Agent

这是一个面向 Windows 的本地 Python 编程 Agent。项目以安全优先：读取范围限定在工作区内；写入和 PowerShell 执行需要明确授权；默认拒绝访问密钥、网络下载命令、依赖安装、破坏性命令、`git commit` 和 `git push`。

## 快速开始

请使用 Python 3.12 和 uv。`uv.lock` 已固定运行时和开发依赖版本。

```powershell
uv sync --all-groups
$env:DEEPSEEK_API_KEY = "你的本地密钥"
uv run agent chat --workspace .
```

`agent chat` 是连续会话模式：同一个终端中的每次提问都会复用会话上下文。输入 `/help` 查看命令，输入 `/exit` 保存并退出。

恢复已退出的会话：

```powershell
uv run agent chat --workspace . --resume <会话ID>
```

仍可执行一次性任务：

```powershell
uv run agent run "检查仓库并建议最小修复" --workspace . --model deepseek-chat
```

`edit` 只有在 `old_text` 恰好出现一次时才会修改文件。不带 `--allow-write` 或 `--allow-shell` 时，交互式运行会请求确认；使用 `--non-interactive` 时，未授权操作会立即被拒绝。

## DeepSeek 配置

将 `.env.example` 中的变量名配置到本地进程环境（不要提交 `.env`）。可选变量：

```powershell
$env:DEEPSEEK_API_KEY = "你的本地密钥"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
$env:DEEPSEEK_MODEL = "deepseek-chat"
```

适配器使用兼容 OpenAI 的 DeepSeek Chat Completions SSE 协议。所有 Agent 运行均使用真实 DeepSeek 配置。

## 架构

`ai` 提供供应商无关契约和 DeepSeek 适配器；`runtime` 只通过模型和工具协议运行事件循环；`workspace`、`policy`、`tools`、`sessions`、`tracing` 和 `memory` 是独立服务。`agent.CodingAgent` 负责装配并提供稳定 Python API；`cli` 仅负责终端输入和输出。

连续聊天会话在内存中保留模型消息历史，每条用户消息产生一个新的运行 ID；会话 ID 保持不变。工具调用和工具结果会进入消息历史，以便模型在下一次提问中理解刚完成的操作。

## 数据与安全

每个工作区的数据均保存在 `.coding-agent/` 下：

- `sessions/<session_id>.jsonl`：不可变的会话事件历史（`agent resume`、`agent status`）
- `checkpoints/<session_id>.json`：连续对话的可恢复模型上下文（`agent chat --resume`）
- `traces/<session_id>/<run_id>.jsonl`：关联运行过程的结构化追踪
- `artifacts/<session_id>/<run_id>/`：可选的脱敏产物
- `logs/application.jsonl`：应用诊断日志

默认追踪等级为 `redacted`。API 密钥、Authorization 头、密码/令牌字段和可识别的密钥赋值都会被脱敏。较长工具输出只保存哈希、长度与简短预览。

## 扩展路线

`CodingAgent` 是 FastAPI 和未来 UI 的入口，因此两者无需复制运行循环。`SessionStore`、`TraceStore`、`ArtifactStore` 与 `MemoryStore` 是稳定协议。未来 MySQL 后端可映射为 `sessions`、`runs`、`events` 和 `artifacts` 表，而不改动运行时；`MemoryRecord` 已为后续经人工审核的记忆检索实现预留接口。本初版有意不实现 Docker 沙箱、多 Agent、Web/TUI/IDE 客户端和真实 MySQL。

## 评测

`coding_agent.evals` 提供可重复的场景和报告契约。测试覆盖歧义编辑拒绝、未授权写入/Shell 拒绝、敏感文件拒绝和会话存储的基本行为。报告会明确区分自动化测量指标与需要人工审核的项目，绝不伪造“代码质量评分”。
