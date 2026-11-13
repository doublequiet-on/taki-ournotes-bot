# QQ 引用图与撤回事件证据

核对日期：2026-10-29。证据来自腾讯公开 SDK 的源码及其问题记录，未登录账号、未发 QQ 消息，也未捕获本项目真实网关事件。现有 V1 已有图片读取与按 scope 缓存能力；**真实 QQ 引用图片入口尚未验通，不能据此宣称引用图全链路完成。** 运行时仍须遵守已配置且经过核对的 adapter profile。

## 入站与出站字段

腾讯 [Python SDK DTO](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py#L132-L170) 把入站引用表示为 `message_type=103`，引用内容在 `msg_elements` 中，元素可带 `msg_idx/content/attachments`。其 [parse_message](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py#L409-L462) 从原消息读取 `message_scene/message_type/msg_elements`；[群事件解析](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/event_parser.py#L118-L138) 保留这些字段。

腾讯 [Node SDK 原始群事件类型](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/types.ts#L254-L278) 同样含上述三个字段。[事件分发](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/gateway/event-dispatcher.ts#L224-L257) 将引用作为 `GROUP_AT_MESSAGE_CREATE` 或 `GROUP_MESSAGE_CREATE` 中的消息类型处理，当前已查 SDK 未声明单独的 quoted 事件名。

`message_reference.message_id` 在此 Python SDK 中位于[出站 MessageToCreate](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py#L278-L323)，用于发送引用。旧 botpy 的[共用频道/私信类型](https://github.com/tencent-connect/botpy/blob/master/botpy/types/gateway.py#L41-L68) 也有 `message_reference`，但共用类型或基类存在该属性，不能单独证明 QQ 群入站事件提供相同字段和语义。不能把出站字段直接套用到群入站引用。

## 已实现的文字预览和缺失提示

网关按上述官方 SDK 定义读取 `message_type=103` 的 `msg_elements[0].content`，只接受 4 KiB 内的非空纯文字，非法/缺失预览不影响原正文处理。经 `quoted_text` 传给后端，并作为当前用户提供的引用材料交给聊天。分类只看原正文，不因引文里的名字触发机器人。原始事件去重指纹包含引用字段；预览限长、协议校验与用户材料角色均有离线回归。

这条文字预览通路不依赖猜测引用索引，不授予原作者或原消息身份，不加载嵌套图片，也不把引文当作已 ACK 的机器人回复。可信 `reference` 仍受既有 profile 白名单约束；真实 QQ 的字段下发和客户端引用显示仍待实机验收。个人聊天后端在 ready 中声明 `chat_quote_hint_v1` 时，新网关可发有限的 `quote_hint`。它区分检测到引用图或检测到引用但没有可读材料，不携带 URL、原消息 ID 或权限。旧网关和未声明能力的后端继续按原请求形状交互。缺省不改变去重摘要，显式提示变化仍是内容冲突。query-local 的能力列表保持原样。

当前引用短追问优先使用可见引文。锚点已经失效时，可用预览作为当前回合材料，仍以未知原作者处理。已知撤回、范围清理及发送前复检继续生效。若使用了无可信原消息时间的失效锚点预览，派生回复只在该回合准备和发送。账本保留请求及回执，派生正文不进入聊天审计、候选、历史投影、续接任务或回复锚点。只剩不可读引用的点名短追问说明具体缺口，附有独立问题时仍处理该问题。查询翻页和确认回执按原合同。

## 原消息 ID 与引用索引

Node SDK [parseRefIndices](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/gateway/event-dispatcher.ts#L98-L139) 从 `message_scene.ext` 提取 `msg_idx=` 和 `ref_msg_idx=`；引用类型 103 存在 `msg_elements[].msg_idx` 时使用其中的索引作为被引索引。普通消息也可能带 `msg_idx`，而原消息业务 ID 仍是 `d.id`。这两个身份不能直接混用。

其 [quoteRef](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/middleware/quote-ref.ts#L124-L187) 把当前消息的 `msgIdx` 与 `messageId/senderId` 记录关联，再查询被引索引。腾讯仓库中的[引用索引问题 #7](https://github.com/tencent-connect/qqbot-nodejs/issues/7) 截至本次读取仍开放，报告普通/被引索引不一致及多级引用出现 `TMP_` 值。该问题是报告者的可复现记录，不是腾讯确认所有账号均有相同行为的公告；它足以说明不能仅凭字段名称宣布本项目关联已验证。

后续离线映射只能建立在当前已核对的 profile 上：原事件同时提供本消息索引与 `d.id`，在完整实例/应用/会话 scope 内记录二者及原作者，处理索引冲突、五分钟过期和容量淘汰；被引索引必须命中唯一关联才能取得原消息缓存。`TMP_`、相互冲突、缺失或无法关联的索引保持不可用。不能用正文相似度、时间靠近或“上一张图”猜原消息，也不能用本次追问者替代原图作者。

## 当前回合读取引用附件

腾讯 [SDK 示例](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/examples/e2e_test.py#L526-L542) 直接处理类型 103 首个元素的结构化附件。Taki 的显式聊天也可读取这个字段。网关复用 QQ 图片白名单与附件校验，兼容该 SDK 支持的 `//` URL 形式，并在个人聊天后端声明 `chat_quote_images_v1` 时以独立的 `quoted_attachments` 传递。只有已准入的显式聊天且识图获批才下载，沿用总张数、字节和超时限制。缺少可用附件时保留原有缺图提示。

这种附件可能没有可核验的原消息 ID、作者或时间。模型会收到独立的引用图片材料，图片和基于它的回复只用于本轮，不写入图片缓存、聊天审计、回复锚点或续聊。由于无法识别被撤回的原消息，有删除或整群清理记录的会话会跳过这种未关联附件。可信引用仍使用独立的原消息关联路径。真实账号能否取得有效图片需实机验收，QQ 仅提供预览或签名过期时仍需重发原图。

## 群消息撤回仍未核实

当前 [Node SDK 网关事件表](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/gateway/constants.ts#L57-L91) 未列出 QQ 群消息撤回事件。旧 botpy [Intents](https://github.com/tencent-connect/botpy/blob/master/botpy/flags.py#L186-L214) 与[分发实现](https://github.com/tencent-connect/botpy/blob/master/botpy/connection.py#L125-L198) 的 `MESSAGE_DELETE/PUBLIC_MESSAGE_DELETE/DIRECT_MESSAGE_DELETE` 对应频道及频道私信；群/C2C 事件表列出消息创建、机器人入退群和主动消息授权状态，不能据此推出群撤回回执已经接入。`GROUP_DEL_ROBOT` 是机器人退群，也不是消息删除。

本次没有找到并核实群撤回原始事件、授权条件或本项目适配器回执，因此结论为“未验证”，不声称平台一定不提供。频道删除事件、HTTP 删除接口或本地记忆删除成功，也不能证明收到 QQ 群原消息撤回通知。未经相应证据，长期记忆可提供用户授权的本地删除与到期清理，但不能宣称会自动跟随 QQ 撤回。

继续验收需取得脱敏的普通图、引用图、连续/多级引用和群原消息撤回事件，核对 profile、事件名、索引与原 `d.id`、scope、作者、过期及删除效果。合成样例只验证解析和隔离逻辑，不替代真实事件合同。
