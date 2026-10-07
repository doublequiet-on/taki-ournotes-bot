# L2-2 Card Rendering

## Purpose

保持成员卡与 SNAP 的列表、详情、原卡面语义及共同视觉规则。文件间实际共享框、排版和分区，作为一个卡牌子域比各自独立子域更稳定。

## Position inside parent L2

属于 [Rendering](../../L2-Rendering.md) 的卡牌业务视图。QQ 已根据结果决定模式，card_visuals 再按成员/SNAP 分派到具体绘制器。

## Inputs

已选单卡或分页列表、条件/页脚、既有技能与属性、素材 URL。
Current 成员列表内部取得 Haneoka Snapshot，并不是已经支持外部传入 snapshot 的接口。

## Outputs

成员/SNAP 列表与详情、保留比例的原卡面图片字节；原图素材不可用时 `art` 可返回 None。内部 Pillow Image 不等同公开返回契约。

## Core Flow

QQ 从 `CardAnswer.request` 选列表/详情/原卡面 → visuals 兼容入口或 card_visuals → 成员/SNAP 绘制器 → Core 编码。
条件只命中一张仍保持列表语义；ID 才进入详情。
旧主来源的成员列表通过 Haneoka 身份匹配取得保守技能摘要，详情使用 Project Yume 完整字段；新 Haneoka Catalog 的摘要和详情随主快照捕获，绘图不重取版本，来源页脚对应实际来源。SNAP 摘要从已有 Lv.5 文本严格模板投影，不猜数值。

## Dependencies

Data 卡牌/详情/成员摘要；Query 的卡类别标签；[Core](L2-2-Core.md) 主题、素材与编码。
Current 专用绘图器互相复用私有 helpers，且反向引用 card_visuals；这些是本子域内部实现回边。

## Boundary with sibling L2-2 modules

与 [Song](L2-2-Song.md) 只共享 Core。卡牌框/详情分区无需提升到全局 Core，也不应为了美观复制第二套主题。
Haneoka 结构指纹/身份校验仍归 Sources；support_summary 属于此处，因为它只服务 SNAP 视觉摘要。

## Files belonging to this subdomain

[card_visuals.py](card_visuals.py)、[member_list_visuals.py](member_list_visuals.py)、[member_detail_visuals.py](member_detail_visuals.py)、[support_visuals.py](support_visuals.py)、[support_summary.py](support_summary.py)。
[visuals.py](../visuals.py) 中卡牌兼容入口属于该调用面；以上链接进入各文件头唯一 L3。

## Relevant Tests

[test_member_list_visuals.py](../../../tests/test_member_list_visuals.py)：列表条件、摘要及 SNAP 分流；
[test_member_detail_visuals.py](../../../tests/test_member_detail_visuals.py)：完整卡面/技能及未知值；
[test_support_visuals.py](../../../tests/test_support_visuals.py)：多技能、单位、不适用与缺失；
[test_card_catalog.py](../../../tests/test_card_catalog.py)：列表/详情/原图模式；
[test_haneoka_members.py](../../../tests/test_haneoka_members.py)：摘要附着身份，不能拿它替代视觉验证。

## 当前实现与边界

五个卡牌文件整体位于 rendering/，根 visuals 的委托入口保留。member_list_visuals.render 仍调用 get_snapshot，可刷新辅助事实；列表图与文字共用选卡集合，不代表所有辅助事实构成原子快照。布局、技能摘要与私有 helper 复用不变。

向上阅读：[仓库 L1](../../../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。

成员稀有度20（BD）采用粉紫到珊瑚红渐变框，列表和详情共享；SNAP不复用该映射。双效果激奏摘要增加一行条件空间，仍保留完整限制。
