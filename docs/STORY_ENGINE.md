# K1 场景资料与人格发布工程

本模块提供本地来源清单、场景原文导入、日中词法检索、相邻场景读取、版本回退、撤回和独立人工人格发布。它不获取账号、不调用模型、不下载语料，也不宣称拥有完整 BanG Dream! 剧情。剧情资料与现有 ON 机制审核库分别维护；格式校验不是事实审核。

入口为 `application/story_service.py::StoryService(root)`。Python 查询层只接收纯 `StoryBundle`，不读取文件、访问来源或平台。来源文件适配在 `sources/story_files.py`，纯记录在 `domain/story.py`。公开工具接线、配置、源码摘要和发布清单由主集成批次维护。

## 本地工具接口

- `import_bundle(path, allow_fixture=False)`：读取一个最多 32 MiB 的 UTF-8 JSON bundle，校验来源、哈希、场景顺序与双向邻接、派生来源及人格稿，再原子发布。导入只接受 draft 或 withdrawn 人格，`approved` 导入会被拒绝。
- `status()`：当前发布 revision、每份来源的覆盖状态／场景数量／撤回状态、可读场景数量及当前人工批准人格。没有当前版本时 `not_available`。
- `search(query, filters=None, limit=6)`：最多 300 字查询；可选精确 `work/source_kind/character/source_id/chapter` 过滤，最多返回 10 个场景且结果投影不超过 12 KiB，参与者摘要最多八名，超限标记截断。结果含 `query_rewrites`、改写后检索词、完整 revision、结果 handle、来源层级、原始 URI／定位、哈希与原文短片段。零匹配表示本次查询没有命中。
- `read(handle, before=1, after=1)`：每侧最多两场，合计最多 24 KiB 场景投影；始终保留目标场景，返回原文发言顺序、说话者、日文文本、独立可选译文、出处、版本、故事内时间、截断状态及 previous_handle／next_handle。handle 绑定发布 revision 与撤回账摘要；版本或撤回发生变化后明确 `stale`，不能猜文件或 URL。
- `withdraw(source_id, reason)`：独立持久来源撤回标记。每次检索／读取／人格读取复核，旧发布 rollback 不清除撤回。
- `valid(source_revision, revocation_revision)`：生成和交付宿主复核本次工具读到的快照；版本或撤回修订变化返回 false，来源状态损坏仍明确报错。工具可见返回不能代替发送前重验。
- `rollback(revision)`：切回本地已验证的版本指针；不复活已撤回来源或人格。
- `approve_persona(persona_id, reviewer, confirmation_ref, revision=...)`：仅供本地可信 operator 的独立人工批准动作。必须绑定当前 revision、已审核的派生依据及未撤回的非合成来源，写入单独批准指针。运行 `persona()` 不信导入文件的 approved 标签，不自动选择 draft。
- `withdraw_persona(persona_id, reason)`：独立人格撤回标记，回退也不恢复该 persona_id。需要重新发布时使用新的版本化 persona_id，重新人工批准。

所有 handle 与来源标签均是材料，不授予任意网络、文件或系统访问权限。源 URI 只作出处，不会被本模块抓取。`source_revision` 是 bundle 的 SHA-256；不是上游实时版本或事实日期。

## Bundle 合同

顶层必须精确包含：

```json
{"schema":"taki-story-bundle/1","sources":[],"scenes":[],"claims":[],"personas":[],"aliases":{}}
```

所有记录字段见对应冻结 dataclass；未知字段拒绝，JSON 重复键和非有限数拒绝。ID 只允许字母、数字和 `_.:-`，最多 100 字；场景最多 10000 个、来源最多 1000 份。每个场景最多 300 条、每条正文最多 4000 字，完整场景记录 UTF-8 投影不超过 16 KiB；长场景在导入准备阶段按自然交流片段分块，并用相邻 ID 保持同章上下文，不随机逐句打散。这些是资源上限，不证明全系列已覆盖。

