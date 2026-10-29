# 聊天图片 V1

V1 的接入数据来自已核对身份的 QQ 原始事件附件。纯图和图文都能进入被点名的群聊或 C2C 消息；正文可以为空。未点名消息、正文中的普通 URL 不因此成为图片输入。附件保留原消息 ID、发言者 ID，显示名不能替代发言者身份。

## 传输与读取合同

`Inbound.attachments` 是可选数组，最多三张。字段采用 QQ SDK 的 `id`、`url`、`content_type`、可选 `size`、`width`、`height`，不包含文件路径或模型指令。实际 QQ 样例可能没有附件 `id`，此时入口按原消息 ID 和位置生成稳定的本地附件 ID；它不代表平台新增身份凭据。

平台无关的 `Attachment` 为不可变元数据，`parse_attachments()` 检查字段、数字、重复 ID 和数量。`ImageLoader.load_many(attachments, timestamp_ms=..., deadline=...)` 返回内存中的 `LoadedImage(attachment_id, mime, width, height, data)`。`timestamp_ms` 是原消息的事件时间，`deadline` 使用 `time.monotonic()`，调用方仍须保留原消息与发言者对应。异常只提供有限的 `ImageError.code`，不包含 URL、签名或原始字节。

首批只接受 `https://multimedia.nt.qq.com.cn/download?...`。这是[腾讯官方机器人仓库中的实际附件样例](https://github.com/tencent-connect/dsh-qqbot/issues/8)使用的来源；安装的 `@satorijs/adapter-qq` 4.14.0 类型也将附件 MIME、URL、尺寸和大小列为独立字段。[官方 Python SDK 的读取实现](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/attachment.py)将 CDN 下载视为公开读取，因此本实现不传 QQ token、Cookie 或代理配置。来源需要鉴权或过期时返回 `image_not_available`，实际账号下的读取尚需独立验证。新增 CDN 须先核实来源并调整白名单，不接受任意 QQ 子域或模型给出的 URL。

下载使用自己创建的 aiohttp 会话，无重定向、无环境代理、无自动解压、无自动重试。连接器使用经过检查的同一份 DNS 结果，所有 IP 均须可公开路由；混合公网/私网结果、回环、链路本地、IPv4 映射私网和 FakeDNS 地址都会拒绝。TLS 仍验证原始主机名。这样不会在预检后重新解析另一个连接地址。

单图最多 6 MiB，单轮最多 12 MiB、三张，单边最多 8192 像素且总像素最多 1600 万。整个图片读取阶段最多十秒，同时服从本轮剩余时间。响应类型、声明 MIME、Pillow 实际解码格式须一致；声明的大小和尺寸存在时也须匹配。PNG、JPEG、静态 WebP、静态 GIF 可读，动画明确拒绝。校验完整文件和像素解码，不能只凭扩展名或 HTTP 头判断。

## 引用与保存

`ImageCache.remember(scope, message_id, speaker_id, timestamp_ms, attachments, images=())` 记录最多五分钟的原图引用。`get(scope, message_id)` 返回 `ImageReference`，保持原发言者，不把追问者记作原图作者。scope 必须包含实例、应用、群或 C2C 身份。跨 scope、过期、被淘汰或不存在的引用均返回 `image_not_available`。相同 scope 和消息 ID 不允许替换原作者、时间或附件。

缓存仅在当前进程内存中，最多 256 条消息、32 MiB 已读取图片。URL 和原始字节的 `repr` 隐藏；调用方不可把它们写进聊天历史、月度记忆、日志或模型审计。长期记录只保留必要的原消息关联及可读的“附件存在/不可用”事实。模型图像输入与图像 token 计费由宿主的模型能力和额度合同负责，本模块不估计 Terra 图片价格。

离线验收覆盖原始事件纯图/图文、身份保留、白名单、DNS、重定向、声明/实际 MIME、尺寸与字节上限、动画、短时缓存隔离与过期。真实 QQ 附件下载、引用事件形状、模型图像理解与客户端送达仍需对应环境单独验收。
