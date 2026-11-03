# 历史分区、留存盘点与恢复合同（任务07）

基于03固定候选 `17212e5df76cf948aa990af571ed6daf5c66ec25`（PR #196），直接前置191为 `e1a86a6db1f327a6dd0cc53fd860c4ababf50923`。未重复叠加165。消费 [QUERY_DATA_CONTRACT](QUERY_DATA_CONTRACT.md) 的 C3/C4；本包不拥有DTO、产品身份、范围批准或恢复激活。

已只读核对05 `6b64e027b25d197981f456d6c87f32db54da6c51` 的 `docs/MANAGEMENT_SCOPE_SAFETY.md`，及09 `b29d69fd3d76b5be81610228ec3cfbb65fd044bf` 的 `docs/PRODUCT_RELEASE.md`。两者没有合入本分支；这是接口依赖核对，不是组合验收。

## 实际 writer、副本与读者

以下是固定源码的盘点，不是生产目录扫描。实际路径受配置影响，10须补齐每台宿主、导出和异地备份。内存对象不会被列作磁盘 writer。

| 数据/副本 | 实际 writer / 读者 | 留存与删除传播边界 |
| --- | --- | --- |
| tracker 单曲历史 | `cutoff_history.configure_sources` → `TrackerCutoffHistory`；正式配置 `scheduled_history_only=True`，仅 `read_board_observed` 后台入口落历史；前台读历史 | v1 或物理107，公开ID/昵称、编成和名片附表；停采样不等于关闭其他来源按需写入 |
| open、Haneoka 单曲历史 | `moenotes_open.board` / `haneoka/event_cutoffs.board` 按需落库，后台也调用同入口；`SourceHistory`、历史活动查询、趋势、最新观测读取 | v2/v3旧库保留；Haneoka /2现在路由独立v4；关闭history阻止读写但不删除旧文件 |
| 综合榜历史 | `total_rankings.TotalRankingHistory/Repository`，后台/业务调用独立库；查询读取 | tracker独立压缩v2、其他来源v1路径，不由本包扩展活动total/point；删除政策必须单独列入，不套单曲SQL |
| 当前榜单JSON | `EventCutoffRepository._save/_load/_cached`，Haneoka继承；avatar-v2旧键、quality-v2新键 | 实际持久副本，含分数、ID/昵称/资料及状态；缓存内存 `_entries` 是另一份进程态，清磁盘不会驱逐它；磁盘保存失败可保留内存成功，不能当持久保存成功 |
| P165公开竞技场/玩家/名片响应 | `haneoka/public_queries.PublicQueries.cache` / `cooldowns` 有界 OrderedDict | **仅内存**，未发现该入口新建公开玩家JSON/SQLite writer；响应可进入后续冻结图文和素材缓存 |
| 玩家字典、payload、附表 | `history_compression.put`，`observation_players[_data]`、`payloads`、`player_dictionary`；`_save_details`保存可逆zlib/base64 | 删除引用行不代表字典/payload或空闲页已删除。压缩、导出仍可还原个人字段，不是匿名化 |
| 压缩/导出/中间副本 | `history_archive.migrate/export_old`，`scripts/compress_cutoff_history.py`；`scripts/backfill_cutoff_players.py` 的 SQLite backup | 显式副本，输入不改；`.partial`及WAL也算副本，失败保留，不自动清除。导出v4仍为v4，不能冒称旧代码可读 |
| WAL/SHM/rollback journal | SQLite，压缩迁移显式启用WAL；其余库不能假定永远存在WAL | 盘点逐文件列出；备份应经在线backup API或停写checkpoint，不拷贝孤立主DB来证明完整性 |
| 冻结查询图文 | `backend.prepare` 的 `work.prepared`、分页 remaining、`image()`，`PreparedReply` / `ReadOnlyContext` | 查询图文目前是进程内冻结对象，不虚构持久图片表；已发送QQ内容是平台副本，Taki本地删除不能撤回。未来删除执行须停接纳/排空或失效这些对象 |
| 投递/费用/unknown事实 | `delivery_ledger` SQLite requests/parts/projections；usage账本及独立聊天恢复 | 事实账本不含查询图像blob；unknown、ACK事实、防重复及防复活记录不归历史清理。不能为腾空间删除 |
| 名片/头像/游戏素材 | 渲染注入 loader → `visuals._asset` 的 `asset-cache` / `haneoka-asset-cache`；`game_assets`及榜图素材缓存 | URL哈希图片是实际磁盘副本；名片缩略图可能含个人信息，不能把整个asset目录一概认作无身份素材；字体/音符/卡面等公共素材另列 |
| 更新器备份 | `deploy/linux_backend.backup`保存代码/venv，排除共享data；停服务后的 `_snapshot_stopped_data` 另建 `*-data.tar` | data快照排除 `data/haneoka-asset-cache`，保留其余data；回退代码不恢复data。业务备份/异地副本由宿主运维负责，`ops_snapshot`只读覆盖/回执，不是备份writer；未核实际宿主部署 |
| Haneoka R2/D1/采集状态 | 外部服务 | 不属于Taki删除或恢复范围，不在此执行清理 |

