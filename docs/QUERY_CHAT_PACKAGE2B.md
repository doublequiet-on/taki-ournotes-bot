# 查询聊天 v3 · 包 2B 知识库工程

本包实现 Markdown 格式校验、独立批准记录、增量向量索引、关键词／语义融合检索和证据失效检查。对应 v3 的 T25、T25a、T25b、T25c。正式百科正文、来源事实核验和用户确认稿尚未交付；仓库不附带任何已批准百科或测试批准记录。

后端持有 `backend.knowledge`，启动先校验当前文件和批准清单，后台独立观察变更与重建索引。玩家知识回答、模型工具、额度结算与消息发送接线属于包 3；本包没有为 `/问` 新增模型生成路径。空库工程入口返回“尚无已审核资料”。真实内容效果、目标服务器资源和 QQ 联调在包 5 验收。

## 文件与技术校验

默认目录为 `knowledge/on`，可用 `OURNOTES_KNOWLEDGE_DIR` 指定。目录外的测试、模板和暂存文件不会进入索引。每篇文件用 UTF-8、LF，`---` 包围 JSON frontmatter，随后为 Markdown；[默认草稿模板](knowledge/knowledge-draft.md) 沿用包 0 的 `taki-knowledge/1`。

必填字段：`schema/id/title/keywords/aliases/game/servers/applicability/sources/evidence_type/review_status/reviewed_by/reviewed_at/limitations`。稳定 ID 为小写字母、数字及短横线，同 ID 多文件报错。`game` 固定 `bang-dream-our-notes`，资源服为 `jp/intl`，不推断其他服适用性。`applicability` 包含 `mode=current|historical`、可空 `game_version/valid_from/valid_to`、必填 `scope_note`。未知版本和日期不意味着永远通用，应在 scope_note 写清经确认的范围。

`sources` 每项包含 `source_id/kind/uri/locator/accessed_at`。已批准文稿至少有一项来源，定位、日期和来源 ID 必须完整且不重复；正文使用 `[source:id]` 时检查 ID 存在。链接和定位的真实性仍由内容审核确认，加载器不会访问来源 URL。证据类型为 `official/source_data/verified_observation/reviewed_analysis`。只读入文本，不执行正文中的 HTML、代码或指令，不加载正文引用的媒体。

草稿必须 `review_status=draft`，审核显示字段为 null。独立批准清单 `approvals.json` 的空模板如下；不能只把正文标为 approved 就供答。

```json
{"schema":"taki-knowledge-approvals/1","revision":0,"approved":[],"revoked":[]}
```

批准条目含 `id/content_sha256/approval_revision/approved_by/approved_at/confirmation_ref`；撤销条目含 `id/content_sha256/revocation_revision/revoked_at/reason`。撤销哈希为 null 表示该 ID 在撤销版本时的全部内容版本。清单 revision 为非负整数；记录 revision 为正整数，不能超过清单 revision。

内容哈希严格遵循包 0：文本换行先统一 LF；解析 frontmatter 后仅删除三个审核显示字段；按 `json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(',', ':'))` 输出，接 LF，再接正文（仅去除末尾 LF 后统一补一个 LF），计算 UTF-8 SHA256。正文其他空格保留。额外元数据也参与哈希。修改来源、适用范围或正文都使原批准失效。

可供答需文件有效、正文为 approved、哈希与批准记录相同、审核人和时间相同、未被对应撤销覆盖，且符合当前查询范围。`confirmation_ref` 记录用户确认的来源；工具只检查其存在及版本一致性，无法代替人工判断确认是否真实，更不会生成批准记录。新批准必须使用高于已观察清单的 approval_revision；重新批准还须有新的 confirmation_ref。撤销记录不能从后续清单删除。

单文件上限 2 MB，目录上限 1,000 篇／32 MB，索引最多 2,000 段；拒绝目录越界、重复 ID、重复 JSON 键、非有限数字及错误字段类型。单篇格式错误会记录路径和错误码并停用该篇；清单损坏、版本倒退、撤销记录消失或目录身份不确定时关闭知识供答，普通查询继续运行。

## 当前批准集合与索引

`application/knowledge_service.py` 持有两份独立状态：

1. 当前允许供答的 `(id, content_sha256, approval_revision)` 集合及 `observed_at`。
2. 已成功发布的候选索引及其集合版本。

