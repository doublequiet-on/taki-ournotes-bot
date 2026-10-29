# 对话 Agent：C0 基线与约定替换

本批基线为 `c6028f9547c337fa09a5275eaef738dc84f847a6`。2026-10-29 成功 fetch 后的 `origin/main` 与该版本一致；开始时工作区干净、HEAD detached，随后在同一独立工作区建立 `codex/chat-agent-c1`。没有修改生产目录、排行榜业务或 X 翻译机器人。

依据为用户确认的 2026-10-29 开工包 `00_START_HERE.md`、完整方案和验收矩阵。旧实现继续作为默认 legacy；本批增加显式可选 Agent。下表只替换聊天产品约定，既有权限、来源、额度与发送规则继续执行。

| 入口／旧行为 | Agent 行为 | 保留边界 |
|---|---|---|
| 普通点名文本经 recognizes 丢弃 | 后端 classify 接收普通文本；未知 `/其他插件` 命令仍不接管 | QQ 原始身份、机器人过滤、点名、Koishi 权限与群管理继续先检查 |
| `/问` 本地优先、500 字／逐行拆分 | 完整多行进入单轮对话，受协议 16 KiB 输入边界约束 | `/查曲` 等直接命令及明确翻页／选择操作保留确定性入口 |
| 先 chat_response 再生成；固定感谢、澄清模板 | 一次请求可直接生成正文，真实工具按需调用 | 不对同一失败请求隐式重跑 legacy |
| 话题卡、同话题六条历史 | 同一群的触发片段和成功 ACK 回复组成有界窗口 | 只保存本群材料；昵称不改变角色、身份或权限 |
| 无工具文本被 model_no_evidence 拦截 | 普通交流／稳定常识可直接回答 | 精确游戏资料、当前状态及规则仍通过既有只读来源核实 |
| 300／800 字整段拒绝 | 复用平台确定性分段与逐段 ACK | 提供方文本仍有 16 KiB／4000 字传输上限，不引入后置润色 |
| 单响应最多一个工具；运行层最多四次许可 | 响应级完整输出、最多八个本地工具操作、六次模型请求含末次收束 | 每次 HTTP 前重新许可、预留、结算；原 profile 的额度／频率上限可以更早终止 |
| 固定 30 秒模型执行 | Agent 最多 60 秒，取内容与消息窗口剩余期限最小值 | legacy 仍 30 秒；实际 worker 未结束不释放槽位；超时不发布晚到正文 |

旧行为定位：`application/model_runtime.py::prepare/run/run_site`、`persona.py`、`model_execution.py::ModelPermissions.acquire`、`sources/model_provider.py::decode_attempt` 和 `koishi/src/receiver.ts::authorizeModel`。这些旧限制由源码与保留的 `NativeRuntimeTests` 核对；没有调用真实模型复现文风。

配置复用 `ModelConfiguration` 捕获的同一 `ModelProfile`：provider、model、wire_profile、config_version、capability_evidence、价格、输出额度与 reasoning_effort 均不重写。本批没有读取生产密钥或猜测 Terra 的实际 API model ID。离线使用合成 DeepSeek wire 和 OpenAI Responses wire，后者覆盖 reasoning／多工具续接；不能据此宣称 Terra 实测兼容。

默认人格为 `taki-v0/legacy-derived`，是明确标注的运行初稿。K1 已补来源目录、场景引擎和人格研究草稿，真实人格审核未完成；只有独立人工批准的版本才替换初稿。本文保留 C0 约定，组合现状见 [F1](CHAT_AGENT_F1.md)，不批准新的百科正文。
