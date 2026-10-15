# 主题参考

当前交互与属性口径以 [图片输出说明](../IMAGE_OUTPUT.md) 为准：按 ID 查详情、`/继续` 续发，角色与留影卡详情展示满级基础值；下文保留原设计记录。
Taki主题：src/ournotes_bot/visuals.py；PAPER #F1F2F8、INK #202B49、MUTED #45516F、ACCENT #475B96、BORDER #AAB5D0、SURFACE #E6EAF4。共用布纹背景、RENDER_SCALE=2；卡框/难度/属性属于语义色。本次字体文件哈希在manifest，不交付字体文件。

Haneoka锁定434afb7a598d43e6a8d78e07560577b44643c2e2；design-tokens锁定7a7d83edc24bc9bb55a9d35526b4f7b61d31d643。

|参考|指定源码|状态|
|---|---|---|
|应用令牌|[src/styles/base/tokens.css](https://github.com/haneoka-gakuen/haneoka/blob/434afb7a598d43e6a8d78e07560577b44643c2e2/src/styles/base/tokens.css)|已读取锁定版本，含语言字体/密度|
|配色角色|[src/schemes.css](https://github.com/haneoka-gakuen/design-tokens/blob/7a7d83edc24bc9bb55a9d35526b4f7b61d31d643/src/schemes.css)|已核对默认浅/深静态角色|
|字体/空间/形状|[src/tokens.css](https://github.com/haneoka-gakuen/design-tokens/blob/7a7d83edc24bc9bb55a9d35526b4f7b61d31d643/src/tokens.css)|已读取字体层级与圆角|
|列表|[src/styles/components/list.css](https://github.com/haneoka-gakuen/haneoka/blob/434afb7a598d43e6a8d78e07560577b44643c2e2/src/styles/components/list.css)|已读取，图文行由内容撑高|
|详情|[src/styles/patterns/detail.css](https://github.com/haneoka-gakuen/haneoka/blob/434afb7a598d43e6a8d78e07560577b44643c2e2/src/styles/patterns/detail.css)、src/styles/card-detail.css|已读取分区/标签角色|
|字体与卡片|src/lib/ui-fonts.ts、src/lit/shared/card-tile.ts|已读取语言字体加载与卡片组成|

默认浅色已核验：primary #555994、surface #fcf8fd、surface-container #f0edf1、on-surface #1b1b1f、outline-variant #c7c5d0。深色对应 #bfc2ff、#131316、#201f23、#e5e1e6、#46464f。来源是schemes.css默认:root和深色选择器；乐队主题还有独立角色，不将它们混称默认。

应用字体以Roboto Variable搭配对应语言Noto Sans JP/SC/TC/KR Variable。默认/紧凑密度通过row-height等空间令牌切换，保留字体层级。列表文字body-large为1rem/1.5rem；title-medium同为1rem/1.5rem但字重500；body-medium为0.875rem/1.25rem。圆角small/medium/large为8/12/16px。网站rem与图片逻辑像素的对应尚未决定。

列表采用最小行高、16px水平留白和随内容增加的高度；详情分区使用pane-gutter、明确标题与surface容器。这些是静态源码参考，独立参考页未运行：主题模式/语言/密度/浏览器/视口均未做网站视觉验收，无网站截图。当前画廊只展示Taki现状。

核验文件SHA256：

|文件|SHA256|
|---|---|
|Haneoka tokens.css|21ccf99dc6f13572a3b2294ad987f26a9fb83566c19c30ced56d031fd486c45f|
|design-tokens schemes.css|995ae28ea244634470ac8b7d9fca0915e0fe4d5f8ff768e4fe81ba420768adc1|
|design-tokens tokens.css|81ed80b45d4c9050ba3df203ad46de24797be8d9f97261f0ebb1d12391f1c930|
|components/list.css|c555d9d3968108a0fc4067476965d33ea32f3e90afe919a04845f5d3502800c5|
|patterns/detail.css|09e6e47776d73417226f22985e811ae21b70e535d70cd64f62133b88d16a4328|
|card-detail.css|9bf737679c4f04839989301b5fd0a4de6bb5177899aa2346b3ab6342eede2da9|
|ui-fonts.ts|b0db462592ac446a317cf9959fde6b91370997431aa16c8eda12b162d3075817|
|card-tile.ts|fa00e239bcfd0578956f72965b2f63adf82138bb80d21c9831f5b759abcffa27|

后续统一字体层级/留白/背景/边框/分组，保留属性/难度/稀有度语义。网站导航、悬停、筛选交互不照搬图片。主题接入尚未实施。
