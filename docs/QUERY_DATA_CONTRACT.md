# C3/C4 查询数据合同

任务03基于 P165 `e1a86a6db1f327a6dd0cc53fd860c4ababf50923`；main 基础 `8d9e7301392ad548fe9554c4f4b09a0d53f615fa`。本包只生产数据与直接查询状态，04消费；07拥有历史 schema。

## 区段登记

- `sources/moenotes_events.py`：`EventSnapshot`、`BoardSnapshot`、`_snapshot`、缓存来源隔离；素材下载及 URL 校验不改。
- `data.py`：`capture_catalog` 的查询时新鲜度；图片、素材和发布锁职责不改。
- `domain/public_queries.py`：公开快照质量与排名字段；`query/contracts/info.py` 展示结构不改，沿用 footer/rows 同步文字。
- `query/card_catalog.py`：`CardAnswer.cache_state/cache_notice` 与构造结果；`query/song_query.py`：命中/未命中 warning。04从这些已有输出接线图片，不另定义新鲜度。
- `sources/haneoka/{event_cutoffs,public_queries,transport,catalog,info_catalog,activity_details}.py` 及直接查询按实际差异修改。
- `query/L2-2.md`：本包核对191地图与02已提交地图，组合时仍需验证02代码。
- 不修改 rendering、visuals、replies、命令、菜单、model_runtime、历史 schema 或 Haneoka。

## 生产者证据与排名口径

只读核对 Haneoka main `54e7bc13b566ba54e2b66bd2d16542cf3f876b40`：`worker/official-ranking-api.ts`、`arena-ranking.ts`、`moenotes-fallback.ts`、`official-player-api.ts`、`src/lib/game-records.ts`。这是源码合同证据，不代表真实四服验收。

活动 Tn 保持响应位置（从1开始）；Top100 是最多100行，不能声称包括第100位置之外的全部并列。`display_ranks` 是上游显示名次，可重复/跳号；`raw_ranks` 未提供，全部为 null，旧缓存也不能从位置反推。竞技场 start/end 是位置窗口，显示名次可能小于 start；选择子窗口按位置，最多100行，不按显示名次过滤。

官方活动适配器按同分生成显示并列；MoeNotes桥接按响应顺序生成显示名次，均不能当作原始游戏名次。官方活动 score 与竞技场 point 的零可能来自协议默认/适配器默认，DTO不能追溯时标记为来源零，不能宣称已证明业务零；任意空行不补零。

活动 total/point/v2历史不在本包接入范围。保存明确的 totalRankingEnabled 只表示能力，不接入新榜种。

## 04/07 消费字段

`sources/moenotes_events.BoardSnapshot` 新字段均有兼容默认值，旧位置参数不变。

| 字段 | 类型 / 缺省 | 含义 |
| --- | --- | --- |
| scores / observed_scores | tuple[int\|None,...] / 空；原字段 | 当前可用值 / 保留观测槽；0不是缺失。位置从1开始，`score(n)`仍按位置 |
| display_ranks | tuple[int\|None,...] / 空 | 对应观测槽的显示名次；源缺失为None，合法正整数非递减且不大于响应位置 |
| raw_ranks | tuple[int\|None,...] / 空 | 本合同所有元素None；当前上游未传可验证的原始名次 |
| value_states | tuple[str,...] / 空 | `value`、`source_zero`、`missing`、`null`、`invalid`、`legacy_unknown`；与观测槽对齐 |
| provider | str\|None / None | 上游声明的实际提供者（目前official/moenotes）；缺失不猜；`source`仍是适配服务haneoka |
| game_region | str\|None / None | jp/tw/en/kr；用户hk先映射tw；cn/intl不是游戏区 |
| snapshot_id | str\|None / None | 上游快照ID；无ID不合成 |
| normalization_version | str / legacy/1 | 新Haneoka归一化为haneoka:challenge-ranking/2；旧缓存保留legacy/1 |
| observed_ms | int\|None / None | 上游显式observedAtMs，UTC epoch毫秒；缺失不补成本机时刻 |
| fetched_ms / server_ms / received_ms / http_date_ms | int\|None | 原字段；来源获取/参考时钟/本机收到/校验过的HTTP Date，均epoch毫秒 |
| age_seconds | float\|None | 源参考时钟减来源获取时间，加本机单调时钟经过时间；不把HTTP成功时间当观测时间 |
| freshness | str / unknown | `fresh` / `stale` / `unknown`；与quality独立，不覆盖clock_unknown等拒绝原因 |
| error_code | str\|None / None | pending、rate_limited、not_found、data_unavailable及现有invalid_*/budget等来源错误；不靠中文status反解 |
| has_values | bool只读属性 | 至少一个当前值非None；(0,)为True，(None,)为False |
| positions | tuple[int,...]只读属性 | 观测槽位的1基响应位置；即使当前值被拒绝也不重排观测 |

