# QQ 引用图与撤回事件证据

核对日期：2026-10-29。证据来自腾讯公开 SDK 的源码及其问题记录，未登录账号、未发 QQ 消息，也未捕获本项目真实网关事件。现有 V1 已有图片读取与按 scope 缓存能力；**真实 QQ 引用图片入口尚未验通，不能据此宣称引用图全链路完成。** 运行时仍须遵守已配置且经过核对的 adapter profile。

## 入站与出站字段

腾讯 [Python SDK DTO](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py#L132-L170) 把入站引用表示为 `message_type=103`，引用内容在 `msg_elements` 中，元素可带 `msg_idx/content/attachments`。其 [parse_message](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py#L409-L462) 从原消息读取 `message_scene/message_type/msg_elements`；[群事件解析](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/event_parser.py#L118-L138) 保留这些字段。

腾讯 [Node SDK 原始群事件类型](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/types.ts#L254-L278) 同样含上述三个字段。[事件分发](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/gateway/event-dispatcher.ts#L224-L257) 将引用作为 `GROUP_AT_MESSAGE_CREATE` 或 `GROUP_MESSAGE_CREATE` 中的消息类型处理，当前已查 SDK 未声明单独的 quoted 事件名。

`message_reference.message_id` 在此 Python SDK 中位于[出站 MessageToCreate](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py#L278-L323)，用于发送引用。旧 botpy 的[共用频道/私信类型](https://github.com/tencent-connect/botpy/blob/master/botpy/types/gateway.py#L41-L68) 也有 `message_reference`，但共用类型或基类存在该属性，不能单独证明 QQ 群入站事件提供相同字段和语义。不能把出站字段直接套用到群入站引用。

## 原消息 ID 与引用索引

Node SDK [parseRefIndices](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/gateway/event-dispatcher.ts#L98-L139) 从 `message_scene.ext` 提取 `msg_idx=` 和 `ref_msg_idx=`；引用类型 103 存在 `msg_elements[].msg_idx` 时使用其中的索引作为被引索引。普通消息也可能带 `msg_idx`，而原消息业务 ID 仍是 `d.id`。这两个身份不能直接混用。

其 [quoteRef](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/middleware/quote-ref.ts#L124-L187) 把当前消息的 `msgIdx` 与 `messageId/senderId` 记录关联，再查询被引索引。腾讯仓库中的[引用索引问题 #7](https://github.com/tencent-connect/qqbot-nodejs/issues/7) 截至本次读取仍开放，报告普通/被引索引不一致及多级引用出现 `TMP_` 值。该问题是报告者的可复现记录，不是腾讯确认所有账号均有相同行为的公告；它足以说明不能仅凭字段名称宣布本项目关联已验证。

后续离线映射只能建立在当前已核对的 profile 上：原事件同时提供本消息索引与 `d.id`，在完整实例/应用/会话 scope 内记录二者及原作者，处理索引冲突、五分钟过期和容量淘汰；被引索引必须命中唯一关联才能取得原消息缓存。`TMP_`、相互冲突、缺失或无法关联的索引保持不可用。不能用正文相似度、时间靠近或“上一张图”猜原消息，也不能用本次追问者替代原图作者。

公开 SDK 支持从引用元素取得附件，但其元素类型没有原发言者字段；仅有引用附件不等于已核实原作者与原消息 ID。缓存未命中时，不得把嵌套附件记作当前消息作者的原图。签名 URL 和图片字节继续仅短时驻留内存，不进入长期记忆或审计正文。

## 群消息撤回仍未核实

当前 [Node SDK 网关事件表](https://github.com/tencent-connect/qqbot-nodejs/blob/main/src/protocol/gateway/constants.ts#L57-L91) 未列出 QQ 群消息撤回事件。旧 botpy [Intents](https://github.com/tencent-connect/botpy/blob/master/botpy/flags.py#L186-L214) 与[分发实现](https://github.com/tencent-connect/botpy/blob/master/botpy/connection.py#L125-L198) 的 `MESSAGE_DELETE/PUBLIC_MESSAGE_DELETE/DIRECT_MESSAGE_DELETE` 对应频道及频道私信；群/C2C 事件表列出消息创建、机器人入退群和主动消息授权状态，不能据此推出群撤回回执已经接入。`GROUP_DEL_ROBOT` 是机器人退群，也不是消息删除。

本次没有找到并核实群撤回原始事件、授权条件或本项目适配器回执，因此结论为“未验证”，不声称平台一定不提供。频道删除事件、HTTP 删除接口或本地记忆删除成功，也不能证明收到 QQ 群原消息撤回通知。未经相应证据，长期记忆可提供用户授权的本地删除与到期清理，但不能宣称会自动跟随 QQ 撤回。

继续验收需取得脱敏的普通图、引用图、连续/多级引用和群原消息撤回事件，核对 profile、事件名、索引与原 `d.id`、scope、作者、过期及删除效果。合成样例只验证解析和隔离逻辑，不替代真实事件合同。
