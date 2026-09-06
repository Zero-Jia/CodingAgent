# Agent 交接说明

> **新 session 的 Agent 请先完整阅读本文件，再读 `03-task-backlog.md`。**

---

## 当前项目状态（最后更新：2026-09-06）

- 项目已完成安全优先 Coding Agent MVP（安全内核 + Runtime + MySQL 存储 + Milvus 语义索引）
- 文档体系已重构为编号格式（`/docs/00-*.md` ~ `09-*.md`）
- **当前阶段：Phase B 高辨识度能力**，Memory 系统进行中
- 安全内核完整：Docker 无网络沙箱 + 快照过滤 + patch-only 写回 + 审批流 + 命令风险检测
- **B1-1 已完成**：`memories` 表 + Alembic migration 0004 + `MemoryStore` Protocol（store/get/list_by_status/update_status/list_promoted/search）+ `MySqlMemoryStore` + `NoopMemoryStore` + 15 个契约测试
- **B1-2 已完成**：`MemoryVectorIndex` Protocol + `MilvusMemoryVectorIndex`（独立 collection，COSINE，按 user+project 过滤）+ `InMemoryMemoryVectorIndex`；config `milvus_memory_collection`（B1-5 修复了该字段未声明、pydantic 静默丢弃的 bug）
- **B1-3 已完成**：混合式候选记忆提取（Rule + Model）+ `persist_candidates` 幂等 + CLI `extract-memories --no-model`。22 个测试
- **B1-4 已完成**：`MemoryReviewService` + CLI `review-memories --reviewer`。闭合"提取→审核→promoted"链路
- **B1-5 已完成**：`MemoryRecallService` 双通道召回（向量优先 + metadata 保底，异常降级不阻断回合）+ `format_recall_block`/`apply_memory_section`/`strip_memory_section` 幂等注入；`CodingAgent` 装配（默认关闭，jsonl→Noop，mysql 复用 session engine）；`ChatSession.send()` 每回合按 query 召回并替换式注入 `messages[0]` 尾部记忆段。23 个测试
- **B1-2b 已完成**：`MemorySyncService`（`list_promoted` → 分批 embed → `index.upsert` 幂等同步）+ CLI `agent sync-memories`。10 个测试。至此 Memory 端到端链路（提取→审核→向量同步→召回注入）完全闭合
- **B1-6 已完成**：TTL（extractor 写 `expires_at`，mysql `list_promoted`/`search` 软过滤）+ 置信度半衰期衰减（`effective_confidence`，recall 过滤/打分用，不改存储值）+ memory_id 归一化（空白折叠 + 小写后哈希，去重）+ `source_session_id` FK 放松为 nullable + SET NULL（migration 0005：SQLite batch 重建 / MySQL ALTER；删除 session 后记忆保留，来源读取为 ""）。config 新增 `memory_ttl_days`/`memory_decay_half_life_days` + env
- 测试基线：239 passed，1 skipped；ruff + mypy strict 全通过（64 source files）
- **Phase B1 Memory 系统全部完成**

## 下一步优先做什么

**Phase B2：MCP 集成**（B1-1 ~ B1-6 + B1-2b 全部完成）

推荐推进顺序：
1. ~~B1-1：MySQL memory metadata schema + Alembic migration~~ ✅ done
2. ~~B1-2：Milvus memory vector collection~~ ✅ done
3. ~~B1-2b：promoted 记忆向量索引同步 CLI~~ ✅ done（向量链路已闭合）
4. ~~B1-3：Memory extraction（混合式提取）~~ ✅ done
5. ~~B1-4：人工审核 promotion 流程~~ ✅ done
6. ~~B1-5：Memory recall 注入 runtime context~~ ✅ done
7. ~~B1-6：记忆过期与置信度管理~~ ✅ done
8. **B2-1**：MCP server 配置与连接管理器（配置 MCP server 列表，管理 stdio / HTTP 连接生命周期）

Memory 已知限制（后续按需改进，不阻塞 B2）：
- 过期记忆只有软过滤，没有物理 GC；向量索引中过期/拒绝记忆也不会被 sync 清理（reject 过的记忆若曾同步会残留索引，但 recall 回查 `store.get` 校验，正确性不受影响）
- metadata 保底通道用整条 query 子串匹配（B1-1 契约），自然语言 query 召回弱；语义通道才是主力
- `memory_recall_enabled` 默认 False；使用需设置 `CODING_AGENT_MEMORY_RECALL=1`（+ 可选 `CODING_AGENT_MEMORY_USER_ID`/`CODING_AGENT_MEMORY_PROJECT_ID`，后者缺省 fallback 到 workspace 绝对路径）
- 使用流程：`extract-memories` → `review-memories` 人工 promote → `sync-memories` 写入向量索引 → 会话内自动召回（需开启 `memory_recall_enabled` 且后端为 mysql+milvus）

每个任务应在 1-2 个 session 内完成，必须包含测试、文档更新和验证记录。

