# Taki 可视化控制台

新增运维入口、任务与审计契约见 [运维控制台](OPERATIONS.md)。正式页面分为总览、查询记录、群管理、数据来源、设置；Astro 仍是隔离演示。

开发基线：`b3d7c4267f8dd304ff0ce59dde2d9e3ff287c35a`。V1a 运输协议为1.0；V1b配对运输协议1.1及组合能力 `group_policies_v1`。管理读取独立声明 `admin_read_v1`、`diagnostics_v1`，握手另声明遥测能力。沿用 Python 业务后端、Koishi 网关两个进程，浏览器只调用受认证 Console RPC。

## 使用入口与事实口径

登录限频按 WebSocket 连接隔离（同一连接每秒一次），失败尝试不会占用其他连接的登录预算。公网域名入口还须使用 [受限反向代理](CONSOLE_DOMAIN.md) 的每 IP 连接数／握手速率限制；不把本机端口直接暴露到公网。

在受保护的 Koishi Console 打开 **Taki 管理**，登录后进入总览、查询记录、群管理、数据来源和设置。总览显示当前健康和最近 24 小时**已受理请求**，不代表全部 QQ 消息。查询详情显示发送生命周期、片段事实、业务结果和观测缺失。群管理使用已观察/通知/配置的群候选，可关联备注、查询、通知与 `/问` 策略；不表示完整平台群名单。

日常使用先看总览的运行状态，再到查询记录排查单次回复；群管理先选群、填写备注，再调整权限。页面采用设计预览的侧栏、卡片和并排详情；窄屏通过导航抽屉切页，查询和群详情可关闭，关闭群详情保留未保存的草稿；导航与详情支持键盘焦点，单请求诊断在查询详情内导出。默认跟随 Koishi 明暗主题，手动切换只影响 Taki 页面并保存在浏览器本地。来源、采集状态、完整请求记录和通知许可可展开查看；未采集的数据仍明确标为缺失。全局与群设置各自显示未保存提示，分别保存。手动关联入口使用 QQ 平台 OpenID，不是 QQ 群号。

总览的主目录状态来自 `sources.catalog`；当前管理接口没有独立的 Moenotes 状态或准备耗时中位数，这两处显示“尚未采集”，不会用预览数据或其他来源推算。今日 `/问` 数量只使用已采集的今日桶，桶或字段缺失时显示“—”。

平台确认发送不代表客户端看到或已读。`processing` 表示准备/运输尚未关闭；有成功片段但未全成功为 `partial`，同时单独显示 uncertain 片段数；无成功但有 sending/uncertain 为 `uncertain`；全部非零片段成功为 `complete`；明确 failure 为 `failed`；关闭且零片段或无发送确认为 `closed_unsent`。生命周期保留 expired/aborted 等原因，合法晚到 ACK 可更新片段事实。汇总、列表、详情共用 `delivery_state()`。

## 字段来源表

| 展示事实 | 唯一来源 | 未知处理 |
|---|---|---|
| 当前 QQ/后端/握手/版本 | 网关共享实时健康读取；后端 `/v1/ready` | 当前探测失败时 ready=false；不能用旧握手推定健康 |
| 查询接收开关 | 原五字段 settings.json | 保存采用 gateway epoch + revision；旧写接口 upgrade_required |
| 受理时间、生命周期、片段发送状态 | Python deliveries-v1.sqlite3 只读 | 不调用有过期处理的 lookup，不写账本 |
| 群、作用域、有限命令类别 | 接纳时写 diagnostics-v1.sqlite3 | 历史不反推；私聊不记录用户身份 |
| 业务结果、原始片段类型、文字回退可用性 | 实际 PreparedReply 元数据 | 原字段缺失显示 unknown；primary 不推定 image |
| 准备、排序等待 | Python 本进程 perf_counter | 仅实际准备调用；不包含等待发送结束 |
| 上传、发送、回执提交耗时 | Koishi 本进程 performance.now，旁路遥测 | 缺失不补零；不跨进程相减，不承诺阶段总和 |
| 主目录缓存 | repository.capture_catalog 的内存快照 | 实际后台尝试/完成/错误快照与采样配置；刷新失败仍保留缓存可用事实，未采集字段保持缺失 |
| 通知状态与群许可 | 现有通知 owner 只读 SQL | NULL 许可保留未知；关闭通知为 unsupported；不领取/生成报告 |
| /问 今日、近七日计数 | QueryMetrics.v2 加锁快照 | 缺日不填零；无文件 unavailable；损坏不改变写入器状态 |

命令到片段的归属来自实际准备结果 command_index，缺失时保持 null；运输覆盖不保证完整，观测质量保留 partial。业务状态归一化为 ok/no_result/invalid/unavailable/unknown，多种业务结果为 mixed。不持久化问句、用户 ID、原消息 ID、图片或完整日志。

