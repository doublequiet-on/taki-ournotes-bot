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

入口 `history_retention.check_recovery(candidate, current_authority, *, current_isolation=None)`，返回 `{status: allow|reject|manual, reason, contract_version:1|2, evidence: str|null}`。有效权威决定返回版本；无有效权威时返回1/manual。current_authority是**无参回调**，必须由受信控制器从待恢复目录、旧DB、旧journal及备份之外获取当前权威；None/不可用/不合法为manual。首版人工核当前记录批准、无法确认不恢复；读取适配器如下，尚未配置生产权威。外部权威的证据版本进入返回值，候选不能自报最新。

v1 candidate 与回调返回对象共同字段（全部必填，不接受额外字段；v2扩展见下一节）：

| 字段 | 类型 |
| --- | --- |
| contract_version | 整数1，bool拒绝 |
| binding | 非空不透明字符串；来自09已验证的实例绑定，07不解释product/AppID |
| policy_generation / deletion_generation | 非负整数；须精确等于当前权威，不默认初始化 |
| journal_sha256 | 64位小写hex；当前删除记录精确摘要，缺失拒绝 |
| evidence_version | 非空证据版本字符串；候选须等于当前权威版本，返回值采用外部权威版本 |
| replicas | 非空 `{opaque_replica_name: {generation, sha256}}`；candidate每项另有state=`verified|pending|unavailable` |

sha256是权威批准的副本状态摘要，candidate摘要须由10独立测量（包括字典/payload、导出、缓存、冻结数据、WAL及备份传播结果），不能从候选journal复制。精确摘要批准不适合作为后台自动清理的替代；后续生产权威/活跃数据证据设计须结合政策再定。本接口不接收路径或执行候选脚本。

`journal_digest(records)` 对严格JSON序列按键排序、紧凑ASCII编码后SHA-256；保留列表顺序，拒绝NaN。记录内容/保留位置/防回滚锚点尚待政策选定，不把摘要当删除已完成证据。

| 判定 | reason |
| --- | --- |
| allow | deletion_state_verified：代际/journal/绑定/全部副本摘要和已应用代际一致，且每份verified；只代表本项通过 |
| reject | malformed_deletion_evidence、deletion_binding_mismatch、deletion_evidence_version_mismatch、deletion_authority_mismatch、replica_deletion_not_applied |
| manual | current_deletion_authority_missing/unavailable/invalid、replica_coverage_incomplete、replica_not_verified、authority_propagation_incomplete |

旧DB＋旧journal会与当前代际冲突；旧DB＋新journal仍因副本代际/摘要不符拒绝；只有旧备份且无当前权威一律manual。漏字典、漏冻结副本、未完成备份传播不能allow。受信控制器须停相关写入并保持隔离，避免检查后状态改变；此函数不是锁、激活令牌或跨进程TOCTOU解决方案。

10先完成09身份检查，再组合05 `allow/deny/manual`（deny映射拒绝）和07 `allow/reject/manual`，按自身激活门决定；任一manual/reject继续隔离。实际组合、Linux恢复/低盘演练、真实QQ、目标宿主、异地备份均未由本包验收。

## C6 v2：完整登记中的隔离禁止恢复副本

对照10固定 `ea1ed3000b6300678c7ede53c43aeefea990e08d` 的 `deploy/INSTANCE_RECOVERY.md`：实体旧备份即使禁止恢复仍须保留在完整登记中。v1不能表达此状态，继续原有全部副本已应用删除规则；不在v1塞额外字段或静默缩小replicas。

v2 authority仍为上述七字段，contract_version=2；candidate增加且必须有 `selection`，是非空、不重复的副本ID列表，明确本次将进入恢复的实物。候选与权威版本必须一致，否则reject/deletion_contract_version_mismatch。**完整replicas键集合仍须一致**，selection从来不取代完整登记。每份副本的字段如下：

| disposition | authority副本字段 | candidate额外字段/要求 |
| --- | --- | --- |
| restorable | generation、sha256、disposition | state；全部此类副本仍须verified且generation等于当前deletion_generation，未选中也不能豁免传播 |
| quarantined | generation、sha256、disposition、isolation_sha256 | state必须为quarantined；其余字段与权威精确相同，generation保留真实已应用代际，可低于但不能高于当前删除代际 |

