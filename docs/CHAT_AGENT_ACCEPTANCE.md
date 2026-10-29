# 对话 Agent：C0＋C1 验收记录

基线：`c6028f9547c337fa09a5275eaef738dc84f847a6`。候选为 `codex/chat-agent-c1` 当前任务提交，最终提交 SHA 由 Git／PR 记录绑定。日期：2026-10-29。环境为 Windows、Python 3.12、锁定的 Node 24.19.0／Koishi 依赖。新路径默认关闭；未调用真实 Terra、未发送 QQ、未合并、未部署。

开工包 SHA-256：`cceacb305d0a8d4ee12a9bab03183cc1aa05c05f53d04e7a43e446ad0bbc716d`。已读取 START_HERE、完整方案和 44 项矩阵，并按 C1 场景适配原包回放输入。素材为合成输入，不进入生产知识库。本地原包与执行器留在忽略的 runtime 下。

## 可复现检查

```powershell
$env:PYTHONPATH='src;tests'
python -B -m unittest test_console_policies.ChatAgentTests -q
python -B -m unittest test_console_admin.HTTPV2ContractTests -q
python -B scripts/check_architecture.py
python -B -m unittest discover -s tests -q
# koishi 目录，按 package-lock.json 准备依赖后
node node_modules/typescript/bin/tsc -p tsconfig.json
node scripts/probe.cjs probe-output.json
node scripts/console-rpc-probe.cjs
node scripts/console-state-probe.cjs
```

新测试加入现有 `tests/test_console_policies.py`；未新增需提交的测试文件。Windows 受限沙箱阻止 asyncio 本机 socketpair 和临时文件原子 rename，定位后在获准的沙箱外运行离线验证，清空模型／QQ 凭据，允许既有本机 HTTP 夹具。没有通过修改业务代码绕过环境故障。

已完成：既有 NativeTransport／WeightedUsage／NativeRuntime 共 96 项通过；最终 C1 及受影响协议／边界共 19 项通过；原包 D01/D02/D03/D04/D15 的 16 轮输入经真实后端、许可和 ACK 回放通过（固定输出只证明编排）；Koishi 编译、116 项网关探针及 Console RPC／状态探针通过。架构门禁与文档链接检查通过。

wheel／sdist 均构建并通过发布内容检查，分别在 checkout 外临时 venv、无 editable 路径、禁外部索引安装；两次 `python -I` smoke 均通过（59 模块、字体／别名／文档／临时历史库、backend ownership）。安装后源码摘要均为 `e5433c199afa5bc609c3d6e63c27423c9cffe90962c92845f84c06a9e2fe572b`，与冻结源码一致。记录：`runtime/chat-agent-package-check/install-results.json`、`runtime/chat-agent-package-install.log`。Windows 原生包不代表 Linux 正式离线包；Linux／Koishi 组合的正式构建留给绑定提交的 CI，不能以本机 Windows ZIP 替代。

账本中未使用的 `final_not_needed` 是 released 预留行；按 `started_at` 非空统计真实模型调用，不能把 summary.attempts 的预留记录数当作付费调用数。成功和取消路径均验证无遗留 reserved。

中途一次全量回归 1393 项，8 失败、2 错误、10 跳过：其间源码持续修改，8 个新聊天用例遇到已加载旧 Request 与新昵称字段不一致；另两项是 legacy 注入 runtime／work 的测试兼容点。已通过默认模式隔离新选路、补齐测试 Work.chat 字段，并对最终源码重新运行目标与完整回归，不将中途结果作为交付通过依据。

本次文档提交时，最终完整回归仍在执行；最后结果按本分支提交绑定的 PR 回执／Checks 和本地 `runtime/chat-agent-full-final.log` 核对。一次取消测试曾因 80 毫秒测试期限在全回归负载下提前到期而等待桩入口，现已改为显式事件同步与有限入口等待，业务执行期限未放宽。

## 原验收矩阵映射

| 条目 | C1 工程状态与证据 | 尚需验证 |
|---|---|---|
| A01 | 基线、干净工作区、远端及旧→新表已核对 | 无生产版本推断 |
| A02–A03 | 普通文本／多行／`/问` 一次调用、直接命令零调用；未知插件命令保留。认证引用协议可回放 | QQ 实际引用 profile 未核验，默认不接纳未经批准 profile |
| A04 | 人格与连续上下文已接线 | 偏好理由质量待真实同 Terra 对照 |
| A05–A06 | 已 ACK 表态跨话题保留；多人 ID、同名与伪 system 不混角色；人类引用归属可回放 | 真实对话语义与 QQ 昵称可见性 |
| A07–A09 | 实际站内搜索→活动详情→关联歌曲，支持同响应双调用、reasoning 一次回放、重复 call_id 一次执行 | Terra 实际 wire 兼容性 |
| A10 | 六次独立许可、八工具上限、预算错误结果、末次无工具收束、时间和加权额度预留 | 真实费用／延迟指标 |
| A11–A12 | 成功、权限拒绝、未知 usage、取消及来源局部失败不触发 legacy 双跑；用量沿旧账本独立结算 | 真实 provider 费用核对 |
| A13–A14 | 多段第一段成功／第二段 uncertain，历史仅含成功段；取消保留实际 worker 槽位，关闭后不晚发，同群等待 ACK | QQ 客户端分段显示 |
| A15 | 同一 v0 人格，未使用话题卡、前置分类或后置润色 | 人格研究与真实语气对照未执行 |
| A16 | 仅触发片段、可见文本、紧凑工具和交付元数据；无隐藏 reasoning；跨群隔离、30 日清扫、审计故障隔离 | 管理员选择删除／备份传播属 C3 |
| A17 | legacy 测试保留；provider 从多调用直接拒绝改为校验各调用，非法工具仍失败并保留 usage | 完整回归见收尾结果 |
| A18–A22 | K1 接口约定已记录，未注册假工具、未发布合成剧情 | K1 来源、导入检索和人格人工审核未执行 |
| A23–A39 | 不属于本批已实现能力 | C2 联网、C3 月度检索、V1 看图、G1 主动群聊未执行 |
| A40–A41 | 协议 2.1 严格配对、新模块摘要登记、独立聊天库／旧版忽略、不回滚额度 | wheel/sdist／安装结果收尾更新；Linux正式组合与生产回退演练未执行 |
| A42–A43 | 直接查询与现有功能离线回归 | 同 Terra 实聊质量／p50-p95／4GB 资源测量未执行 |
| A44 | 离线、真实模型、平台与生产分开报告 | 未上线 |

工具循环、预算和交付变体对应原 D06/D07/D17/D18，具体测试名在 ChatAgentTests；D05 的合成卡等级不是生产资料，未宣称已用真实来源验出这些数值。D01–D04/D15 的输入结构、身份与历史经本地 `runtime/replay_chat_agent_c1.py` 适配原 JSONL；其结果不是固定话术质量评分。

自查补修覆盖：QueryContext／DeliveryCursor 引用读取冻结展示页，聊天库写失败不阻断 ACK／finish，工具提前截止保留最终生成时间，剩余日额度不足继续检索时消费预留收束额度。

下一批：先按同一 Terra profile 核对可用 wire／估算成本并在有效付费授权内做连续对照；K1a 来源清单和场景导入／检索，K1b 人格人工审核；随后 C2/C3，再做 V1/G1。第一批不等待这些资料完整收齐，但不能把离线桩当作真实聊天效果证据。启停与回退见 [运行手册](CHAT_AGENT_RUNBOOK.md)。