文件删除、改为草稿、哈希不符或撤审，先提交持久状态再发布新的允许集合。旧索引只能召回与此集合相交的片段。向量编码或 SQLite 索引提交失败保留旧索引，不延迟撤审。持久状态保存已观察的清单高水位、历史批准记录和停用版本下限；恢复旧文件、删掉撤销项或重启都不能恢复已停用版本。

状态位于后端 `--state-dir/knowledge-v1.sqlite3`，不得放进内容目录，也不能当作可随意删除的缓存。向量表只存内容编码，不保存玩家问题或聊天正文；新增、变更的片段编码，未变片段跨重启复用。索引成功后只保留当前代需要的向量。索引数据不作为批准依据，启动必须先读真实文件与清单。SQLite 损坏时关闭知识能力并报告错误，不自动新建数据库覆盖撤审历史。

每秒轮询文件名、大小和 mtime；发现变化即观察批准状态。每 30 秒完整读取字节并尝试重建，捕获保留 mtime 的外部改写。观察任务与索引任务分开，编码期间仍可撤审。检索、生成前检查和交付前检查也会完整观察。生效点是实例完成观察并发布当前集合，不承诺尚未观察到的磁盘改动已经生效。

同一内容目录和状态库只允许一个进程持有。CLI 与正在运行的同目录后端不能并行导入；未来后台操作须调用运行实例的方法。不要通过换状态库路径或删除锁文件绕开所有权。

## 检索与包 3 接口

纯类型在 `domain/knowledge.py`；加载器在 `sources/knowledge_files.py`；融合算法在 `query/knowledge_retrieval.py`。接口：

```python
result = knowledge.retrieve(question, KnowledgeScope(server="jp", version=None, date=None),
                            knowledge.approved_set_revision, limit=6, max_chars=6000)
# result.code: ok / empty / missing / unavailable / stale
lease = result.lease
tool_evidence = knowledge.prepare_evidence(lease)  # 排队结束、生成前校验
# 包 3 执行模型调用；tool_evidence 是不可信工具数据，不能作为系统指令。
if not knowledge.valid(lease):                    # 生成后、发送前、写入上下文前校验
    # 丢弃整次知识回答；不静默删引用、不再调用模型补答；已知 usage 照常结算。
    ...
```

`EvidenceLease` 必须与排队项、缓存片段、prompt、模型结果和回答上下文一起传递。`valid` 核验全部引用：当前批准版本、内容、来源、范围及实际片段都需一致；任一失效就拒绝整份回答。包 2B 的隔离验证模拟排队／生成期间撤审，真实模型及 QQ 路径留给包 3 接线和包 5 验收。

`EvidenceChunkV1` 含 `doc_id/content_sha256/approval_revision/chunk_id/chunk_sha256/text/source_refs/applicability/evidence_type/limitations`。片段按原正文切片，目前至多 360 字，低于合同的 1,200 字；最多 6 段，序列化后的正文、来源及元数据合计不超过 6,000 字。更小的模型剩余预算通过 `max_chars` 传入；连一段完整证据也放不下时返回不可用，不删除引用来凑预算。

先按批准、游戏、资源服、版本和日期过滤，再做 BM25（中文二元短语、拉丁实体词、标题／别名精确命中）与语义余弦召回，以 RRF 融合，稳定片段 ID 打散同分。显式指定版本时资料必须声明相同版本；完整日期区间可以独立确认按日期提出的问题。历史资料需要明确匹配版本或落在完整起止区间内的日期；默认请求不召回历史资料。没有完整日期范围的资料不能用于推断过去／未来的指定日期。日期为 ISO 日期，默认以 UTC 当天判断现行资料。未知适用范围不自动推断。

## 本地语义模型

采用 [BAAI bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) 的 [Xenova ONNX 量化导出](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/fcecc3c5fef6becfa2b2bdda15c1c938857be534)：512 维、CLS 池化、L2 归一化，查询侧加检索指令。只使用 CPU，每次最多 4 条，算子线程和并行线程均为 1；不新增向量数据库服务，不调用聊天 API 或付费 embedding。

固定下载版本 `fcecc3c5fef6becfa2b2bdda15c1c938857be534`，模型目录需预先提供以下文件：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| tokenizer.json | 439125 | 48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26 |
| model_quantized.onnx | 24010842 | 15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc |

