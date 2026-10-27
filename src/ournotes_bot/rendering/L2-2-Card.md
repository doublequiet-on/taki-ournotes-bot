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

QQ 新入口为 [catalog_layout.py](catalog_layout.py)、[card_detail_layout.py](card_detail_layout.py)，捕获完整记录、三种技能与本排对齐；原卡面沿用旧入口。
角色卡列表保持原始确认稿：人物头像与姓名在左栏；每名人物按红蓝绿黄紫纵向分行，同色内 SSR→BD/EX→SR→R 向左紧排，最多八卡换行，不预留稀有度空列；正文32px，原稿卡面和文字尺寸的两倍输出。留影卡使用独立紧凑排版路径：多人组合置顶，按来源乐队ID、人物ID稳定排列，单人及完整多人组合各自合并左栏，每页每组只显示一次头像姓名。多人头像最多三列，姓名逐一对应；仅组间画分隔线，组内不画线。保留颜色行与稀有度顺序，卡面288×162、列距348，ID和24px完整技能在各自卡面下方，宽度随实际同色最大列数变化。单图优先，达到既有像素、边长或字节预算才按完整行拆分；跨页重画完整组标识，缺人物元数据仍保留已捕获姓名。同一卡仅展示一次，卡牌总数按唯一ID计数，图片区间标为展示位置。角色筛选传规范人物名，避免别名被绘图层误当成全人物列表。EX双支援分别保留“支”标签。缩略图、属性与原生稀有度素材仍由注入加载器并发准备。参考版本、哈希与验收入口见 [列表原稿约定](../../../docs/CARD_LIST_REFERENCE.md)。

`catalog_layout` 的角色卡和 SNAP 列表按最大候选页准备解码素材（含续页人物头像），缩页编码重试复用该页结果及缺图结果，页作用域退出后全部关闭；不另建跨页解码缓存，沿用底层 32 MB 缓存返回的独立副本。五种稀有度图标字节在一次渲染开始时捕获，绘制只查准备映射，不再访问原素材加载器。分组、字号、编码、顺序与展示范围保持。该作用域约束解码素材工作集，不替代整次回复编码字节预算。

详情绘制先串行捕获整次渲染共用的主图、乐队标志、属性图与稀有度字节，再按最大候选页准备头像、技能图和关联素材；缩页复用该候选，退出即释放。绘制只查已准备映射，缺失键属于契约错误；字体、边框与图标缓存仍由主题层所有。
绘制完成后、编码接管画布前，复用 Core 的大图空闲页回收阈值归还临时绘制缓冲；候选素材仍由页作用域持有，编码缩页时继续复用。
