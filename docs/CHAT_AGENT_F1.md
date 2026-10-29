# 对话 Agent 组合候选与验收边界

实现基线为 `c6028f9547c337fa09a5275eaef738dc84f847a6`，组合分支 `codex/chat-agent-next`，最终版本以 PR 当前完整 SHA 及验证记录绑定。本文描述可审查候选，未批准生产启用。协议为 2.2；直接业务命令继续零模型，聊天继续现有 Terra profile，不切换模型。

## 配置与入口

| 功能 | 配置与合同 |
|---|---|
| 触发聊天 | `OURNOTES_CHAT_ENGINE=agent`，默认 legacy。点名普通文本／`/问` 可直接回复、连续调用只读工具，逐次取得当前许可；六个模型请求／八次函数／60 秒为上限，最终回答预留时间与额度。 |
| 剧情与人格 | `OURNOTES_STORY_DIR` 指向独立资料目录；`python -m ournotes_bot.application.story_cli --help`。版本化导入、日中别名检索、场景邻接、来源层级、撤回和回滚；人格仅经独立人工批准后生效。见 [引擎](STORY_ENGINE.md)、[来源覆盖](STORY_SOURCE_COVERAGE.md)、[人格草稿](STORY_PERSONA_RESEARCH.md)。 |
| 原生联网 | state-dir `chat-capabilities-v1.json` 的 web_search，绑定 config_version／fingerprint 和币种。工具使用同一 Responses profile，仅发送公开查询；分别限制全部原生操作和收费 search。返回可点击引用，费用未知保留不完整状态。见 [C2](CHAT_AGENT_C2.md)。 |
| 看图 | 能力文件 schema 2／3 中 vision 的 `max_image_tokens`、`detail`、`evidence_ref`。证据须覆盖当前模型及输入上界；无证据时不下载／不声称看过。纯图、图文、有效本群引用进入 input_image；Base64 长度不作为 token。见 [V1](CHAT_IMAGES_V1.md)、[真实引用限制](CHAT_QQ_QUOTE_EVIDENCE.md)。 |
| 月度记忆 | 独立 `chat-v1.sqlite3`，30 日源时间到期，逐段成功 ACK 才成为 assistant 历史；本群检索／读取、派生摘要、删除与墓碑约束恢复。摘要与原文分层，来源删除会传播。见 [C3](CHAT_MEMORY_C3.md)。 |
| 服务任务 | `chat-services-v1.json` 以 `taki-chat-services/1` 明确群级主体、批准引用、币种和 summary／proactive 小时预算，绑定同一模型版本。摘要只在压缩需要且无排队请求时使用剩余时间／许可，独立作业 ID 与服务账；失败保留有界原文。见 [服务任务](CHAT_SERVICE_JOBS.md)。 |
| 主动参与 | 另须能力文件 schema 3 的 proactive，精确包含 groups、receive_evidence_ref、send_evidence_ref。非点名消息只进有界内存，合并后同一 Agent 决定 reply／skip／defer；最多两次暂缓重评。新消息、点名、策略或来源变化取消旧发送。候选版本绑定与当前许可、Intent、ACK 仍逐层执行。见 [G1](CHAT_PROACTIVE_G1.md)。 |

能力文件 schema 1 保留原 web_search；schema 2 再含 vision；schema 3 同时含 web_search、vision、proactive，未启用项写 null。proactive 的接收／发送证据是平台实测记录，不能只填写功能名冒充验收。真实语料、人格批准、能力批准、群主体批准、图片和私有数据库均不进入 wheel／sdist。

## 持久化、故障与回退

记忆只保存已触发片段和成功参与后的现场，skip／defer／取消不归档观察正文；生成未发内容不当作已说历史。图片 URL／字节只存五分钟缓存；原发言者与引用 ID 保留。删除还撤销本群内存与待发工作，服务费用、真实成功发送元数据独立保留，防止删除正文降低预算。

辅助审计／服务 ACK 写失败不阻断已落盘的交付账 ACK 与核心清理。主动候选在审计异常后暂停；重启从交付账重建漏写的服务成功事实，使用回复窗口上界保守恢复时间。未知原生联网费用在恢复预算时计入最大工具费，不按零处理。用量账、交付账的必需故障保持既有失败语义。

关闭单项能力或群服务批准即可停止相应新任务。整体回退使用 legacy，先停止接纳并结算／取消在途任务，再成对切换网关／后端；不在同一失败轮双跑、不重发 uncertain。保留当前用量、交付和已删除数据的状态。旧代码恢复备份前必须先应用独立删除墓碑。完整操作见 [运行手册](CHAT_AGENT_RUNBOOK.md)。

## 验证状态与范围

随包 18 个场景与 44 项矩阵用于验收，不是人格答案模板。固定模型桩验证真实后端编排、工具结果、来源、身份、预算、ACK、到期和删除；它们不证明模型语义判断或文风。详细场景映射、源码快照及实际执行结果保存在当前任务验证记录；完成项按实际日志填写，不能将全表自动写成通过。

当前已完成目标实现与分模块离线验证；组合全回归、包内容、wheel／sdist checkout 外安装、Linux 离线与 Koishi 同版本组合、统一固定版本审查在本批收口时逐项绑定结果。最初 C1 的历史结果见 [C1 验收](CHAT_AGENT_ACCEPTANCE.md)，不能代替后续组合检查。

仍须分别取得真实证据：现有 Terra 的原生联网／图像能力与实际价格、同模型连续对话质量和 p50／p95、QQ 非点名接收与主动发送、真实引用索引关联、客户端分段送达、成对回退演练及 4GB 实测。没有实施这些操作，也没有生产部署／重启。K1 已形成全系列来源目录和一个官方简介短样例的真实导入／检索／读回；全系列正文未收齐，版权／使用范围及人格候选仍待人工核实和批准，不能称为完整剧情库。
