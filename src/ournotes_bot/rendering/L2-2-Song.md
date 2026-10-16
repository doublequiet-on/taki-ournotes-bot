# L2-2 Song Rendering

## Purpose

把歌曲及谱面结果投影为图片，复用歌曲身份、颜色和激奏展示；同时维护活动歌曲榜线视图。

## Position inside parent L2

属于 [Rendering](../../L2-Rendering.md) 的歌曲业务视图；使用 Core 的视觉基础，消费 Query/Data 结果。

## Inputs

已选歌曲及分页文本、Song/Chart 和可选 score 字典。当前 `render_song_list` 接收歌曲列表，不直接接收整个 SongAnswer。

## Outputs

歌曲列表、谱面和活动榜线的编码图片字节；不输出查询计划，不获取完整谱面，不发消息。

## Core Flow

用歌曲身份区域保留标题/颜色/激奏信息 → 按列表、谱面时间线或榜线视图布局 → Core 统一编码。
榜线由 [cutoff_trends.py](cutoff_trends.py) 消费捕获的当前值和历史，默认三曲横排一张总览，指定一曲保留单曲卡；非常规数量按活动原序号完整分页，绘图并发限制为一个。真实时间、线性整数坐标、缺口及相同分数均保留；零点／单点、完全同值的重叠排名、活动与单曲有效期明确标注。折线保留全部观测，仅稀疏显示方点标记以免遮挡。复用原图片编码预算，不能清楚展示时退完整文字。合成手机宽度样图脚本为 `scripts/preview_cutoff_trends.py`。
完整谱面由 `qq._chart_image` 按独立来源配置准备；默认 `load_chart_score`，Haneoka 经 `load_chart_data` 返回相同 score 及来源状态。Haneoka 来源／旧缓存状态追加在原页脚，布局和图片预算保持；取不到时可绘制不含完整音符的资料图。

## Dependencies

Data 的 Song/Chart/traits、[Core](L2-2-Core.md) 画布/素材/编码。
Current 直接引用 song_traits 的显示常量；这属于领域表现依赖，不是独立来源刷新器。

## Boundary with sibling L2-2 modules

与 [Card](L2-2-Card.md) 共用 Core，但不共享卡牌模式分派或技能详情语义。歌曲激奏图标属于本子域业务表现，下载机制归 Core。
不把缺失谱面或分析数据用模型补成可视化内容。

## Files belonging to this subdomain

[event_cutoff_visuals.py](event_cutoff_visuals.py)：活动榜线趋势／排名表视图，复用 Core 主题和编码器；排名表共享排名列和全部选定歌曲，最多25行一页，按高度与文字回退预算可减少行数，返回 `CutoffPage(image, text)` 供平台预算分配；只加载来源白名单素材，同 URL 合并请求并遵守限流退避，不重新取榜。[test_event_cutoffs.py](../../../tests/test_event_cutoffs.py) 与 [preview_event_cutoffs.py](../../../scripts/preview_event_cutoffs.py) 验证视图、单曲有效区间、缺图、长名、完整整数及多曲分页；`preview_cutoff_trends.py` 可生成固定合成的三曲趋势、附近表和完整100名分页。

[visuals.py](../visuals.py) 中 `render_song_list/render_chart`、歌曲身份/激奏辅助及谱面绘制。
对应 [visuals.py 的唯一文件头 L3](../visuals.py)；本能力的主体实现保留在该文件。

## Relevant Tests

榜线当前值、数字ID与用户名使用同一份 `BoardSnapshot`；趋势当前值及排名表均显示两者，缺失字段独立标注，长数字或名字按单格换行，历史不携带玩家身份。`CutoffPage.text` 保存同一批排名／分数／ID／用户名，图片失败后无需取榜；回归见 [test_cutoff_player_ids.py](../../../tests/test_cutoff_player_ids.py)。

[test_visuals.py](../../../tests/test_visuals.py)：歌曲身份、长标题及原生标记缺图回退；
[test_chart_data.py](../../../tests/test_chart_data.py)：时间线、跨栏长键及难度身份；
[test_song_traits.py](../../../tests/test_song_traits.py)：图文 traits 同源及预算。

## 当前实现与边界

歌曲/谱面主体仍在根 visuals.py；榜线视图独立位于 rendering/event_cutoff_visuals.py。visuals 同时服务 Core 与卡牌入口，只有一个文件头 L3。素材可按需读取，图片失败由 QQ 退捕获的文字；本域不发送替代消息。


向上阅读：[仓库 L1](../../../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。

`visuals.py` 保留 `render_song_list/render_chart` 与歌曲身份／激奏辅助；完整谱面来源由 `sources/chart_data.py` 路由，条件由 `query/song_query.py` 解析。回归 `test_visuals.py`、`test_chart_data.py`。

QQ 新入口为 [song_layout.py](song_layout.py)、[song_detail_layout.py](song_detail_layout.py)、[chart_layout.py](chart_layout.py)、[ranking_layout.py](ranking_layout.py)。[note_skin.py](note_skin.py) 只消费注入的原版 atlas；数据获取在来源层。
歌曲详情的技能事件按时间排序，判定统计按来源拍号与 binary32 小节时钟生成长条八分拍点，通过 [chart_judgments.py](chart_judgments.py) 支持变拍、隐藏/可见 guide、自动控制点、重合单点吸收和合并端点；恢复密度、五秒覆盖数、占比和排序。固定公开谱面参考向量见 `test_image_repairs.py`，不把判定统计等同于算分模型。谱面使用 trace 原生贴图；固定节拍列只显示栏号，所有栏横向连续排列为单张整首图；9600 万像素／30000 像素边长为谱面专用预算，字节上限沿用编码器默认预算，超限退文字。`0:00`、15 秒间隔和末节点时间按来源 BPM 时钟定位；技能实心／空心编号牌、激奏三角／方块区分起止，跨栏断口不冒充端点，两类侧线同宽，端点图标与竖线分离并按局部 4 倍覆盖采样抗锯齿；纯文字时间标记与技能端点在同一留白区域避让。歌曲时长优先主资料音频长度，谱面时长按末节点计。回归见 `test_chart_annotations.py`、`test_visual_sample_fidelity.py`。