模型来自上述仓库的 `tokenizer.json` 与 `onnx/model_quantized.onnx`，配置目录内使用表中的文件名。运行实例不下载；文件缺失、哈希不符或可选依赖未安装都会明确报错。设置 `OURNOTES_KNOWLEDGE_MODEL_DIR` 并在候选运行环境安装 `.[knowledge]`：ONNX Runtime 1.23.2、tokenizers 0.22.2、NumPy 2.2.6。模型和这些可选依赖不自动塞入现有生产离线包；部署时需另行准备并核验目标环境。空库无须加载模型，普通查询也不引入这些依赖。

当前余弦召回门槛为 0.50，是隔离中文样本校准的候选阈值，不是事实置信度。真实文稿交付后需复测召回、无答案问题及范围冲突，再核定正式内容效果。

2026-10-23 只读资源快照：目标主机 2 个逻辑 CPU、约 1.92 GiB 物理内存、约 933 MiB 当时可用内存。本地 Windows CPU 实测固定权重与词表共 24,449,967 字节，小语料冷加载及建库约 1.18 秒、进程峰值约 111 MiB；16 个校准问法首位命中 16 个，另 8 个复核问法命中 7 个，4 个无关问题全部拒绝。漏召回的是“听到鼓点与看到提示的时机错开了”，返回缺资料，没有选错另一篇。查询中位约 89 ms，最大约 108 ms；这是含文件／批准复核的本机小样本数据，不是目标服务器延迟保证。当前以单核、短批次和 2,000 段硬上限控制资源；正式内容规模与目标服务器性能仍需包 5 实测。

## 校验与受控导入

安装包提供 `taki-knowledge`，源码可运行 `python -m ournotes_bot.application.knowledge_cli`。工具不启动曲库、QQ 或聊天模型。

```text
taki-knowledge validate --root <交付目录>
taki-knowledge validate --root <交付目录> --delivery <交付清单.json>
taki-knowledge import --source <交付目录> --delivery <交付清单.json> --root <知识目录> --state <持久状态.sqlite3> --model-dir <固定模型目录>
taki-knowledge sync --root <知识目录> --state <持久状态.sqlite3> --model-dir <固定模型目录>
taki-knowledge retrieve --root <知识目录> --state <持久状态.sqlite3> --model-dir <固定模型目录> --server jp --query <问题>
```

`validate` 只读，输出每篇内容哈希和分类错误，`fact_review=false`。`import` 接受完整快照与用户交付清单，拒绝暂存目录和正式目录重叠。先验证全部格式、清单和确认记录，停用将被移除／替换的旧版本，验证候选索引，再逐文件原子替换（批准清单最后写入），重新观察后发布整份索引。外部编辑与暂存修改会使导入停止，不能覆盖期间的新改动。文件阶段中断后按当前真实文件重验；已经生效的停用记录保留。受控操作仅在观察结果持久化后返回成功。

交付清单为 `taki-knowledge-delivery/1`，含 `delivery_id/prepared_by/items/missing_materials`。每项需准确匹配 `id/file/content_sha256/game/servers/applicability/source_locators/limitations/confirmation_ref/status`：`source_locators` 按来源顺序列定位字符串，applicability 为同份对象；approved 文稿使用 confirmed 和对应确认记录，草稿使用 draft 和 null 确认记录。完整目录每篇都要列出；缺正文、来源或确认的材料列在 missing_materials，不虚构条目或正文。默认空交付模板：

```json
{"schema":"taki-knowledge-delivery/1","delivery_id":null,"prepared_by":null,"items":[],"missing_materials":["正文","精确来源定位","适用范围","用户确认记录"]}
```

空模板用于记录待交付状态，正式 import 前须提供真实交付标识和准备人。单篇已批准稿的技术校验不等于整本百科已通过，也不授权提交、部署或生产操作。

## 工程验收

已有 `test_platform_boundary.py` 扩展禁 QQ／禁网入口、空库、删除后恢复旧文件、批准版本、范围过滤与证据预算；`test_console_policies.py` 验证真实后端组合、释放所有权以及重建期间观察任务继续运行；`test_install_paths.py` 验证配置路径。安装烟雾检查导入全部新模块，在不安装可选模型依赖的隔离 wheel/sdist 中运行空库入口。

本次本地合同测试与真实模型评测放在忽略的 `runtime/package2b`，合成审批记录仅在临时目录中。其记录用于工程自查；没有把测试审批或测试文稿放入默认知识目录。最终验证数量和检索评测见本次草稿 PR。正式内容接入、生产部署和用户可见问答仍各自验收。