## 接口契约

Console RPC：`taki/overview`、`taki/requests/list`、`taki/requests/detail`、`taki/sources`、`taki/notices/list`、`taki/query-metrics`、`taki/groups/list`、`taki/groups/update`、`taki/settings/update`。所有读取和写入要求独立管理员会话及 authority 5；浏览器不获得后端 token。原 `taki/settings` 只返回 `upgrade_required`。

Python 对应 `/v1/admin/{overview,requests,requests/{id},sources,notices,groups,query-metrics}`，遥测 POST `/v1/admin/telemetry`。继续要求 Bearer 与实例标识；遥测另验证活跃网关 epoch、后端 epoch、请求/交付/片段/attempt 关联，不允许更新发送状态。

响应：`schema_version=1,generated_at,backend_epoch,capabilities,sections`；每个 section 包含 `availability,observed_at,stale,data,reason_code`。availability 为 available/partial/unavailable/unsupported。未采集数据为 null，不反推历史。

请求列表接受 limit（默认25，1–100）、from/to（秒，半开区间，最多7日）、state、group_id、attention、cursor。首次默认最近24小时；游标绑定窗口、过滤条件与 `(created,id)` 排序点并校验签名，重启后重新读取。attention 包含仍保留的历史未决记录。未知参数、非法窗口/状态/游标返回受控 400；身份不匹配 403，未知请求 404，关联冲突 409。返回不包含正文或平台消息 ID。通知列表默认25、最多100，签名游标固定首次插入窗口并按rowid倒序；旧账本无时间戳，不能按24小时筛选或用版本SHA推断时间，返回partial及notice_timestamps_not_collected。群候选默认25、最多100，以进程内有界快照游标固定排序，绑定会话、全局guard和策略revision，5分钟过期；配置群优先，切换页不丢编辑草稿。

页面总览15秒，未结束详情5秒，来源及指标60秒；隐藏、退出、卸载暂停。管理读取预算约2秒，共享缓存5秒并合并并发，读取错误保留最近数据与草稿；明确 unauthorized 或页面卸载时清除登录状态。轮询不替换脏草稿；保存冲突提供服务器值。

保存提交时固定快照；等待响应期间的新编辑继续作为未保存草稿保留。失效页面的保存响应不能重新填入管理数据。同一标签页断线或刷新后可通过浏览器 sessionStorage 恢复未保存的全局、分群和模型设置草稿，以及支持重试的未确认运维操作标识；需重新登录。草稿可能包含管理员可见的群标识，未提交的模型密钥不会存入浏览器并会清除。主动退出会清除暂存；浏览器禁止暂存时提示风险，不承诺关闭标签页或浏览器后的恢复。网关实例变化或原实例未知时，分群草稿只能查看，须核对后载入服务器值才能继续保存；再次断线或刷新不能替换草稿原来的实例身份。组合包仅对指纹匹配的 Koishi 5.30.11 客户端修正重连：卸载扩展页前保存首次断线的完整 URL，连续失败时递归保留，恢复后返回该地址；避免先跳欢迎页再刷新而丢失 `/taki`、查询参数或锚点。指纹不符时构建失败。指标写锁忙时返回 `metrics_snapshot_busy`，不等待查询线程的磁盘写入，也不停止后续指标写入。

## 观测与资源

网关 `activity()` 增加唯一占用 `pending_total`、`capacity`、`epoch`、`started_at`、`startup_error` 和固定枚举 `admission_attempts`，累计数重启归零；其中 `capacity_rejected_unclassified` 不能等同已识别业务的超载。后端 overview 的 `sections.ledger.data` 直接查询 requests／highwater／epochs 及未决事实数量，提供各自 limits、at_capacity、reason_codes 与 retention_seconds。现有受认证读取通路承载这些字段，不扩大发送遥测的身份校验；配置及生命周期见 [查询性能与容量](../PERFORMANCE_FOUNDATION.md)。

独立诊断库由 Backend 单写，保留24小时，最多4096请求、每请求32跨度。SQLite使用失败即返回的锁预算；异常只降低观测可用性，不影响发送许可。设置 `TAKI_DIAGNOSTICS_ENABLED=0` 可关闭旁路采集而继续业务，不创建诊断库。新增span_results采用附表，不改原span列数；V1a旧模块升级前后仍能读写。Koishi 队列256条，每批32条且不超过24KiB，失败最多重试一次；关键运输流程仅同步入队，不等待遥测 HTTP。库故障与丢弃计数在采集健康中显示。队列进程退出未送达部分会缺失，不能当作完整覆盖。

管理读取不触发目录刷新、通知 claim、发送 intent/ACK、过期处理或指标写入。QueryMetrics 的 ask_total 是问请求口径，route 与 AI 工作计数不能相加作为请求总数；不计算账单、客户端送达率。

