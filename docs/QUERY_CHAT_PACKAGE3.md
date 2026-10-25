# 查询聊天 v3：包 3 模型路由与用量账

包 3 从包 2B `524c588749254fe1f3dba5ccc5c6ebb1206b720f` 继续。实现位于 Python 业务后端；Koishi 只增加慢提示交付，继续拥有当前平台与群策略的许可。此文说明工程行为，不代表正式百科、真实 QQ 身份、模型账号或上线验收已经通过。

## 入口与结果

长度合同：用户单次问题上限 500 字符；轻量聊天及人格直接回答上限 300 字符，第二次事实／百科生成上限 800 字符，超出返回 `model_response_too_long`，不会静默截断。模型输出另受配置的 Token 上限约束（默认 768，允许 64–1536）；Token 不等于中文字数。生成上下文最多保留 20 条历史消息且正文合计不超过 8000 字符，社交原型仅带同主题最近 6 条；会话展示快照另有 16000 字符保留预算。

U1 群回复为原生 @ 预留空间：群正文上限 1500 字符，长文本按 1400 分块；私聊仍为 1800／1700。连续页、慢提示重排及图片失败后的文字续发沿用该预算，完整文字保留。网关含 @ 的总载荷仍限 1800，不放宽模型 300／800 的回答门禁。需配套更新后端与网关，真实 QQ 验收单独执行。

`Backend.prepare_command` → `ModelRuntime.prepare`：明确查询先按请求核实 Haneoka 当前 release，再运行既有本地解析和模板。翻页继续读固定展示快照；传统指令、旧 Python SDK 入口、后台刷新和公告的旧 `AIClient.request()` 合同保留。新版后端不会落回旧 JSON 路由／修复链，也不写旧 `ai-quota.json` 次数账。

剩余 `/问` 在入场时固定模型配置、价格、受信用户身份与引用，进入包 1 的 5 执行／20 等待队列，每用户 1 执行／3 等待。等待最多 60 秒；执行总预算 30 秒，包括工具读取与第二次生成。AI 等待和 HTTP 不占 QueryGate；资料读取和绘图仍受它约束。一个入站消息最多开启一个 AI 编排；同批直接查询／本地查询仍保留顺序。

手动注册的原生工具覆盖歌曲、谱面、成员卡、SNAP、字段短答、活动歌曲榜线、人物／活动／卡池／缩写／帮助／状态、歌曲指标、审核知识和已显示结果解释。模型只能给注册参数；本地 schema 与原句实体／条件双重校验。拒绝未知字段、任意 URL／路径／SQL／管理员动作、多工具调用、参数错误以及无依据的条件放宽。普通查询直接模板输出，不让模型重写。

歌曲指标的同类“最短和最长”由一个工具返回两组，每个资源服只捕获一次快照。仍沿用包 0 核定合同及包 2A：歌曲级时长无需难度，谱面字段过滤／排序需明确难度；不在包 3 改动已经核定的难度缺省行为。难度不明的原 v3 产品描述与核定合同差异需在最终产品验收中确认。

知识问答、解释／简化已显示结果及 U2 的语义聊天允许第二次生成。第二次不提供工具，不接受返回的工具调用。纯寒暄及本地整句识别的偏好／日常交流保留一次直接文本生成；其他聊天先由注册工具 `chat_response` 选择话题，路由调用自带的文字不展示。其他零工具事实回答拒绝。`conversation_reply` 仍只有致谢、告别、指代澄清三个枚举值；`clarify_term` 和 `unavailable_fact` 只接受当前原句中的有界对象，分别返回局部澄清或资料缺口，不执行搜索。事实生成最多 800 字，所有聊天生成最多 300 字。玩家纠错重新检索，不修改知识库。

