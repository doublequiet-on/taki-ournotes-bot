# L2-2 Catalog

## Purpose

持有统一可查询的领域记录及其缓存兼容语义。领域名称与仓库属于同一资料目录能力；无需把每个模型或缓存编码器提升为独立地图。

## Position inside parent L2

属于 [Data](../L2-Data.md) 的事实消费面。它负责“可见记录及其状态”，Sources 负责“外部结构如何可信地进入”。当前 `data.py` 同时承载领域定义与仓库编排。

## Inputs

主缓存、来源适配返回的记录/详情、同步请求与名称检索词；外部数据基址和主缓存路径由运行配置传入。

## Outputs

`Song/Chart/Card/SupportCard/Skill`、名称规范化与角色身份、`SongRepository` 的资料集合、技能/分类完整度、同步与缓存状态。dataclass 虽为 frozen，部分字段仍含 dict，不能推导为深度不可变的全局快照。

## Core Flow

`load` 检查主缓存有效性和扩展完整度，必要时 `refresh` 调 Sources 获取基础表及并发详情；仓库组合记录、附加已持有 traits，再保存兼容格式。
卡牌详情可在请求时按需补全并留内存缓存；效率仓库只在相关消费时加载/刷新。
`event_cutoffs` 持有独立活动榜仓库，构造不联网也不创建缓存；查询时再按请求服获取当前活动及整榜，不参加主资料 `load/refresh` 事务。
磁盘布局刻意保留旧读者兼容形状；traits 只关联到内存歌曲，不直接写入旧歌曲行。

## Dependencies

Data 来源适配器；基础标准库文件/锁/序列化；Query 与 Rendering 读取其结果。
`data → song_traits → data.normalize` 和 `data → yatta → data.领域类型` 是 Current 延迟回边，不是完成的单向层次。

## Boundary with sibling L2-2 modules

Catalog 决定聚合/主缓存兼容、何时补全详情；[Sources](sources/L2-2.md) 决定允许的响应、字段转换、来源身份和专项快照。
Project Yume 的并发详情调度位于 Catalog，不应按“网络相关”把整个 `refresh` 归给 Sources。

## Files belonging to this subdomain

唯一主实现：[data.py](data.py)。
`Chart.notes` 在内存中允许 `None`。持久缓存的旧歌曲行仍保留整数 Note，`chart_notes_known` 在顶层与 metadata 内记录相同可信状态，以便旧版普通加载／重写后仍可恢复；顶层状态优先。没有标记的旧零视为未核实。旧版完整刷新重建 metadata 且将缺失与零合并为 0，无法保证来源标记；再次升级时保守显示未知，需由新版重新获取来源数据确认。未新增数据文件或改变原子缓存写入，文本和图片使用相同语义。
从 [data.py 文件头 L3](data.py) 继续；来源适配器不重复列为本域所有文件。

## Relevant Tests

- [test_status.py](../../tests/test_status.py)：`test_untrusted_timestamp_and_failed_save_do_not_claim_success` 区分 unsaved 与同步成功。
- [test_support_data.py](../../tests/test_support_data.py)、[test_card_catalog.py](../../tests/test_card_catalog.py)：旧卡牌行、扩展字段往返、缺索引升级失败。
- [test_query.py](../../tests/test_query.py)：两类 `*_details_are_loaded_only_on_demand` 验证惰性详情。
- [test_song_traits.py](../../tests/test_song_traits.py)：`test_main_cache_does_not_write_new_song_fields`。

## 当前实现与边界

data.py 是唯一主体实现，类、名称规则、聚合仓库及缓存职责均保留。其来源引用指向 sources/；data 与来源的现有延迟回边保持，不新增 models/names/repository 文件或 re-export 空壳。字段和持久格式由代码维护，不随目录变化迁移缓存。

向上阅读：[仓库 L1](../../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。
