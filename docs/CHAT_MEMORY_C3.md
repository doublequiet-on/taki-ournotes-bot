# 群级参与记忆：C3 存储合同与恢复边界

本批扩展已有 ChatStore，不改用量、额度或发送账本。只保存 Taki 已参与的触发片段和成功送达的回复，所有查询由宿主注入当前 `(instance_id, app_id, platform, kind, conversation_id)`。显示名只是原群内材料，同名用户仍按可信 ID 区分；模型参数不得指定另一个群或实例。

## 实际结构

`application/chat_store.py` 保留 C1 的 `runs`、`events` 列和 `meta` 两键：`schema=chat/1`、实例/应用 identity。增量表版本通过 `PRAGMA user_version=2` 表示；未知后续版本拒绝打开，不自动重写。旧 C1 可继续读取旧审计表，不读取新记忆表。

新增结构化 `memory_messages`，保存消息 ID、角色、发言者 ID、显示名、源时间、文字和引用；`memory_run_sources` 记录审计片段重复了哪些来源；`memory_dependencies` 记录已送达回复实际参与的上下文来源；`memory_summaries` 与 `memory_summary_sources` 保存带来源的派生摘要；FTS5 trigram 索引支持日中子串检索，小于三字的词使用同群 `instr` 筛选。索引、源消息和派生关系由同一库拥有，没有另一个可漏删的向量库或导出缓存。

`begin(work, messages, participating=True)` 兼容原入口。显式不参与时立即返回；真实主动候选应由宿主在确认参与后才调用。用户消息来自规范化上下文；正文中的 role 标签不会赋权。上下文里声称是 assistant 的消息，只有在本库已有成功 ACK 时才被承认为 assistant 记忆。`generated` 仍保存可见生成状态用于审计，不将其当作已发历史。

`event(..., kind='delivery', data)` 仅在 `status=success` 且 `allow_context=True` 时建立 assistant 记录。失败、不确定、未发及撤销上下文许可均不能建立已说历史，多段回复逐段处理。C1 历史回填也遵守相同 ACK 约束；C1 没有交付时间戳的旧记录按更早的 run 源时间到期，避免迁移续期。回填分两遍关联来源，不把最早源时间误当作 run 创建次序；回退 C1 期间新增的旧格式片段在再次升级时仍会发现并回填。

## 公共接线方法

| 方法 | 返回与限制 |
|---|---|
| `search(scope, query, limit=8, before_ms=None)` | 返回 `status`、普通消息列表、`source_kind=group_dialogue` 和 revision；query 最多 120 字，limit 最多 20。FTS 操作符作为普通文本。 |
| `read(scope, message_ids)` | 返回仍保留的当前群消息；最多 48 个 ID，跨群不存在状态不泄露另群内容。 |
| `recent(scope, limit=48, max_bytes=24576, since_ms=None)` | 按实际源时间排序的有界规范化消息列表，用于重启恢复。 |
| `put_summary(scope, summary_id, text, source_message_ids)` | 所有来源须仍有效，返回明确来源及最早源的到期时刻；不生成摘要、不调用模型。 |
| `summaries(scope, query='', limit=8)` | 返回带来源 ID、`source_kind=derived_summary` 和绝对到期时刻的派生摘要。 |
| `delete_messages(scope, ids)` / `clear_scope(scope)` | 纯存储管理方法，宿主必须先执行既有管理员鉴权；模型普通工具不得执行。 |
| `status(scope)` | 无正文的条数、记忆版本、revision、保留时间与删除标记统计。 |
| `deletion_state()` / `restore_deletions(state)` | 导出或合并独立删除状态，不能通过导入旧状态撤销已生效删除。 |

`ChatContext(store=store)` 是可选接线；先创建 store，再创建 context。每个 scope 按 store revision 更新缓存。默认自动上下文仍是最近十五分钟、48 条、24 KiB，实例缓存仍受八 MiB/1024 群限制；跨更早时间的月度记忆通过显式检索或引用读取。明确引用可带入仍在 30 日保留期内的旧消息，而不永久扩大自动窗口。首次当前问题在尚未持久化时不会被恢复覆盖。

SQLite 连接归创建它的事件循环线程所有，方法同步执行；不要将它直接交给 `asyncio.to_thread`。摘要模型任务须由主运行层使用原模型 profile、合法群级主体、独立许可/额度和调度进行；此模块没有假摘要或额外模型调用。

## 到期、删除与恢复

每次读取前清扫源消息时间超过三十日的记录；创建摘要或再次引用不会刷新来源到期。源删除/到期会清除依赖它的摘要、FTS 行、已送达派生回复和包含这些正文的 C1 审计片段。独立的其他群、其他来源消息和额度事实不受影响。

显式删除首先将无正文墓碑写入同目录 `chat-v1.deletions.json`（具体前缀随数据库文件名），原子替换成功后才清理数据库。清群使用删除时间截点；新截点之后的真实新消息仍可保留，旧消息和旧快照不能重新入库。删除标记不会随清理正文一起删除。损坏、身份不符或无效的删除清单导致启动失败，不能静默忽略。

备份恢复时必须保留独立删除清单，并在打开恢复库时与备份内墓碑取并集；构造器在任何检索可用前应用清单和到期清扫。外部维护可以显式传入 `restore_state`，同样先合并而不替代当前删除状态。备份本体有单独保留期，恢复删除门禁并不等于已经擦除了所有旧备份；需按实际备份政策另行销毁。

旧 C1 不了解删除侧车，故回退代码不能顺带用旧代码直接恢复备份。普通代码回退保留当前已清理数据库即可；需要恢复历史备份时先通过本版删除合并/到期清扫，再考虑成对回退。不得删除侧车或恢复额度数据库来让结果看起来一致。

## 已做的离线验证

目标测试位于已有 `tests/test_continuation.py::ChatMemoryStoreTests`：成功 ACK 与假 assistant、同名不同 ID、跨群/跨实例、日中长短词、源删除级联、摘要到期、侧车约束的备份恢复、清群后的新消息、损坏清单、C1 迁移/回退元数据与乱序/再升级回填、未来版本拒绝、重启上下文、缓存失效和旧引用。使用真实临时 SQLite 与侧车，不接模型、QQ 或生产数据。

Windows/Python 3.12/SQLite 3.42：`python -B -m unittest test_continuation.ChatMemoryStoreTests -q` 最新 15 项通过；此前与原 C1 `test_console_policies.ChatAgentTests` 联跑 30 项通过（当时 C3 为 14 项），最后增加的再升级回填用例已单独复验。`python -B scripts/check_architecture.py` 通过。本分支未运行全仓回归，由集成分支接线后执行；未做真实群级数据恢复或线上管理员操作。
