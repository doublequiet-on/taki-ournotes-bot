# 第5批：网络出口与退役门槛

> 阶段记录：本文保留接入／研究时的合同与证据。2026-10-09 已确认的默认来源、原生普通／激奏参考分数表、活动静态关联及时间策略见 [当前统一说明](HANEOKA_UNIFIED.md)；本文旧默认值和当时待办不直接代表现状。

> 2026-10-09：本地默认入口及用户确认的 Haneoka 普通／激奏原生 meta 改用见 [统一来源说明](HANEOKA_UNIFIED.md)。本文保留先前合同／研究及显式旧源说明；其中原默认值、静态关联未实现及必须复现 Moenotes 模型的结论不再表示当前默认行为。生产发布须另行验收。

**当前结论：完成出口核验和回归约束，未达到整批旧联网路径退役条件。** 前四批的实现、预览和合同缺口不能合并表述为“五批迁移已完成”。默认来源未切换；没有移除仍被查询、绘图、后台任务或人工回退使用的代码，也没有删除旧缓存／历史。本文以 2026-10-08（香港时间）的源码和 PR 状态为依据，不是生产抓包报告。

## 五批状态

| 批次 | 已交付 | 切换或完成前仍需满足 |
|---|---|---|
| 1 完整谱面 | [PR #48](https://github.com/doublequiet-on/taki-ournotes-bot/pull/48)：固定 JP release、真实返回路径、既有解析／绘图、独立缓存与开关 | 独立审查、发布切换及真实 QQ 验收；默认仍为旧谱面源 |
| 2 主资料与图片 | [PR #49](https://github.com/doublequiet-on/taki-ournotes-bot/pull/49)：JP 数值、核对后的中文、完整技能转换及图片隔离 | 得意乐曲标签名称关联、完整覆盖验收、装饰素材和生产／QQ 验收；详见 [Catalog](HANEOKA_CATALOG.md) |
| 3 四服榜线与历史 | [PR #50](https://github.com/doublequiet-on/taki-ournotes-bot/pull/50)：四服 DTO 预览、原位空槽、独立 schema 3 历史和三来源断线读取 | 原始挑战序号完整性、实时四服与静态服务器的正式映射、素材及联网稳定性；预览中歌曲 N／名称筛选尚不等价，详见 [榜线](HANEOKA_CUTOFFS.md) |
| 4 新版分数表 | [PR #51](https://github.com/doublequiet-on/taki-ournotes-bot/pull/51)：最新公开合同核验、最小输入需求及验收步骤 | 等价 TW 种子／权重／区间统计、真实模型指纹及独立对拍未满足，尚未实现新适配；详见 [输入合同](HANEOKA_META_CONTRACT.md) |
| 5 旧联网路径退役 | 本批执行调用边界审计并加入持续离线回归，核对保留依赖和旧缓存／历史兼容 | 旧主资料、谱面、榜线默认值和分数表／图标仍有运行依赖，不能宣称正常运行零旧域名访问 |

第5批出口审计时 #48–#51 均为堆叠草稿，观测时各自 head 的 CI 均成功。后续合并状态以实际 PR/head 为准；所有默认开关、计算模型、平台收发及生产来源配置保持不变。CI 不证明生产、真实 QQ、接口全量覆盖或来源合同等价。集中待研究问题及所需证据见 [研究与验收清单](HANEOKA_RESEARCH.md)。

## 实际调用链与剩余出口

以下“实跑”指运行真实调用链，在 HTTP 请求边界返回离线样本并记录请求 URL；不会访问外网、上传 QQ 或改生产缓存。它证明选择与错误回退路径，不能证明真实服务器的响应时间、重定向目的地或长期可用性。

| 入口／配置 | 请求目的地与路径 | 核验和保留理由 |
|---|---|---|
| 主资料 `data_source=haneoka` | `haneoka.org/api/v1/servers/{jp|intl}/release` 及固定 release 的 catalog／批量资源 | `SongRepository.load/refresh` 实跑；详情直接使用捕获记录，不去 Yume 补全。冷启动失败明确报错，热缓存失败只用同源旧快照；旧主缓存不改写 |
| Haneoka 封面、成员／SNAP 图片 | `haneoka.org/assets/jp/…?release=…` | 经 `visuals._asset → catalog_assets.download_asset → public_get` 实跑；坏缓存重取和下载失败都不进入旧 urllib／aiohttp 备用下载路径 |
| 谱面 `chart_source=haneoka` | Haneoka identity、固定版本歌曲实体和返回的 `.bytes` 路径 | 经公开 `load_chart_data` 实跑；失败用独立已验证缓存。人工选回 moenotes 后原谱面缓存仍能读取，未改写 |
| Haneoka 成员列表摘要 | 新主资料随卡捕获的 `member_summary` | 完整列表绘图实跑，未触发旧 `get_snapshot` 再刷新。旧主来源仍可使用该 Haneoka 摘要仓库 |
| 颜色／卡牌属性图标 | `bdon.yatta.moe/images/CardType{1..5}.webp` | Haneoka 歌曲、成员／SNAP 列表及详情绘图实跑仍请求；有运行依赖，不能通过删图标或改变美工假称退役 |
| 激奏任务图标 | `haneoka.org/assets/jp/…/Icon_gekisou_*.png`，既有未固定版本路径 | 仍走通用图像下载器，不应与固定 release 的新 Catalog 图片混称。完整素材版本与等价验收尚需补齐 |
| 榜线 `cutoff_source=haneoka` | `haneoka.org/api/v1/game/records/{jp|tw|kr|en}/events/current` 及具体 event/challenge ranking | `configure_sources → HistorySampler.sample_once` 实跑四服；正常与故障回退均无旧 tracker 请求，独立历史写入，未创建旧源库 |
| 榜线默认 tracker | `api.bdon.moe` 当前活动／挑战榜；`metadata.bdon.moe` Master；`assets.bdon.moe` 版本及素材 | 主资料选 Haneoka 不会擅自改变榜线开关。默认榜线调用链实跑仍使用三个旧域名，须保留 |
| 榜线显式 open | `bdon.moe/api/open/v1/moenotes/…/challenge-ranking`；活动发现及素材仍经旧公开路径 | 源码与既有认证／隔离测试核对；本批未调用真实认证接口。不能因挑战分数入口不同就删发现、元数据与图片依赖 |
| 现行分数表主请求／刷新 | `storage.bdon.moe/moenotes/music-data/music-data.json` | 在主资料选 Haneoka 时实跑默认双榜查询和 `MusicDataRefresher.run` 单周期；两者仍使用当前 TW 统计，不能被第2批开关关闭或替换 |
| 现行分数表封面 | `assets.bdon.moe/ja/Image/Jacket/…` | 从实际 `MusicSnapshot.records` 取得封面，再经 `_asset` 实跑，仍有依赖 |
| 旧主资料／谱面显式选择与默认值 | Yume JSON／素材；`assets.bdon.moe/zh-Hans/Live/MusicScore/…` | 配置和调用方核对；选择 Haneoka 只隔离对应模块，不删除人工回退能力 |

QQ 官方接口、更新器的 GitHub 访问和显式启用的模型服务不是旧游戏数据出口，本批不以断开它们达到“零旧源”。独立 X 转发机器人不在本项目范围。

## 必须保留的代码与数据

- `sources/yatta.py`：旧主目录、详情、转换和图像常量仍被使用。新数据模式下没有调用旧详情，不代表模块可以整块删除。
- `sources/chart_data.py`：公开来源分派、`ChartData`、异常、大小限制和解析器由 Haneoka 复用；旧缓存读取和人工旧源选择仍存在。
- `sources/moenotes_events.py`：领域类型、时间与身份清理、共享并发槽和缓存工具被 Haneoka、历史、采样、查询及绘图直接引用，旧 tracker 也仍是默认来源。
- `sources/moenotes_music_data.py`：现行 TW 快照和刷新仍为运行依赖；数值／文本工具与模型版本常量也是有限求值器的依赖。
- `sources/moenotes_open.py`：仍是可选正式榜线来源；来源编排和 v2 历史兼容保留。
- 旧 `haneoka/song_meta.py`、`song_traits.py`、成员摘要模块：旧分数表开关以及被其他模块导入的类型／常量／转换仍存在。不能按文件名把“旧 Haneoka”一起删除。
- 旧谱面／主资料缓存、tracker v1 与 open v2 历史保留；Haneoka 缓存和 v3 历史独立。来源切换不改写数据血缘；回退代码后原库继续可读，新增库留给恢复新版读取。

本次没有确认可安全整块退役的旧适配器，因而没有为了形成删除 diff 而移除功能、共享工具或兼容读取。真实上游血缘与许可继续见 [THIRD_PARTY](../THIRD_PARTY.md)。

## 回归与最终退役验收

[test_source_egress.py](../tests/test_source_egress.py) 有七个综合探针：主资料／详情／冷启动和热缓存失败；图片坏缓存重取；谱面同源回退与旧缓存手工读取；四服采样；新主资料图像的旧图标依赖；双榜主请求／封面／后台刷新；榜线默认源的独立选择。使用真实 URL 构造、来源解析、缓存、绘图、来源编排和后台任务；只替换 HTTP 响应。外网 socket/DNS 禁止，Windows asyncio 自用回环连接允许。模拟素材仅用于触发下载，不作为视觉或真实游戏素材验收。

执行：`python -m unittest discover -s tests -p 'test_source_egress.py' -q`。完整离线回归继续使用 `python -m unittest discover -s tests -q`；其中既有历史测试验证三来源断线、文件版本和无玩家身份。安装包必须包含新适配、资源与说明，且不含运行缓存、审计 JSON 或真实身份。

后续只有以下条件全部满足，才能移除对应旧联网适配：

1. 解决第2批标签／装饰素材、第3批序号与静态映射、第4批统计等价合同，并完成各批独立对拍。
2. 经分别授权完成来源发布切换和生产／QQ 验收；不能用本地测试或草稿 PR 替代实际运行版本证据。
3. 在冷／热缓存、详情缺项、图片损坏重取、后台刷新、四服切期、异常回退、分页／续查等入口重新记录实际运行网络出口，确认没有旧游戏域名请求，包括重定向。
4. 解除共享代码依赖后再删除网络适配；保留旧缓存／历史兼容读取、来源和许可说明。完整离线回归、wheel/sdist／隔离安装及代表性图文检查通过。

本批没有生产抓包、真实 QQ 发送、发布或服务器重启。当前“零旧联网”门槛明确未通过，不能启动无条件退役。
