# 在本机连接 QQ 测试

本项目使用 QQ 官方机器人 API 的 WebSocket 长连接。程序从本机主动连接 QQ，因此本地测试无需域名、HTTPS 和路由器端口映射；运行期间电脑不能休眠或断网。

## 1. 选用 QQ 机器人

如果推文 bot 已使用 QQ 官方机器人，查询 bot 可以复用同一组 `QQ_APP_ID`、`QQ_APP_SECRET` 用户环境变量，不必重新创建机器人。建议先单聊或在测试群中验证。只有想把测试和现有机器人身份完全隔离时，才需要新建测试机器人。以下步骤适用于新建机器人，复用时可直接跳到第 2 步。

1. 登录 [QQ 开放平台](https://q.qq.com/)，创建机器人。
2. 在机器人的“开发设置”中保存 `AppID` 和 `AppSecret`。
3. 在权限或功能配置中启用群聊与单聊消息能力。代码监听 `GROUP_AT_MESSAGE_CREATE` 和 `C2C_MESSAGE_CREATE`。
4. 如果控制台要求 IP 白名单，加入当前网络的公网出口 IP。PowerShell 可执行：

   ```powershell
   Invoke-RestMethod https://api.ipify.org
   ```

   应填写命令返回的公网地址，不要填写 `192.168.x.x`。家庭宽带公网出口可能变化，连接突然失效时重新检查。

5. 在“沙箱配置”或“测试人员”中加入自己的 QQ 号。不同主体和机器人权限显示的测试场景可能不同：有测试群选项时配置一个自己管理的群；没有时先用机器人单聊完成联调。

## 2. 准备本地环境

如果本机已按 README 安装并同步数据，可直接进入项目目录运行第 3 步的 `start-bot.ps1`。新建机器人或重装环境时，在项目目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup-local.ps1
notepad .env
```

使用独立机器人时，在 `.env` 中填写：

```dotenv
QQ_APP_ID=开放平台中的AppID
QQ_APP_SECRET=开放平台中的AppSecret
OURNOTES_CACHE_FILE=data/ournotes-cache.json
OURNOTES_CACHE_TTL_HOURS=6
AI_API_KEY=
AI_MODEL=deepseek-chat
AI_BASE_URL=https://api.deepseek.com
AI_DAILY_LIMIT=100
OURNOTES_QUERY_CONCURRENCY=2
OURNOTES_QUERY_QUEUE_LIMIT=4
```

不要把 `.env` 截图、上传或提交到 Git。项目的 `.gitignore` 已排除该文件。

## 3. 启动机器人

```powershell
.\start-bot.ps1
```

看到类似“机器人 xxx 已上线”的日志后保持终端开启。首次设置快捷按钮可另开 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m ournotes_bot.main setup-menu
```

## 4. 在 QQ 中验证

单聊机器人发送：

```text
帮助
/查曲 迷星叫
/查谱面 100001 EXPERT
/查卡 1
/查卡 高松灯
/问 迷星叫的EX物量
/问 MyGO的EXPERT 25级以下歌曲
/问 tmr的SSR成员卡
/问 高松灯的得分提升技能成员卡
/问 tmr的SR支援卡有哪些
/问 迷星叫和另一首歌哪个好
/问 推荐最强阵容
/问 一个当前数据中不存在的角色有哪些卡
数据状态
```

测试群中需要明确 `@机器人`；单独发送 `/查卡 1` 等指令不会触发群聊回复：

```text
@你的机器人 查谱面 100001 EXPERT
```

以 Project Yume 当前缓存为准，`/查谱面` 应显示等级和 Note 数；谱面源可用时附完整音符谱面图。`/查曲` 返回歌曲列表图，`/查卡 1` 返回卡图和数值图。前五条常见 `/问` 应直接返回与普通查询一致的文字和图片；比较、推荐和未收录对象必须明确终止，不得自动换对象或放宽条件。重复发送同一确定性失败不应再次消耗 AI 额度，网络故障恢复后同一问题则应能重新尝试。

未填写 `AI_API_KEY` 时，已收录实体的简单列表问法和常见等级筛选仍可本地处理；其他 `/问` 会提示改用普通查询指令。输入 `/查卡`、`/查谱面` 等不带参数的指令时，会收到用法说明。`/数据状态` 应分别说明进程响应、最近成功同步和当前缓存状态。人工验收后可检查缓存目录旁的 `ai-metrics.json`：文件只应包含日期、分类计数和提供方实际返回的 Token 用量，不应出现测试问题正文、QQ 号或群号。

## 5. 常见问题

### 启动后立即鉴权失败

重新核对 `QQ_APP_ID`、`QQ_APP_SECRET`。修改 `.env` 后需要重启程序。

### 日志显示来源 IP 不在白名单

重新执行 `Invoke-RestMethod https://api.ipify.org`，把结果添加到开放平台白名单，然后重启。

### 机器人上线但不回复

依次检查：

1. 发送消息的 QQ 号是否已加入测试人员。
2. 对话或群是否处于机器人沙箱范围。
3. 群里是否真正选择并 @ 了机器人。
4. 是否开通群聊与单聊消息事件权限。
5. 本地终端是否仍在运行，以及电脑是否休眠。

### 私聊正常，群聊不工作

通常是机器人尚未获得群聊权限，或者当前账号的沙箱没有开放测试群。先保留私聊测试；群聊能力需要按照开放平台控制台当前显示的申请或审核流程开通。

### 数据状态提示正在使用旧缓存

最近一次同步没有成功，机器人仍在读取此前保存的数据。检查网络和数据源可用性后运行 `ournotes-bot sync`，或等待下一次自动刷新；不要删除唯一可用的缓存。

### 图片只显示文字或自然语言查询不可用

图片需要可用的中日韩字体；缺字体时先安装字体再重启。`/问` 的复杂问法还需要 `AI_API_KEY` 和可写的额度记录目录，额度用尽或记录损坏时普通查询仍能使用。
