# CodingAgent 威胁模型

本文档记录当前 `CodingAgent` MVP 的安全边界、主要威胁和已有控制措施。它描述的是当前真实实现，不把路线图能力写成已完成能力。

## 保护资产

- 宿主机工作区源码和 Git 工作树。
- 本地密钥、token、`.env*`、私钥和凭据文件。
- `.coding-agent/` 下的 session、checkpoint、transcript、trace、artifact 和 application log。
- 沙箱生成的 pending patch、diff preview 和 changed files。
- Docker 沙箱镜像及其离线依赖环境。

## 信任边界

以下输入一律视为不可信：

- 用户提供的自然语言任务。
- 仓库文件、README、AGENTS.md、CONTRIBUTING.md、issue 文本和注释。
- 模型输出、模型生成的工具参数和模型生成的 patch。
- 沙箱命令 stdout、stderr、测试输出和 Git diff。

以下组件是当前强制边界：

- `PolicyEngine` 决定工具是否允许、拒绝或需要审批。
- `WorkspacePathPolicy` 统一拒绝敏感路径、内部路径和越界路径。
- `SnapshotService` 只把允许的工作区文件复制进沙箱快照。
- `DockerSandboxExecutor` 在一次性、无网络、无宿主挂载的容器中执行命令。
- `PatchRegistry` 校验沙箱 patch 后，才允许申请回写宿主工作区。

## 威胁与控制

### 仓库 Prompt Injection

威胁：

仓库文件可能包含伪造的系统指令、要求泄露密钥的文本、误导性测试结论或诱导模型绕过审批的内容。

当前控制：

- 系统提示明确声明仓库内容和命令输出是不可信数据。
- Runtime 只根据注册工具和 policy 执行动作，不根据仓库文本改变安全边界。
- 项目规则可以作为上下文使用，但必须服从更高优先级的 agent 安全策略。

剩余风险：

- 模型仍可能被不可信文本影响推理质量。
- 当前没有专门的 prompt injection 检测器或隔离渲染层。

### 敏感文件泄露

威胁：

模型可能尝试读取 `.env`、SSH key、token、凭据文件、内部 trace 或 session checkpoint。

当前控制：

- `WorkspacePathPolicy` 拒绝 `.env*`、密钥后缀、精确凭据文件名、`.ssh`、`.git`、`.coding-agent`、缓存和虚拟环境目录。
- 敏感文件名规则避免使用宽泛的 `token`、`secret` 或 `credential` 子串匹配，防止 `token_usage.py` 这类普通源码被排除出沙箱快照。
- `read`、`search`、snapshot 和 patch 回写共用同一套路径策略。
- trace、artifact、transcript 和 checkpoint 写入前会做基础脱敏。

剩余风险：

- 脱敏规则是模式匹配，不等于完整 DLP。
- 已经进入普通源码文件的秘密仍可能被读取；后续需要 secret scanner。
- 未来新增敏感命名规则时必须同时覆盖误伤回归测试，确保沙箱验证看到的源码集与宿主工作区一致。

### 宿主机写入风险

威胁：

模型直接修改宿主工作区可能破坏代码、覆盖用户未提交修改或写入敏感路径。

当前控制：

- 默认 runtime 不暴露直接宿主机 `edit` 和 `write` 工具。
- 宿主机写回只能通过 `apply_patch` 处理沙箱生成的 pending patch。
- `PatchRegistry` 拒绝二进制 patch、子模块、文件模式变化、符号链接、可执行权限变化、重命名、复制、敏感路径和 changed-file 不一致。
- 回写前校验宿主文件 hash，发现快照后并发修改会拒绝。
- 回写前运行 `git apply --check`。

剩余风险：

- Pending patch 当前只存在于单个 runtime 内存中，没有持久化审批队列。
- 当前不支持二进制文件、重命名和复杂文件模式变更，需要人工处理。

### Shell 执行风险

威胁：

命令可能删除文件、下载恶意依赖、访问网络、读取宿主敏感文件或消耗大量资源。

当前控制：

- 宿主机 `shell` 明确被 policy 拒绝。
- `sandbox_shell` 和 `verify` 需要授权；非交互模式未授权时直接拒绝。
- 命令只在 Docker 沙箱中执行。
- Docker 使用 `--network none`、只读根文件系统、非 root 用户、`no-new-privileges`、drop capabilities、PID/CPU/内存/tmpfs/ulimit 限制。
- 沙箱通过 stdin 接收过滤后的 tar snapshot，不挂载宿主目录。

剩余风险：

- 当前没有 command risk detector。
- Docker 镜像尚未做 digest pinning、SBOM 或漏洞扫描。

### 沙箱逃逸假设

威胁：

恶意命令可能尝试利用容器、内核或镜像漏洞逃逸沙箱。

当前控制：

- 不挂载宿主工作区。
- 禁用网络。
- 使用非 root 用户。
- 使用只读根文件系统和 tmpfs 工作区。
- 删除 Linux capabilities 并启用 `no-new-privileges`。
- 每次命令使用一次性容器，执行结束后删除。

剩余风险：

- Docker 不是绝对安全边界；它依赖宿主 Docker Desktop、内核和镜像安全。
- 当前没有远程隔离执行集群、seccomp 自定义策略、镜像签名验证或运行时行为监控。

### 审批与审计边界

威胁：

用户可能批准了不理解的 patch，或系统记录不足导致事后无法复盘。

当前控制：

- 高风险工具会产生 approval request。
- `apply_patch` 审批详情包含 changed files 和脱敏 diff preview。
- session、trace、artifact、transcript 和 application log 会保存在 `.coding-agent/`。
- 完整工具输出写入脱敏 artifact，trace 中保留摘要、字符数和 hash。

剩余风险：

- 当前审批在 CLI 中完成，没有 Web 审批页、多级审批或 reviewer comment。
- 审计数据当前是本地 JSONL，不是集中式不可篡改审计日志。
- 脱敏不应被视为合规级秘密发现系统。

## 未实现的安全能力

以下能力属于路线图，当前不能对外声称已经实现：

- GitHub Actions 以外的完整 CI/CD 质量门禁。
- command risk detector。
- RBAC、组织级 policy 和多租户隔离。
- PostgreSQL 审计存储。
- 远程沙箱执行器。
- Docker 镜像 digest pinning、SBOM 和漏洞扫描。
- Web patch 审批界面。
- MCP、Skills、Hooks 的权限和审计集成。
- 长期 memory 的人工审核和 recall 注入。
