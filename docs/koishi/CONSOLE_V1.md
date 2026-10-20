# Taki 可视化控制台

开发基线：`b3d7c4267f8dd304ff0ce59dde2d9e3ff287c35a`。V1a 运输协议仍为 1.0；管理能力独立声明为 `admin_read_v1` 和 `telemetry_v1`。沿用 Python 业务后端、Koishi 网关两个进程，浏览器只调用受认证 Console RPC。

## 使用入口与事实口径

在现有 SSH 隧道的 Koishi Console 打开 **Taki 管理**，登录后进入总览、查询诊断、分群管理。总览显示当前健康和最近 24 小时**已受理请求**，不代表全部 QQ 消息。查询诊断显示发送生命周期、片段事实、业务结果和观测缺失。V1a 群入口只读，不能保存尚未执行的群策略。

平台确认发送不代表客户端看到或已读。`processing` 表示准备/运输尚未关闭；有成功片段但未全成功为 `partial`，同时单独显示 uncertain 片段数；无成功但有 sending/uncertain 为 `uncertain`；全部非零片段成功为 `complete`；明确 failure 为 `failed`；关闭且零片段或无发送确认为 `closed_unsent`。生命周期保留 expired/aborted 等原因，合法晚到 ACK 可更新片段事实。汇总、列表、详情共用 `delivery_state()`。

## 字段来源表

| 展示事实 | 唯一来源 | 未知处理 |
|---|---|---|
| 当前 QQ/后端/握手/版本 | 网关共享实时健康读取；后端 `/v1/ready` | 当前探测失败时 ready=false；不能用旧握手推定健康 |
| 查询接收开关 | 原五字段 settings.json | 保存采用 gateway epoch + revision；旧写接口 upgrade_required |
| 受理时间、生命周期、片段发送状态 | Python deliveries-v1.sqlite3 只读 | 不调用有过期处理的 lookup，不写账本 |
| 群、作用域、有限命令类别 | 接纳时写 diagnostics-v1.sqlite3 | 历史不反推；私聊不记录用户身份 |
| 业务结果、原始片段类型、文字回退可用性 | 实际 PreparedReply 元数据 | 原字段缺失显示 unknown；primary 不推定 image |
| 准备、排序等待 | Python 本进程 monotonic | 仅实际准备调用；不包含等待发送结束 |
| 上传、发送、回执提交耗时 | Koishi 本进程 performance.now，旁路遥测 | 缺失不补零；不跨进程相减，不承诺阶段总和 |
| 主目录缓存 | repository.capture_catalog 的内存快照 | 保留 stale 与缓存可用事实；其他来源未采集明确 unsupported |
| 通知状态与群许可 | 现有通知 owner 只读 SQL | NULL 许可保留未知；关闭通知为 unsupported；不领取/生成报告 |
| /问 今日、近七日计数 | QueryMetrics.v2 加锁快照 | 缺日不填零；无文件 unavailable；损坏不改变写入器状态 |

命令到片段的归属来自实际准备结果 command_index，缺失时保持 null；运输覆盖不保证完整，观测质量保留 partial。业务状态归一化为 ok/no_result/invalid/unavailable/unknown，多种业务结果为 mixed。不持久化问句、用户 ID、原消息 ID、图片或完整日志。

## 接口契约

Console RPC：`taki/overview`、`taki/requests/list`、`taki/requests/detail`、`taki/sources`、`taki/notices/list`、`taki/query-metrics`、`taki/groups/list`、`taki/settings/update`。所有读取和写入要求独立管理员会话及 authority 5；浏览器不获得后端 token。原 `taki/settings` 只返回 `upgrade_required`。

Python 对应 `/v1/admin/{overview,requests,requests/{id},sources,notices,groups,query-metrics}`，遥测 POST `/v1/admin/telemetry`。继续要求 Bearer 与实例标识；遥测另验证活跃网关 epoch、后端 epoch、请求/交付/片段/attempt 关联，不允许更新发送状态。

响应：`schema_version=1,generated_at,backend_epoch,capabilities,sections`；每个 section 包含 `availability,observed_at,stale,data,reason_code`。availability 为 available/partial/unavailable/unsupported。未采集数据为 null，不反推历史。

请求列表接受 limit（默认25，1–100）、from/to（秒，半开区间，最多7日）、state、group_id、attention、cursor。首次默认最近24小时；游标绑定窗口、过滤条件与 `(created,id)` 排序点并校验签名，重启后重新读取。attention 包含仍保留的历史未决记录。未知参数、非法窗口/状态/游标返回受控 400；身份不匹配 403，未知请求 404，关联冲突 409。返回不包含正文或平台消息 ID。

页面总览15秒，未结束详情5秒，来源及指标60秒；隐藏、退出、卸载暂停。管理读取预算约2秒，共享缓存5秒并合并并发，错误保留最近数据与草稿，只有明确 unauthorized 才清登录。轮询不替换脏草稿；保存冲突提供服务器值。

保存提交时固定快照；等待响应期间的新编辑继续作为未保存草稿保留。失效页面的保存响应不能重新填入管理数据。指标写锁忙时返回 `metrics_snapshot_busy`，不等待查询线程的磁盘写入，也不停止后续指标写入。

## 观测与资源

独立诊断库由 Backend 单写，保留24小时，最多4096请求、每请求32跨度。异常只降低观测可用性，不影响发送许可。Koishi 队列256条，每批32条且不超过24KiB，失败最多重试一次；关键运输流程仅同步入队，不等待遥测 HTTP。库故障与丢弃计数在采集健康中显示。队列进程退出未送达部分会缺失，不能当作完整覆盖。

管理读取不触发目录刷新、通知 claim、发送 intent/ACK、过期处理或指标写入。QueryMetrics 的 ask_total 是问请求口径，route 与 AI 工作计数不能相加作为请求总数；不计算账单、客户端送达率。

## 验证与交付

回归入口：`python -B -m unittest discover -s tests -p test_console_admin.py`、既有续查回归、架构门禁及 `node scripts/probe.cjs`。正式包资源检查与浏览器证据、性能数据和附件矩阵执行结果记录于本任务验收记录；未执行项目不能按通过报告。

本功能开发不包含合并、部署、重启或 QQ 发送。自动更新继续暂停。生产采集需批准部署后另验。
