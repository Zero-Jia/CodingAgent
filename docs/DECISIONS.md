# CodingAgent 技术决策记录

本文档记录后续 session 应该保留的架构决策。除非有明确理由，否则不要轻易推翻这些决策。

## 决策 1：保留沙箱执行和 patch-only 写回作为核心安全模型

状态：已接受。

背景：

很多本地 coding agent 会直接给模型宿主机 shell 和文件写权限。这种方式能力强，但对企业场景风险很高。当前项目选择让命令进入 Docker 沙箱执行，并且只通过经过校验的 patch 写回宿主机。

决策：

模型不能获得默认宿主机 shell 权限或宿主机写权限。宿主机修改必须经过沙箱生成 Git patch 和 `apply_patch` 审批。

影响：

- 默认行为更安全。
- 审计链路更清楚。
- 快照、patch 校验、审批会带来额外实现复杂度。
- 二进制文件、重命名等变更在被明确设计前需要人工处理。

## 决策 2：使用快照，而不是挂载宿主工作区

状态：已接受。

背景：

把宿主工作区 mount 到 Docker 容器中很方便，但容器会和宿主文件系统产生直接关系。

决策：

沙箱通过 stdin 接收过滤后的 tar snapshot，不挂载宿主工作区。

影响：

- 隔离更强。
- 更容易排除敏感文件。
- 会有额外复制开销。
- Pending patch 只对当前 runtime 快照有效。

## 决策 3：仓库内容一律视为不可信

状态：已接受。

背景：

仓库文件中可能包含 prompt injection、恶意指令、伪造工具输出或误导性任务说明。

决策：

仓库内容、issue 文本、命令输出、项目规则都属于不可信上下文。系统提示词和 runtime 必须维持更高优先级的安全约束。

影响：

- 项目规则可以使用，但必须与 agent 安全策略兼容。
- 工具输出在持久化前应该摘要化和脱敏。
- 未来 Web/API 客户端也必须保留同样的信任边界。

## 决策 4：先使用 JSONL，再通过协议增加 MySQL

状态：已接受。

背景：

当前项目偏本地和面试展示。JSONL 容易查看和调试。企业部署则需要结构化查询、多用户归属、审批状态和审计报表。

决策：

保留 JSONL 作为本地开发存储。平台化阶段在现有 store protocol 后面增加 MySQL 实现。

影响：

- 本地模式保持简单。
- 数据库迁移可以渐进完成。
- Store protocol 必须保持稳定并可测试。

## 决策 5：CLI 保持为薄客户端

状态：已接受。

背景：

项目最终会支持 CLI、API、Web UI 和 worker。

决策：

业务逻辑应保留在 `CodingAgent`、`AgentRuntime`、stores、tools、policy 和 sandbox 模块中。CLI 只负责解析参数、收集审批、渲染事件。

影响：

- FastAPI 和 WebSocket 层可以复用同一套 runtime。
- CLI 特有格式不能泄漏到核心模块。
- 未来 TUI/Web UI 应消费 `AgentEvent`，不要复制 runtime 逻辑。

## 决策 6：企业级能力必须渐进增加

状态：已接受。

背景：

MewCode 已经实现了 TUI、MCP、Skills、Hooks、memory、sub-agent、teams、worktrees 等高级能力。一次性复制这些能力会让当前项目变得脆弱。

决策：

可以借鉴 MewCode 的架构思想，但必须围绕当前安全优先内核渐进实现。

影响：

- 优先做 model gateway、context manager、mock LLM tests 和 CI。
- MCP、Skills、Hooks、memory、多 agent 应在核心 runtime 测试更充分后再加入。
- 不能用直接本地写文件工具替代 sandbox-patch 链路。