## 分区与旧数据

`sources/history_partitions.ProviderHistory` 包装原Haneoka v3 store。新文件位于原库同级 `<旧库stem>-partitions-v4/<sha256>.sqlite3`，身份为固定顺序JSON `[provider, normalization_version, time_kind]` 的 SHA-256；文件内 `partition_identity`再次核对完整值，不信任文件名。

初始化先取得SQLite `BEGIN IMMEDIATE`写锁并重新核对版本，分区身份、provenance表、基础schema及user_version在同一事务提交，之后才开始首笔观测事务。首笔观测回滚不撤销已提交身份；初始化本身失败则整体回滚。仅版本0且sqlite_master为空的分区可初始化；已有schema4缺失/错配身份（包括旧缺陷留下的文件）继续拒绝，需隔离审阅，不自动补身份、清库或重建。

当前接受的组合来自03：

| provider | normalization_version / contract_version | time_kind |
| --- | --- | --- |
| official | haneoka:challenge-ranking/2 | official_observed_via_haneoka |
| moenotes | haneoka:challenge-ranking/2 | bridge_fetched_via_haneoka |

未来归一化版本、未知provider或不匹配时间语义拒绝写入，不自动合并到任一旧分区。区服、活动、挑战、曲目在分区内仍按原键校验；`game_region`须匹配事件区服。`observed_ms`可空且不从接收时间补造，时间均为UTC epoch毫秒；原来源获取/参考/接收时刻及HTTP Date校准证据保留。已有曲线横轴沿用来源获取时刻及明确记录的校准，并由time_kind说明含义，不换成 observed_ms 冒充统一时间。

每个v4文件沿用现有观测事务，新增 `observation_provenance` 保存显示名次、raw未知、value_states、snapshot_id、observed_ms及完整分区身份。同分不同名次/状态的同一观测不会覆盖原解释，返回storage_error并保留前值。全None不能valid。时钟隔离保留原 `clock_quarantine`，另以 `quarantine_provenance`保留完整被拒C3/C4快照；恢复采样不清除隔离证据。

旧schema1/2/3及其107压缩文件不原地迁移。旧provider映射为 `None`（语义unknown）、归一化 `legacy/1`，旧latest的每个value_state为 `legacy_unknown`；旧零不重写、不推断display/raw rank。旧 /1 writer仍可写自己的旧库。新provider与旧unknown的数据从不拼接。

`SourceHistory.read_board(event, board, ranks)` 是查询接线入口：provider观测读取精确分区，unknown读其原来源；旧的无board `read` 是保留的旧来源分段API，不汇入任何v4观测。历史latest选择一整份可用观测；provider间按本机received_ms选择显示分区，不比较两个来源的时钟，也不拼玩家资料。趋势只读该board分区。未接入ProviderHistory的调用方继续得到03的 `provider_schema_pending` 门禁。

## 压缩、归档与回退

物理schema107继续表示可逆压缩，`store_meta.legacy_version`现在可为1/2/3/4。v4压缩保留分区身份、provenance及隔离附表，逻辑摘要逐表校验；export恢复原逻辑版本。`migrate`回执增加source_bytes、saved_bytes（允许负数）、anonymous=false。小库的索引/字典开销可以使体积增大，不能按压缩率承诺容量。

旧代码可继续读写未改变的旧文件；03旧版本会拒绝v4及107(logical4)，且恢复为03后显式provider写入重新受门禁。禁止把v4替换成v3路径或仅改user_version。部署自动扫描的09 schema集合必须显式登记 `cutoff_history_provider=4`；若选择压缩，还须登记物理107和每库logical4身份，不能把107当作统一逻辑合同。09只有整数区间，10仍须核查meta及分区内容。本包没有替09修改manifest或为旧发行签发兼容资格。

失败、低盘、校验不一致不发布成功目标；迁移保留源与.partial以便诊断/续跑。代码回退不回退数据，数据恢复不恢复当前权威；压缩导出不是已批准的恢复激活。

## Dry-run接口

