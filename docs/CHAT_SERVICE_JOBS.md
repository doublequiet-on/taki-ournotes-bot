# 群级摘要与主动参与服务

`application/chat_jobs.py` 只提供管理员配置、群级预算和摘要输入/输出校验。它不调用模型、不创建后台线程、不直接写 ChatStore，也不写入额外的对话原文。宿主使用原 Terra profile 和原模型调度、许可、额度、账务及交付回执；摘要不调用第二个模型，工具列表为空，失败保留原文。

## 默认关闭与批准文件

文件为后端 state-dir 下的 `chat-services-v1.json`，由管理员在受保护目录维护。缺失、未批准、配置错误或 profile 绑定变化时服务关闭；普通聊天独立运行。文件最多 128 KiB、128 群，拒绝软链接、重复 JSON 键和非有限数字，POSIX 文件不能由组或其他用户写入。模块只读该文件，不提供群聊指令写入入口。

```json
{
  "schema": "taki-chat-services/1",
  "instance_id": "configured-instance",
  "app_id": "configured-app",
  "profile": {
    "config_version": "current-model-config-version",
    "fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
    "currency": "USD"
  },
  "groups": {
    "verified-group-openid": {
      "approved": false,
      "service_subject_id": "administrator-approved-group-service",
      "approval_ref": "approval/change-record",
      "summary": {
        "enabled": false,
        "max_jobs_per_hour": 0,
        "max_cost_micro_per_hour": 0
      },
      "proactive": {
        "enabled": false,
        "max_calls_per_hour": 0,
        "max_replies_per_hour": 0,
        "max_cost_micro_per_hour": 0,
        "cooldown_seconds": 300
      }
    }
  }
}
```

示例保持关闭，不是启用或付费授权。启用某项须有批准记录，`approved: true`、该项 `enabled: true` 和正的次数/费用上限。`profile.config_version`、`fingerprint` 和 `currency` 必须与当前受信 `ConfigSnapshot` 相符；不修改原模型配置 schema，不把服务主体伪装成普通群友。

`ChatServices(state_dir, instance_id, app_id).policy(snapshot, group_id)` 返回不可变 `ServicePolicy` 或 `None`，`.code` 提供有限诊断。策略有 `.summary`、`.proactive` 和各自上限、批准引用、服务主体及 profile 绑定。

## 独立主动参与模型

控制台「模型与额度 → 管理配置 → 主动参与模型」可独立保存 provider、模型 ID、密钥、推理强度、输入／输出预算、频率及价格。配置文件默认位于 state-dir 的 `model-proactive-v1.json`，使用现有 `taki-model-config/2` 或 `/3` 合同；可由 `OURNOTES_AI_PROACTIVE_PROFILE_FILE` 指定路径、`OURNOTES_PROACTIVE_AI_API_KEY` 提供后端密钥。未填写独立密钥时不继承主模型或备用模型的密钥。付费能力测试也是独立入口，只有显式确认才执行，记录到同一账本，不会自动批准群服务。

旧安装完全没有独立主动配置及批准文件时保留共用主模型的行为。管理员配置独立角色后，主动判断及本轮只读工具循环、最终回复均使用该 profile；被点名聊天、`/问`、预算备用路由及摘要保持原配置。独立文件出现即选择独立角色，当前进程随后即使文件丢失也不会自动回到主模型；重启时独立能力／服务文件的存在也会维持独立路由。需要显式固定该选择的安装应设置 `OURNOTES_AI_PROACTIVE_PROFILE_FILE`。主动任务额度不足或提供方失败时停止，不自动调用主模型或备用模型。

独立角色另用 state-dir 下的两个批准文件，均绑定独立 profile 的 config_version 和 fingerprint：

- `chat-proactive-capabilities-v1.json`：与原 `chat-capabilities-v1.json` 相同的 schema。proactive 必须有目标群、真实平台接收及发送证据；初次试运行的 web_search、vision 均设 null，主模型的联网／图片批准不会继承。
- `chat-proactive-services-v1.json`：与本文 `chat-services-v1.json` 相同的 schema、群服务主体和小时预算。仅 proactive 生效，独立角色的 summary 设置不接入摘要任务。原文件继续服务主模型摘要与旧路由。

保存模型本身不创建这两个批准文件、不收集群消息、不启用主动发送。修改独立模型后需要重新核对并批准对应版本；缺失、无效、币种或指纹不符均关闭主动参与。在途请求及发送前会重新核验模型与批准，旧结果不能换绑到新配置。已发生的模型费用和成功回复事实保持不变。