`EventSnapshot.provider: str|None` 与 `total_ranking_enabled: bool|None` 分别保留来源与明确总榜能力。后者为None表示未声明，不从活动类型猜测。

`domain/public_queries.PublicSnapshot` 保留body/fetched_at/resource/release原字段。新增：provider、game_region、resource_server、source_id、snapshot_id均为`str|None`，observed_ms/server_ms为`int|None`，age_seconds为`float|None`；normalization_version=`public/2`，source=`haneoka`，freshness三态同上，time_kind为unknown/source_observed/source_fetched/bridge_fetched。`fetched_at`是本机收到响应的epoch秒；`release`空字符串为未知，仅静态资源有release；`resource`沿用原路径或资源服用途，新消费者以resource_server区分jp/intl，不能当作游戏区。

竞技场body.rows增加`position: int`（从请求first开始）、`rawRank: null`、`pointState: str`（value/source_zero/null/missing），原rank为可空显示名次，point为可空非负整数。不会用rank过滤位置窗口。默认1–20、连续最多100位置；上游最多返回start至start+99。profileId精确字符串，不转浮点数；玩家查询JP接受1–19位正十进制且不超过int64，tw/en/kr为2/3/4开头11位。源身份必须与请求相等。

InfoAnswer/InfoView不新增平行状态结构；公开查询把来源与新鲜度放入rows/footer，备用正文通过同一视图生成。CardAnswer新增cache_state及cache_notice；SongAnswer.warning在未命中时也保留旧缓存提示。04仍需验证其实际成功图片/详情图片是否显示这些字段，不能用本包文字测试代替图片验收。

## 年龄、失败与隔离

- 活动榜沿用现有120秒陈旧、600秒拒绝当前值阈值；归档最后观测不冒充实时。来源stale为True始终保留。official时刻类型为official_observed_via_haneoka；moenotes桥接为bridge_fetched_via_haneoka，原始观测未知，不能因桥接刚获取便标fresh。旧无provider记录沿用tracker_observed_via_haneoka。
- 竞技场榜单1200秒、人数1800秒、玩家21600秒与核对版本生产者一致；内存缓存寿命仍60秒/人数1800秒，两者是不同概念。每次消费重算年龄，源stale不会被本地命中清掉。缺参考时间标unknown。
- 静态主目录使用配置cache_ttl_hours（默认6小时）；查询捕获时重算。明确在线identity检查成功可作为该固定release仍为当前的证据，记录identity_checked_at（秒），不重写来源fetched_at。未命中也保留状态。
- 只有明确JSON error.kind=not_found表示确认不存在。202为pending，429为rate_limited；404/503需读错误正文，pending不得被解释成不存在；不明/损坏错误正文为data_unavailable。有界错误正文上限64KiB，现有public_get仍在非200时拒绝；请求路径权限不变。错误不刷新成功快照时间。
- cutoff缓存使用quality-v2后缀与含活动provider的身份摘要；payload独立保留榜单provider（允许官方tracker下的MoeNotes榜单回退）。刷新替换整份快照，不拼行；不同provider不比较源时间、不把它判成同源迟到。故障回退显示原快照自己的provider。
- PublicQueries按完整路径（游戏区/请求身份或资源区/release）有界缓存整份不可变JSON，刷新不跨区、跨release补字段；无新增公开玩家持久writer。

