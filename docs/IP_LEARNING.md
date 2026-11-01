# IP 知识整理与回答练习

`scripts/ip_learning.py` 在 QQ 进程之外运行：读取选定的公开正文快照，提取带原文定位的候选知识，生成练习题，再对回答做短语覆盖检查并输出复习清单。人物、声优关联、剧情、梗分别标记；全部产物为待审核稿。

## 三个步骤

1. `build`：模型提取事实和练习题。每条事实附一段能够在快照中原样找到的摘录；每个可接受答案须出现在整理后的事实里。输出 `pack.json`、`cases.json` 和 `drafts/*.md`。可用保存的提取结果离线重放。
2. `probe`：让指定模型在给定候选证据下回答练习题，保存模型名与答案。这检验资料可用性和该模型回答情况，尚不经过 Taki 的检索、人格、工具与 QQ 链路。
3. `evaluate`：读取题包与答案，输出命中、缺答、待复核项目及 `review_queue`。也接受从实际 Taki 验收得到的答案。梗接得是否自然、事实是否真实仍需人工复核。

评分使用去空白、不区分大小写的答案短语匹配；同题 `answers` 为同义替代项，命中任意一项即记为 matched。否定句、反讽或含有正确短语的错误答案仍可能命中，所以所有结果保留 `manual_review=true`，不据此自动批准知识或宣称通过正式机器人验收。修改题包会使旧答案的哈希绑定失效。

当前 ON 知识库的 game/scope 合同没有改变；这里生成的是独立 IP 候选，不冒充 ON 资料，不写 approvals.json，也不自动发布、改提示词或调整模型。后续需单独接入 IP 正式库和控制台审核。重复执行同一来源可生成新目录，按 source_id/source_sha256、稳定 entry id 比较变化；复习队列供修订资料后再次出题，程序不以自己的回答为新事实。

## 公开来源快照

先通过来源允许的方式保存 UTF-8 正文片段。脚本本身不联网采集网页；萌娘百科的 JavaScript 页面不应被登录页或反爬提示替代后当成知识。来源许可与署名由资料整理者核对，文件相对 manifest 所在目录定位。

```json
{
  "schema": "taki-ip-sources/1",
  "sources": [{
    "id": "mygo-official",
    "title": "官方人物资料",
    "url": "https://bang-dream.com/artist/mygo/",
    "kind": "official",
    "accessed_at": "2026-11-02T12:00:00+08:00",
    "attribution": "记录实际署名、许可说明及具体页面/段落定位",
    "file": "mygo.txt"
  }]
}
```

`kind` 可为 official/community。梗资料优先保存具体原出处，官方设定与社区用法分开。每次最多 50 个来源、每份正文最多 40,000 字符。这里只处理文字与人物公开资料，不提供真人照片身份识别。

## 模型和预算

沿用项目 `ModelProfile`、`NativeClient` 和 `UsageLedger`；无新 SDK 或模型服务。使用独立、无 api_key 字段的 profile JSON（字段同 `domain/model_calls.py::ModelProfile`，price 结构同 Price），密钥由环境变量 `TAKI_LEARNING_API_KEY` 提供。OpenAI 代理读取 HTTPS_PROXY。价格需由操作人核对；当前工具要求 USD 报价，不自动猜测价格或汇率。

`--ledger` 指定本工具独占的持久账本。`--budget-usd` 限制该账本累计消费和未决预留，跨进程和跨天保留；每次 HTTP 前预留完整输入/输出上限，失败或未知用量不按零结算。不使用生产账本，不自动重试付费请求。输出花费为当前价格配置下的账本值，不代替服务商账单。

在仓库根设置 `PYTHONPATH=src`（或安装本项目），使用已有 Python 环境：

```text
python -B scripts/ip_learning.py build --sources runtime/ip/sources.json --profile runtime/ip/profile.json --ledger runtime/ip/learning.sqlite3 --budget-usd 1 --output runtime/ip/round-01
python -B scripts/ip_learning.py probe --pack runtime/ip/round-01/pack.json --profile runtime/ip/profile.json --ledger runtime/ip/learning.sqlite3 --budget-usd 1 --output runtime/ip/answers-01.json
python -B scripts/ip_learning.py evaluate --pack runtime/ip/round-01/pack.json --answers runtime/ip/answers-01.json --output runtime/ip/report-01.json
```

各输出使用新路径；不会覆盖旧报告或题包。失败已发生的模型费用保存在账本，纠正资料/配置后使用新输出路径继续。source 文本与 pack 可发给指定模型服务，使用者应只放本次允许处理的公开资料。候选草稿、答案和来源快照是操作者的显式工作产物，不是新增群聊日志。

## 离线使用与真实 Taki 答案

无付费调用的重放入口：

```text
python -B scripts/ip_learning.py build --sources sources.json --extractions extractions.json --output round-01
```

extractions.json 按来源 ID 映射到模型输出（EXTRACT 提示词定义了 entries/facts/questions 的具体格式）。离线重放也执行原文匹配和答案来源检查，不能借此产生批准。

来自实际 Taki 的答案可整理为以下文件再运行 evaluate；缺少的题目记为 missing，未知/重复 ID 拒绝评测，不把遗漏题目计为通过：

```json
{
  "pack_sha256": "复制 pack.json 的 content_sha256",
  "mode": "taki_actual_answers",
  "answers": [{"id": "复制 cases.json 的题目 ID", "answer": "实际收到的可见回答"}]
}
```

正式 IP 知识库、网页候选审核与实际 Taki 自动答题适配是后续集成项，本工具不创建定时任务。付费 build/probe 需明确配置与预算；离线重放/evaluate 不需要模型密钥。
