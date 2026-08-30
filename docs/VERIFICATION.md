# CodingAgent 验证说明

本文档记录如何验证项目，以及最近一次观察到的验证结果。

## 推荐本地命令

在受限 Codex Windows 账户下运行时，建议使用项目内 cache 和临时目录：

```powershell
uv --cache-dir .uv-cache run ruff check
uv --cache-dir .uv-cache run mypy
uv --cache-dir .uv-cache run mypy src
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider
```

原因：

- `uv run ...` 可能尝试使用 `C:\Users\HP\AppData\Local\uv\cache`，当前沙箱账户可能无权访问。
- `pytest` 可能尝试创建 `C:\Users\HP\AppData\Local\Temp\pytest-of-HP`，当前沙箱账户也可能无权访问。
- `--basetemp .codex-test-tmp` 可以让 pytest 临时文件留在项目目录内。
- `-p no:cacheprovider` 可以避免 pytest 写入已有 `.pytest_cache`，因为该目录可能属于另一个 Windows 账户。

## 最近一次验证结果

日期：2026-08-30

环境：

- 工作区：`D:\Software\CodingAgent`
- Shell：PowerShell
- uv 环境中观察到的 Python 版本：3.13.3

结果：

```text
uv --cache-dir .uv-cache run ruff check
All checks passed!
```

```text
uv --cache-dir .uv-cache run mypy
Success: no issues found in 36 source files
```

```text
uv --cache-dir .uv-cache run mypy src
Success: no issues found in 36 source files
```

由于旧 `.codex-test-tmp` 目录存在权限残留，本轮改用新的项目内临时目录运行全量测试：

```text
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-stream-all -p no:cacheprovider
30 passed, 1 skipped
```

在完成 P1 `真正的 Streaming Delta` 任务后，以上验证集已重新执行，结果通过。本轮新增的 runtime streaming 测试不依赖真实 DeepSeek API，也不启动 Docker。

定向 runtime 测试：

```text
uv --cache-dir .uv-cache run pytest tests/test_runtime.py --basetemp .codex-test-tmp-stream -p no:cacheprovider
7 passed
```

## 已知本地环境问题

### uv cache 权限

直接运行：

```powershell
uv run pytest
```

可能失败并出现：

```text
Failed to initialize cache at C:\Users\HP\AppData\Local\uv\cache
```

请改用：

```powershell
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider
```

### Git dubious ownership

在 Codex 中运行 `git status` 可能失败：

```text
fatal: detected dubious ownership in repository at 'D:/Software/CodingAgent'
```

原因：

仓库属于用户的 Windows 账户，而 Codex 可能使用沙箱账户运行。

除非用户明确要求，不要修改全局 Git 配置。

## 后续 Session 验证要求

只修改文档时：

- 读回修改后的文件。
- 如果文档改动影响命令、架构承诺或示例，再运行测试。

修改 Python 代码时：

```powershell
uv --cache-dir .uv-cache run ruff check
uv --cache-dir .uv-cache run mypy
uv --cache-dir .uv-cache run mypy src
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp -p no:cacheprovider
```

修改 sandbox 相关代码时：

- 运行以上全部 Python 验证命令。
- 增加针对 snapshot filtering、patch validation 和 policy decision 的测试。
- 如果 Docker 行为发生变化，在镜像可用后运行手动 sandbox smoke test。

修改模型 adapter 时：

- 增加 mock/fake adapter 测试。
- 自动化测试不能依赖真实 API key。
- Trace 和 artifact 中必须脱敏 key 与 Authorization。

修改数据库相关代码时：

- 增加 migration 测试。
- 增加 repository contract tests。
- 除非任务明确要求移除，否则保留 JSONL 本地模式。