## 分群策略与保存

原五字段 settings.json 保留；全局更新需当前gateway_epoch/revision，跨进程旧guard拒绝。群策略独立文件 group-policies.v1.json 包含schema=1、qq/app_id/instance_id身份、持久revision及最多128项策略；备注最多80个Unicode字符。每次保存先验证当前持久版本，再fsync临时文件并原子替换；异常后核对最终持久内容，确认未保存则保留原生效值。损坏、非普通文件或身份不匹配限制新群查询与通知，并在页面明确报告；不重置为全开放。

queries/notifications支持inherit、allow、pause；allow仍受全局范围与平台许可约束。natural_query支持inherit、off、local、model；继承维持既有后端能力，model仍受配置及额度约束。有效规则只有一个入口，供页面、查询接纳和通知范围使用。1.1网关即使关闭网页入口也必须加载管理目录，网页开关不影响策略执行。

新请求使用接纳时不可变快照，网络重试不重新取策略；相同请求快照冲突拒绝而不替换已受理请求。通知all/selected/none在领取前过滤、发送前复查当前规则；selected不能为空，零个可发群为none，不能转成all。暂停新请求不取消在途查询，撤权及在途ACK继续处理。

/问使用每请求ContextVar，off受控拒绝，local仅本地解析且不调用模型、修复或额度预留；local不复用model/unknown来源缓存。结果、上下文、混合续查携带实际解析来源，权限收紧后不扩大旧续查的许可；直接指令继续按原顺序执行且不调用模型。group-policies不是业务账本，页面不管理密钥或任意插件。

## 回退与恢复

受信控制器在启动不支持群策略的旧版前保存原设置、群策略、属主权限及摘要，并原子关闭全局查询和通知；冻结失败不启动。独立launcher每次启动也验证冻结许可及固定目录。仅允许已审阅的旧版SHA，未知旧版保持停止；旧版必须启用管理加载冻结设置。新版恢复仅在当前文件仍匹配冻结摘要时恢复原开关，人工修改保持现状并报告conflict。业务账本、额度、缓存及群策略不随代码回退。控制器安装与正式运维步骤见 [M4 runbook](../KOISHI_MIGRATION_M4.md)。

## 验证与交付

回归入口：`python -B -m unittest discover -s tests -p test_console_admin.py`、既有续查回归、架构门禁及 `node scripts/probe.cjs`。正式包资源检查与浏览器证据、性能数据和附件矩阵执行结果记录于本任务验收记录；未执行项目不能按通过报告。

本功能开发不包含合并、部署、重启或 QQ 发送。自动更新继续暂停。生产采集需批准部署后另验。


## Agent 费用与执行观察

新增“模型与费用”页面；“查询记录”显示为“请求记录”。总览 service 的 `model_costs` 来自完整当日账本：金额为精确分数字符串，单位微货币；按模型、提供方和币种分别列出已发生费用、进行中预留及未知用量预留。DeepSeek 支出单列，不计入主模型美元预算。页面读取不发起模型探测。

`next_route` 复用运行时的首轮与最终回复预留门槛，仅表示采集时下一轮的预算选择，不保证权限、额度或网络连通。在途请求继续使用原模型。实际路由新增 `model_route` 诊断阶段；旧记录未采集原因时保持未知。请求详情显示实际模型尝试、Token、费用及处理阶段，释放预留不等于付费调用，平台回执不等于客户端收到。

前端费用投影由 `koishi/console/costs.js` 提供，随 `taki-console-assets` 分发；只读总览接口和已有 Console RPC 传递新增字段，不新增凭据或持久库结构。


## 群能力、记忆及模型管理

`GET /v1/admin/chat/status` 与 `/v1/admin/chat/groups/{gid}/status` 投影主模型、备用模型和独立主动参与模型各自的版本、联网／图片／主动发言批准元数据及群服务每小时上限；不触发探测或修改批准。模型配置、能力批准与当前群策略分别显示，登记证据不等于实际功能验收。群详情通过现有记忆接口读取数量与 30 天保留规则，不返回正文。Console 的删除请求绑定群、当前网关 epoch 和 `expected_revision`；冲突或超时只刷新核对，不自动重放。删除沿用宿主的摘要、图片来源、上下文及在途回答失效流程，保留费用账本。

“模型与费用”及设置页复用同一模型编辑状态，分别管理主模型、DeepSeek 备用与主动参与模型配置。`GET/POST /v1/admin/model/fallback` 复用受保护配置写入、版本保护和元数据审计；备用配置仅允许 DeepSeek，已有计费参考币种／单位不能改变。切换服务商须提供对应新密钥。浏览器恢复草稿保留模型角色归属，不保留密钥；有草稿时先保存或放弃再切换配置。

