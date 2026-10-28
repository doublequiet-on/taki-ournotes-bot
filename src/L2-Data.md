# L2 Data — 资料与领域事实

## Purpose

为查询和展示提供可识别来源、可区分缺失与陈旧状态的游戏事实。主资料、领域记录和来源适配需要共同解释“系统知道什么”，值得一个完整 Data 域；不把 Catalog、每个网站各升为 L2。

## Responsibilities

维护规范化记录、主目录及详情补全；验证外部记录与本地身份的对应关系；管理主缓存和独立来源快照。来源刷新失败时，各自保留可用事实与状态，不伪造空值为零。

## Non-Responsibilities

不解释用户命令、不调用模型、不决定结果排序或页面布局、不发送 QQ。图片素材的解码与缓存属于 Rendering；别名对用户意图的解析属于 Query。

## Inputs / Outputs

输入为来源响应、缓存文件、刷新请求及本地实体身份；输出为领域记录、聚合仓库、来源快照和新鲜度/完整度。输出不是覆盖所有来源的一个原子快照。

## Core Contracts

- 已审核知识的纯文档／适用范围／证据类型在 [domain/knowledge.py](ournotes_bot/domain/knowledge.py)；本地文件校验和可选语义编码分别位于 [knowledge_files.py](ournotes_bot/sources/knowledge_files.py)、[knowledge_embeddings.py](ournotes_bot/sources/knowledge_embeddings.py)。批准状态和持久停用记录由 Application 持有；读取来源链接不会触发联网。

- 领域身份：`Song/Chart/Card/SupportCard/Skill` 由 [data.py](ournotes_bot/data.py) 定义；`Song.traits` 当前引用来源文件中的 `SongTraits`。
- 人物、卡池与活动基础资料由纯类型 [domain/info_catalog.py](ournotes_bot/domain/info_catalog.py) 定义，与 Haneoka 卡牌在同次 Catalog 发布和捕获；不从卡牌集合反推人物目录。
- 来源契约由 [Sources](ournotes_bot/sources/L2-2.md) 维护；不得把列表技能摘要当作完整详情，有限统计求值也不证明游戏实际得分。正式榜线保留公开发现／素材和本地历史例外。

## Internal Subsystems

[Catalog](ournotes_bot/L2-2-Catalog.md) 持有统一领域视图；[Sources](ournotes_bot/sources/L2-2.md) 将外部结构变为可信记录或快照。两者值得分开是因为本地持有/兼容策略与第三方 schema/获取策略有不同变化原因。


## Dependencies

主方向为 Query/Rendering/QQ → Data；Catalog 仓库调用 Sources，Sources 又需要领域类型与名称规则。当前这些类型和仓库同在 `data.py`，故文件级有回边。Data 依赖 Application 的路径工具，不依赖其组合根；核心无 QQ SDK 依赖。

## State / Side Effects

榜线历史独立保存于 SQLite，单曲以服／活动／挑战 ID 隔离，tracker 综合榜按完整活动身份存于独立压缩库。历史长期保留，低磁盘暂停；仅机器人运行入口按开关启动受控采样，详见 [运维说明](../docs/CUTOFF_HISTORY.md)。

主缓存、详情内存缓存、各来源快照和锁分开管理。仓库初始化可读专项缓存；`load` 可联网刷新；查询调用详情或效率入口也可引发网络和写缓存。跨来源不保证同一时刻更新，不能写成“纯内存只读仓库”。


## Failure / Degradation Boundaries

主来源失败且无可读主缓存时启动报错；有旧缓存可降级。新事实获取成功但落盘失败与“旧缓存”不同。来源身份或结构未验证时不合并；独立 Haneoka 故障不等同主目录不可用。细分状态见两个子域。

## Navigation to L2-2 / L3

源码范围为 `data.py` 与七个来源文件（含新增活动榜适配器）；精确所有权见下层源码入口。从两个子域进入文件头 L3，不在本层展开每个文件的字段。

## Relevant Tests

[平台边界测试](../tests/test_platform_boundary.py) 表达核心不依赖 QQ；
[状态测试](../tests/test_status.py) 表达主目录状态语义。来源及缓存兼容的具体测试由子域导航维护。

## 当前实现与边界

Catalog 的主体实现保留在根 data.py；Haneoka 适配器位于 sources/ 内部簇。既有领域类型、名称规则和延迟回边保留；新增资料记录放在纯 domain 模块。主缓存和各来源缓存仍保持原位置、格式与独立生命周期。

向上阅读：[仓库 L1](../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。

资料访问：`SongRepository.load/refresh`、按需详情、`song_traits` 和 `event_cutoffs`。主资料、完整谱面与榜线独立配置；主资料后台对齐整点／半点刷新。歌曲分数表数据源已移除。
