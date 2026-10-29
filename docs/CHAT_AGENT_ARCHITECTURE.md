# 对话 Agent：C1 实际结构

`Backend.submit` 在启用 Agent 时识别 `/问` 与非业务普通文本，保留完整多行。已识别的直接业务命令、明确翻页／选择及未知斜杠插件命令保持原入口。QQ 网关仍只接收原来允许的点名／私聊事件，未新增路过消息观察或主动发送。

`Backend.prepare` 通过当前群的弱引用锁串行提交聊天，锁持有到交付结束；不同群与直接查询仍可独立处理。捕获配置、校验身份、调度器排队后，`ModelRuntime.prepare_chat` 委托 `ChatAgent.run`。新循环使用 `domain/chat.py` 的人格和预算，不读取 legacy 话题卡。每次 `ModelRuntime.attempt` 沿用 UsageLedger 的 reserve/start/settle 与 ModelPermissions；提供方错误或取消仍有独立用量状态，未知 usage 不计作已知零费用。

每轮最多六次模型请求、八个本地工具操作、60 秒，含最后收束；工具顺序执行。首次工具执行前另在现有账本预留最坏输入／输出的收束额度；继续检索的 reserve 因日额度不足时，直接消费这笔预留发送无工具收束请求，未多发一次 HTTP。未用预留作为 released 记录保留，不冒称已调用。最后一个请求不暴露工具。上一响应超出剩余工具额度的调用收到 `budget_exhausted` 结果；工具提前截止但总预算仍有效时返回 `tool_timeout`，利用剩余时间收束。实际 profile 的输入／输出、频率与每日额度继续约束每次请求，未增加群额度，也不绕过旧频率限制。

`AttemptResult.output_items` 拥有一次 Responses 输出；消息级回放一次完整 reasoning/function 序列。`ToolCall` 只表示可执行调用，新核心不按工具复制整份响应。旧单工具路径保留 `response_items` 兼容。重复 call_id 同参数不再执行、同 ID 不同参数终止；重复 provider item ID 只接受完全相同项。原始／加密 reasoning 只存在当前工具循环，不写日志或历史。

新路径复用能力查询、字段、榜单、目录、站内搜索／读取和审核知识检索，移除聊天／澄清伪工具。站内搜索可改写词，检索仍只在既有已登记来源内；区服／版本／日期和直接业务筛选校验继续执行。KnowledgeService 不可用或某个来源读取失败以局部工具结果返回，不擦掉此前有效材料。精确来源租约在生成和交付继续复核。

`ChatContext` 只记录本实例参与的触发消息和成功 ACK 回复。作用域是 instance/app/platform/kind/conversation；保留 message_id、可信 speaker_id、可选显示名、时间、reply_to 和 role。用户正文与名称中的 system／assistant 标签不改变 role。每群最多 48 条、24 KiB 文本、15 分钟，实例最多 1024 群／8 MiB，旧交流从头裁剪。没有摘要调用，没有跨群个人画像；重启不从日志恢复历史，月度检索属于 C3。

`ChatStore` 在后端 state-dir 下独占 `chat-v1.sqlite3`，schema `chat/1` 且绑定实例／应用。`runs` 保存参与上下文、可见生成正文、触发类型、人格版本、模型配置版本和最终状态；`events` 保存 attempt 引用、用量状态、耗时、工具名称／结果状态／来源元数据及逐段交付。无工具参数、完整 provider JSON、密钥、隐藏推理或网页全文。普通未参与消息不进入这条链路。启动与每次触发清理 30 日到期记录，整个片段以最早源消息时间到期，重复引用不续期；SQLite secure_delete 开启。提供方费用事实仍由独立 UsageLedger 拥有。

审计库后续写入失败以有限错误日志和进程内 audit_failures 计数诊断，不中断已经结算的模型结果、ACK、许可撤销或交付收尾；实例绑定／schema 错误仍在启动时明确失败。业务查询引用从锚点的冻结展示页读取，兼容 QueryContext 与 DeliveryCursor。

`Backend.apply_event` 在现有 ledger 接受 ACK 后记录逐段交付，只有当前有效、允许上下文且 success 的文本加入 ChatContext。uncertain/failure/aborted 与未发送段不进入“已说过”。发送、顺序、去重、平台被动回复槽位及现有 progress hint 均复用原链路。关闭 Agent 后旧候选不再获得发送许可。

## 协议与制品

网关／后端成对使用协议 **2.1**。新增可选 `sender_name`（省略时为空），只作不可信显示数据；Python 严格拒绝未知字段和旧协议，网关严格比较相同版本。昵称取 QQ 原事件可选 author.username，无昵称时保留可信 ID。六次许可上限同步到 Receiver。组合检查器只增列 2.1，保留旧 1.0／1.1／2.0 配对验证，没有更新已安装控制器或生产策略。

引用继续使用已有 `quoted_reply` 与 reference profile 白名单合同。离线从认证协议验证了引用已参与人类消息与已 ACK 回复；QQ 实际引用字段和 profile 尚未实机核验，生产默认没有获准的 reference profile，不能把离线引用描述成已接通真实 QQ 引用。新模块和人格源码登记到 backend_sources 摘要清单，随 Python 包发布；真实日志和开工包合成资料不入发布包。

## 后续 K1 接口

K1a 的 `story_search(query, filters)` 只返回场景 handle、片段、source_kind、source_revision、出处与可继续读取标识；`story_read(handle, range)` 返回同发布版本的日文交流片段、发言者、前后场景和截断状态。未知时间保持空，原文／翻译／玩家分析分层。工具由 ChatAgent 的受限 registry 注册，宿主注入范围，返回普通结构化数据；当前未注册空壳工具，也没有宣称已有剧情全集。

K1b 运行人格发布只接受经人工确认的版本，研究稿与运行稿分离；当前 v0 不自动吸收群友偏好或模型推断。C2 联网、C3 月度记忆检索／删除、V1 图像、G1 主动参与均未在 C1 启用。
