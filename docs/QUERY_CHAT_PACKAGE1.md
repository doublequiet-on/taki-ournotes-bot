# 自然语言查询聊天 v3：包 1

本包在 `e7306d57c6e4f06f32b6fccf16fe7d202062eec4` 上实现共同协议、执行许可、独立 AI 调度及短期上下文，使用既有只读结果验证。Meta 查询属于包 2A，知识库属于包 2B，原生工具、轻量聊天和加权账务属于包 3。本包不开放未经实测的 QQ 引用或按钮入口。

## 成对协议与当前许可

- HTTP 路径保留 `/v1`，载荷协议升级为 **2.0**，能力为 `qq-query-chat-v3`。组合清单须同时声明 `group_policies_v1`、`query_chat_contract_v1`；后端、网关和源码摘要成对更新。
- 入站增加必填 `trigger`（command/ask/quoted_reply/button）、`reference`、`interaction`。后两者通常为 null；引用只接受后端明确配置的 adapter profile，按钮尚不接受。生产默认 profile 列表为空，测试中的 `fixture` 不代表生产能力。
- 准备状态返回 `pending_model_permission`；网关通过 `POST /v1/requests/{request_id}/model-permissions/{attempt_id}` 回答。沿用 Bearer、实例头和网关代次认证，绑定 scope、用户、请求、attempt、nonce、双方代次；不修改入场策略摘要。
- 每次真实尝试重新核验：新的 Koishi Session 重读当前平台权限，再检查接收范围、查询开关和 natural_query。当前许可与入场上限取交集，排队后放宽不扩大原请求能力。
- 挑战最多 3 秒，许可最多 1 秒，且不超过执行截止时间；只消费一次。重复决策只确认已有事实，不能解除后续尝试的等待。版本回退、代次变化、断连、撤权或过期均关闭调用。
- 旧 1.1 仅兼容 ACK 事实记录，强制 `allow_context=false`；不能产生新发送许可或恢复上下文。发送账 schema 保持 1。

## 调度和交付

`AIQueryParser.prepare_local` 在原 QueryGate 中完成本地阶段，只有 `DeferredModel` 进入 `ModelScheduler`。全局 5 个运行、20 个等待；每可信入站用户 1 个运行、3 个等待；等待 60 秒、执行总预算 30 秒。用户键暂为场景／会话／用户组合，不推断群聊与私聊身份等价。

AI 等待和 HTTP 不占绘图槽，返回后再进入 QueryGate 渲染。超时／取消的真实线程继续占原模型槽直至结束；迟到结果不能继续执行工具或提交回复。后端中的旧 JSON 路径最多两次模型调用，关闭修复重试。现有按次额度仍保留原语义，未实现加权 reservation、退款或新 usage 结算；许可失败在旧 reserve 之前，但 reserve 后遇许可过期仍可能消耗旧额度，包 3 须用新账务解决该窗口。

独立请求移除全局入场票号；同话题锁保留至前驱最终 ACK。同消息多命令、片段、回退仍按 position、intent、msg_seq、ACK 顺序交付，不增加 QQ 额度。旧 Python bot 保留原 ReplySequencer，本包仅在该入口统一期限与空闲清理。

## 上下文、固定页面和可见范围

scope 为实例／应用／平台／场景／会话，发言者单独记录。独立请求各有话题，成功 ACK 的真实 message_id 才创建锚点。同群其他成员可引用已确认锚点；跨群、跨私聊、未确认或失效引用不可用。无引用时仅唯一可用话题允许本地续查，多个话题不猜测。

`Conversations` 保存不可变 PreparedReply 页面和全局序号，既有查询复用 QueryContext。翻页只读已捕获页面，不查询业务或模型；详情仍遵循原来源版本检查。图片片段只开放该片段已展示的序号；无可靠行映射的文字分片不开放实体选择。长文字回退的短提示绑定纯文本续传游标，不开放隐藏行或未发送图片分页。Meta 行值／来源 release／版式 DTO 已由 [包 2A](QUERY_CHAT_PACKAGE2A.md) 接入此接口；Meta 序号详情消费原捕获数据。

原内容最多保留 600 秒；读取、ACK、翻页和续传不延期。新后端禁用上下文相关问句计划缓存，AgentState 按请求 scope 隔离；旧入口成功计划也设绝对期限。访问检查与每秒清理并用，到期／淘汰的依赖任务不能恢复上下文。

容量：上下文／快照全局各最多 1,024，单会话最多 20 个快照、20 个消息锚点；快照文字合计最多 8 MiB，图片及在途准备服从原 128 MB 总预算。包 0 的单会话 16,000 字符细化为可供对话读取的 ReadOnlyContext 文本；完整确定性列表展示文本使用快照总预算，以保留现有完整查询。包 2A/3 不得把完整展示页直接作为模型上下文绕过该预算。普通非 @ 群消息在保留原始正文之前即被拒绝。

## 验证和下一包

相关回归在 `test_continuation.py`、`test_stage5_limits.py`、`test_reply_pipeline.py`、`test_console_admin.py`、`test_console_policies.py`、`test_query_refactor.py`。网关 `scripts/probe.cjs` 使用锁定的真实 Koishi／adapter 包、合成事件和截获 HTTP，覆盖当前权限重读、暂停、local、绑定和非 @ 正文丢弃；不连接 QQ 或付费模型。

对应 v3：T10a/T13a 为期限、空闲清理、作用域；T16a/T16b 为独立查询、队列和依赖／片段顺序；T22a 为每次许可、过期、撤权和代次。生产引用字段、无 @ 回复事件、跨场景身份、真实模型和客户端送达仍待另行验收，不能据此宣称“回复即可继续”实机可用。

正式上线仍依赖包 5。支持 2.0 的受信控制器须经现有审核安装流程，应用更新不自动覆盖控制器。本包不合并、不部署、不重启服务。
