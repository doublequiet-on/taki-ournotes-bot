# 成员卡列表网页样稿

按成员一人一行排列卡面，左侧显示头像与姓名，有艺名时另起一行。每张卡使用游戏属性角标和稀有度卡框，底部三列依次显示队长技能、演出技能和激奏技能的 Lv.5 摘要值，卡面下方显示 ID。各行共用三列宽度，可以上下比较百分比、次数和 pt。

本工具独立生成设计评审用的三倍 PNG。线上 `/查卡` 继续走既有回复链。列表读取已确定的分组、顺序、技能读数与单位。详细条件见卡牌详情。

## 复现

需要 Node.js 22+、pnpm、Python 3.10+、Pillow 和 Chromium。以下命令从仓库根目录开始。

```sh
cd tools/member-list
python -m pip install Pillow
pnpm install --frozen-lockfile --ignore-scripts
pnpm exec playwright-core install chromium
python prepare.py
node render.mjs pair out/pair.png
node render.mjs all out/all.png
```

已有 Chrome 或 Chromium 时可把 `BROWSER_PATH` 设置为可执行文件的绝对路径。首次准备从固定版本取得公开资源，逐文件校验 SHA-256，并从游戏描边和渐变生成卡框。后续出图只读取本地文件。页面由 Lit 排版，等待实际卡面和字体加载、对齐技能列后，由 Chromium 截取整张列表。

| 样例 | 内容 | 成员 / 卡数 |
|---|---|---:|
| `pair` | 椎名立希、Doloris / 三角初华，含 `+50pt` 和 `+200%` | 2 / 9 |
| `taki` | 椎名立希 | 1 / 4 |
| `blue` | MyGO!!!!! 蓝色成员卡 | 3 / 4 |
| `birthday` | 生日稀有度 | 1 / 1 |
| `yellow-ssr` | 黄色 SSR | 7 / 7 |
| `all` | 该版本全部成员卡 | 25 / 81 |

换用其他查询结果时可运行 `node render.mjs CASE out/list.png LISTS.json`。样本中的 `cases[CASE].people[].ids` 给出每行的卡及顺序，`cards` 按 ID 给出卡面、属性、稀有度和三项读数。新卡引用的 PNG 需放入 `.cache/media/`。整表原图用于检查完整性。进入生产回复链时还需验证手机分图与每图字节预算。[查看样本](examples/lists.json)

## 来源

展示使用 Haneoka 的 `cardTile`、`tileMedia` 和 `LazyImages`。准备脚本按[源码清单](examples/haneoka.json)读取提交 `b0bf8aff023095f2a6693c71b88b5fde5a8cc796` 的文件及 MPL-2.0 许可证。角色、卡面和技能来自日服公开版本 `r-c7a12eb078c437aee966` 的冻结设计样本。图片路径、摘要及稀有度颜色见[素材清单](examples/media.json)。游戏美术与数据权利归原权利方。字体使用 Fontsource 提供的 Roboto Variable 和 Noto Sans SC Variable，版本见锁文件。
