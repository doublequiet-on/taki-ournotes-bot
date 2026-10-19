# Koishi 本机业务后端协议 v1（M2 / M3）

本阶段以 `b60c12dc2acff3d170894e3fb60c15f12392c65b` 的 M0/M1 为起点，在同一迁移分支继续。实现 Python 常驻查询后端和共享回复准备；Koishi 消息接收器/HTTP 客户端与网关接线已在 [M3](KOISHI_MIGRATION_M3.md) 实现，真实平台送达待验；后台任务和通知发送属于 M4。生产切换仍需另行确认。

## 启动与所有权

在独立测试数据配置下运行（不要与旧 bot 共用可写缓存/额度/历史）：

```sh
ournotes-bot backend --instance-id taki-test --app-id APP_ID --state-dir /absolute/test-state --port 8765
```

环境变量 `OURNOTES_BACKEND_TOKEN` 必须至少 32 字符；令牌通过环境注入，不放在命令行、URL 或日志。业务配置继续使用现有 `OURNOTES_CACHE_FILE`、来源、AI 和额度设置；`--state-dir` 只决定新交付账本位置，不自动搬迁业务数据。后端仅绑定 `127.0.0.1`，未提供公开监听选项。AppID 是命名空间身份，无需 QQ secret；后端组装时清空 Settings 中的 QQ 凭据。

组装入口 `application/backend_bootstrap.py` 只创建一份 repository、AIQueryParser（含既有额度管理）、QueryGate 和 ContextStore。`repository.load()` 完成后才 ready；失败维持 503，记录错误类型，不伪装成可查询。初始化、资料读取和渲染仍可访问配置的数据源，离线测试用固定资料替换源初始化。`health` 不代表 ready 或 QQ 可用。

本阶段不启动主资料/属性周期刷新、HistorySampler 或通知任务，ready 明确返回 `background_tasks: false`；旧 bot 仍按原入口管理它们。M4 接管时必须核对真实配置和单写者，不能同时启动两套任务。关闭时停止接纳、终结租约，等待正在运行的准备线程实际结束后关闭账本。线程无法强杀；取消 HTTP 等待不会取消共享工作或提前释放 QueryGate 容量。

账本为 `state-dir/deliveries-v1.sqlite3`，旁边的文件锁由 OS 持有，进程退出自动释放；锁文件存在本身不表示正在运行。第二个使用同一账本的后端拒绝启动。schema=1，namespace 为 instance_id/AppID；不同命名空间不得复用同一个账本。它只保护交付账本，不能替代 M4 的所有共享业务文件单写者校验。

## HTTP 公共约定

每个端点，包括健康、图片和通知端点，都要求：

- `Authorization: Bearer <token>`
- `X-Taki-Instance: <instance_id>`
- 查询状态/图片额外用 `X-Taki-Epoch: <gateway_epoch>` 表明读取者；不匹配时可读最小交付事实，但不得获取发送内容/图片。

请求 JSON 最大 32 KiB；正文 UTF-8 最大 16 KiB；不接受任意本地文件路径或远端图片 URL。协议严格采用 `{"major":1,"minor":0}`，不兼容版本返回 409。错误是 `{"error":"分类","protocol":...}`，不回显 token、正文、原始 QQ 身份或异常细节。访问日志关闭；业务准备日志在后端模式隐藏查询正文，使用 request_id 关联后端错误。

| 端点 | 结果 |
|---|---|
| GET /v1/health | `alive` 与 protocol；只证明进程处理请求。 |
| GET /v1/ready | ready、protocol、backend_epoch、backend_code_digest、capability、features、background_tasks；未初始化为 503。代码摘要覆盖后端/协议/账本/准备/组装五个文件，不冒充整个仓库 SHA。 |
| POST /v1/gateways | 输入仅 `gateway_epoch`；登记新代次并撤销旧代次。重复当前代次幂等，已退役代次不能重新激活。 |
| POST /v1/classify | 输入仅 body，返回 recognized；只读识别，不查询/扣额/写上下文。 |
| POST /v1/gateways/retire | 输入仅 gateway_epoch；撤销该代次并终结其活动请求，不影响新代次。 |
| POST /v1/requests | 首次接纳或返回已有请求，HTTP 202；业务/交付状态从 JSON 判断。 |
| GET /v1/requests/{request_id} | 返回同一次准备/交付状态，不重新执行查询。 |
| GET /v1/deliveries/{delivery_id}/parts/{part_id}/image | 只取当前有效计划归属的已编码图片，PNG/JPEG MIME 按实际魔数返回；失效为 409。 |
| POST /v1/deliveries/{delivery_id}/events | 上传失败分支、发送 intent、ACK 和终结。 |

JSON 和图片响应使用 no-store。未 ready、新任务/账本容量不足为 503；身份/认证不匹配为 403/401；冲突、过期和非法状态转换为 409。

## 请求与入站顺序

请求字段必须完整且没有额外字段：

