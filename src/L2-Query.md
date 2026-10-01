# L2 Query — 请求到可信结果

## Purpose

把明确命令或自然语言问题转为有依据的资料选择与文字答案。确定性执行和自然语言解释共用事实与结果契约，应合为一个 L2，以两个 L2-2 描述信任边界。

## Responsibilities

解析、消歧、保留条件、执行本地资料筛选和分页；自然语言路径先本地处理，再按预算使用模型提出受限动作，经过程序验证后执行；捕获结果供文字与图片复用。

## Non-Responsibilities

不从模型取得游戏事实，不绘图或发送消息，不负责上游 schema 和缓存维护。同步实现由 Data 承担，但 Query 的某些读取会触发 Data I/O，因此“不管理缓存”不等于“绝无副作用”。

## Inputs / Outputs

输入为命令/问题、仓库、别名、可选模型配置；输出为文本和 `CommandResult`、`QueryResult` 或明确终态。直接命令并不全部先转换为 `QuerySpec`；两类入口通过专项结果实现一致性。

## Core Contracts

`QuerySpec/QueryResult` 的当前位置是 [structured_query.py](ournotes_bot/structured_query.py)，`CommandResult` 在 [commands.py](ournotes_bot/commands.py)。专项选择为 `SongAnswer/CardAnswer/MetaAnswer`；实体坐标为 `EntityRef`。这些契约的所有者均是 Query，不因被 QQ/Rendering 使用而归入一个 Shared 域。

`AIQueryParser.answer_with_plan` 返回文本与结果；不是已渲染图片，也不是模型自由文本。

## Internal Subsystems

- [Deterministic Query](ournotes_bot/query/L2-2.md)：命令与可信条件到结果；包括共享实体、词法及诊断命令。
- [Natural Query](ournotes_bot/natural_query/L2-2.md)：问题到可信条件/终态，管理模型预算和查询可观测性。

二者并非两个独立产品或互不关联的 L2。Natural 中的“本地解析”按入口信任职责归属，不因算法确定性就重复纳入 Deterministic。

## Dependencies

Application 和 QQ → Query → Data。Natural → Deterministic 的契约、解析辅助和执行器；模型传输依赖 Application 的 `ai_client.py` 叶子。当前 `commands.py` 还读取 Natural 的调试计数，这是诊断读边，不应因此把模型调用反向引入直接执行链。

Rendering 消费结果契约，不应由 Query 导入 Rendering。根入口与专项结果保留既有函数内回边。

## State / Side Effects

确定性路径主要计算与读取，但详情补全和效率快照可能联网；别名解析读取本地 JSON。Natural 另有内存计划/终态缓存、持久额度和匿名指标；这三类状态独立于发布通知。

## Failure / Degradation Boundaries

未知实体、歧义、空集合、索引不完整、上游不可用分别保留语义；不删除条件来“查到结果”。模型失败不能变为事实答案。未配置或额度不可用时，本地可处理的问题仍可工作。

## Navigation to L2-2 / L3

当前范围为命令、实体与专项执行文件，以及 AI 门面/agent/校验/额度/指标文件；下层源码入口 指向两个子域，再进入 文件头 L3。`bot_info.py` 是直接命令内容叶子，不归 QQ。

## Relevant Tests

[图文同源测试](../tests/test_reply_pipeline.py) 核对两种入口复用一次选择；
[自然语言评测](../tests/test_natural_query_eval.py) 核对路由、校验、执行衔接；
[平台边界测试](../tests/test_platform_boundary.py) 约束无 QQ 依赖。具体子域测试见下层。

## 当前实现与边界

commands.py、structured_query.py 和 ai_query.py 保留在包根。query/ 与 natural_query/ 是本域两个实现目录，契约类型仍由原根模块定义。私有帮助函数、结果方法及函数内导入保持现有关系；目录整理不等于严格单向分层。

向上阅读：[仓库 L1](../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。
