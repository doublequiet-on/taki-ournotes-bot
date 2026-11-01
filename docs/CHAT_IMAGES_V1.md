# 聊天图片 V1

V1 的接入数据来自已核对身份的 QQ 原始事件附件。纯图和图文都能进入被点名的群聊或 C2C 消息；正文可以为空。普通 URL 不成为图片输入，未点名图片还受 G1 群服务及平台能力批准。附件保留原消息 ID、发言者 ID，显示名不能替代发言者身份。协议 2.2、Responses input_image、图像 token 上界及实际 usage 已接线，默认能力证据缺失时不声称可看图，真实模型理解另验。

## 传输与读取合同

`Inbound.attachments` 是可选数组，最多三张。字段采用 QQ SDK 的 `id`、`url`、`content_type`、可选 `size`、`width`、`height`，不包含文件路径或模型指令。实际 QQ 样例可能没有附件 `id`，此时入口按原消息 ID 和位置生成稳定的本地附件 ID；它不代表平台新增身份凭据。

平台无关的 `Attachment` 为不可变元数据，`parse_attachments()` 检查字段、数字、重复 ID 和数量。`ImageLoader.load_many(attachments, timestamp_ms=..., deadline=...)` 返回内存中的 `LoadedImage(attachment_id, mime, width, height, data)`。`timestamp_ms` 是原消息的事件时间，`deadline` 使用 `time.monotonic()`，调用方仍须保留原消息与发言者对应。异常只提供有限的 `ImageError.code`，不包含 URL、签名或原始字节。

首批只接受 `https://multimedia.nt.qq.com.cn/download?...`。这是[腾讯官方机器人仓库中的实际附件样例](https://github.com/tencent-connect/dsh-qqbot/issues/8)使用的来源；安装的 `@satorijs/adapter-qq` 4.14.0 类型也将附件 MIME、URL、尺寸和大小列为独立字段。[官方 Python SDK 的读取实现](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/attachment.py)将 CDN 下载视为公开读取，因此本实现不传 QQ token、Cookie 或代理配置。来源需要鉴权或过期时返回 `image_not_available`，实际账号下的读取尚需独立验证。新增 CDN 须先核实来源并调整白名单，不接受任意 QQ 子域或模型给出的 URL。

下载使用自己创建的 aiohttp 会话，无重定向、无环境代理、无自动解压、无自动重试。连接器使用经过检查的同一份 DNS 结果，所有 IP 均须可公开路由；混合公网/私网结果、回环、链路本地、IPv4 映射私网和 FakeDNS 地址都会拒绝。TLS 仍验证原始主机名。这样不会在预检后重新解析另一个连接地址。

单图最多 6 MiB，单轮最多 12 MiB、三张，单边最多 8192 像素且总像素最多 1600 万。整个图片读取阶段最多十秒，同时服从本轮剩余时间。响应类型、声明 MIME、Pillow 实际解码格式须一致；声明的大小和尺寸存在时也须匹配。PNG、JPEG、静态 WebP、静态 GIF 可读，动画明确拒绝。校验完整文件和像素解码，不能只凭扩展名或 HTTP 头判断。

## 引用与保存

当前附件与有效显式引用直接进入模型 input_image。主动参与的最后一条消息没有附件时，宿主还会选择本轮最近四条群成员消息中、距触发消息不超过 60 秒且晚于最近已送达 Taki 回复的最近一条图片消息，自动带入其仍有效的附件；最多一条图片消息，仍受独立主动模型的识图批准、单轮三张／12 MiB、图像 token 上界及服务预算约束。下载失败只在该来源的 `image_status` 中注明有限错误码，继续根据文字判断是否参与；删除、候选失效等取消条件仍终止本轮。当前消息自带图像的失败语义保持。

比较或追问其他近期图片时，同一 Agent 可调用只读 `image_read(message_id)`，仅允许本轮上下文中原消息 ID、同 scope 且缓存仍有效的附件；宿主追加原作者／原时间的多模态材料，工具正文、审计与记忆均不含图片字节。已回复、较早或超出自动选图窗口的图片不自动重传，必要时用此工具读取。附件标记本身不证明模型已看到图像。

有效引用也可指向 G1 短时观察中尚未归档的图片。此时原图作为 image_reference 来源材料进入模型，记忆只保存必要的来源元数据和依赖，不捏造原消息文本。源删除／到期会清相关派生内容；下载期间删除或迟到结果也必须再过墓碑检查，不能复活已删除图片。

`ImageCache.remember(scope, message_id, speaker_id, timestamp_ms, attachments, images=())` 记录最多五分钟的原图引用。`get(scope, message_id)` 返回 `ImageReference`，保持原发言者，不把追问者记作原图作者。scope 必须包含实例、应用、群或 C2C 身份。跨 scope、过期、被淘汰或不存在的引用均返回 `image_not_available`。相同 scope 和消息 ID 不允许替换原作者、时间或附件。

缓存仅在当前进程内存中，最多 256 条消息、32 MiB 已读取图片。URL 和原始字节的 `repr` 隐藏；调用方不可把它们写进聊天历史、月度记忆、日志或模型审计。长期记录只保留必要的原消息关联及可读的“附件存在/不可用”事实。模型图像输入与图像 token 计费由宿主的模型能力和额度合同负责，本模块不估计 Terra 图片价格。

离线验收覆盖原始事件纯图/图文、身份保留、白名单、DNS、重定向、声明/实际 MIME、尺寸与字节上限、动画、短时缓存隔离与过期。真实 QQ 附件下载、引用事件形状、模型图像理解与客户端送达仍需对应环境单独验收。

主动聊天的 `context` 诊断记录初始上下文实际携带图片时的 `image_detail`（`low`／`high`）；未携带图片为 `none`，即使配置已批准识图也不算已输入图片。该字段不描述后续 `image_read` 工具读取，也不证明模型读对了图片。它只保存固定枚举，不保存图片内容、地址或文件名。旧诊断缺少此字段时精度未知；不得解释为 `low`。精度仍来自当前有效能力配置，不因聊天内容自动提高。