「主动参与限额」在模型设置区域按群编辑启用状态、每小时模型请求数、回复数、费用和冷却。后端为 `GET/POST /v1/admin/model/proactive/groups/{gid}/limits`，Console RPC 为 `model/proactive/limits/settings` 与 `model/proactive/limits/save`。保存沿用管理鉴权和网关 epoch，增加服务文件与模型双指纹保护；不重置历史预算、不授予新群批准。未批准群可先保存限额，仍不会自动发言。

独立主动参与使用 `GET/POST /v1/admin/model/proactive` 和 `POST /v1/admin/model/proactive/probe`，对应 Console RPC 为 `model/proactive/settings`、`model/proactive/save`、`model/proactive/probe`。权限、网关 epoch、版本冲突及探测幂等要求与原主模型入口一致。密钥独立保存；读取或保存不触发付费测试，也不批准群内发送。具体配置文件、群服务批准和账本约束见 [独立主动参与模型](../CHAT_SERVICE_JOBS.md#独立主动参与模型)。

OpenAI Responses 发送 `reasoning.effort`，表单提供默认及 API 支持的枚举，具体档位支持仍取决于模型；Chat Completions 接口不发送本应用尚未接入的推理参数。DeepSeek 分开提供关闭及 low/high/max，发送 `thinking` 和 `reasoning_effort`；旧配置省略该字段仍关闭思考。DeepSeek 思考模式只用 auto 工具选择，不发送该模式不支持的 required。工具循环精确回传当轮 `reasoning_content`，仅在内存中保留；历史只恢复可见文本，不保存或展示推理内容。输入序列化预算和输出计费上限仍生效。参考：[OpenAI reasoning](https://developers.openai.com/api/docs/guides/reasoning)、[DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/)、[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)。

保存会生成新模型版本，绑定旧指纹的能力及服务批准需重新核对；不会自动批准或付费测试。启用 DeepSeek 推理档位后，旧版本不支持该配置值：回退应用前须在当前版本关闭该选项，或按既有受保护配置恢复流程处理，不能只回退代码。


## 知识资料管理

“数据来源”包含独立的已审核 ON 百科、剧情与派生人格资料视图。主机缓存和群记忆不进入正式知识库。状态页按 20 项分页，展示来源、版本、审核人／时间、确认编号、适用范围、索引和发布状态；来源超过 3 个或已存剧情版本超过 50 个时明确显示截断。人格批准仍独立于资料包导入。

管理读接口为 `GET /v1/admin/library/{knowledge|story}/{offset}`，写接口为 `POST /v1/admin/library/{preview|compare|publish|withdraw}`；Console RPC 为 `library/status` 及相应动作，继续使用 authority 5、当前管理会话和活动网关 epoch。`application/library_admin.py` 调用当前宿主拥有的 KnowledgeService／StoryService，不创建第二个数据库 owner、不调用模型或抓取 URL。

浏览器导入入口保持既有 32 KB 管理消息边界，单个 JSON 内容限制 18000 UTF-8 字节。百科包结构为 `{schema:"taki-console-knowledge/1", files:{"相对路径.md":"完整 Markdown"}, approvals:原批准清单, delivery:原交付清单}`；所有批准、哈希、审核人与确认记录仍由内容方提供，并通过原有 `validate_delivery`。剧情使用原 `taki-story-bundle/1`，不接受合成来源或包内已批准人格。更大资料包使用原本地导入工具，不拆成多次整库替换。

预览比较当前整库与候选，列出新增、移除、修改和正文／审核清单差异，绝不把缺失文件当增量保留。完整差异超过 100 项、20000 字符输入或 30000 字节输出时不提供发布令牌。可发布令牌保存在内存中，10 分钟有效、最多 8 个，绑定后端 epoch、源版本及撤回状态，执行前再次检查；令牌先消费再写入，失败或结果不明不自动重放。编辑正文、切换资料类型或退出会话会清除前端预览；不保存导入草稿到浏览器恢复存储。正式版本变化后页面禁用旧预览。

发布必须勾选人工审阅确认并确认完整快照替换。此操作不会生成批准材料或自动批准人格。剧情可比较已存版本，当前撤回记录继续生效；百科仅比较当前与上传候选，不声称持有旧正文。百科撤回先持久化禁止使用的版本下限，再写既有格式的撤回清单，因此写失败或旧材料恢复不能重新放行。重新批准须保留撤回记录，并提供更高版本及新的人工确认。

发布和撤回按宿主既有证据边界使旧回答失效；不重发消息或清除计费。耗时索引操作可能超过管理响应预算，此时先刷新状态核对，不能盲目再次发布。CLI、数据库结构与既有批准格式保持兼容。
