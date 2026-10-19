# Koishi 迁移 M0 / M1 基线与实施记录

日期：2026-10-19。仓库 `doublequiet-on/taki-ournotes-bot`，基线 `0ce787aa1dcaa218014dee7cc424982830a26d76`。开始时当前工作区干净，成功 fetch 后与 `origin/main` 相同；未更新其他 checkout。开发分支 `codex/koishi-migration-m0-m1`。

用户已要求补齐三处复核结果并从 M0/M1 开始实施，生产切换另行确认。本轮完成本地基线、版本锁和安装包级探针；真实 QQ、生产配置/运行版本与线上送达未验证。M2–M4 的后端、完整接线和发布包仍待实施。

## 方案修订

[方案 v1.1](KOISHI_MIGRATION_PLAN.txt) 增补：

1. 接收顺序与 HTTP 到达顺序分离：gateway epoch + ingress_seq 登记；同上下文接纳高水位；先查重复请求，迟到新请求不得创建旧代次。对应 A26，M2 落实。
2. 原始 QQ 事件→实际安装适配器→Session→业务入口的完整门禁，身份字段窄转换、触发和群许可路径。对应 A27，M1 固定夹具已验证，实机待验。
3. 主动通知独立的发现/许可事件、任务 claim、发送 intent/ACK 契约，与被动查询分开；继续使用原通知许可/账本语义。对应 A28，M2 冻结协议、M4 接线。

用户提供的原 TXT 已同步修订；仓库副本 SHA-256 为 `cd8ba4c80ca415bc4dc401ad3e73d5ba4a373178145f74ba233eecc165174602`。本文是实施状态，不能用方案中的待实现内容替代验收。

## M0：现有行为对照

以下来自本次基线源码和 `.env.example`，不是生产 `.env` 的实测配置。现有 CLI 只有 query/repl/sync/bot/setup-menu，尚无 backend 入口。

| 领域 | 固定兼容样本 | 必须保持的结果语义 / 验证入口 |
|---|---|---|
| 歌曲及条件 | `/查曲 蓝色 MyGO EX 激奏=JUST`、`／查曲 100001` | 颜色、条件、难度和别名由 Python 解析；`test_query.py`、`test_song_traits.py`，M3 两入口对照待执行。 |
| 谱面 | `/查谱面 100001 EX` | 捕获同一谱面数据；缺失/零 Note 不混同；`test_reply_pipeline.py`。 |
| 卡牌/SNAP | `/查卡 mygo SSR`、`/查卡 BD`、`/查留影卡 1` | 同一捕获结果的文本/图片和类型；`test_reply_preparation.py`、`test_reply_pipeline.py`。 |
| 活动/卡池/角色 | `/查活动 hk`、`/查卡池 历史`、`/查角色 tmr` | 四服活动与日服资料范围保持；`test_info_command_pipeline.py`，M3 对照待执行。 |
| 四服歌曲榜 | `/查榜线 100 hk`、`/查榜线 20-40` | Top 100 观测、来源与历史语义；`test_event_cutoffs.py`，M3 对照待执行。 |
| 自然语言 | `/问 迷星叫 EX 多少 Note` | 本地优先，模型仅解析条件；直接命令不调用模型；M3 验证完整路由，本轮未调用模型。 |
| 帮助/状态/别名 | `/帮助`、`/数据状态`、`/查缩写 tmr` | 继续使用现有 Python 指令与别名表；不把探针识别器当作命令实现。 |
| 多命令/连续查询 | `/查卡 1\n/查留影卡 1`、`/继续`、`下一页`、`选1` | 原消息最多 4 条命令、共用发送预算；只有已确认回执推进可见上下文；`test_multi_command.py`、`test_continuation.py`。 |
| 群与私聊入口 | GROUP_AT / GROUP_MESSAGE / C2C | 新群事件只收自身 @ 的查询，忽略其他机器人/普通聊天；`test_media.py`、`test_qq_mentions.py`。 |
| 更新通知 | 群加入/移除、许可/拒绝、已提交更新 | `UpdateNoticeTests`：已提交版本、群许可、记录及防重复；跨进程接线待 M4。 |

M0 留下完整功能样本；M1 只验证运输层固定正文，不宣称这些命令已由 Koishi 跑通。

| 配置/状态 | 当前源码或模板 | 迁移归属 |
|---|---|---|
| QQ 凭据/网关 | QQ_APP_ID/SECRET；gateway host 默认 api.bot.qq.com | Koishi；不把 QQ 密钥传给 Python backend。 |
| 入场与排序 | 查询并发 2、额外等待 4、多命令 4；reply_order 默认开启 | Python 业务准入/代次，Gateway 交付顺序；入场顺序契约待 M2。 |
| 发送 | 同一原 msg_id，递增 msg_seq；每条原消息最多 5 槽；图片先上传 srv_send_msg=false | Koishi 单原消息 QQDelivery；群/C2C 实际窗口与额度待验。 |
| 文图 | 文本 1800 字限制、1700 分块；图片 3,000,000 字节、边 8192、像素 12,000,000 | Python 准备/编码；Gateway 再检查字节和文本上限。 |
| 上下文 | ContextStore 容量 1024、TTL 600 秒、留存图片合计 128 MB；当前进程内 | 单 Python 后端；不能从高层 send 返回直接判定 commit。 |
| 数据/刷新 | Haneoka 默认资料/谱面/榜线；主缓存 TTL 0.5h；历史启用、采样默认关闭 | Python 单写者管理；真实生产路径和开关尚未读取，切换前盘点。 |
| AI 与统计 | key/model/base_url、日限额 100、quota/metrics 路径 | Python 复用既有状态；M2 不重置额度。 |
| 主动通知 | notices 默认开启；群账本和已提交更新门禁 | Python 产生与记录，Koishi 发送；旧进程与新进程不能并发写账本/发送。 |

