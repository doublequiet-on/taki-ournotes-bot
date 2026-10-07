# L2-2 Song Rendering

## Purpose

把歌曲及谱面/效率结果投影为图片，复用歌曲身份、颜色和激奏展示。这三类画面共享同一歌曲语义和来源状态，合为一个稳定子域，无需三个对称节点。

## Position inside parent L2

属于 [Rendering](../../L2-Rendering.md) 的歌曲业务视图；使用 Core 的视觉基础，消费 Query/Data 结果。

## Inputs

已选歌曲及分页文本、Song/Chart 和可选 score 字典、`MetaAnswer` 捕获的 cells/notes/song_records。当前 `render_song_list` 接收歌曲列表，不直接接收整个 SongAnswer。

## Outputs

歌曲列表、谱面和效率表的编码图片字节；不输出查询计划，不获取完整谱面，不发消息。

## Core Flow

用歌曲身份区域保留标题/颜色/激奏信息 → 按列表、谱面时间线或效率表布局 → Core 统一编码。
效率图使用已捕获的表格和歌曲记录，不再次读取效率仓库。
分数表新模式仅替换原三处数值的标签和值；逻辑宽1500、基础行高112、主题、字体、封面裁切和难度标识保留。条件超过原 scope 四行时转入既有说明区，保留字号和完整条件。新封面仅增加已确认 Moenotes jacket 白名单；激奏图标沿用既有视觉素材例外。来源合同和冻结验收见 [META_OPEN](../../../docs/META_OPEN.md)。
单曲新模式沿用900宽文字卡，标题使用捕获的分数表标题；文字先列歌曲与主要结果，再列条件和来源。表格时长星号对应说明区的本页行号及实际口径，技术诊断不占图片；旧来源单曲标题仍为歌曲效率。
榜线由 [cutoff_trends.py](cutoff_trends.py) 消费捕获的当前值和历史，默认三曲横排一张总览，指定一曲保留单曲卡；非常规数量按活动原序号完整分页，绘图并发限制为一个。真实时间、线性整数坐标、缺口及相同分数均保留；零点／单点、完全同值的重叠排名、活动与单曲有效期明确标注。折线保留全部观测，仅稀疏显示方点标记以免遮挡。复用原图片编码预算，不能清楚展示时退完整文字。合成手机宽度样图脚本为 `scripts/preview_cutoff_trends.py`。
完整谱面由 `qq._chart_image` 按独立来源配置准备；默认 `load_chart_score`，Haneoka 经 `load_chart_data` 返回相同 score 及来源状态。Haneoka 来源／旧缓存状态追加在原页脚，布局和图片预算保持；取不到时可绘制不含完整音符的资料图。

## Dependencies

Data 的 Song/Chart/traits、Query 的 MetaAnswer、[Core](L2-2-Core.md) 画布/素材/编码。
Current 直接引用 song_traits 的显示常量；这属于领域表现依赖，不是独立来源刷新器。

## Boundary with sibling L2-2 modules

与 [Card](L2-2-Card.md) 共用 Core，但不共享卡牌模式分派或技能详情语义。歌曲激奏图标属于本子域业务表现，下载机制归 Core。
不把缺失谱面或分析数据用模型补成可视化内容。

## Files belonging to this subdomain

[event_cutoff_visuals.py](event_cutoff_visuals.py)：活动榜线趋势／排名表视图，复用 Core 主题和编码器；排名表共享排名列和全部选定歌曲，最多25行一页，按高度与文字回退预算可减少行数，返回 `CutoffPage(image, text)` 供平台预算分配；只加载来源白名单素材，同 URL 合并请求并遵守限流退避，不重新取榜。[test_event_cutoffs.py](../../../tests/test_event_cutoffs.py) 与 [preview_event_cutoffs.py](../../../scripts/preview_event_cutoffs.py) 验证视图、单曲有效区间、缺图、长名、完整整数及多曲分页；`preview_cutoff_trends.py` 可生成固定合成的三曲趋势、附近表和完整100名分页。

[visuals.py](../visuals.py) 中 `render_song_list/render_chart/render_meta`、歌曲身份/激奏辅助、谱面绘制及效率表布局。
对应 [visuals.py 的唯一文件头 L3](../visuals.py)；本能力的主体实现保留在该文件。
`chart_data.py` 归 Sources，`song_query.py/efficiency_query.py` 归 Query。

## Relevant Tests

榜线当前值、数字ID与用户名使用同一份 `BoardSnapshot`；趋势当前值及排名表均显示两者，缺失字段独立标注，长数字或名字按单格换行，历史不携带玩家身份。`CutoffPage.text` 保存同一批排名／分数／ID／用户名，图片失败后无需取榜；回归见 [test_cutoff_player_ids.py](../../../tests/test_cutoff_player_ids.py)。

[test_visuals.py](../../../tests/test_visuals.py)：歌曲身份、长标题、原生标记缺图回退、效率行动态布局；
[test_meta_render_memory.py](../../../tests/test_meta_render_memory.py)：不同高度双榜的完整输出一致性、临时画布释放和同源标记捕获；
[test_chart_data.py](../../../tests/test_chart_data.py)：时间线、跨栏长键及难度身份；
[test_song_meta.py](../../../tests/test_song_meta.py)：`test_table_image_uses_captured_cells_and_text_fallback`；
[test_song_traits.py](../../../tests/test_song_traits.py)：图文 traits 同源及预算。

## 当前实现与边界

原歌曲/谱面/效率主体仍在根 visuals.py；榜线视图独立位于 rendering/event_cutoff_visuals.py。visuals 同时服务 Core 与卡牌入口，只有一个文件头 L3。素材可按需读取，图片失败由 QQ 退捕获的文字；本域不发送替代消息。

分数表双榜消费 `MetaAnswer.panels`，捕获同一份属性／激奏标记并测量布局，逐表绘制、合成后立即释放临时画布，只编码一次；分数表绘图复用进程内同一工作线程，避免多个查询同时占用大画布或各自保留绘图分配器内存。保留各自完整行、数值和列内选择号，与续查场景对应。单榜原画布、行高及样式保持，统一1.5MB／8192边长／1200万像素预算不增加。

向上阅读：[仓库 L1](../../../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。