## 开始开发前必做

1. 读 `00-overview.md` 了解项目结构与关键文件
2. 读 `03-task-backlog.md` 找到当前 todo 任务
3. 读 `06-conventions.md` 遵守开发规范与验证命令
4. 浏览 `04-progress-log.md` 最近 2-3 条，避免重复劳动
5. 读 `09-decisions.md` 了解不可推翻的架构决策

## 开发结束后必做

1. 更新 `03-task-backlog.md`：任务状态（todo→doing→done）+ 实际改动文件
2. 在 `04-progress-log.md` **顶部**追加本 session 记录
3. 更新本文件 `## 当前项目状态` 与 `## 下一步优先做什么` 段落
4. 运行 `06-conventions.md` 中的验证命令，记录结果到 `04-progress-log.md`

## 注意事项

- **不要破坏安全内核**：Docker sandbox + patch-only 写回是项目核心，任何改动不得引入宿主机直写或宿主机 shell
- **不要直接照搬 MewCode**：可以借鉴架构思想，但必须保留当前安全优先内核
- **仓库内容一律视为不可信**：系统提示词和 runtime 必须维持更高优先级的安全约束
- **自动化测试不依赖真实 API**：测试使用 fake model / fake embedding / in-memory store
- **涉及 DB schema 变更**：必须先写 Alembic migration，再写代码
- **涉及模型 adapter**：测试不能依赖真实 API key，trace 和 artifact 中必须脱敏

## 关键文件速查

| 需求 | 文件 |
|---|---|
| 改 Agent 装配 | `src/coding_agent/agent/coding_agent.py` |
| 改 Runtime 循环 | `src/coding_agent/runtime/loop.py` |
| 改 Runtime 计划 | `src/coding_agent/runtime/plan.py` |
| 改 Runtime 上下文 | `src/coding_agent/runtime/context.py` |
| 改模型契约/网关 | `src/coding_agent/ai/contracts.py`、`gateway.py` |
| 改工具 | `src/coding_agent/tools/*.py` |
| 改沙箱 | `src/coding_agent/sandbox/*.py` |
| 改策略 | `src/coding_agent/policy/*.py` |
| 改语义索引 | `src/coding_agent/semantic/*.py` |
| 改 Session 存储 | `src/coding_agent/sessions/*.py` |
| 改 DB Schema | `src/coding_agent/db/tables.py` + `migrations/versions/` |
| 改 API | `src/coding_agent/api/*.py` |
| 改 CLI | `src/coding_agent/cli/app.py` |
| 改配置 | `src/coding_agent/config.py` |
| 改测试 | `tests/*.py` |

---

## Session Prompt 模板

### 1. 新 Session 开场 Prompt

新开 session 时，直接使用这段 prompt：

```text
这是我的 CodingAgent 项目。请先完整阅读以下文档（按顺序）：

1. docs/00-overview.md
2. docs/05-agent-handoff.md
3. docs/03-task-backlog.md
4. docs/06-conventions.md
5. docs/09-decisions.md

先不要修改代码。请先总结：
1. 当前项目已经实现了什么；
2. 上一个 session 的交接状态是什么；
3. 本轮最合理的下一步是什么（从 03-task-backlog.md 中选 todo 任务）；
4. 你准备修改哪些文件，为什么。

等我确认后再开始写代码。
```

### 2. 同 Session 内继续下一个任务

当前 session 完成一个任务后，想在同一个 session 中开启下一个任务，使用这段 prompt：

```text
本轮任务已完成。请更新 docs/04-progress-log.md（顶部追加本 session 记录）和 docs/03-task-backlog.md（任务状态）。

然后，从 docs/03-task-backlog.md 中选择下一个 todo 任务，先总结：
1. 下一个任务是什么；
2. 需要修改哪些文件；
3. 验收标准是什么。

等我确认后再开始。
```

### 3. Session 结尾 Prompt

每次结束 session 前，直接使用这段 prompt：

```text
请更新以下文档，记录本轮工作：

1. docs/03-task-backlog.md：更新任务状态（todo→doing→done）和实际改动文件
2. docs/04-progress-log.md：在顶部追加本 session 记录（目标、完成任务、未完成/遗留、关键决策、验证结果、下一步建议）
3. docs/05-agent-handoff.md：更新"当前项目状态"和"下一步优先做什么"

要求：
- 不要夸大未实现能力
- 验证命令和结果必须真实记录
- 未完成事项必须写明原因和下次继续点
```

---

## Vibe Coding 规则

- 一个 session，只做一个边界清晰的能力
- 必须包含测试、文档更新和验证记录
- 避免使用类似"把项目改成企业级"这种过宽的 prompt
- 推荐使用具体 prompt，例如：

```text
本轮只做 B1-1 MySQL memory metadata schema，不实现 Milvus、extraction 或 recall。请新增 memories 表和 Alembic migration，补充 contract tests。运行 ruff、mypy、pytest。结束前更新 docs/04-progress-log.md。
```