失败分类保留调用与业务两层事实：合法零工具文本若不属于受支持的寒暄，返回 `model_no_evidence` 与本地固定说明，不显示原文、不追加模型重试；它不表示知识库已检索后无匹配。真正协议异常仍为 `model_protocol_error`，超长仍为 `model_response_too_long`，不把模型超长归咎于用户的问题。工具参数结构错误为 `tool_arguments_invalid`；不支持的区服及区服／日期／版本／对象／难度不匹配继续拒绝。明确两服共通问题使用包 2B 的交集范围，不再一律拆问。中文年月日先规范为 ISO 日期，再校验与当前问句或有效继承条件一致；未提供的历史条件不得猜测。已发生用量仍按提供方返回结算；失败正文不作为已核验模型事实进入上下文。

`model_runtime.PERSONA` 为两次模型调用共用的表达原则：先回答当前问题，再给必要依据和条件；使用具体事实与自然短句，减少空泛免责声明、反复道歉、自我削弱及检索流水账。缺资料时说明具体缺口，有适用的已支持查询或必要澄清才补下一步。反防御性表达不隐藏失败，不扩大结论，不抹去区服／版本／日期及未知项，也不补写风险或建议。人格与任务提示词负责生成风格；直接查询模板不由模型改写，固定错误提示保留有限分类，响应结构、证据有效性、权限与用量门禁不变。实际文字质量仍需真实模型验收。

`application/persona.py` 提供共享语气、六类固定话题及一般聊天提示。整句命中保留快路径，其他主观比较和日常表达使用语义路由。没有本地查询提示的句子不先刷新游戏资料；提示匹配仅决定是否尝试本地解析，不授权事实输出。旧的外部游戏实体拒绝不再提前拦截原生模型聊天，传统入口行为保持。混合事实请求仍取证。语义追问要求同会话成功 ACK 的 `chat_topic` 与所选话题相同，生成传最近 6 条；新话题丢弃旧正文。概率、机制、日期、剧情等显式事实请求及继承问题不能进入无证据聊天。该补充检查不代替真实模型事实性验收。话题继承绝对期限并计入原内存预算，不持久化，既有证据租约仍检查撤审。性格来源、创作选择与缺口见 [Taki 性格说明](TAKI_PERSONALITY.md)。

每次模型调用：验证上下文／审核依据 → 预留用户与群额度 → 获取新的执行许可 → 再查上下文／依据／许可期限 → 持久标记 started → HTTP。第二次完整重走，不复用第一次许可；无自动修复、重试、模型切换或数据源切换。当前许可绑定同一个账务 attempt ID。

DeepSeek 非思考模式的业务路由首轮使用 `tool_choice=required`，防止模型跳过取证直接输出文字；明确偏好聊天使用独立无工具请求。仍拒绝多工具、未知工具及条件不符，第二次生成不提供工具。其他提供方保留原已登记行为，不自动尝试新参数或降级重试。协议依据：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)，实际能力另以本次固定题真实调用验证。即使提供方违规返回零工具文字，原证据门禁仍生效；明确致谢只映射为本地“不客气”，不展示自由生成事实。

实际执行超过 8 秒才发一次“还在核实，请稍等。”，排队静默。提示使用原发送账本的 intent／ACK、同一个 QQDelivery 和消息序号，占一个普通回复名额；最终结果最多使用剩余四个名额，其余进入原继续发送游标。提示不发布对话锚点；提示发送期间网关继续轮询模型许可。上传、发送、ACK 保留各自预算，不计入模型 30 秒。

## 知识、上下文与当前资料

U3 站内查证由 `application/site_lookup.py` 编排请求内目录和对象 handle，`sources/haneoka/notices.py` 提供固定公告接口。进入 `site_search/site_read` 后最多三次查证／四次模型调用；普通聊天和知识流程仍最多两次。每次继续重新预留、许可、结算；30 秒总预算不变，来源读取共享 `deadline-5`，为最终回答预留时间。网关上限同步到四次，配对发布与回退，不能单换后端。

