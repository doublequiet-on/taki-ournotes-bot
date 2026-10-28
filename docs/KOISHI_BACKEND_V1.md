# Koishi 本机业务后端协议 v1（M2 / M3 / M4）

本文保留 M2–M4 阶段记录。查询聊天包 1 已将载荷升级为 2.0，新增执行前许可与锚点，并调整调度／上下文期限；当前差异见 [包 1 协议说明](QUERY_CHAT_PACKAGE1.md)，旧请求示例不能直接用于新后端。

本阶段以 `b60c12dc2acff3d170894e3fb60c15f12392c65b` 的 M0/M1 为起点，在同一迁移分支继续。实现 Python 常驻查询后端和共享回复准备；Koishi 消息接收器/HTTP 客户端与网关接线已在 [M3](KOISHI_MIGRATION_M3.md) 实现，真实平台送达待验；后台任务和通知发送已在 [M4](KOISHI_MIGRATION_M4.md) 接线。生产切换仍需另行确认。

## 启动与所有权

在独立测试数据配置下运行（不要与旧 bot 共用可写缓存/额度/历史）：

```sh
ournotes-bot backend --instance-id taki-test --app-id APP_ID --state-dir /absolute/test-state --port 8765
```

环境变量 `OURNOTES_BACKEND_TOKEN` 必须至少 32 字符；令牌通过环境注入，不放在命令行、URL 或日志。业务配置继续使用现有 `OURNOTES_CACHE_FILE`、来源、AI 和额度设置；`--state-dir` 只决定新交付账本位置，不自动搬迁业务数据。后端仅绑定 `127.0.0.1`，未提供公开监听选项。AppID 是命名空间身份，无需 QQ secret；后端组装时清空 Settings 中的 QQ 凭据。

组装入口 `application/backend_bootstrap.py` 只创建一份 repository、AIQueryParser（含既有额度管理）、QueryGate 和 ContextStore。`repository.load()` 完成后才 ready；失败维持 503，记录错误类型，不伪装成可查询。初始化、资料读取和渲染仍可访问配置的数据源，离线测试用固定资料替换源初始化。`health` 不代表 ready 或 QQ 可用。

后端在资料初始化成功后启动主资料半小时刷新、300 秒属性刷新和按配置启用的 HistorySampler；`background_tasks` 表示这些 RuntimeTasks 任务均存活，过期维护任务另见 `maintenance`。关闭时先关闭接纳并等待查询/后台线程退出，再关闭账本和业务目录锁；调用者反复取消也必须排空真实线程，随后才传播取消。`notices_enabled` 单独反映通知配置。新 `OURNOTES_RUNTIME_DATA_DIR` 控制素材/谱面缓存根目录；详细单写者、数据路径和旧版本限制见 M4 runbook。

账本为 `state-dir/deliveries-v1.sqlite3`，旁边的文件锁由 OS 持有，进程退出自动释放；锁文件存在本身不表示正在运行。第二个使用同一账本的后端拒绝启动。schema=1，namespace 为 instance_id/AppID；不同命名空间不得复用同一个账本。它只保护交付账本，不能替代 M4 的所有共享业务文件单写者校验。

## HTTP 公共约定

每个端点，包括健康、图片和通知端点，都要求：

- `Authorization: Bearer <token>`
- `X-Taki-Instance: <instance_id>`
- 查询状态/图片额外用 `X-Taki-Epoch: <gateway_epoch>` 表明读取者；不匹配时可读最小交付事实，但不得获取发送内容/图片。

请求 JSON 最大 32 KiB；正文 UTF-8 最大 16 KiB；不接受任意本地文件路径或远端图片 URL。当前运输协议严格采用 `{"major":2,"minor":0}`，不兼容版本返回 409。错误是 `{"error":"分类","protocol":...}`，不回显 token、正文、原始 QQ 身份或异常细节。访问日志关闭；业务准备日志在后端模式隐藏查询正文，使用 request_id 关联后端错误。

