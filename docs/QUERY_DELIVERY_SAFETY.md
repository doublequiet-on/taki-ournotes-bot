# 查询投递与续查安全合同（C2）

适用入口：`application/backend.py` 的规则查询与 Koishi 投递链；聊天历史投影仍由既有恢复机制管理。当前 HTTP 载荷 2.2、ledger schema 2 不变；本包无持久话题、durable stamp 或 outbox。

## 话题身份与占有

后端接纳请求时只绑定一次 scope（instance/app/platform/kind/conversation）内的锚点及 lineage。内部 ContextKey.user 承载原始 request_id，实际发言者仍在 Request.user_id；同群换人不创建新的旧话题。无引用时只选择唯一话题，有多个则拒绝猜测。引用 profile 仍须显式启用。

独立完整查询创建新 key/lineage；衍生请求绑定旧 key/lineage，在整批锁下执行至终局。排队不重新 resolve，不改绑后来出现的新话题。Ticket.generation 表示本轮上下文提交资格；lineage 表示整条话题。每次 intent 重验 active ticket、runtime、epoch、期限、证据与前片 ACK。

| 状态/事件 | 结果 |
| --- | --- |
| 接纳独立查询 | 建立 volatile ticket；准备结果，等待 intent |
| 接纳继续/翻页/选择/旧引用 | 绑定一次已确认话题；排队等待该话题整批锁 |
| 正常成功 ACK | 先持久化 receipt/message，再 commit 游标，最后 confirm 可见锚点；两步都成功才继续本批 |
| commit/confirm 返回 False 或抛异常 | receipt 保留；撤销整个 lineage 的上下文、快照和锚点；请求 failed，余片禁止 intent；重复 ACK 不重投影 |
| definite failure ACK | failure 保留，停止本批；没有未知尝试时保留最后确认位置，可另次继续未发送部分 |
| uncertain ACK，或终止时仍有 sending/uncertain | 终局 unknown：先撤销整条话题，再持久化终止并释放批次；排队/旧页/旧锚点均不再许可 |
| ACK SQLite 写入失败 | 话题立即撤销，本批 failed；保留未知尝试及终止重试，不因未落盘恢复许可 |
| 迟到 ACK | 只记录事实；不恢复已撤销话题；相同事实幂等，不同事实 receipt_conflict |
| 重启 | 内存话题不存在，sending 转 uncertain；既有 facts 保留；独立新查询正常接纳 |

请求终态（aborted/expired/failed）与片段事实（success/failure/uncertain）是不同维度。aborted/expired 下仍可能有 uncertain；unknown 不能根据请求状态单独判断。Ledger.has_unknown 检查片段事实，不把明确 failure 当作 unknown。

`/继续` 即使引用早期消息也读取该 lineage 的最新确认游标，防止正常批次完成后重新从 P2 开始；翻页和选择仍使用指定锚点的可见范围。话题失效、缺失、歧义或未确认的衍生请求返回不可发送的终态空计划，不发送额外“已过期”提示，因此旧派生新增 intent=0。用户需重新发送不引用旧消息的完整查询。独立新查询不会因其他话题失效被撤销。

## ACK 与发送协议

沿用 POST `/v1/deliveries/{delivery}/events`：intent → granted/existing；只有首次 granted 可以发送，existing 仅确认事实，不能重发。ACK 的 recorded/duplicate 表示账本事实，不表示上下文可用；网关按 send_allowed 与状态停止后续片段。ACK 写成功但上下文失败时返回 recorded=true、status=failed、send_allowed=false；异常导致 HTTP 失败时允许重交同一事实，但不允许重发平台消息。终止落盘失败保留 work.terminal_pending，并沿用现有重试/接纳维护门。

已发出的 grant 无法收回：网络中的 grant 或已通过本地有效期检查的调用，可能在后端话题失效后才真正发送。本合同保证失效后**不发新许可**，不保证撤回已授许可。其迟到 ACK 仍只补事实。

## 输出接纳（04/05/08/06 消费）

- 每个原始 msg_id 最多 5 个发送槽；processing hint 与多命令溢出提示各占一个。planner 的 QQ_PASSIVE_REPLY_LIMIT、后端总片数检查、TS wire.outputLimits 与 sender 均为 5；msg_seq 单调且最多 5，继续须使用新的用户消息。
- 本地回复窗口两端均 298 秒。官方 2026-11-03 核查：群聊被动 300 秒/5 次，C2C 被动 3600 秒/5 次；Taki 使用更短的共同窗口，不扩大 C2C 生命周期。[官方发送文档](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/send.md)
- 本地编码/HTTP 二进制/sender 接纳均最多 3,000,000 字节；只允许 PNG/JPEG。此为产品预算，不宣称平台最大值。编码器使用最多 8192 边长、12,000,000 像素；网关现有 material shape 校验另有绝对保护，编码产物在其范围内。
- 文本 sender 上限 1800 Unicode 码点；群 planner 正文 1500，为真实 mention 及图片 Markdown 留空间，C2C 正文 1800；完整 Markdown 在 intent 前再核查。长 fallback 只发短通知并保存完整文本游标，通知同样占槽。
- 图片 GET 会在 intent 前拒绝超限/非 PNG/JPEG（image_admission）。网关仅对图片获取的 image_admission/response_size/image_mime/backend_transport，以及编码/上传/完整图片 caption 接纳失败申请 upload_failed。后端只允许 pending 且无 attempt 的 primary 切换 `:f`；intent 后任何失败/unknown 均不回退。
- C2C 保留既有 `sendFilePrivate(file_data, srv_send_msg=false)` 路径。官方富媒体页仍将 file_data 标为暂未支持，故其线上可用性不由本次离线验证证明；群聊使用既有官方 material API + Markdown。[官方富媒体说明](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/rich-media.md)

05 的撤权接线应在新 intent 前执行，且用 allow_context=false 阻止被撤权成功 ACK 的上下文投影；不能删除 facts。08 保留 backend/receiver 的上述合同；04 遵守槽、字节与 fallback 约束；06 区分平台 success、ACK 持久化和上下文提交失败，不把 recorded=true 当作续查许可。

## 兼容与验证入口

无 schema 迁移；旧版能读取本包事实，但代码回退会重新引入旧续查漏洞，需成对评估后端/网关版本。没有恢复旧话题的承诺。现有 `tests/test_continuation.py` 与 `koishi/scripts/probe.cjs` 覆盖固定页面和实际网关；本任务额外故障注入/跨语言崩溃夹具按开发约定保留在本地 `runtime/task02`，路径和结果见交付记录，不纳入 PR。真实 QQ、引用客户端支持、目标宿主部署均另行验收。
