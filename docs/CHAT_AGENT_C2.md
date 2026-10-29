# C2：同 profile 联网与资料工具接入

本批以 C1 `10d2194` 为基础，保留同一 Terra profile、旧直接命令和逐次许可。联网工具只在管理员提供匹配当前配置版本与 fingerprint 的能力证据后注册；缺省保持不可用，不能把模型别名或 OpenAI 文档当成本账号的实测结果。

联网采用宿主 `web_search(query)`：主 Agent 提出不超过 160 字的公开检索词，同一 ModelRuntime 用相同 profile 发出一个只含检索词的 Responses 请求，使用原生 `web_search`、`search_context_size=low`、`max_tool_calls<=2`。该请求不含群聊窗口、用户／群标识或记忆，宿主还拒绝明确私有标识。每次请求仍独立取得当前许可与用量结算；内层联网请求计入六次整轮模型预算，最后收束仍有预留。不会引入第二模型、shell、任意 URL 抓取或将私有记录原样交给搜索。

提供方解码接受完成的 web_search_call、可见正文和 typed url_citation；来源采用实际注释中的公开 HTTP(S) URL，并在最后答案追加可点击出处。私有 IP／localhost、非 HTTP 链接、伪造注释结构会明确失败，不假装搜索成功。单个联网失败作为局部工具结果，已有业务资料仍可用；没有出处的生成文本不当作搜索证据。

在后端 state-dir 的 `chat-capabilities-v1.json` 登记：schema=`chat-capabilities/1`、config_version、fingerprint，以及 web_search 的 max_calls、unit_cost_micro、currency、evidence_ref。证据必须对应现有 profile 的真实能力测试，价格币种与当前加权账一致；配置变更使旧证据失效。工具费用单价由已核验配置提供，本批不猜测 Terra 别名的计费方式，也不自动生成批准记录。

UsageLedger 使用增量 attempt_actions 表记录额外工具费预算、实际次数与 known/unknown 状态，保留原有 meta/schema 和 attempts。预留费用进入既有每日额度；搜索动作按实际次数结算，未知次数保留 billing_incomplete，不能记成已知零费用。迟到用量只补缺口，已有事实冲突失败；超出请求预算的实际费用仍记实，并停止相关结果。旧版可读取既有 attempts 总费用，回退须按原手册先结算／取消在途请求。

离线验收包含：原生搜索响应／出处、内层请求只含公开词、三个独立许可、同 profile、额外费用预留及迟到结算、未知费用、能力证据失效及 C1／旧账回归。实际 API 兼容性和付费对照尚未执行；需要在全部批次统一审核时核对有效授权后运行，未批准前不宣称真实联网可用。

协议依据（2026-10-29核对）：[Responses web search](https://developers.openai.com/api/docs/guides/tools-web-search)、[max_tool_calls](https://developers.openai.com/api/reference/python/resources/responses/methods/create)。这些只证明 API 合同，不证明当前 Terra profile 已通过能力探测。