旧入口已有：`reply_commands` 入站时创建完整查询代次，续查持锁贯穿准备/发送；跨 HTTP 后不能仅迁移发送排序。`_deliver_reply` 保持捕获的备用文字，上传失败不重新查询。以上结构仍原样保留。

## M1：候选版本与证据

| 组件 | 锁定值 |
|---|---|
| Node / npm | 24.19.0 / 12.2.0 |
| koishi / @koishijs/core | 4.18.11 / 4.18.11 |
| @koishijs/plugin-adapter-qq / @satorijs/adapter-qq | 4.14.0 / 4.14.0 |
| @satorijs/core / TypeScript | 4.6.0 / 5.9.3 |
| package-lock SHA-256 | `c5c30654b24dc38d2f7de857866ebfaeefd69ccfcf0476f0f53e0a7e77b22119` |

完整包树由 npm 官方 registry 安装并锁定 integrity；禁止安装脚本。随后 `npm ci --offline --ignore-scripts` 从本地缓存重装 174 个包成功，重新编译与 25 项探针通过，`npm ls --all --json` 无依赖错误。依赖来源和行为以安装包为准，不把在线仓库 HEAD 当成 npm 包。CI 使用 [setup-node v6](https://github.com/actions/setup-node/tree/v6) 读取 `.node-version`，独立运行离线探针；候选插件未进入 Python 生产包，既有发布依赖门禁未改变。

| 能力 | 群聊 | C2C | 证据边界 |
|---|---|---|---|
| 原事件身份/时间 | group_id + author.id；legacy group_openid/member_openid 窄转换 | author.id；legacy user_openid 窄转换 | 实际 adaptSession + 声明夹具；消息时间使用显式时区 ISO 字符串。 |
| WS 接收/订阅 | GROUP_AT、GROUP_MESSAGE；USER_MESSAGE=1<<25 | C2C_MESSAGE_CREATE；同一订阅位 | 实际 WsClient HELLO/DISPATCH，替身 socket，无认证连接；平台事件权限未验证。 |
| API 地址 | /v2/groups/{id}/files、messages | /v2/users/{id}/files、messages | 实际 internal 方法生成请求，HTTP 截获；endpoint 候选 api.bot.qq.com。 |
| 上传/回执 | base64 图片，srv_send_msg=false；同 msg_id + seq 1、2 | 同左，私聊目标为 userId | 固定 PNG 和模拟回执；真实上传和客户端展示未验证。 |
| 发送预算/窗口 | 候选 5 次、298 秒 | 候选相同，独立待实测 | 5 来自 Taki，298 秒来自适配器的 5 分钟减 2 秒；不是账号权限结论。 |
| 群许可事件 | internal qq/group-msg-receive、reject；声明秒时间戳 | 当前主动通知仅群 | 实际 WS internal 分发已覆盖；事件落盘和通知链待 M2/M4。 |

版本差异已复现：

- 不经转换直接喂 legacy 事件时，安装包得不到会话/用户 ID。`ingress.ts` 仅映射明确的身份字段；冲突/缺失拒绝，不猜公共会话键。
- `WsClient.prepare()` 保留下发的 `api.sgroup.qq.com`；仅配置 REST endpoint 不足以继承旧入口 host 修正。原生 `gatewayUrl` 覆盖可用；M3 必须根据实际 discovery 保留路径/参数生成有效地址，不能使用探针中的示例路径上线。
- `session.send()` 的实际编码器先成功发送一段、后段失败时返回 `[]`。采用 `QQDelivery` 每段调用原生 internal API，显式持有 intent/回执，解决聚合返回无法表达部分成功的问题。

## 验证与剩余门禁

`koishi/scripts/probe.cjs` 共 25 项安装包级离线场景通过：身份转换/冲突、触发和作用域、WS 订阅/许可、网关差异、群/C2C 图片与文字、部分成功、上传失败、空回执、超时、审核、重复片段、ACK 丢失、卸载及预算。TypeScript strict 编译通过。验证命令见 [插件说明](../koishi/README.md)。

Python 3.12 的 85 项现有回归通过：`test_continuation`、`test_media`、`test_multi_command`、`test_qq_mentions`、`test_reply_order`、`test_reply_pipeline`、`test_reply_preparation` 和 `test_updater.UpdateNoticeTests`。运行时禁止外部 DNS/连接并清空 QQ/AI 凭据；错误日志是失败场景夹具。`python -B scripts/check_architecture.py` 通过，Python 源码未改动。未执行全仓测试，本轮范围由上述旧回复链回归和新运输层探针覆盖。

探针不会用伪造 Session 替代入站路径。临时 JSON 输出本地留存，CI 上传同格式结果绑定提交。未新增通用 Python 测试文件；M1 版本探针属于方案要求的交付物。

后续按方案进入 M2：共享准备逻辑、backend 启动、鉴权/限流、epoch/ingress_seq 接纳、durable intent/ACK 和通知协议；M3 接通真实接收器、网关兼容、业务识别与每原消息单一 delivery 所有权；M4 单写者、后台任务、发布与回退。当前 service 接口和 events port 不等于后端已实现。

生产切换前仍须补：脱敏实机事件（含时间和身份）、现有账号实际开放群/C2C、权限/订阅、网关地址、窗口/预算、真实上传/回执/审核/客户端送达、生产配置和数据路径、单写者及回退演练。未执行真实 QQ、付费模型、部署或重启；生产切换等待另行确认。