历史 Console V1a 使用1.0，V1b配对1.1；当前配对协议为2.0与 `qq-query-chat-v3`，仍要求 `group_policies_v1`，不允许混装。新请求必须携带 `policy={natural_query,epoch,global_revision,group_revision}`，模式 off/local/model，revision 为非负安全整数。该不可变快照参与请求摘要；重试复用原快照，身份相同但快照不同返回409，不重新执行。通知领取使用严格的 scope：all+exclude_group_ids、selected+非空group_ids、none；none不生成报告或领取。ACK和撤权继续记事实。共同源码摘要清单为 `application/backend_sources.py`，管理、遥测、分页与隐私契约见 [Console V1](koishi/CONSOLE_V1.md)。

| 端点 | 结果 |
|---|---|
| GET /v1/health | `alive` 与 protocol；只证明进程处理请求。 |
| GET /v1/ready | ready、protocol、backend_epoch、backend_code_digest、capability、features、background_tasks、maintenance、pid、notices_enabled；未初始化、关闭中或维护故障为 503。`maintenance` 给出原因、待保存终态数量/最早年龄/耗尽重试数量及 reaper 存活/错误类型。代码摘要覆盖后端/协议/账本/准备/组装与四个 M4 运行模块，不冒充整个仓库 SHA。 |
| POST /v1/gateways | 输入仅 `gateway_epoch`；登记新代次并撤销旧代次。重复当前代次幂等，已退役代次不能重新激活。 |
| POST /v1/classify | 输入仅 body，返回 recognized；只读识别，不查询/扣额/写上下文。 |
| POST /v1/gateways/retire | 输入仅 gateway_epoch；撤销该代次并终结其活动请求，不影响新代次。 |
| POST /v1/requests | 首次接纳或返回已有请求，HTTP 202；业务/交付状态从 JSON 判断。 |
| GET /v1/requests/{request_id} | 返回同一次准备/交付状态，不重新执行查询。 |
| GET /v1/deliveries/{delivery_id}/parts/{part_id}/image | 只取当前有效计划归属的已编码图片，PNG/JPEG MIME 按实际魔数返回；失效为 409。 |
| POST /v1/deliveries/{delivery_id}/events | 上传失败分支、发送 intent、ACK 和终结。 |

JSON 和图片响应使用 no-store。未 ready、新任务/账本容量不足为 503；身份/认证不匹配为 403/401；冲突、过期和非法状态转换为 409。

## 当前 HTTP 2.0 解码边界

网关 `src/wire.ts` 将未经验证的 JSON `unknown` 解码为内部类型；图片读取单独返回 `Uint8Array`。普通响应的额外元数据字段允许并忽略；协议对象、许可 scope／策略版本保持精确键校验。未知请求／片段状态、mode、kind 和许可判别值停止本次消费。Classify、登记／退役、intent 与模型决策回执不带 protocol，不套用 Plan 的信封。

Plan 按 `send_allowed` 区分可发正文与持久事实：preparing 可没有正文，也可带进度提示；提示片段在发送中／成功后仍可留在 preparing 计划。prepared 也可能已撤销发送且没有正文（例如终态尚未保存）。`processing_hint=true` 可留在 prepared／终态；终态片段只有身份、顺序、模式、状态、attempt 和 message。`business_status` 保持业务字符串，与交付状态分开。图片 fallback 的原位置不变，ID 为 `i:f`。

结构解码不替代 Receiver 的原请求、双方代次、用户、scope、图片资源和当前平台权限核验。许可 `remaining_ms=0` 是不足一毫秒时可能产生的合法结构值，但既有授权仍拒绝它并停止该交付。expires_at 为秒，入站 timestamp_ms 为毫秒。

解码在幂等 HTTP retry 返回之后执行，结构错误不触发额外 HTTP 重试；原网络／HTTP／JSON 解析错误重试分类保持。intent 仍不重试。ACK 的 recorded／duplicate 与返回计划分别校验，未知发送结果继续按原事实账本处理。

共享脱敏夹具为 `koishi/scripts/fixtures/http-v2.json`：TS 编码由 Python 的实际 Request.parse／event 消费；Python 的 snapshot／事件处理／challenge_view 实际输出由 TS 解码并继续核验绑定。Python 不额外实现生产未用的 Plan decoder。样例、已有 unittest 与已安装网关 probe 一同回归；文件随离线组合包交付，不依赖包外 tests 目录。