```json
{
  "protocol": {"major": 1, "minor": 0},
  "request_id": "SHA256_HEX",
  "instance_id": "taki-test",
  "app_id": "APP_ID",
  "platform": "qq",
  "kind": "group",
  "conversation_id": "OPAQUE_GROUP_ID",
  "user_id": "OPAQUE_USER_ID",
  "message_id": "ORIGINAL_MESSAGE_ID",
  "timestamp_ms": 1792382400000,
  "body": "/查曲 MyGO",
  "capability": "qq-m1-candidate",
  "gateway_epoch": "RANDOM_PER_PLUGIN_LIFETIME",
  "ingress_seq": 1
}
```

request_id 等于紧凑 JSON 数组 `[instance_id,app_id,"qq",kind,conversation_id,user_id,message_id]` 的 UTF-8 SHA-256（与 M1 ingress.ts 相同）。C2C 的 conversation_id 必须等于该事件的 user_id。opaque ID 不转换为 QQ 号。

同一 request_id 首先检查稳定内容摘要：相同内容复用原状态；正文、身份、原消息时间或 capability 变化返回 request_conflict。gateway_epoch 和 ingress_seq 不进内容摘要。不同代次读取旧请求不授予发送权；已经过期的重复请求仍可读事实。未见过的过期事件明确拒绝，不重新查询。

新事件只允许 `0 <= now - timestamp < 298s`。298 秒是 M1 候选窗口，仍待真实群/C2C 测试。每个已登记代次内，同一 instance/AppID/platform/kind/conversation/user 的 ingress_seq 必须超过已接纳高水位。B 先登记后，之前未登记且序号更低的 A 返回 stale_admission，不能执行查询、扣额或清除 B 的上下文。网关必须按接收顺序串行完成轻量登记，再并行取准备结果；高水位仅防止迟到旧请求复活，不能替网关补回未登记的 A。

首次登记成功后，完整查询立即 begin 一次；重复请求不 begin。续查在接收时捕获 lineage，并持有用户锁直到准备、等待交付、回执或租约结束。新完整查询可以覆盖旧 generation；迟到 ACK 只记事实，旧票据不能覆盖新上下文。

## 计划、片段和交付事件

状态响应包含 `request_id, delivery_id, plan_version=1, status, send_allowed, expires_at, parts`。准备期间为 preparing；可交付为 prepared；终态为 finished/failure/uncertain/aborted/expired/failed。业务状态位于片段 `business_status`（来自原查询结果；纯提示为 message），与传输状态分开。

片段包含 `id, position, mode, status, attempt, message`；有发送权时额外返回 kind、纯文本 text，图片还返回 image 受控路径和 mime。初始片段 ID 为位置的十进制字符串，position 从 0 开始；当前最多五片段，复用共享 `expand_replies` 的分页与余图语义。send_allowed 只说明计划当前有效，**不能代替每片段 intent 的发送许可**。

所有事件共同字段：`protocol,request_id,plan_version,gateway_epoch,event,part_id,attempt_id`，严格拒绝额外字段。M3 ready.features 包含 `classify`、`gateway_retire`、`ack_context_guard`，新网关要求三者齐备。ACK 允许可选布尔 `allow_context`，false 只补记发送事实并终结活动批，不提交上下文；省略保留 M2 兼容语义。ACK 还必须包含 `result,message_id`；success 要求非空消息 ID，其余 result 的 message_id 必须为 null。结果可为 success/failure/uncertain/aborted；审核中由网关映射为 uncertain。

| event | 前置条件与行为 |
|---|---|
| intent | 当前有效代次、未过期、前面片段全部 success、本片段 pending；先落盘，再返回 permission=granted 和 msg_seq=position+1。相同 attempt 再提交仅 existing；不同 attempt 冲突。existing 绝不是第二份可执行发送许可。 |
| ack | 必须对应原 owner epoch、part 和已登记 attempt；相同 ACK 幂等，冲突拒绝。可在重载/过期/重启后补记真实结果，但不恢复发送/业务提交权。 |
| upload_failed | 未发起媒体发送、无 attempt，且前片段已确认；把当前片段改为 ID `原id:f` 的 fallback 文字，position 不变、不消费发送槽位。重交原 upload_failed 只返回同一分支。 |
| aborted | 终结当前批并撤销未发送片段及旧上下文提交权；在途 intent 仍待真实回执或重启时 uncertain。 |
| batch_finished | 只接受所有片段已经 success 的批；末片段 ACK 本身也会完成批次，重复终结安全。 |

上传失败后，短备用文字来自同一捕获结果；长文字返回“完整文字已保留；/继续”提示。只有提示 success 才保存 `fallback_cursor`，包含完整文字分块与原余页；提示 uncertain 不提交 fallback_pending。媒体发出后超时不得转文字。网关不能自行截断、重新查询或从图片 URL 推导文件路径。

同一原消息跨多条命令和所有片段，M3 必须复用一个 QQDelivery。顺序是 intent 已落盘 → 一次 QQ 发送 → ACK 获确认 → 下一片段。后端永不调用 QQ 发送。完整查询在当前批终结时提交最后确认的有效候选；续查逐确认片段推进，保持原语义。多段文本在具备完整选择上下文的末段前不暴露整份可选列表。

## 容量与恢复