独立模型的群日额度与频率只能收紧主模型现有群上限，不能放宽；频率窗口采用两者较长值。独立模型与主模型必须使用同一账本参考单位（币种和 reference_rate），不自动换汇；各模型的输入、缓存和输出单价可以不同。服务费用仍使用原群服务请求命名空间及实际 price 快照，继续受本群小时费用／请求／回复上限、成功 ACK 冷却和账本每日预算约束。非 DeepSeek 的美元费用仍计入全机器人主模型日预算；DeepSeek 沿用既有提供方预算语义，不将人民币伪装成美元。不同币种不能通过单独角色绕开账本限制。

## 预算与账务接线

`service_request_id(task, instance_id, app_id, group_id, source_key)` 为 `summary` 或 `proactive` 生成独立请求身份。摘要可使用源集合哈希作为 `source_key`，主动参与可使用触发批次身份；同一业务的模型连续调用共享该 job request_id，不能混进普通用户请求。

`ServiceBudget(usage, instance_id, app_id).check(policy, task, profile, request_id, reserve_cost_micro, reply_times=())` 同步读取注入的 UsageLedger，返回 `ServicePermit`。`.user_key` 严格使用 `usage.key('service', instance, app, group, subject, task)`，`.group_key` 使用现有 `usage.key('group', group)`；宿主必须把这些 key 和独立请求 ID 交给原有 `reserve/start/settle`。原群每日额度和频率仍适用。宿主在自己的服务接纳锁内紧邻实际用量预留执行检查，不能拿旧的检查结果跨异步等待后直接放行。

费用单位是精确 micro currency，使用 `Fraction`，不把 weighted tokens 当钱，不转换 USD/CNY。`maximum_cost_micro(profile)` 返回完整模型输入/输出上限的预留费用；调用方也可提供经过已有估算器证明的本次上界。小时窗口按账本 `created` 计算，摘要限制独立 job 数，主动参与限制模型 attempt 数；同群同任务的历史不会因更换服务主体而消失。预算助手只读账本，不迁移表、不拥有 SQLite 连接、不在线程中使用宿主连接。

已知费用按真实 `cost` 计算，未开始的有效预留按 `reserved` 换算。已开始、部分用量和未知费用至少保留完整模型上界，即使 UsageLedger 重启调和后清空了 weighted reservation，也不会变成零成本。旧版本的不完整记录使用受支持模型输入/输出的保守上界；未解决费用跨小时继续占用，直到账本补齐真实用量。币种不一致、账本不可读或记录超过读取上限时拒绝新的服务任务。

主动回复次数与冷却使用宿主从持久化成功交付回执得到的 `reply_times`（Unix 秒），不能使用“已经生成”的时间代替。宿主应提供至少 `max(3600, cooldown_seconds)` 回溯范围的已送达事实，并在实际发送前重新检查回复预算和当前许可。助手不声称模型完成等于群中送达，也不从调用数推断回复数。

## 摘要候选与校验

`summary_candidate(messages, summarized_source_sets=())` 接收普通 `ChatMessage` 字典，字段为 `message_id/speaker_id/text/timestamp_ms/role`，可含 `reply_to/display_name`。调用方只能提供已经持久记录、Taki 参与过的消息；不接受未经参与的整群观察流或模型 wire `role/content` JSON。助手只取最近 48 条，至少 24 条且含已送达的 assistant 发言，总正文最多 24 KiB。系统/工具角色、重复消息 ID、越界字段和已摘要的同一源集合均拒绝。

返回 `SummaryCandidate.source_key/source_ids/original_messages`。`summary_messages(candidate)` 返回 system + user 两条提示，要求只压缩提供的来源，保留发言者、分歧、已答与未决事项，不建立跨群画像。宿主用原 profile 作低优先级单次调用并传空工具列表。

模型输出须是唯一 JSON 对象：`source_key`、按原顺序复制的 `message_ids`、不超过 2000 字的 `summary`。`validate_summary(text, candidate)` 返回摘要字符串或 `None`，拒绝来源不符、重复键、额外字段、超长正文和图片签名 URL。结构校验不能证明摘要每句话的语义正确；原文和源关联由宿主保留，失败不删除或覆盖原文，也不追加第二次润色调用。

测试在现有 `tests/test_console_policies.py` 的独立 `ChatServiceJobsTests` 中。默认关闭、批准/profile/币种、同群预算、重启未知费用、小时次数、成功回执冷却及摘要来源均可离线验证，无真实模型或 QQ 调用。