## 请求与入站顺序

请求字段必须完整且没有额外字段：

```json
{
  "protocol": {"major": 2, "minor": 0},
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
  "capability": "qq-query-chat-v3",
  "gateway_epoch": "RANDOM_PER_PLUGIN_LIFETIME",
  "ingress_seq": 1,
  "policy": {"natural_query": "model", "epoch": "POLICY_EPOCH", "global_revision": 0, "group_revision": 0},
  "trigger": "command",
  "reference": null,
  "interaction": null
}
```

request_id 等于紧凑 JSON 数组 `[instance_id,app_id,"qq",kind,conversation_id,user_id,message_id]` 的 UTF-8 SHA-256（与 M1 ingress.ts 相同）。C2C 的 conversation_id 必须等于该事件的 user_id。opaque ID 不转换为 QQ 号。

同一 request_id 首先检查稳定内容摘要：相同内容复用原状态；正文、身份、原消息时间或 capability 变化返回 request_conflict。gateway_epoch 和 ingress_seq 不进内容摘要。不同代次读取旧请求不授予发送权；已经过期的重复请求仍可读事实。未见过的过期事件明确拒绝，不重新查询。

新事件只允许 `0 <= now - timestamp < 298s`。298 秒是 M1 候选窗口，仍待真实群/C2C 测试。每个已登记代次内，同一 instance/AppID/platform/kind/conversation/user 的 ingress_seq 必须超过已接纳高水位。B 先登记后，之前未登记且序号更低的 A 返回 stale_admission，不能执行查询、扣额或清除 B 的上下文。网关必须按接收顺序串行完成轻量登记，再并行取准备结果；高水位仅防止迟到旧请求复活，不能替网关补回未登记的 A。

首次登记成功后，完整查询立即 begin 一次；重复请求不 begin。续查在接收时捕获 lineage，并持有用户锁直到准备、等待交付、回执或租约结束。新完整查询可以覆盖旧 generation；迟到 ACK 只记事实，旧票据不能覆盖新上下文。

## 计划、片段和交付事件

状态响应包含 `request_id, delivery_id, plan_version=1, status, send_allowed, expires_at, parts`。准备期间为 preparing；可交付为 prepared；终态为 finished/failure/uncertain/aborted/expired/failed。业务状态位于片段 `business_status`（来自原查询结果；纯提示为 message），与传输状态分开。

片段包含 `id, position, mode, status, attempt, message`；有发送权时额外返回 kind、纯文本 text，图片还返回 image 受控路径和 mime。初始片段 ID 为位置的十进制字符串，position 从 0 开始；当前最多五片段，复用共享 `expand_replies` 的分页与余图语义。send_allowed 只说明计划当前有效，**不能代替每片段 intent 的发送许可**。

所有事件共同字段：`protocol,request_id,plan_version,gateway_epoch,event,part_id,attempt_id`，严格拒绝额外字段。M3 ready.features 包含 `classify`、`gateway_retire`、`ack_context_guard`、`notice_gateway_v1`，新网关要求四者齐备。ACK 允许可选布尔 `allow_context`，false 只补记发送事实并终结活动批，不提交上下文；省略保留 M2 兼容语义。ACK 还必须包含 `result,message_id`；success 要求非空消息 ID，其余 result 的 message_id 必须为 null。结果可为 success/failure/uncertain/aborted；审核中由网关映射为 uncertain。

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

持久接纳成功后立即登记 Work，后续初始化失败记为 failed。终态落库失败时先停止该请求的发送许可，保留 Work 和固定目标终态；新接纳及 ready 为 503，原因 `delivery_storage_unavailable`，查询仍展示实际账本状态且不允许发送。reaper 在其他过期维护前按 1、2、4 秒最多重试三次幂等终态写，不重执行业务或再次扣额。全部写成且维护恢复后才恢复接纳；耗尽后保留故障，修复存储并重启，由上述既有恢复规则收口。reaper 的 SQLite 错误会记录并在下一轮维护复查；未知异常停止任务并保持不可接纳。账本 schema 与协议版本不变。

## 主动通知协议（M4 已接线）

