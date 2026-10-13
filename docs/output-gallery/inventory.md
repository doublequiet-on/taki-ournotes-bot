# 可达输出盘点

核对基线4e22db866c9318479fc7aa4a710e68490503a3fa。相对交接6563f0a仅开发说明变化，绘图代码相同。生成物另记录实际运行SHA及生产文件哈希。

|模板|用户入口 / 捕获结果|真实绘图分派|代表性用例|
|---|---|---|---|
|song-list|查曲、本地自然语言、翻页；song_selection/QueryResult.songs|qq._image_from_result → visuals.render_song_list|1/4/16/17/30/31条所有当前页、颜色激奏、长名/未知Note、三语言、缓存素材|
|chart|查谱面、歌曲选择/难度切换；result.chart/parsed chart|qq._chart_image → visuals.render_chart → _draw_score|四难度、缺谱但有资料、旧缓存、长谱跨栏长键、真实缓存完整EXPERT|
|member-list|查卡 条件；catalog.visible|render_card_list → card_visuals.grid → member_list_visuals.render|单命中仍列表、SSR/BD、16/17/30/31、摘要/未知、长条件、真实缓存|
|member-detail|查卡 ID、选择续查；catalog detail|render_card → card_visuals.detail → member_detail_visuals.render|完整技能、BD、同类多效果、长文本/旧缓存/未知、真实缓存|
|member-art|查卡面 ID；catalog art|card_visuals.art → visuals._bytes|匹配缓存原卡面原比例；缺图文字回退|
|snap-list|查SNAP/查支援卡 条件；support catalog|render_support_card_list → grid → support_visuals.render_list|EX、单命中、多角色去重、16/17/30/31、长条件、未知、缓存|
|snap-detail|查支援卡 ID、详情续查|render_support_card → detail → support_visuals.render_detail|EX多技能、秒/%/LIFE、未知与不适用、长文本/旧缓存、缓存|
|snap-art|查支援卡面 ID|card_visuals.art|匹配缓存原比例；缺图文字|
|cutoff-trend|查榜线无排名；result.cutoff|qq._render_prepared → render_cutoff → cutoff_trends.render_trends|三曲/单曲/双曲、0/1点、缺口/重叠/混合缺数据、四服时间|
|cutoff-ranks|查榜线带排名；result.cutoff|render_cutoff → _tables|附近50/100、区间/离散/单名、单/三曲1–100所有页、长ID/用户名/大整数|
|easter-947|完整单命令查卡947，普通查询前拦截|qq._easter_egg_reply → assets/card-947.jpg|原JPEG；缺文件文字回退，非游戏卡图|

一般路径：_prepare_reply → resolve_command / AIQueryParser.answer_with_plan → _render_prepared → _image_from_result。续查：_prepare_followup → execute_followup → _render_prepared。榜线返回有序CutoffPage，经_expand_replies核算整批预算；画廊保留全部渲染页，另演示预算文字终态。

边界注入捕获结果，调用相同生产绘图函数；证明模板表现，不证明合成组合可在真实数据命中。自然语言复用模板不另算输出族，模型辅助分支未运行。

## 纯文字路径

- 活动信息：activity_query → result.activity.text，无绘图分派；合成活动通过真实查询入口导出，未查询远端活动。
- 帮助/介绍/语言/数据状态/调试/缩写/未开放命令：handle_command固定或检索文字。
- 明确仅数值：render_cutoff返回空页，保留完整数值。
- 字段短答：QuerySpec.field使_image_from_result返回None；数量/单字段/技能由检索产生。
- 无结果、未知实体、歧义、参数错误、页码超限、来源不可用：捕获错误或文字。
- 无权限/不满足群触发条件：接入层过滤，不是新图片模板；未启动QQ/真实身份验收。
- 队列忙/等待超时、批次/被动回复预算超限、无续查/过期/资料变化：容量或续查终态；预算例离线执行，其余由源码及既有回归覆盖。
- 绘图失败/原卡面缺失/彩蛋缺文件/上传失败：文字回退。前三类离线示例；上传未测。

## 内部、兼容与已移除

_image_reply是兼容预览helper，线上使用_prepare_reply；render_catalog与_canvas/_draw_score等是内部函数，不另算模板。歌曲分数表已移除，活动排名表保留。Haneoka配队器不属于Taki输出。

当前分页16条。续查30条上限针对可见选择；总结果30/31均分页，不能声称测试到31个可见选择。导出这些集合所有页，manifest记录真实context.visible。

## 未测与边界

完整指有限模板与关键状态，不穷举游戏记录组合。默认夹具缺卡面，匹配公开缓存组才能验证原比例。Haneoka夹具时间戳为测试数据，不能当近期上线数据。榜单身份全部合成。

线上冷素材、上传/回执/客户端、并发/峰值内存未测。参考网站未运行；CSS阅读不等于视觉验收。逐例缺素材/失败见manifest。
