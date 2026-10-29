# C2：同 profile 联网与资料工具接入

本批以 C1 `10d2194` 为基础，保留同一 Terra profile、旧直接命令和逐次许可。联网工具只在管理员提供匹配当前配置版本与 fingerprint 的能力证据后注册；缺省保持不可用，不能把模型别名或 OpenAI 文档当成本账号的实测结果。

联网采用宿主 `web_search(query)`：主 Agent 提出不超过 160 字的公开检索词，同一 ModelRuntime 用相同 profile 发出只含检索词的 Responses 请求，使用原生 `web_search`、`search_context_size=low`、`max_tool_calls<=2`。不附群聊窗口或记忆；宿主拒绝已知身份、显示名、内部地址及从参与／记忆材料复制的长私有片段。长片段按 NFKC／casefold 比较，当前公开问题中的普通词仍可改写；不透明长标识不会因被网页建议或重复到当前问题就自动公开。命中作为局部 private_search_query 返回，仍可收束。每次请求独立取得许可与结算，内层也计入六次预算。不引入第二模型、shell 或任意 URL 抓取。此门验证已知片段的直接复制，不证明编码／语义改写外传全部可拦；真实模型抗注入另验。

参与／记忆材料中明确标记的 PIN、密码、口令、secret、token 或验证码字段另提取短值保护，不因值短于八字符而允许复制到搜索。该校验针对已知材料和明确字段，不把任意未标记短文本都识别成秘密；编码／语义改写的剩余边界仍需单独验收。

提供方解码接受完成的 web_search_call、可见正文和 typed url_citation；来源采用实际注释的公开 HTTP(S) URL，最后答案追加可点击出处。全部 search／open_page／find_in_page 都占原生操作预算，仅 search 进入搜索费次数。合法 incomplete 返回局部失败并保留用量／额外费未知状态，已有资料仍可收束。私有 IP／localhost、非 HTTP 链接或伪造注释结构明确失败；没有出处的生成文本不当作搜索证据。

在后端 state-dir 的 `chat-capabilities-v1.json` 登记：schema=`chat-capabilities/1`、config_version、fingerprint，以及 web_search 的 max_calls、unit_cost_micro、currency、evidence_ref。证据必须对应现有 profile 的真实能力测试，价格币种与当前加权账一致；配置变更使旧证据失效。工具费用单价由已核验配置提供，本批不猜测 Terra 别名的计费方式，也不自动生成批准记录。

UsageLedger 使用增量 attempt_actions 表记录额外工具费预算、实际次数与 known/unknown 状态，保留原有 meta/schema 和 attempts。预留费用进入既有每日额度；搜索动作按实际次数结算，未知次数保留 billing_incomplete，不能记成已知零费用。迟到用量只补缺口，已有事实冲突失败；超出请求预算的实际费用仍记实，并停止相关结果。旧版可读取既有 attempts 总费用，回退须按原手册先结算／取消在途请求。

离线验收包含：原生搜索响应／出处、内层请求只含公开词、三个独立许可、同 profile、额外费用预留及迟到结算、未知费用、能力证据失效及 C1／旧账回归。实际 API 兼容性和付费对照尚未执行；需要在全部批次统一审核时核对有效授权后运行，未批准前不宣称真实联网可用。

协议依据（2026-10-29核对）：[Responses web search](https://developers.openai.com/api/docs/guides/tools-web-search)、[max_tool_calls](https://developers.openai.com/api/reference/python/resources/responses/methods/create)。这些只证明 API 合同，不证明当前 Terra profile 已通过能力探测。