下列路由已认证并接入唯一 NoticeStore；通知关闭时返回 503 `notifications_disabled`。仅已提交部署且已有/生成有效报告才能领取；后端 ready 不构成公告条件。群许可、通知意图和 ACK 追加事实见 M4 运行准备。领取响应为 `{"claims":[...]}`，空结果不落请求元数据；相同空轮询 ID 后来可以观察新任务，非空领取则持久幂等。

| 路由 | 请求字段（均含 protocol、instance_id、app_id、gateway_epoch） | 返回及约束 |
|---|---|---|
| POST /v1/groups/events | event_id、group_id、kind（discover/add/remove/allow/reject）、timestamp_ms、ingress_seq | 事件去重和同群版本比较；返回 group_version、applied。discover 不授予许可，remove/reject 撤销未开始的通知发送权。适配器原秒时间戳在网关明确转换为毫秒。 |
| POST /v1/notices/claims | claim_request_id、limit（1–16）、可选 group_allowlist | 幂等领取：notice_id、claim_id、expires_at、group_id、group_version、deployment_revision、text、send_type=active；只有既有部署已提交且符合现有账本/许可条件的任务可领取。白名单最多 128 个唯一合法群 ID，在领取额度和租约之前过滤；省略或空数组沿用全部群。无原用户 msg_id，不复用被动序号或预算。 |
| POST /v1/notices/{notice_id}/events | claim_id、event_id、event（permission/intent/ack/aborted）、group_version；permission 带 allowed+observed_at_ms；intent 带 attempt_id；ACK 带 attempt_id+result+message_id | bot_state 核验绑定领取时的 group_version，不能覆盖随后发生的撤权；每通知只有一个有效领取者。发送前落盘 intent，ACK 幂等；未决发送在过期、重载、同部署再领取时不重授发送权。 |

通知稳定键为 instance/AppID/group/deployment_revision；claim_request_id 和 event_id 同 ID 内容冲突拒绝。所有身份沿用 1–256 字符、无控制符限制；文本沿用既有报告 120 字上限，一条主动纯文本，不套用被动五槽。ACK result 与被动相同；主动发送权限变化和通知真实客户端送达仍属 M4/实机验收。

Python 保留唯一现有通知账本和部署提交门禁；Koishi 只完成 bot_state 和实际发送，不复制完成账本。M4 已覆盖核验期间撤权、领取后重载、重复 ACK/领取、重启未决发送和旧版账本回读；实际平台送达仍待 M5。

## M2 本地验证（2026-10-19）

Windows / Python 3.12：22 项本机 HTTP + SQLite 专项通过，覆盖认证、就绪、请求冲突/去重、接纳乱序、逐片段意图与幂等 ACK、重启/代次变更、迟到 ACK、过期、容量、单写者、上传失败文字分支和续查锁跨交付期。实际 `backend` CLI 使用隔离目录及明确的 repository.load 夹具启动，/ready 返回 200，未导入 QQ SDK。专项禁止外网并清空 QQ/AI 凭据；不代表真实资料初始化、QQ 发送或 Koishi 端到端路径已验收。

wheel/sdist 构建及内容检查通过；wheel/sdist 分别在 checkout 外临时目录安装，33 个模块、静态资源、历史库及随包后端协议文档烟测通过。架构门禁通过。专项脚本保存在本工作区忽略目录 runtime/test_backend_m2.py；按照新增测试文件默认不提交的约定留作本地验证，既有 test_continuation.py 内的 3 个接纳/回执回归纳入交付。

完整现有回归运行 994 项，结果 OK（8 项按既有 POSIX/GNU tar 平台条件跳过，交由 Linux CI 覆盖）。修正了共享层提取后画廊脚本的调用位置，并保持原图字节/分页验收。首次完整尝试暴露的导入路径、画廊引用和日志断言问题均已修复；一次额外 faulthandler 诊断运行发生 Windows 原生进程异常退出，正常启动器完整重跑通过，未把诊断退出计作通过。

详细本地日志：runtime/m2-full-tests-corrected.log、runtime/m2-tests-final.log、runtime/m2-cli-final.log、runtime/m2-installed.log、runtime/m2-sdist-installed.log。生产切换、真实 QQ 回执和完整 Koishi 接线未执行；通知和周期任务按 M4 完成。