隔离副本sha256是仍存在实物的测量摘要，不是假造的“删除后摘要”；isolation_sha256是人工批准的宿主隔离登记精确字节SHA256。登记由10维护稳定ID、实物位置/文件身份/hash、原期限起止、禁止恢复与隔离规则及修订证据；07只比对不写入。候选不得给隔离副本state=verified，不能漏掉它；选中权威标为quarantined的副本直接reject/quarantined_replica_selected。改名、复制、过期、人工仅修改标记均不能解除禁令；重建出的合格新副本必须另登记、独立测量并换批准版本，原实体在销毁核验前继续登记。旧备份的删除传播仍未完成，不把隔离说成已删除。

只有存在quarantined条目时，07额外调用受信 `current_isolation()` 无参回调。**验证者是10宿主观察器**，由08注入，不允许候选或Console提供静态声明。返回对象必须恰为：

```text
{contract_version:2, binding:<当前实例>, evidence_version:<当前权威版本>,
 selection_sha256:journal_digest(sorted(candidate.selection)),
 replicas:{<每个隔离副本ID>:{sha256:<实物摘要>, isolation_sha256:<批准隔离登记摘要>,
                            restore_denied:true, writers_denied:true}}}
```

证明的replicas恰好覆盖完整登记中所有quarantined项，不接受额外/缺失项；两个true必须是真布尔值而非整数。10必须在受信锁/停写边界内重新观测实物身份、隔离登记及访问规则，并核实所绑定selection的恢复树、所有writer/trigger与导入/导出/恢复选择入口不能读取、写入或重新接纳该旧实物（包括别名、硬链接和已知副本）。restore_denied承诺所有业务/恢复读取入口均被隔离，writers_denied承诺所有业务写入/触发器均被隔离；人工审阅路径不因此获恢复许可。不能以“文件在另一个目录”“已过期”或DB未打开替代验证。任何入口/副本不可见、未知或无法证明时，回调返回None或抛IO错误，07保持manual。

该证明不是删除豁免或永久许可。路径/身份/hash、隔离权限或规则、完整副本集合、selection、批准版本变化，管理员撤销、宿主观察失联或停写证明失效时必须立即失效。10负责持续复核并撤销现有恢复会话；独立批准/防回滚按已有合同，禁止复用撤销版本。07在隔离回调后再次读取current_authority，任何变化均manual；这不能代替宿主锁、持续观察或解决ABA。本轮没有实现或伪造10宿主观察器；未接入可信当前证明的实际环境继续manual。

v2通过且含隔离对象时reason=`deletion_state_verified_with_quarantine`，只允许selection及其余原恢复门条件下的恢复，绝不授权隔离对象。无隔离对象时仍为deletion_state_verified。新增reject：deletion_contract_version_mismatch、quarantined_replica_selected、replica_disposition_mismatch、quarantined_replica_mismatch；新增manual：quarantine_generation_invalid、current_isolation_missing/unavailable/invalid/mismatch、isolation_coverage_incomplete、current_deletion_authority_changed。v1原判定不变。

08/10必须显式支持返回contract_version=2及新reason，并把selection与隔离登记证据纳入当前会话绑定，实际选择器独立拒绝隔离实物；不把allow解释为“全部replicas均可恢复”。固定旧消费者 `8bdde8392ab79180ebf32a11fc05ed0a5190fd1e` 对v2权威返回manual；仅改version数字也因额外字段拒绝。禁止通过删除字段把v2降级成v1。07的读取器复用同一人工批准文件格式校验v1/v2，无新存储框架。

## 首版人工当前记录读取（2026-11-08）

核查固定集成提交 `602a95f49fd059deb7a59377be74becb4d55e008` 的src/scripts/deploy：`application/chat_store.py::deletion_state/_read_journal` 提供 `taki-chat-deletions/1`（identity/messages/scopes），只证明聊天删除集合；`deploy/release_retention.py` 的记录证明发行目录归属与清理进度。两者均不提供历史分区的policy_generation、完整replicas或当前人工批准，不能适配为07权威。`inventory`始终discovery_complete=false，也不能替代完整登记。未读取生产库/实际账本，本次源码核查没有找到可直接消费的历史删除权威生产者；不能据此断言生产从未删除过。

固定最小接口：`read_current_authority(path, *, excluded_roots, read_approved_sha256)`。08可将其包装为check_recovery的无参回调。path为受信宿主配置的绝对路径，excluded_roots为非空绝对路径列表，必须包含全部候选/恢复/备份树；候选、Console和待恢复配置均不能决定这两个参数。解析后的文件路径在任一排除树中即不接受，文件自身符号链接拒绝；祖先目录/文件的所有权、替换权限及整个检查期间的隔离锁由10负责，路径检查不是权限证明。

