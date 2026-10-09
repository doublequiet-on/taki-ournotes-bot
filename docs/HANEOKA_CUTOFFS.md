# Haneoka 挑战榜预览接入

> 阶段记录：本文保留接入／研究时的合同与证据。2026-10-09 已确认的默认来源、原生普通／激奏参考分数表、活动静态关联及时间策略见 [当前统一说明](HANEOKA_UNIFIED.md)；本文旧默认值和当时待办不直接代表现状。

> 2026-10-09：本地默认入口及用户确认的 Haneoka 普通／激奏原生 meta 改用见 [统一来源说明](HANEOKA_UNIFIED.md)。本文保留先前合同／研究及显式旧源说明；其中原默认值、静态关联未实现及必须复现 Moenotes 模型的结论不再表示当前默认行为。生产发布须另行验收。

这是迁移第 3 批的可审查实现，**尚未通过生产切换门槛**。默认 `OURNOTES_CUTOFF_SOURCE=tracker` 不变。显式设置 `haneoka` 后，仅请求 `https://haneoka.org/api/v1/game/records/{jp|tw|kr|en}/events/current` 及捕获活动的 `/events/{eventId}/challenges/{challengeId}/ranking`，不请求积分榜、永久歌曲榜或旧来源补全。配置模板不会自动修改运行配置。

## 已实现的合同

- 活动、区域、挑战和歌曲关联在本次查询内捕获；榜单响应必须匹配区域、活动和挑战。缓存键包括完整活动／挑战／歌曲／时间身份；并发活动切换后丢弃过时结果。
- 名次按响应位置，不排序、不压缩空槽；DTO 的 rank 必须等于原位置。只接受非负安全整数分数 `0..2**53-1`。其他值留空，并明确提示。数字 ID 只取同一行的十进制 `profileId`，不以 `playerId` 替代；名字沿用已有清理规则。当前缓存不保存队伍或完整玩家档案。
- 来源时间取 DTO `fetchedAtMs`，参考时钟取 `serverTimeMs`；不拿接收时间冒充采集时间。未知、相对参考时钟明显超前、期外和超限快照不显示可信当前值；陈旧标记保留。2026-10-09 用户确认：单纯的上游／本机时钟偏差照常显示且不加偏差提示，超过 60 秒的偏差只暂停该观测写入历史并记录缺口。Haneoka 的真实上游为 MoeNotes tracker，时间口径记为 `tracker_observed_via_haneoka`，不冒充开放平台的上游获取时间。
- 独立 `haneoka-cutoff-v1/` 缓存；活动缓存 30 秒，榜单 60 秒，故障回退最多 600 秒且必须通过同源校验。迟到榜单不覆盖较新缓存。匿名请求 512 KiB 上限、单次最多 4 秒、查询总预算沿用 12 秒；拒绝重定向和非预期出口。写盘失败保留内存结果。
- 独立 `haneoka-cutoff-history-v3.sqlite3`，schema 3，只保存分数、时间、质量与来源，不保存玩家身份。tracker v1、open v2 原文件不迁移；读取组合三段并在来源切换处断线。新文件错指旧库时拒绝写入。旧版继续读取原库，新库保留供恢复新版后读取。
- 现有查询、分页、历史绘图、后台采样和 QQ 交付复用既有接口。来源署名显示 Haneoka / MoeNotes。新来源未核实原序号时，歌曲 N 明确不可用；按歌曲 ID 或查询全部仍可用。

## 切换前仍需解决

1. **完整挑战原序号。** 官方 Worker 的当前活动标准化会过滤无效挑战 ID／musicId；DTO 不带原序号或完整性证明。`challenge` 专项实体也只有标题、封面和歌曲链接，没有 eventId 或原序号。当前几个样本不能证明所有活动完整。需要版本化的完整关联／序号合同；不能重新编号后声称保留原命令语义。
2. **四服静态关联。** 静态服务是 `jp/intl`，实时服务是 `jp/tw/kr/en`。现有样本 ID、时间相同不能代替正式关联合同。本实现不推断 tw/kr/en 到 intl 的映射，也不访问旧源补图；歌名和素材暂使用 ID／占位。需要正式映射并核验 event→challenge→music→素材，后续静态请求必须固定 release、校验响应身份，再补齐同输入视觉验收。
3. **精度覆盖。** 四服样本分数都在安全整数范围，不能推论所有未来分数；越界保留空槽。若需要更大整数，上游须提供精确十进制合同。

因此本批不能宣称完整保留歌曲序号／名称筛选／素材体验，不能直接进行生产切换。默认旧来源继续正常工作；分数表迁移不在本批范围。

## 证据与验证入口

合同来自 [公开类型与 Worker](https://github.com/haneoka-gakuen/haneoka/tree/61061658da3f59dfd022b2dc44cc06f2de0dc9b5)、[OpenAPI](https://docs.haneoka.org/openapi.json) 和 [版本合同](https://docs.haneoka.org/concepts/servers-and-releases/)。JP 样本固定 `r-2d7fe14470e2e90b2aae`，intl 固定 `r-8da1a1f914879ac54d8a`。不复制上游实现，来源及许可见 [THIRD_PARTY](../THIRD_PARTY.md)。

`python -m unittest discover -s tests -p 'test_haneoka_cutoffs.py' -q` 覆盖合成四服数据、原位空槽、整数边界、身份／时间拒绝、缓存重启／损坏／写盘失败、来源切换、旧库兼容、序号拒绝及图片输出。完整回归为 `python -m unittest discover -s tests -q`。真实 API 样本、样图、CI 与安装证据随版本交接；离线通过不证明静态关联完整、生产采样或 QQ 手机送达。生产切换与 QQ 验收分别授权。
