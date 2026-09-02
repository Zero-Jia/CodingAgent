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

日期：2026-09-02

环境：

- 工作区：`D:\Software\CodingAgent`
- Shell：PowerShell
- uv 环境中观察到的 Python 版本：3.13.3

结果：

本轮增加 MySQL-backed pending patch package。JSONL 本地模式仍保留进程内 `PatchRegistry`；配置 MySQL session store 后，沙箱生成的 pending patch 会把 patch text、patch sha256、changed files、snapshot hashes、diff preview、状态和 session/run 关联写入 `patches` 表。`apply_patch` 审批详情可从持久化 package 读取；审批通过后仍会重新执行结构、路径、文件 hash 和 `git apply --check` 校验，成功标记 `applied`，失败标记 `invalidated`。新增 Alembic migration `0003_add_persistent_patch_packages` 和跨 registry patch 回归测试后，验证结果如下。

```text
uv --cache-dir .uv-cache run ruff check
All checks passed!
```

```text
uv --cache-dir .uv-cache run mypy
Success: no issues found in 50 source files
```

```text
uv --cache-dir .uv-cache run mypy src
Success: no issues found in 50 source files
```

定向 sandbox patch 测试：

```text
uv --cache-dir .uv-cache run pytest tests\test_sandbox.py --basetemp .codex-test-tmp-patch-sandbox -p no:cacheprovider
18 passed, 1 skipped
```

定向 session store 和 migration 测试：

```text
uv --cache-dir .uv-cache run pytest tests\test_session_store_contract.py --basetemp .codex-test-tmp-patch-schema -p no:cacheprovider
8 passed
```

定向 API approval/patch 测试：

```text
uv --cache-dir .uv-cache run pytest tests\test_api.py --basetemp .codex-test-tmp-patch-api -p no:cacheprovider
16 passed
```

定向 mypy：

```text
uv --cache-dir .uv-cache run mypy src\coding_agent\sandbox\patches.py src\coding_agent\tools\sandbox.py src\coding_agent\runtime\loop.py src\coding_agent\agent\coding_agent.py tests\test_sandbox.py tests\test_api.py
Success: no issues found in 6 source files
```

定向 ruff：

```text
uv --cache-dir .uv-cache run ruff check src\coding_agent\sandbox\patches.py src\coding_agent\tools\sandbox.py src\coding_agent\runtime\loop.py src\coding_agent\agent\coding_agent.py src\coding_agent\db\tables.py tests\test_sandbox.py tests\test_api.py tests\test_session_store_contract.py migrations\versions\0003_add_persistent_patch_packages.py
All checks passed!
```

全量测试：

```text
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-patch-final -p no:cacheprovider
129 passed, 1 skipped
```

全量类型检查：

```text
uv --cache-dir .uv-cache run mypy
Success: no issues found in 50 source files
```

```text
uv --cache-dir .uv-cache run mypy src
Success: no issues found in 50 source files
```

全量 lint：

```text
uv --cache-dir .uv-cache run ruff check
All checks passed!
```

本轮自动化测试仍不依赖真实 DeepSeek API，也不启动 Docker。数据库后端自动化测试使用 SQLAlchemy SQLite URL 覆盖同一 `MySqlSessionStore`、`MySqlApprovalStore` 和 `MySqlPatchStore` 行为、API 装配、checkpoint、summary、events、transcript、Alembic migration、跨 registry approval 决议、跨 registry patch 应用、工作区漂移失效和配置错误脱敏，并使用 SQLAlchemy MySQL dialect 编译测试覆盖 MySQL DDL 兼容性。本轮未对本机真实 MySQL 执行 `0003` migration。

历史验证摘要：

```text
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-sqlalchemy-final2 -p no:cacheprovider
101 passed, 1 skipped
```

```text
uv --cache-dir .uv-cache run pytest tests/test_api.py --basetemp .codex-test-tmp-api -p no:cacheprovider
7 passed
```

```text
uv --cache-dir .uv-cache run pytest tests/test_command_risk.py tests/test_policy_and_tools.py tests/test_runtime.py --basetemp .codex-test-tmp-risk -p no:cacheprovider
42 passed
```

当前仓库 snapshot smoke test：

```text
uv --cache-dir .uv-cache run python -c "<SnapshotService check>"
True
True
```

实际 Docker 沙箱 smoke test：

```text
uv --cache-dir .uv-cache run python -c "<DockerSandboxExecutor check>"
success
0
......                                                                   [100%]
6 passed in 0.56s
```

此前定向 Plan Mode/runtime 测试：

```text
uv --cache-dir .uv-cache run pytest tests/test_plan_mode.py tests/test_runtime.py --basetemp .codex-test-tmp-plan -p no:cacheprovider
14 passed
```

本轮改用新的项目内临时目录运行全量测试：

```text
uv --cache-dir .uv-cache run pytest --basetemp .codex-test-tmp-risk-final2 -p no:cacheprovider
88 passed, 1 skipped
```

在完成 command risk detector 后，以上历史验证集已重新执行；此前额外 Docker smoke test 已确认沙箱 `/workspace` 中包含 `token_usage.py`，并可运行 `tests/test_plan_mode.py`。

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