| 记录 | 必需字段与含义 |
|---|---|
| SourceDocument | source_id、work、medium、language、uri、acquisition、source_kind、version、content_sha256、coverage、published_at、acquired_at、use_scope、coverage_note。未知时间／哈希保持 null；只有 collected 来源可以包含场景。 |
| Scene | scene_id、source_id、title、chapter、locator、participants、story_time、previous_scene、next_scene、utterances。故事内时间允许未知或非日历描述；不得用发布时间替代。邻接须同来源、双向一致且无环。 |
| Utterance | order 从 1 连续递增；speaker 必须在本场参与者内；text_ja 为原文，translation 是可空的独立译文；locator 保存原始行号／时间码。 |
| DerivedClaim | claim_id、text、kind、source_scene_ids、review_status、reviewed_by、reviewed_at、confirmation_ref。kind 区分 original_fact 与 analysis；原作事实只能引用 official_original，玩家分析不能升格原作。 |
| PersonaRelease | persona_id、prompt、source_claim_ids、covers_until、review_status、reviewed_by、reviewed_at、confirmation_ref。研究稿须可追溯至场景；运行发布由独立 operator 批准记录决定。 |

`coverage` 取 `collected/missing_source/pending_validation/unavailable`，来源清单可以逐项记录缺来源或待核验而没有场景，不补造“已收录”。`source_kind` 取 `official_original/official_summary/translation/fan_analysis/synthetic_fixture`。`use_scope` 取 `public_reference/permitted_local/licensed_reuse/fixture_only`，用于记录获准使用范围，不能把公开可见自动当作原文全集再发行许可。

`content_sha256` 绑定本来源已导入的场景投影，不冒称整个上游全集哈希：对该来源 `scenes` 原始顺序，以 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)` 序列化完整场景记录（含发言与译文），UTF-8 后 SHA-256。工具函数 `source_content_hash(scenes)` 产生同一结果。collected 必须包含至少一个场景，哈希必须一致；其他覆盖状态不进入检索。

检索先 NFKC 规范化，再按显式 `aliases` 将中文昵称／中文关键词替换为登记的日文名称／术语，输出改写轨迹；随后使用包含假名与汉字的词／二元组词法匹配，以整场为检索单位。译文只辅助命中，回答仍能回到日文原文及前后文；未引入中文向量模型或运行时下载依赖。

单次改写最多 16 项、改写后最多 1200 字，异常扩张返回 `story_query_too_complex`，不会无限展开同义词。读取／检索截断后通过句柄或更明确过滤继续，不把截断资料当全集。

## 发布与恢复

文件布局为 `releases/<revision>.json`、`current.json`、独立 `revocations.json`、独立 `persona.json` 与进程间 advisory lock。先完整校验 bundle，写入不可变哈希版本，再原子替换当前指针；失败保留旧指针。文件大小、路径名、符号链接和哈希在读取处验证。撤回账损坏时相关读取明确失败，不能退回无撤回信息的旧版本。

导入合成资料必须显式 `allow_fixture=True`，仅在隔离验收目录使用；该材料永远不能批准人格。真人资料、用户聊天、模型密钥与语料全集不提交到仓库或打入公开制品。备份恢复必须一并保留最新撤回账，先核对撤回再提供检索；本地模块不自动管理异地冷备份。

```powershell
$env:PYTHONPATH='src'
python -B -m ournotes_bot.application.story_cli validate --root runtime/story-fixture --bundle runtime/source-bundle.json --allow-fixture
python -B -m ournotes_bot.application.story_cli import --root runtime/story-fixture --bundle runtime/source-bundle.json --allow-fixture
python -B -m ournotes_bot.application.story_cli search --root runtime/story-fixture --query '立希 练习'
python -B -m ournotes_bot.application.story_cli status --root runtime/story-fixture
```

人格批准另需 `approve-persona --persona-id ... --revision ... --reviewer ... --approval-ref ... --confirm-human-review`，这个参数确认来自人工内容所有者。采集或生成任务不应调用该命令。当前工作没有执行真实人格批准。

## 验收与边界

`tests/test_platform_boundary.py::StoryPublicationTests` 使用纯合成临时 bundle，覆盖日中改写、原文与邻接读取、默认拒绝合成发布、合成人格拒绝、原文哈希、缺来源清单、分析分层、人工批准版本绑定、撤回后回退、错误元数据和脱离游戏／QQ 的 CLI。带 official_original 标签的单元夹具也仍是合成合同测试，不是已采集官方原文或真实审核。

本批提供导入／检索／发布工程，真实来源覆盖表、可核实真材及人格研究内容由资料任务收集。全系列语料获取、真实检索质量、人格人工判断和模型对照分别记录，不把这些结构测试写成已收齐剧情或已经学会原作人格。
