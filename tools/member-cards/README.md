# 三团成员卡详情样稿

用 Chromium 把一张卡的查询数据排成完整的 3× PNG，供审阅三团定稿。此工具有独立的依赖和命令入口。线上 `/查卡` 继续使用 Taki 已有的渲染器。

| 乐队 | 可复现的卡 ID | 建议先看 |
| --- | --- | --- |
| MyGO!!!!! | 4、72、74 | 卡 4 的两折歌词本，卡 72 的三折活动剪片与卡池票根 |
| 梦限大 MewType | 61、80 | 两位成员的设备高亮、属性调音台与五档技能读数 |
| Ave Mujica | 7、73、76 | 卡 7 的两页剧场，卡 73 的活动海报与三张金色卡池票 |

## 从干净的 checkout 出图

需要 Node.js 22 或以上、pnpm、Python 3.10 或以上、Pillow 和 Chromium。以下命令从仓库根目录开始，在同一个 Python 环境运行。

```sh
cd tools/member-cards
python -m pip install Pillow
pnpm install --frozen-lockfile
pnpm exec playwright-core install chromium
python prepare_assets.py
node render.cjs examples/card-72.json out/mygo-72.png
node render.cjs examples/card-61.json out/mewtype-61.png
node render.cjs examples/card-73.json out/ave-73.png
```

已有 Chrome 或 Chromium 时，可以把环境变量 `BROWSER_PATH` 设为可执行文件的绝对路径，跳过浏览器下载。`PYTHON` 可指定 Python 解释器，Windows 默认用 `python`，其他平台用 `python3`。

首次准备会按清单下载图片和字体，校验后写入 `.cache/`，并从游戏描边和渐变生成卡框。之后可以离线渲染。使用其他缓存目录时，给准备脚本传入 `--cache 目录`，并把同一目录作为 `render.cjs` 的第三个参数。

## 换卡与核对

以同团的样例 JSON 为起点。`bandId` 选模板，`characterId` 选成员设备。卡名、日文副题、实装日期、原图与图标均由输入给出。`stats` 提供等级基础值，`cultivation` 提供特训、觉醒与满培养数据，梦限大还用 `levelCurve` 的逐级数据定位推子。

梦限大和 Ave 的每个技能提供 `sourceTexts`，依次为 Lv.1–5 的完整说明。模板按原句排列条件和读数。MyGO 复用 `clauses` 和可选的 `annotation` 排版笔记。`relations.events` 和 `relations.gachas` 都为空时收成两页，有记录时展开右页。卡 74 可核对仅有关联活动的情形，卡 72、73 可核对同一页有多个卡池的情形。

换用新卡时，把输入引用的图片放入同一素材目录，保持 JSON 中的文件名。随附清单覆盖表中的八张卡。卡面、徽标、技能图标、关联横幅的来源和校验值见 [素材清单](examples/media.json)。其中的游戏素材来自 [Haneoka](https://haneoka.org/) 提供的指定日服资源版本。

模板的装饰与样式分别位于 [MyGO](base/)、[梦限大](mewtype/) 和 [Ave Mujica](ave/)。字体使用仓内的 MyGO 手写字形，以及固定版本的 Fontsource 字体包、猫啃什锦黑、霞鹜文楷和 Google Fonts 字体。字体上游见 [Fontsource](https://fontsource.org/)、[猫啃什锦黑](https://github.com/maoken-fonts/MaokenAssortedSans)、[霞鹜文楷](https://github.com/lxgw/LxgwWenKai) 和 [Google Fonts](https://github.com/google/fonts)。