## 旧缓存与07接线门

仍接受schema1。无provider的活动可以只读接收原avatar-v2缓存：不改旧文件，不批量重写零；display/raw rank缺失为None，value_states为legacy_unknown，normalization_version为legacy/1。新缓存写入独立quality-v2键；回退旧代码仍读取旧键。源字段类型不合格则拒绝，不能通过移除校验装成兼容。

03固定候选的旧历史schema没有provider分区。未声明provider的旧合同保留contract_version=/1及原读写；显式provider快照为/2。任务07现通过 `ProviderHistory` 接入独立schema4分区，按provider、normalization_version、time_kind隔离，按需与后台均走相同writer；查询使用 `read_board` 只读该分区。未配置分区能力的调用方仍返回 `provider_schema_pending`，不写入或拼接旧历史。旧历史数据不删除、不原位迁移，provider为unknown，旧零和旧排名不作推断性迁移。完整兼容、回退与恢复合同见 [HISTORY_DATA_CONTRACT](HISTORY_DATA_CONTRACT.md)。

## 可复现夹具

既有测试文件中的新增案例随本包交付：

- `test_haneoka_cutoffs.HaneokaCutoffTests.test_c3_ties_positions_and_unknown_raw_ranks`：rank=[1,1,3]、score=[100,100,0]，T2=100，raw全部None。
- `test_c4_empty_missing_invalid_and_zero`：`[null,{}, {rank:3,score:null},{rank:4,score:false},{rank:5,score:0}]`得到`[None,None,None,None,0]`及独立状态；字符串/list/浮点空行拒绝；全None不success。
- `test_provider_history_gate_and_total_capability`：official明确来源、totalRankingEnabled=false，新观测不写旧历史。
- `test_info_queries.PublicQueryContractTests.test_arena_position_window_tie_and_freshness`：请求位置100–101，显示99/101，point=0/null；到1200秒后仍可命中但新鲜度变stale；cn/intl拒绝。
- `test_records_error_body_overrides_ambiguous_http_status`、`test_haneoka_facilities.HaneokaFacilityTests.test_records_error_body_is_bounded_and_legacy_get_still_rejects`：404/503 pending、确认not_found、无效正文、429、64KiB上限。
- `test_song_traits.SongTraitsTests.test_old_catalog_miss_keeps_stale_state_at_query_time`：启动后年龄跨阈值、查询未命中仍stale。

02的投递合同绑定`d9240dffecee0f0a0066d5f70c8357fc9a398632`。本分支未叠加02；query/L2-2保留191公开查询地图，并登记组合核对点，不把02尚未合入的行为写成本分支已实现。

## 04投影补充（兼容旧head）

InfoRow末尾追加position:int|None=None、point_state:str|None=None，仅standing行从body.position/pointState原样投影；旧构造兼容。显示名次仍是原rank，不能从标题反推响应位置。正文同样列出响应位置及积分状态，source_zero与null分别保留。源缺字段为None，不由积分推断状态。

quality_lines以PublicSnapshot为唯一观测/参考时钟来源：observed_ms/server_ms为epoch毫秒，fetched_at为本机接收epoch秒（显示前乘1000）；统一北京时间UTC+8。缺失观测显示未获取，不以接收或服务器时间代替；body无需重复observedAtMs。玩家lastUpdatedAtMs（来源更新）和body.fetchedAtMs（来源采集）仍独立展示，不与本机接收混淆。来源、provider、游戏/资源区、source_id、snapshot_id、release、归一化与time_kind进入共享正文。04可直接消费新增逐行属性及body/quality行；07历史schema和来源DTO不变，无数据迁移。
