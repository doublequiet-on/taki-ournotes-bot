# 当前字段表

当前交互与属性口径以 [图片输出说明](../IMAGE_OUTPUT.md) 为准：按 ID 查详情、`/继续` 续发，角色与留影卡详情展示满级基础值；下文保留原设计记录。
当前字段来自捕获结果，catalog来自所选来源的详情。未知、缺失、不适用分开表示；未批准新增字段。

|模板|字段来源|必需性/条件/单位|未知与换行|
|---|---|---|---|
|歌曲列表|Song.id/title/localized/band/jacket_url|身份、乐队、可缺封面|长名按现有布局处理；缺图占位|
|歌曲列表|charts[].difficulty/display_level/notes|已知难度、等级、来源Note数|None为暂无资料，不能填0；保留难度色|
|歌曲列表|traits.color/missions及来源覆盖|确认后展示颜色、JUST/COMBO/LUCK|未知保留提示，缺图标退文字|
|所有列表|SongAnswer.footer/CardAnswer.footer/page_notice|总数、当前页、可见范围、下一页、来源警告|允许换行，不是记录本身|
|谱面|Song身份/作词作曲、已选charts、ChartData.score.events/notes/difficulty/notice|资料可有而音符缺失；位置/时间按谱面契约|缺谱明确提示，完整轨迹不裁切；Note不按节点数推算；来源可换行|
|成员列表|Card.id/thumbnail_url/rarity/card_type、catalog.member_summary|附属ID、稀有度框、属性、Lv.5三类摘要/条件|未知显式；过长摘要可提示看详情，不以摘要代全文|
|成员详情|Card身份/full_url、catalog.tags/tags_complete|角色/乐队/卡名/稀有度/属性/得意乐曲/完整卡面|映射未确认或未获取；标题/标签换行|
|成员详情|catalog.stats_level1|已核实三有限非负值；Lv.1/Rank1/觉醒0；综合力三数和|未知不展示培养数值，不把缺失作0|
|成员详情|Skill.kind/name/description/localized、skill_languages|每个Lv.5完整队长/演出/激奏技能，同类多个效果保留|无详情“不代表无技能”；全文分区换行；其他kind独立区|
|成员详情|localized.title/start_at/detail_stale/catalog.source|别名、上游含时区时间、旧缓存/来源|无时区未确认；不当生成时刻|
|SNAP列表|SupportCard身份/characters/rarity/type/thumbnail、技能摘要|角色多选去重；EX只属SNAP；演出/激奏|分类未知不当不适用；条件换行|
|SNAP详情|SupportCard身份/full、catalog.stats_level1、Skill/skill_slots/categories|原比例；三属性%不合计综合力；多演出技能，秒/%/LIFE|确认不存在才为不适用，未获取不当不存在；全文换行|
|两类卡面|full_url|仅contain与均匀编码缩小|缺图文字，不生成AI卡面、不改变比例|
|榜线共用|EventSnapshot.server/title/start_ms/end_ms/banner、EventSong身份/窗口/来源|活动时区、歌曲、有效期；非确认终榜|缺封面占位，长标题换行|
|趋势|HistoryView.points[].time_ms/scores/source_ms、warning/断线|排名1/2/3/10/100；整数分数与真实时间轴|0/1点不制造曲线；缺口保留；曲间纵轴独立|
|排名表|request.ranks/mode、BoardSnapshot.scores/player_ids/player_names|附近/区间/离散/单名，同一行身份与分数|缺榜/身份明确；长ID/名/分数按现有测量分页|
|榜线来源|fetched_ms/server_ms/received_ms/status/warnings|源采集/源时间/本地接收/延迟，时间ms|生成时间不代替刷新时间；警告换行|
|彩蛋|包内JPEG|直接字节输出，无字段布局|缺文件文字回退|

新增字段尚未批准。草图注明名称、可靠来源、适用模板及优先级；来源无法确认保持待定。单图容量、@、缓存与优化后续讨论。