文件UTF JSON的顶层恰为 `{"authority": <上述完整七字段对象>, "journal": <按顺序的真实删除记录列表>}`；最大16MiB，拒绝重复JSON键、非有限数字及journal摘要不符。列表内容由人工核实的真实账本提供，07不创造记录字段或默认空账本；空列表仅在外部批准明确证明无删除记录时成立。authority中的replicas是人工逐项核对的完整登记，不是从候选复制；generation均须达到deletion_generation才能由check_recovery通过。

`read_approved_sha256()`由受信宿主每次读取**独立于该文件和备份树**的当前人工批准，返回批准文件精确字节SHA256；缺失、撤销或不能证明当前性返回None。不能用常量、启动时缓存、文件自身的hash或随备份恢复的批准记录作为生产回调。读取器前后各调用一次，摘要须相同并匹配读取字节；不匹配/非法返回None，check_recovery映射manual/current_deletion_authority_invalid；IO/Timeout由check_recovery映射manual/current_deletion_authority_unavailable。每次调用重新读取，不保存最后成功值；它不写文件或批准记录。

人工换代顺序：先撤销当前批准并停用恢复准入；核对当前journal、政策与所有副本，更新记录后重新批准其精确摘要。政策/字段范围改变递增policy_generation，删除集合变化递增deletion_generation，批准版本/副本集合或测量结果变化必须换唯一evidence_version。任何无法证明代际连续性的旧快照不获批准。生产回调的独立防回滚锚点与撤销保全由宿主落实；07不生成它。双读只检测读取期间批准变化，不代替10的持续撤销观察/停写证明，也不消除批准撤销再恢复的ABA问题；禁止复用已撤销版本。

08消费此回调和已有C6结果；10独立测量candidate（包括各副本state），在启动屏障与持续观察中重新核验，并将批准摘要/版本纳入自己的deletion代际证据。07不修改两包的组合接口。恢复身份仍由09固定，05的批准不替代07记录。

当前可交付的是上述读取合同；**当前真实来源缺项**为：宿主外部记录绝对路径及管理者、独立当前批准读取来源及撤销机制、09已验证binding、批准政策代际、真实journal及删除代际、全部本地/异地副本名称与应用证明。没有这些实物即manual，不填合成值，不把旧DB+旧journal当当前权威。

## 已确认期限与仍需决定的执行政策

此前已确认：分数/来源长期保留；身份/昵称/名片/编成30天，起算为Taki首次持久接收，复制/读取/压缩/导出/恢复不重置；玩家缓存24小时，个人素材7天，公开素材连续30天未使用并设容量上限；本地每日业务备份14天、加密异地备份30天。起算未知的记录隔离人工处理；unknown/facts/防复活记录不因清理删除。本次不执行这些生产政策。

2026-11-08用户确认按推荐执行的政策（本轮仅固定合同）：

- 不可改写旧备份标记为禁止恢复并隔离，按已确认保留期限到期处理。未核实不能恢复，未完成传播的副本不得标verified；禁止恢复/隔离登记不代表已物理销毁。本轮没有写生产隔离标记，也不执行到期删除，真实销毁须另行执行并核验结果。
- 人工批准责任：个人模式由用户指定管理员，定制模式由接收方指定管理员；具体管理员身份未配置时保持manual。独立批准及防回滚来源由10配置、08注入，07不另建批准框架或生产写入器。

仍需实际落实/测量：

- 公开素材容量上限及RPO/RTO等待实际测量，不编默认数值，不重问既定期限。
- 受信记录位置、管理员身份配置、异地备份传播时限、定向删除请求的字段范围及平台已发内容边界仍需落实；到期留存批准不自动扩大到这些动作。

以下原执行范围清单继续用于落实，不再表示上述期限尚未确认：

1. 保留字段：公开ID、昵称、名片缩略图/URL、收藏卡/编成、分数与时间，分别保留或投影；去ID不等于匿名。
2. 各数据层期限及起算时刻：观测、当前缓存、素材、导出、冻结输出、备份；是否仅按到期或支持定向请求。
3. 删除范围及失效顺序：内存、磁盘、字典、空闲页、WAL、归档、异地备份、QQ平台已发内容的责任界限。
4. 按上述已确认责任配置管理员、当前删除权威存放位置、批准/撤销及独立防回滚来源；防复活journal和unknown/facts不得随清理删除。
5. 落实备份责任、传播时限及旧备份禁止恢复/隔离登记；实测恢复目标RPO/RTO，物理销毁另行执行核验。

上述尚未落实的记录、配置和测量继续阻塞生产删除执行、自动留存和恢复激活，不阻塞本包独立分区写入、dry-run与隔离验证。