QueryGate 沿用配置的并发/排队（默认 2+4），全后端最多持有 64 个请求任务；当前准备计划和 ContextStore 余图/文字按实际共享对象引用去重计入 128 MB 留存预算。超过预算的准备结果终结并释放，不发图片。渲染过程中临时画布/临时编码内存仍由现有图片像素限制与 QueryGate 约束；该预算不是整个 Python 进程 RSS 上限。HTTP 输出和 M3 网关复制的字节还需在各自运输队列中限制，不能由此宣称全链路内存峰值已验收。

交付元数据最多 4096 个请求、4096 个代次和 4096 个当前代次上下文高水位。终态至少保留 24 小时再按接纳触发清理；sending/uncertain 不被普通淘汰遗忘，达到上限停止新接纳。代次记录不自动删除，达到上限需后续明确维护策略。该保守容量门禁优先于重复发送风险。

账本不保存正文、图片或 QueryContext。重启将 preparing/prepared 标过期，未确认 sending 标 uncertain，旧代次全部退役；重复原事件不重查/重发。旧 success ACK 可补记，但内存游标不重建。业务缓存/额度/历史文件格式未改变，代码回退不能把删除交付账本当作恢复方法。

## 冻结的主动通知协议（M4 接线）

这些路由已经保留且认证，目前统一返回 501 `notification_transport_pending_m4`；未接入现有 NoticeStore、不发现群、不产生通知、不消费通知额度。v1 冻结以下字段与状态，M4 的实现和测试按此核对：

| 路由 | 请求字段（均含 protocol、instance_id、app_id、gateway_epoch） | 返回及约束 |
|---|---|---|
| POST /v1/groups/events | event_id、group_id、kind（discover/add/remove/allow/reject）、timestamp_ms、ingress_seq | 事件去重和同群版本比较；返回 group_version、applied。discover 不授予许可，remove/reject 撤销未开始的通知发送权。适配器原秒时间戳在网关明确转换为毫秒。 |
| POST /v1/notices/claims | claim_request_id、limit（1–16） | 幂等领取：notice_id、claim_id、expires_at、group_id、group_version、deployment_revision、text、send_type=active；只有既有部署已提交且符合现有账本/许可条件的任务可领取。无原用户 msg_id，不复用被动序号或预算。 |
| POST /v1/notices/{notice_id}/events | claim_id、event_id、event（permission/intent/ack/aborted）、group_version；permission 带 allowed+observed_at_ms；intent 带 attempt_id；ACK 带 attempt_id+result+message_id | bot_state 核验绑定领取时的 group_version，不能覆盖随后发生的撤权；每通知只有一个有效领取者。发送前落盘 intent，ACK 幂等；未决发送在过期、重载、同部署再领取时不重授发送权。 |

通知稳定键为 instance/AppID/group/deployment_revision；claim_request_id 和 event_id 同 ID 内容冲突拒绝。所有身份沿用 1–256 字符、无控制符限制；文本上限与既有通知分块/预算对照后冻结，不能套用被动五槽。ACK result 与被动相同；主动发送权限变化和通知真实客户端送达仍属 M4/实机验收。

Python 保留唯一现有通知账本和部署提交门禁；Koishi 只完成 bot_state 和实际发送，不复制完成账本。M4 必测：核验期间撤权、领取后重载、发送后 ACK 丢失、同部署重复领取、旧版回退。M2 的 501 不能当作这些行为已实现。

## M2 本地验证（2026-10-19）

Windows / Python 3.12：22 项本机 HTTP + SQLite 专项通过，覆盖认证、就绪、请求冲突/去重、接纳乱序、逐片段意图与幂等 ACK、重启/代次变更、迟到 ACK、过期、容量、单写者、上传失败文字分支和续查锁跨交付期。实际 `backend` CLI 使用隔离目录及明确的 repository.load 夹具启动，/ready 返回 200，未导入 QQ SDK。专项禁止外网并清空 QQ/AI 凭据；不代表真实资料初始化、QQ 发送或 Koishi 端到端路径已验收。

wheel/sdist 构建及内容检查通过；wheel/sdist 分别在 checkout 外临时目录安装，33 个模块、静态资源、历史库及随包后端协议文档烟测通过。架构门禁通过。专项脚本保存在本工作区忽略目录 runtime/test_backend_m2.py；按照新增测试文件默认不提交的约定留作本地验证，既有 test_continuation.py 内的 3 个接纳/回执回归纳入交付。

完整现有回归运行 994 项，结果 OK（8 项按既有 POSIX/GNU tar 平台条件跳过，交由 Linux CI 覆盖）。修正了共享层提取后画廊脚本的调用位置，并保持原图字节/分页验收。首次完整尝试暴露的导入路径、画廊引用和日志断言问题均已修复；一次额外 faulthandler 诊断运行发生 Windows 原生进程异常退出，正常启动器完整重跑通过，未把诊断退出计作通过。

详细本地日志：runtime/m2-full-tests-corrected.log、runtime/m2-tests-final.log、runtime/m2-cli-final.log、runtime/m2-installed.log、runtime/m2-sdist-installed.log。生产切换、真实 QQ 回执和完整 Koishi 接线未执行；通知和周期任务按 M4 完成。