目录固定已验证发布并核对当前 JP／intl release；公告校验区服、语言、24 小时新鲜度、响应头采集时间及列表／详情的一致性。公告 HTML 只抽正文文本，图片与超长正文标明缺口；不执行网页指令，不接受模型 URL。候选标题不是正文证据，多个候选须按问题选择或澄清。原始证据合计限 6000 字符，答案仍限 800 字符，程序另追加来源脚注；证据进入原 ACK 内存上下文并保留原期限，公告期限更短时提前收紧。无新持久化格式，不自动入百科。范围及未接入项见 [U3 计划](QUERY_CHAT_NEXT_PLAN.md#u3haneokamoenotes-站内自主查证)。

知识检索使用包 2B 的 `EvidenceLease`。生成前、第二次许可后、生成后、发送 intent 前和成功 ACK 发布上下文前均核对。撤审／删除的已捕获答案整份失效，旧索引失败不会恢复内容；已发生用量仍结算。已经发出的 QQ 请求无法撤回，晚 ACK 只记发送事实，不恢复失效上下文。

上下文保存已确认显示文本、最多 20 条短期角色消息、最多 8,000 字历史及其证据依赖；明确解释普通查询时保留至多 6,000 字的已核验工具证据，后续解释复用证据，不把模型自己的上次回答当事实。与原每会话 20 个快照、16,000 字和全局 8 MiB 文字预算共同生效，证据、历史、检索主题和待改写问题均计入内存体积。派生内容继承最早到期时间，绝不因新回复续期；重启不恢复。正文不进入用量账、配置、诊断或导出。

知识回答附带至多 500 字的检索主题和已校验范围，只在成功 ACK 后发布，纳入原内存预算。`knowledge_search.followup=true` 才继承这个主题；当前明确区服优先，明确日期／版本或“现在”替换原时间条件。新话题不继承。`visible_result` 可对“说简单点／没懂”使用最后一次知识租约或普通查询事实，绝不把上一段模型正文当证据；所有历史依赖仍需有效。直接查询即使紧接知识对话也继续本地零模型处理。

另保存最近一次实质问题（最多 500 字）；简化生成只传这个问题、上一条展示文本和当前改写要求，不带整段旧话题。问题及展示文本作为用户上下文明确编辑对象，不是事实来源，不提升为系统指令；事实仍从完整有效租约取得。

`knowledge_search.mode` 只接受入门、讲解、比较、计算四类任务。入门先解释参与和操作，公式问题才展开计算缺口；有已审核依据的正常玩法教学及新手建议开放，未经审核的逐段打法、自动配队与预测不开放。待确认百科正文见 [激奏内容稿](GEKISO_CONTENT_PENDING.md)，该文件不在知识目录，不产生批准效力。

COMBO 满连／名次问题在证据包含唯一完整的 COMBO 排序行、并列条件和区间前连击说明时，直接按已审核列值生成固定解释，省去第二次自由生成；并列／连击说明缺失或排序行冲突时不使用此模板。模板不硬编码表格数值，不猜单局差异，仍携带全部租约并走发送前／ACK 前复核。其他玩法解释仍按对应任务生成。

`CatalogRepository.current_with_info` 成功核实 JP／intl release 后，可复用同版本的已验证内容；版本变化才读取完整新资料。核实失败返回不可用，不把缓存当当前答案；当前请求只更新独立内存视图，不改旧持久缓存、刷新状态或公告增量。新版结果的实体详情核对当前版本，变化时请重新查询；固定分页和 Meta 已捕获详情不重查。

## 配置与启用条件

后端读取 `OURNOTES_AI_PROFILE_FILE` 指向的管理员保护文件；新版查询密钥单独来自 `OURNOTES_QUERY_AI_API_KEY`。旧 `AI_API_KEY`／`AI_MODEL`／`AI_BASE_URL` 继续服务旧公告等调用。密钥不会进入 profile 文件、`repr`、状态或导出。文件须原子替换且修改 `config_version`；已受理请求继续用自己的快照。版本与文件摘要在新账本登记，跨重启不能用同一版本替换内容。

配置结构如下。占位符必须由实际能力证据、登记价格及可信身份绑定替换；此示例不提供可上线的报价或平台证明。

```json
{
  "schema": "taki-model-config/1",
  "instance_id": "your-instance",
  "app_id": "your-app",
  "profile": {
    "provider": "deepseek",
    "model": "verified-model-name",
    "wire_profile": "deepseek-chat-v1",
    "config_version": "config-1",
    "capability_evidence": "admin-acceptance/model-probe-id",
    "price": {
      "currency": "USD",
      "input_rate": "REPLACE_WITH_INTEGER_MICRO_CURRENCY_PER_MILLION",
      "cached_rate": "REPLACE_WITH_REGISTERED_RATE",
      "output_rate": "REPLACE_WITH_REGISTERED_RATE",
      "reference_rate": "REPLACE_WITH_FIXED_REFERENCE_RATE",
      "pricing_version": "registered-price-1",
      "fetched_at": "REPLACE_WITH_UNIX_TIMESTAMP",
      "estimated": true,
      "source": "registered-default"
    }
  },
  "bindings": [
    {"kind": "group", "conversation_id": "trusted-group-id", "user_id": "trusted-member-id",
     "subject": "verified-player-1", "evidence_ref": "admin-acceptance/unified-identity-proof"},
    {"kind": "c2c", "conversation_id": "trusted-private-id", "user_id": "trusted-private-id",
     "subject": "verified-player-1", "evidence_ref": "admin-acceptance/unified-identity-proof"}
  ]
}
```

同一个受信 subject 才能共享群／私聊额度。不同群的绑定也须明确登记。不从昵称、头像或裸 ID 推断同人。v1／v2 没有绑定时返回 `identity_unverified`；配置中的证据引用由管理员提供，结构校验不等于平台实测已经通过。

v3（`taki-model-config/3`）在 v2 的 `secret`、`groups`、`bindings` 之外要求 `group_admissions`：最多 128 个 `{"conversation_id":"verified-platform-group-id","evidence_ref":"admin-acceptance/group-proof"}`。管理员核验真实网关身份后，可明确放行该群全员；不支持通配符，也不使用 QQ 群号码替代平台群标识。其他群和未绑定私聊继续拒绝。网关验签／认证、每次模型调用的动态许可、群策略及用量预留不变。

群成员的稳定 subject 由 `group_subject(instance_id, app_id, conversation_id, user_id)` 生成，跨重启保持一致；个人额度分别计算，全群共享群额度。显式绑定优先。经核验的同一账号私聊绑定必须使用已有群成员的 subject，才能共用个人额度且保留历史用量。不同群的成员身份不自动合并。控制台保存配置时原样保留受保护的群准入列表，不能通过额度编辑扩大准入。

撤销准入须原子写入新配置版本；此前已受理请求仍按其配置快照处理，如需立即停止模型调用须同时撤销网关许可。回退到只支持 v1／v2 的代码前恢复对应受保护配置；保留用量账和身份核验／批准／撤销记录，不清空额度。旧代码拒绝 v3 配置，不会自动放开权限。

目前 wire profile 为 `deepseek-chat-v1`、`openai-chat-v1`、`openrouter-chat-v1`，端点分别固定在官方 HTTPS 域名；不接受玩家或配置中的自定义 URL，也不跟随重定向。仅接文本 Chat Completions；DeepSeek 请求显式设置 `thinking: {"type":"disabled"}`，响应若仍含非空 reasoning_content，继续按不支持的协议拒绝并保留用量。不支持的 Responses-only／DeepSeek thinking 工具链须另实现适配，不能靠改模型名声称支持。没有 profile／密钥／能力证明／价格时不启动 AI。

知识生成提示词区分事实资料日期与整理／访问／批准日期，按证据声明的共通机制范围回答，同时保留实体、材料数和日期条件。省略号及示例凭据不视为真实泄露；代办账号操作请求使用固定拒绝答复，不追加凭据状态判断或操作建议。这些生成约束仍需真实模型逐题验收，不能代替既有证据、权限和响应校验。

可选工程默认：`max_input_tokens=32768`、`max_output_tokens=768`（允许 64–1536）、`user_daily_limit=100000`、`group_daily_limit=1000000`。这些是实现默认，不是用户选定的额度。输入按文本 wire 序列化 UTF-8 大小加 framing 余量上界检查，并预留该上界及最大输出；实际能力验收须覆盖所选模型的分词与输出上限。每可信用户 10 秒最多进入 3 个 AI 请求；第二次生成不算新请求。`parallel_control` 默认 false，仅已证实支持的 OpenAI／OpenRouter 组合可设 true；`cache_usage=true`、`reasoning_usage=false` 按真实响应能力登记，不能把缺失计数填为零。

OpenRouter 配置启用后，后台从官方 models API 刷新本模型价格；失败或收费维度不适用时保留登记的估算默认价。其他 provider 使用登记默认价，不从 OpenRouter 或第三方网站借价。币种及参考单位固定于账本，换成不兼容单位会拒绝新预留，不能混算或自动猜汇率。配置页面和主动能力测试属于包 4；本包不在页面打开时发付费探测。

原生协议和用量字段依据：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)、[DeepSeek completion](https://api-docs.deepseek.com/api/create-chat-completion/)、[OpenRouter models](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)。官方文档说明协议，不能证明当前账号或所选模型实际支持。

## 新账本

`usage-v1.sqlite3` 独立于旧次数账、发送事实账和公告账；Python 单进程独占写。保存 request／attempt、HMAC 用户／群键、provider／model／配置版本、价格快照、预留、started、用量、精确费用、错误分类及时间；不保存原始 OpenID、问题、回答、工具参数或密钥。稳定盐仅存该账本私有元数据。

价格单位为整数“微货币／百万 Token”，额度为固定参考输入价格的等价 Token。设非缓存输入、缓存输入、输出数量为 `i,c,o`，对应单价为 `pi,pc,po`，参考单价为 `pr`：

- 加权额度：`(i*pi + c*pc + o*po) / pr`。
- 参考成本（微货币）：`(i*pi + c*pc + o*po) / 1000000`。

两者都用有理数保存，不用浮点累积。缓存包含在总输入中，reasoning 包含在总输出中，不重复相加。`total_tokens` 单独存在时不猜分项价格；缺失部分记 partial／unknown，只结算能确认的分项。失败、参数错误、撤权、超时、QQ 失败都不会抹掉已确认用量。

原子检查用户与群的当日已用量＋在途预留，再创建 attempt。未 started 的失败释放预留；收到明确响应后按真实可确认用量结算、释放余额。北京时间日界线以实际 started 时刻归属，跨午夜重新检查新日预算。价格刷新不重算历史 attempt。

重启仅释放未 started 的预留；started 未结算项保留至开始后 5 分钟，再标为未知、释放预留。不会重放 HTTP。晚到可信用量按同 attempt 幂等补结，可导致余额为负，之后停止新 AI。已结算事实冲突可诊断，不能覆盖成另一组计数。

管理员现有 overview 的 `service` 提供 `native_model` 和 `weighted_usage` 只读状态，包括 unknown／partial、估算价、账务不完整与参考成本；玩家不显示 Token、额度数字或费用。未知用量可能低估消耗，参考成本不是供应商账单或硬费用上限。

## 验证与后续门槛

既有测试文件扩展覆盖原生 HTTP（隔离 loopback 服务）、权限与额度、日界线、失败用量、撤审及晚 ACK、超时与晚到用量、固定配置、同工具分组和正文隐私。Koishi 禁网探针验证实际安装适配器的发送序号及慢提示并行许可轮询。完整回归、架构与 wheel／sdist 安装记录绑定交付提交，不用隔离夹具声称真实资源／模型／QQ 通过。

上线仍需：真实模型工具与 usage 能力、真实跨场景身份映射、正式审核百科、真实回复引用／按钮／QQ 客户端送达，以及包 4–5 的控制台和整体验收。此包不编写百科正文，不合并或部署。
