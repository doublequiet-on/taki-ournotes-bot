# taki-ournotes-bot

Taki 是《BanG Dream! Our Notes》日服的非官方社区 QQ 查询机器人。本项目以 [NeriWST/ournotes-qq-bot](https://github.com/NeriWST/ournotes-qq-bot) 为基础开发；歌曲、卡牌数据来自 [Project Yume](https://bdon.yatta.moe/)，音符谱面文件按需取自 [MoeNotes](https://github.com/StarMoe-org/moenotes) 公共资源。不包含 QQ 凭据或游戏素材。

目前实现 `/查曲`、`/查谱面`、`/查卡`、`/查缩写`、`/数据状态`。歌曲可按名称、乐队、ID 或等级搜索；谱面显示等级、Note 数和可获取的完整音符谱面静态图；卡牌可按角色、卡名、乐队、ID 搜索，输入 ID 显示卡图及按需获取的数值。支持中、日、英名称和部分角色缩写。`/问` 是可选的自然语言入口：AI 只识别查询条件，最终答案仍由程序在本地数据缓存中检索。它不做攻略、推荐、问答或实时档线。未配置 AI 时，普通指令照常可用。

## 在本机试用

需要 Python 3.10 以上。PowerShell 进入**本项目目录**后执行。Python 发布包名为 `taki-ournotes-bot`；为兼容已有部署，命令仍叫 `ournotes-bot`，模块仍叫 `ournotes_bot`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\ournotes-bot.exe --help
.\.venv\Scripts\ournotes-bot.exe sync
.\.venv\Scripts\ournotes-bot.exe query 查谱面 100001
.\.venv\Scripts\ournotes-bot.exe query 查卡 1
.\.venv\Scripts\ournotes-bot.exe repl
```

`--help` 可在没有 QQ 凭据和数据缓存时运行；`query`、`repl` 和 `bot` 需要先有数据，首次使用请运行 `sync`。`repl` 是本地测试台，输入 `exit` 退出。`sync` 从 Project Yume 的公开 JSON 同步角色、卡牌、歌曲和歌曲统计，保存到 `data/ournotes-cache.json`；机器人运行中每六小时尝试刷新。首次网络失败且没有缓存时无法查询；已有同源缓存时会继续使用。`/数据状态` 分别显示本次查询的进程正在响应、最近一次**成功保存**的同步时间，以及本次运行已同步、使用已有缓存、同步失败后沿用旧缓存等状态。它不表示 QQ 连接始终稳定，也不代表上游网站的数据更新时间。

从源码目录安装时，`.env`、昵称表和 `data/` 仍放在项目根目录。若安装为普通 Python 包，程序从**运行命令时所在的目录**读取 `.env`，默认将缓存和 AI 额度记录写入 Windows 的 `%LOCALAPPDATA%\ournotes-qq-bot`，或 Linux/macOS 的用户数据目录；不会写进 Python 安装目录。安装包自带发布时的昵称表。需要修改昵称时，在自己的可写目录放一份 `query_aliases.json`，再在 `.env` 里设置 `OURNOTES_ALIAS_FILE=该文件的完整路径`。`OURNOTES_CACHE_FILE` 若填相对路径，则相对于 `.env` 所在的运行目录。

## 连接 QQ

可以复用已经用于推文推送的 QQ 官方机器人：如果 `QQ_APP_ID` 和 `QQ_APP_SECRET` 已在 Windows 用户环境变量中，直接执行下面的命令即可；推文 bot 平时通过 HTTP 推送，不会占用查询 bot 的长连接。若要使用独立的测试机器人，按照 [LOCAL_QQ_TEST.md](LOCAL_QQ_TEST.md) 创建后，在本目录复制 `.env.example` 为 `.env` 并填写这两个值。

```powershell
.\.venv\Scripts\ournotes-bot.exe bot
```

建议先单聊验证，再在测试群 @机器人发送查询。群聊只有明确 @机器人时才回复；普通群消息中的查询指令不会触发。代码使用 QQ 官方 WebSocket 事件，兼容 `GROUP_AT_MESSAGE_CREATE` 与 `GROUP_MESSAGE_CREATE`，HTTP API 地址为 `api.bot.qq.com`。`setup-menu` 会修改 QQ 菜单和指令面板，本地测试无需安装菜单。本分支尚未完成群聊回复的端到端验证。

## 可选的自然语言查询

在 `.env` 中填写：

```dotenv
AI_API_KEY=你的API密钥
AI_MODEL=deepseek-chat
AI_BASE_URL=https://api.deepseek.com
AI_DAILY_LIMIT=100
OURNOTES_QUERY_CONCURRENCY=2
OURNOTES_QUERY_QUEUE_LIMIT=4
```

发送 `/问 迷星叫的 EXPERT 有多少 Note`。常见等级筛选，以及 `/问 MyGO的歌有哪些`、`/问 tmr的卡有哪些` 这类已收录实体的简单列表查询，会在本地处理。其他受支持问法由模型返回 `song`、`chart`、`card` 或 `unsupported` 的结构化条件；程序从**用户原问题**中锚定歌曲、乐队、角色或卡牌，再核对模型提出的名称是否为同一对象。对象不存在、与原文不符或原文有多个对象时不执行查询。通过校验后按结构化条件直接检索本地数据，文字与图片共用同一结果，不直接转发模型自由文本。只有需要模型解析的 `/问` 会消耗 AI 请求；成功解析的重复问题有内存缓存。额度按北京时间自然日计算，计数保存在缓存目录旁的 `ai-quota.json`，重启不会清零。请求在调用 AI 前计入额度；即使提供方超时、返回错误或结果无效，也算一次，避免无法确认费用时自动重复请求。额度用尽、文件损坏或无法保存时停止调用 AI，普通指令和本地可解析的 `/问` 仍可用。不要删除或手工重置额度文件；如需改位置，可设置 `OURNOTES_AI_QUOTA_FILE`（相对路径以 `.env` 所在目录为基准）。此限制只保证单实例，不适用于多实例共享配额。发送给 AI 服务的内容是需要模型解析的 `/问` 文本，不应输入私人信息。

群聊和单聊共用查询处理上限：默认同时准备 2 条回复，另有 4 条等待；达到上限的请求会立即收到“当前查询较多”提示。准备阶段包括本地查找、AI 解析及图片绘制；图片上传和发送仍按各自请求执行。可用 `OURNOTES_QUERY_CONCURRENCY`（至少 1）和 `OURNOTES_QUERY_QUEUE_LIMIT`（至少 0）调整，修改配置后需重启查询机器人生效。

人工确认的群内叫法可填入 [query_aliases.json](query_aliases.json)：`song`、`band`、`character`、`card` 分别对应歌曲、乐队、角色和卡牌别名，右侧必须是当前缓存中存在的原名或 ID；`outside_catalog` 放确认不属于当前曲库的叫法。词表中的昵称可用于 `/问`、`/查曲`、`/查卡` 和 `/查缩写`；别名目标不存在时不会被采用。同一类查询中有歧义的昵称不会任选一个结果。不要把未经核实的昵称猜测写进词表。具体步骤、冲突处理及升级前后对比见 [昵称词表维护规范.md](昵称词表维护规范.md)。修改后用 `python -m json.tool query_aliases.json` 检查格式，再在 QQ 验证。

## 当前边界

- Project Yume 的公开文件不是官方游戏 API；同步结果取决于其更新与可用性。卡图、封面仅运行时获取，素材权利不属于本仓库，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
- `/查谱面` 优先展示所选难度的音符谱面静态图；未指定难度时预览 EXPERT。谱面文件无法获取时退回等级和 Note 数；不提供实时档线或预测线。
- 卡牌列表不预取每张卡的详情；输入卡牌 ID 才请求数值与技能名称。第三方详情暂不可用时仍显示卡面，数值会标为不可用。
- 未接入玩家账号、代练或任何游戏操作。

运行测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

本项目代码沿用原仓库的 [MIT License](LICENSE)；游戏和第三方数据、素材不在此许可范围内。

图片回复需要系统安装可用的中日韩字体，例如 Windows 微软雅黑或 Noto Sans CJK。缺少字体时，群聊和单聊会记录原因并退回文字回复；本项目不附带字体文件。

## 常见问题与发布检查

Windows 单实例可启用[五分钟自动更新](自动更新说明.md)：只部署 main 上通过 GitHub Checks 的提交，在独立环境验证后切换，启动失败恢复上一运行版本。本地有未提交修改时暂停更新。此功能需要单独初始化并安装计划任务，不会因安装 Python 包而自动启用。

- `--help` 可以运行但 `query` 提示数据初始化失败：先运行 `sync`。首次同步需要能访问 Project Yume；同步失败时保留同源旧缓存，`/数据状态` 会标明是否正在使用旧缓存。
- `bot` 提示缺少 QQ 配置：在 `.env` 或环境变量设置 `QQ_APP_ID`、`QQ_APP_SECRET`，不要把真实值提交到 Git。
- 只收到文字，没有图片：确认系统有中日韩字体；谱面源不可用时会退回等级与 Note 数。上传或发送失败也可能导致文字回退或未送达，查看脱敏日志确认阶段。
- `/问` 提示额度用尽或额度文件不可用：普通指令仍可使用。额度按北京时间换日；损坏文件不会被自动清零，检查文件权限或备份后再处理，避免重复付费请求。

GitHub Actions 在无真实凭据的 Ubuntu 环境运行测试、构建 wheel 与源码包，并检查昵称表随包提供且不包含运行缓存和媒体文件。本地可运行 `python scripts/check_release_artifact.py dist` 检查已构建的两种归档。自动测试不等于真实 QQ 收发验证；发布前仍需人工核对测试群回复与图片。