`history_retention.inventory({replica_label: file_path})` 只盘点显式路径及 `-wal/-shm/-journal`，输出present/missing/unreadable/symlink_rejected、字节数，始终 `discovery_complete=false`。它不声称发现所有备份/导出。`inspect_history(path, before_ms=None)` 只读校验1/2/3/4/107库，输出总观测、非valid保护数、拟投影的身份/资料行数、页分配/空闲页/物理大小。只有调用方显式给before_ms才计算候选；没有生产TTL默认值。

```text
python -B scripts/inspect_history_data.py <隔离历史副本>
python -B scripts/inspect_history_data.py <隔离历史副本> --before-ms <明确的UTC毫秒>
```

该计划仅统计“valid观测中的身份字段投影”，保留分数、零、非valid、来源证据，不宣称匿名。unknown、投递facts、删除journal和权威记录排除在清理范围外。未提供生产apply入口、定时清理、Console按钮或默认期限。合成删除传播实验在临时副本执行，验证引用行投影后重新归档/导出及恢复判定；不代表已完成生产多副本擦除。

## 给10的C6恢复判定

入口 `history_retention.check_recovery(candidate, current_authority)`，返回 `{status: allow|reject|manual, reason, contract_version:1, evidence: str|null}`。current_authority是**无参回调**，必须由受信控制器从待恢复目录、旧DB、旧journal及备份之外获取当前权威；None/不可用/不合法为manual。本包不选择生产权威实现。外部权威的证据版本进入返回值，候选不能自报最新。

candidate 与回调返回对象共同字段（全部必填，不接受额外字段）：

| 字段 | 类型 |
| --- | --- |
| contract_version | 整数1，bool拒绝 |
| binding | 非空不透明字符串；来自09已验证的实例绑定，07不解释product/AppID |
| policy_generation / deletion_generation | 非负整数；须精确等于当前权威，不默认初始化 |
| journal_sha256 | 64位小写hex；当前删除记录精确摘要，缺失拒绝 |
| evidence_version | 非空证据版本字符串；返回值采用外部权威版本 |
| replicas | 非空 `{opaque_replica_name: {generation, sha256}}`；candidate每项另有state=`verified|pending|unavailable` |

sha256是权威批准的副本状态摘要，candidate摘要须由10独立测量（包括字典/payload、导出、缓存、冻结数据、WAL及备份传播结果），不能从候选journal复制。精确摘要批准不适合作为后台自动清理的替代；后续生产权威/活跃数据证据设计须结合政策再定。本接口不接收路径或执行候选脚本。

`journal_digest(records)` 对严格JSON序列按键排序、紧凑ASCII编码后SHA-256；保留列表顺序，拒绝NaN。记录内容/保留位置/防回滚锚点尚待政策选定，不把摘要当删除已完成证据。

| 判定 | reason |
| --- | --- |
| allow | deletion_state_verified：代际/journal/绑定/全部副本摘要和已应用代际一致，且每份verified；只代表本项通过 |
| reject | malformed_deletion_evidence、deletion_binding_mismatch、deletion_authority_mismatch、replica_deletion_not_applied |
| manual | current_deletion_authority_missing/unavailable/invalid、replica_coverage_incomplete、replica_not_verified、authority_propagation_incomplete |

旧DB＋旧journal会与当前代际冲突；旧DB＋新journal仍因副本代际/摘要不符拒绝；只有旧备份且无当前权威一律manual。漏字典、漏冻结副本、未完成备份传播不能allow。受信控制器须停相关写入并保持隔离，避免检查后状态改变；此函数不是锁、激活令牌或跨进程TOCTOU解决方案。

10先完成09身份检查，再组合05 `allow/deny/manual`（deny映射拒绝）和07 `allow/reject/manual`，按自身激活门决定；任一manual/reject继续隔离。实际组合、Linux恢复/低盘演练、真实QQ、目标宿主、异地备份均未由本包验收。

## 用户必须决定的政策

1. 保留字段：公开ID、昵称、名片缩略图/URL、收藏卡/编成、分数与时间，分别保留或投影；去ID不等于匿名。
2. 各数据层期限及起算时刻：观测、当前缓存、素材、导出、冻结输出、备份；是否仅按到期或支持定向请求。
3. 删除范围及失效顺序：内存、磁盘、字典、空闲页、WAL、归档、异地备份、QQ平台已发内容的责任界限。
4. 当前删除权威与策略代际存放在哪里、由谁批准/撤销、如何独立防回滚；防复活journal和unknown/facts不得随清理删除。
5. 备份责任、可接受传播时限、无法物理改写的备份如何隔离，以及恢复目标/RPO/RTO。

以上决定阻塞生产删除执行、自动留存和恢复激活，不阻塞本包独立分区写入、dry-run与隔离验证。
