# L2 Rendering — 结果到图片字节

## Purpose

将已选择的游戏事实转成一致、完整、符合输出预算的图片。统一主题、素材处理和编码是该系统域存在的理由；不按每种图片另建 L2。

## Responsibilities

承接查询选择，绘制歌曲/谱面/效率和卡牌视图，共用布局原语、字体、素材加载与完整图片编码。保留未知、旧数据、分页和条件说明。

## Non-Responsibilities

不解析用户问题、不重做业务筛选、不调用 AI、不上传或发送 QQ。完整谱面数据获取属于 Data，由 QQ 回复准备调用。Current 成员列表会直接取得 Haneoka 摘要，这是明确例外，不描述为已完成的纯渲染层。

## Inputs / Outputs

输入为已选择的记录、专项结果、分页/条件说明、谱面字典和素材。
**公开 render 入口主要输出编码后的 `bytes`，不是 Pillow Image**；部分入口无图可返回 `None`，也可抛异常。Pillow Image/画布是内部步骤。
活动歌曲榜线返回完整有序的 `CutoffPage(image, text)` 元组；默认三曲趋势横排合图，显式排名共享歌曲列、按排名分页，每张附对应捕获结果的有界文字回退。平台在发送前统一核算整批回复预算，渲染器不发送消息。
趋势合图或单曲卡使用已捕获的各曲历史，五档排名视觉标识一致、纵轴独立；真实时间、整数分数和缺口保留。绘图并发为一，历史读取和平台发送均不在渲染器内。

## Core Contracts

Query 的 `MetaAnswer` 捕获表格数据和歌曲记录；卡牌入口接收单卡或已分页列表；歌曲入口接收已选歌曲与页脚。`visuals._bytes → image_output.encode_image` 汇总编码预算。预算常量是项目保守策略，不宣称是 QQ 官方硬限制。
分数表每榜沿用已有三处数值区域、字号和行布局；双榜并列共图后统一执行图片预算，增加场景标题和选择号，单榜样式保持。scope超长进入既有说明区；图文消费同一捕获结果，绘图不重新查询或排序。

## Internal Subsystems

- [Rendering Core](ournotes_bot/rendering/L2-2-Core.md)：共享主题、素材、布局原语和编码。
- [Song Rendering](ournotes_bot/rendering/L2-2-Song.md)：歌曲身份、谱面时间线、效率表的业务投影。
- [Card Rendering](ournotes_bot/rendering/L2-2-Card.md)：成员/SNAP、列表/详情/原卡面的分派及视觉语义。

Core 与 Song 目前同在 `visuals.py`，是已存在的职责切面，还不是独立可导入子包。三个子域无需再按“列表/详情/编码器”机械分层。

## Dependencies

QQ/离线预览 → Rendering → Query 结果和 Data 记录；业务绘图 → Core → Pillow/素材 I/O/路径工具。Current 还直接依赖来源常量与 Haneoka 摘要，并存在卡牌簇回边；目录分组不代表现有回边已消除。

## State / Side Effects

CPU/内存密集绘图、字体文件读取、素材缓存和允许来源的下载；成员列表还可能触发来源 JSON 刷新。输出字节只属于当前请求，不采用跨请求共享输出文件。

## Failure / Degradation Boundaries

缺素材可占位或退文字图标；原卡面缺失可无图；编码或绘图失败由 QQ 退文字。绘图器不决定上传后的重试。成员摘要未知不能替换完整技能，不把缺失属性当作零或“不适用”。

## Navigation to L2-2 / L3

范围是 `visuals.py`、`image_output.py`、卡牌绘图簇与 `support_summary.py`；符号切面和 文件头 L3 导航由三个子域维护。

## Relevant Tests

[回复准备](../tests/test_reply_pipeline.py) 验证选择同源与绘图失败回退；
[平台隔离](../tests/test_platform_boundary.py) 验证核心绘图可脱离 QQ。布局与编码测试在子域；人工视觉步骤仍见 [DEV_GUIDE](../docs/DEV_GUIDE.md)。

## 当前实现与边界

visuals.py 的主体实现原位保留；六个既有独立绘图文件位于 rendering/。Core、Song、Card 是职责切面，不创建空 core/song 子目录，不拆主题或歌曲绘制。成员摘要的绘图时读取及共享 helper 回边保留。

向上阅读：[仓库 L1](../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。
