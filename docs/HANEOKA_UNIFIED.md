# Haneoka 默认资料入口

本地实现：2026-10-09；不代表服务器已发布或真实 QQ 已验收。用户确认歌曲 meta 改用 Haneoka 自身参考计算，并保留普通、激奏两种场景。此决定替代旧迁移方案中“必须复现 Moenotes TW 八类排行”的条件；旧适配器和缓存保留用于人工选择，不自动回退。

## 默认配置与请求范围

| 配置 | 默认 | 数据与限制 |
|---|---|---|
| `OURNOTES_DATA_SOURCE` | `haneoka` | JP 歌曲、角色卡、SNAP、属性、技能及原生属性图标；中文仅合并通过身份与规则核对的 intl 文本 |
| `OURNOTES_CHART_SOURCE` | `haneoka` | 固定 JP release 的完整谱面；失效仅用本来源已验证缓存 |
| `OURNOTES_META_SOURCE` | `haneoka-site` | JP 页面原始普通／激奏效率与倍率，无本地分数公式 |
| `OURNOTES_CUTOFF_SOURCE` | `haneoka` | 四服 Game Records 活动与 Top 100 观测，上游为 MoeNotes tracker |
| `OURNOTES_CACHE_TTL_HOURS` | `0.5` | 主资料缓存有效期；QQ 后台对齐香港时间每个整点与半点刷新 |

实际应用入口 `Settings.from_env()` 使用上述默认；已有环境变量优先，因此升级代码不会覆盖明确写着旧源的生产配置。内部 `SongRepository` 和直接构造 `Settings` 的历史默认保留兼容，运行入口始终显式传入配置。不读取或覆盖旧源缓存、历史与额度。

主资料启动时加载，后台完成一次刷新后重算下一整点／半点，错过时段不补跑。分数表单独启动 300 秒刷新任务；查询复用同一缓存与锁，release 不变只检查身份，变更才下载 songs 和 song-meta。失败用最长 24 小时同源已验证快照并标记，冷启动没有快照则明确不可用。获取时间不冒称上游生成时间。

默认主资料、谱面、meta、榜线和各自素材的应用网络出口为 Haneoka。图标缺失退回现有文字／颜色展示，不从旧源补图。历史读取保留本地旧源库，来源切换断线；这不产生旧源联网请求。显式旧源配置仍可联网到旧提供方。

## 普通与激奏分数表

`site_meta.py` 固定同一 JP release 获取 songs 与 song-meta，核对响应 release/source 身份、歌曲 ID、难度及数值。普通读取 `chart`（mode=normal、scoreKind=chart-relative-factor），激奏读取 `gekisou`（mode=gekisou、scoreKind=gekisou-relative）；必须 metaStatus=available 且有 referenceId。缺失值不当成 0，不借另一场景补齐。同榜混有多个 referenceId 时拒绝混排。

效率=`eff`，倍率=`score`，均将上游比值乘 100 显示百分数；时长来自该参考的 durationSeconds 或 chart.time。直接展示页面参考结果，不下载或执行上游代码，不声称实际游戏分数。激奏参考包含近似条件，来源警告保留并提示。

`/查分数表` 默认同图左右并列普通／激奏，各自排序、各自排名，页内选择号跨两榜连续。`/查分数表 普通` 或 `激奏` 选择单榜；省略难度为全难度，精确歌曲查询默认 EXPERT。支持歌曲／乐队、颜色、激奏任务条件、等级边界、eff／score、升降序、前10／20／30和页码。续查保留场景与条件。旧活动积分、跳过得分及可调模型参数明确拒绝。

截图的 intl 页面用于确认两种场景；Taki 的资料仍按日服 JP 查询，不将 intl 数值混入 JP。

## 活动与榜线

新增 `/查活动 [jp/hk/kr/en]`，复用所选榜线来源的当前／最近活动，提供名称、状态、时间和挑战歌曲 ID，无玩家排行请求。时间按各服时区显示。

`event_metadata.py` 使用 Haneoka event-tracker 的首选静态区域：日服→jp，其余→intl，不跨静态区回退。活动 ID 和开始／结束时间必须与实时 DTO 一致；挑战 ID 与 songHref 的 musicId 必须匹配，才附加名称和固定版本素材。失败时保留实时 ID 与时间；未知名称不从其他源补全。每 300 秒检查 release，失败至少 60 秒后重试，旧静态资料最多使用 24 小时。

Game Records 过滤过非法挑战条目，仍无法证明原始序号完整，因此拒绝“歌曲1／歌曲一”；请用歌名或 `歌曲ID=100111`。排名保留响应位置与空槽，不按分数重新排序。采集时间、上游时钟、安全整数、挑战有效区间与历史规则保持原校验；未知或时钟异常不冒充当前分数，不改用本机获取时间。

## 兼容与已知限制

- 新 meta 缓存为 `haneoka-site-meta-jp-v1.json`，活动静态缓存为 `haneoka-event-metadata-v1/`；与旧缓存分离。
- 原主资料缓存增加可选 ui-marks 资源，领域属性图标字段有缺省值；旧转换器忽略新增资源，原文件 schema 不变。历史 Haneoka schema 3 保留。
- 上游得意乐曲 tag 的正式名称关联仍未确认；不猜 tag ID 等同乐队 ID，相关查询明确不完整。
- 实源 JP release `r-073d884b07d594061fd6` 的成员卡 64 激奏 Lv.5 缺第二项效果，继续标记参数未确认，不拿 Lv.4 代填。
- 2026-10-09 08:06–08:10 UTC 本地实源核对：87 首歌曲、67 张角色卡、68 张 SNAP，普通／激奏均有 87 条 EX 结果；四服活动 2 的名称及挑战链接通过日期身份校验，日服活动横幅和三曲封面成功下载。
- 同次榜线响应有 100 行及时间字段，但 DTO 上游时钟约比本机／HTTP Date 快 120 秒，超过现有 60 秒时钟校验，故当前分数暂不可用；这项实源验收尚未通过，不能写成榜线已可正常上线。

## 验证入口

`test_haneoka_site_meta.py`：上游独立参考值、双榜独立排名、场景续查、缺值、混合模型、身份、过期／未来缓存与刷新失败；`test_haneoka_event_metadata.py`：四服静态映射、时间／歌曲错配、损坏缓存、无排行的活动查询、素材 release；`test_source_egress.py`：实际下载调用链的主资料／谱面／卡牌和歌曲渲染／后台 meta／四服采样出口，离线网络桩不证明上游可用。

实源预览与探测写入工作区忽略目录 `runtime/haneoka-unification/`，不覆盖生产。完整回归、包内容检查、发布 CI、服务启动与 QQ 实收分别验收。上游合同见 [固定版本说明](https://docs.haneoka.org/concepts/servers-and-releases/)、[歌曲分析页面](https://haneoka.org/jp/zh-CN/song-meta/)。
