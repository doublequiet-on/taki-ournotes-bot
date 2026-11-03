# 产品身份与发行合同 v1（任务09）

本合同供05/08/10接线；本阶段只提供离线校验和精确文件装配基础，未接入现有控制器激活路径。个人版现有 schema=1 组合包流程保持原行为，不因此获得定制交付资格。新产品合同缺失时不能降级到旧验包器继续激活。

## C1：外部预期与自报

入口 `deploy/product_identity.py::check_identity(candidate, expected, observed)`，三个参数均为 JSON object，返回 `{status, reason, contract_version, evidence}`；status 为 `allow/reject/manual`，allow 只表示本项匹配，10仍须组合05/07和实际安装检查。错误不包含AppID、路径或输入值。读取JSON使用同模块 `read_json`，拒绝重复键和非有限数值。

`candidate` 是发行附带的 `product-manifest.json`；`expected` 来自实例的外部受信配置，必须位于发行包、备份及待恢复树之外，由当前管理权威取得。不得用候选自报、旧备份或环境变量填补缺失字段。`observed` 来自受信控制器的独立测量，不能直接复制候选内容。此接口不决定05的每次启动批准/最新批准锚点模式。

| 对象 | 必填字段 / 类型 |
| --- | --- |
| candidate | contract_version=1；product=`taki-personal` 或 `haneoka-custom`；instance为非空不透明字符串；app_id为数字字符串（不转换为整数）；channel分别为 `personal` / `custom`；release_seq非负整数；source_commit为40位小写hex；capabilities为无重复非空字符串数组（可为空）；components为组件名→64位SHA-256非空映射；protocol为 `{major,minor}` 非负整数；schemas为存储名→`{read:[min,max],write:[min,max]}` 非空映射；min_controller为正整数；inventory_sha256为递归清单原始字节摘要 |
| expected | contract_version=1；product/instance/app_id/channel与候选同类型；capability_ceiling为无重复字符串数组；manifest_sha256为经外部审核锁定的候选manifest原始字节摘要；minimum_release_seq非负整数；protocol为 `{major,min_minor,max_minor}`；schemas为当前实际存储名→非负版本号的非空映射 |
| observed | manifest_sha256为候选文件实算摘要；components为实际组件摘要映射；controller为受信控制器正整数能力版本 |

未知顶层字段拒绝，布尔值不是整数。所有字段必填、不可null。组件集合必须完全一致，不只校验交集；能力必须在外部上限内。定制版永远拒绝 `chat.free` 与 `chat.proactive`，08须提交完整能力名称和符号闭包；这个检查不证明物理裁剪。schema当前版本须同时落在读写闭区间，集合也必须一致，不能用缺省版本1绕过未知存储。protocol须major一致且minor在外部支持范围；最低控制器按受信版本比较，不读取候选提供的可执行校验器。

组件摘要由所用归档字节或规范文件清单生成：各文件以POSIX相对路径排序，每行 `path\0sha256\n`，UTF-8无BOM后SHA-256；不能复用未包含全部选中模块的旧backend digest。现有 `backend_sources.CODE_FILES/TRANSPORT_PROTOCOL` 仍是个人版运行协议来源；08固定共享闭包后才能提供定制inventory。不得按model/knowledge/query_agent文件名整删。源commit与component摘要分别记录；不同依赖SHA只作依赖登记；源码装配须收口到一个经审核的固定组合提交，外部组件另按明确版本与摘要登记，未登记混搭拒绝。

## C6：判定与回退

| reason | status / 含义 |
| --- | --- |
| malformed_contract | reject：字段缺失、未知、类型/格式错误或contract_version不支持 |
| identity_mismatch | reject：product/instance/app_id/channel不符或产品通道组合错误 |
| unapproved_manifest | reject：实算manifest不等于外部锁定摘要 |
| capability_exceeded | reject：超过上限或定制聊天能力 |
| component_mismatch | reject：实测组件集合/摘要不等于manifest |
| protocol_incompatible / controller_too_old | reject：协议不兼容或旧控制器 |
| rollback_denied | reject：release_seq低于外部下限 |
| schema_incompatible | manual：缺存储版本、额外存储或无法同时读写；保持外发隔离，交07/10迁移或恢复审阅，不自动迁移 |
| identity_verified | allow：仅C1与09负责的C6部分通过 |

回滚目标必须作为候选重新校验，外部权威明确锁定其manifest和允许的序号下限；05/07权威不能从旧包恢复。controller rollback、代码回退和数据恢复独立。首装的schemas由10明确提供实际初始化计划，不能伪装现有存储。个人旧baseline没有自动豁免：旧流程仅维持已部署个人实例的现状；进入新合同需按精确旧commit/组件/协议/schema重新审阅并出具新manifest，不能把任意旧包标成兼容。当前未认证任何旧baseline的新合同迁移或定制回退目标。

## 200固定候选的发行增量（2026-11-03）

方案B轮的固定接口参考更新为08 `eed7e70fa8b5194befc379a9929ce72e7617707a`，只读未合入；[下游Console](CONSOLE_DOWNSTREAM.md)候选具有独立组件身份，需要00安排08隔离接入，尚不能替换最终产品组件摘要或selection。05可信checker来源不随该组合SHA改变。

以下矩阵源于第四轮：当时08统一源码输入为 `20b8cb2179475df2bf0ae365a2088403702557c5`，拆分源码检查点 `037d08333338bdbbfc122ac658293b5c48f30fc4`。其已包含09 `9a3eca0b21dd7a7724143f988def59a5fc558dca`，不能再次叠加09全历史。09仍在原B0分支维护；后续只把审阅后的发行增量移入200，不能用09旧backend/Console文件覆盖08拆分。未合入200、10或移动分支。

| 层 | 个人准备 | 定制准备（无默认selection） |
| --- | --- | --- |
| 共享查询 | 保持原入口并验证新LocalQueryParser/QueryRuntime委托 | 保留parser、QueryAgent本地分支、query_context/continuation、协议/账本/ACK、所选来源/渲染；domain/chat_images是共享输入校验，不能整删 |
| 聊天叶子 | personal_runtime及聊天/剧情/主动任务保持 | 08清单20个personal_leaf是排除候选；先消除共享引用，再精确排除，当前不能直接删文件装包 |
| 混合文件 | 兼容回归 | backend未注册个人函数/惰性导入、query_agent/query_capabilities模型路由、replies兼容分支、RuntimeTasks知识分支、admin/operations投影、backend_sources个人表仍待08拆分 |
| 可选能力 | 原配置保持 | ai_query/model/knowledge/notice/site_lookup按独立用途决定，不按名称归为聊天；范围、采样/留存、947/品牌/资源仍待选 |
| 网关/Console/包层 | 当前npm与补丁保留 | console.cjs、RPC/页面/轮询/旧草稿、main/qq入口、pyproject脚本/extras/data-files及全量发布脚本继续待选和拆分；开发查询根零模型不等于无聊天字节 |
| 历史/控制器 | 保留07格式边界及原事务语义 | 每库physical/logical+partition与07恢复权威照常验证；10工具必须单独登记实际控制器源/hash、版本、rollback及安装树，不因200已组合就认定包含199 |

10参考固定 `ca34d361b083626777927997f7379bb2ae71e063`，已有真实05/07/09调用及逐库只读观察证据，但安装恢复/自动激活未接通。12未完成审查不作放行依据。08机器清单及本轮读取hash保留在task09材料索引；它不是获批selection。

### 受信checker来源与发行绑定

05当前只接受 `checkerCommit=3dda6d29955328777f7e526c21bfd2828dbbea8a`。已独立比较该提交、09/9a3eca0、08/20b8cb三处 `deploy/product_identity.py`，字节SHA-256均为 `736c983ae4b0d03e39c893a886df07d3839b434381dd6fbef406ab2143ca1d98`。相同字节不授权伪贴来源：不能把200组合SHA填入checkerCommit，也不能将从200提取的文件宣称从3dda6d2取得。

10的受信控制器材料应实际从上述3dda6d2 Git对象导出该文件，记录repo、完整来源commit、路径、字节hash及控制器材料自身hash，由恢复树外管理权威锁定；在控制器受信安装区加载，绝不加载待验候选自带checker。原来源导出与hash核对成功后才将真实checkerCommit连同当前check_scope_binding结果交05会话。产品candidate.source_commit仍写最终组合提交，组件摘要仍测实际工件，两种来源分别记录。先beginCheck使旧会话失效，再由受信路径publish；失败或变更/恢复/重启必须invalidate并保持隔离。

这些是发行/控制器材料要求，本轮未实现10运行接线。如果将来checker字节需要改变，须05与10显式审核新来源及失效测试后升级；09不会自行扩大05允许集合。当前check_identity/check_scope_binding函数、字段、reason/evidence及预检含义保持不变，范围批准政策仍未代选。

## 07历史格式的具体映射（10采集，09预检）

固定输入为07 `a63de717df487f896ff2182ea5bf6008672db4ad` 的 `docs/HISTORY_DATA_CONTRACT.md`，其真实base为03 `17212e5df76cf948aa990af571ed6daf5c66ec25`；本分支base仍为 `8d9e7301392ad548fe9554c4f4b09a0d53f615fa`，没有合入07代码。以下声明须在最终统一候选实现验证后才能填入其manifest，不能据此宣称本分支已支持schema4。

沿用现有schemas映射，不新增第二套兼容入口。启用provider历史的候选登记 `cutoff_history_provider`，07对应read/write均为 `[4,4]`。每个实际数据库副本另登记 `cutoff_history.<replica_id>.physical` 和 `.logical` 两个键；replica_id由10在包外的受信存储清单分配，绑定具体副本及partition身份，不使用真实账户或用户路径。expected填实测单值，candidate填经验证读写区间。未压缩schema4为physical=4、logical=4；压缩后为physical=107、logical=4。旧1/2/3同样按实际格式单独登记，不能把 `[1,107]` 写成支持范围，不能仅登记107而遗漏logical。不使用provider能力标记代替逐库证据。

| 目标代码 / 实际数据 | 读写与回退边界 |
| --- | --- |
| 07 / 原有1、2、3或107(logical1/2/3) | 按原存储路径和原读写约束；不原地迁移，不自动认定所有旧writer可写所有旧版本 |
| 07 / provider schema4 | 仅新分区；保留provider、normalization、time_kind以及observation/quarantine provenance |
| 07 / 107(logical4) | 需同时具有107压缩实现和logical4支持；export恢复logical4，不是降级到3 |
| 03旧代码 / schema4或107(logical4) | 拒绝新分区；在09中读或写范围不覆盖4即manual/schema_incompatible，保持隔离；即使允许回退release_seq也不能绕过 |
| 旧代码 / 未变的旧文件 | 只允许已验证旧格式路径；不代表新分区可恢复，provider/2写门仍按旧代码拒绝 |

10必须从实际SQLite的user_version、store_meta.legacy_version（107）、完整状态和partition_identity采集两维版本，并核provider/normalization/time_kind及路径身份；未知、损坏或缺逻辑版本时保持manual，不编造版本或遗漏副本。check_identity本身不打开数据库：它只比较完整键集合及读写区间。候选和expected同时遗漏某库不会由纯比较发现，因此完整存储枚举、未选存储的隔离和实际打开验证是10的独立必过门。首次安装以明确初始化计划单列，不冒充已存在数据库。实例存储集合变化使原manifest绑定失效，须重新审核并预检。

代码回退不是数据恢复。不得改user_version、把v4改名到v3路径或用旧库覆盖现状求兼容。恢复还须单独调用07 `history_retention.check_recovery(candidate, current_authority)`；其allow/reject/manual与reason/evidence不由09身份allow代替，当前删除权威来自恢复树、DB及journal之外。旧库与旧journal、旧库与未应用删除的副本不能因身份相符获准恢复。

最终选择历史功能时，08/09须核选中入口到 `sources/history_partitions.py`、`cutoff_history.py`、`haneoka/event_cutoffs.py`、`moenotes_events.py` 的真实闭包；选择压缩/归档/恢复时再核 `history_compression.py`、`history_archive.py`、`history_retention.py`。随行包含07历史合同、保留/恢复说明及适用的 `scripts/inspect_history_data.py` 工具。此列表是核对入口，不能替代符号闭包或整文件allowlist。实际DB、WAL/SHM、旧备份、名片和生产缓存不作为发行材料。

## 发行清单与后续输入

两个通道分别出候选、manifest和审计回执。同仓不代表同工件；本阶段没有完整组合候选。

| 交付层 | 必须登记与检查 |
| --- | --- |
| wheel / sdist / git source archive | 每层独立路径、大小、hash、构建源SHA、逐文件清单；查询别名/visual skill contracts/正确game_assets索引；包数据和安装入口；研究/夹具不因Git跟踪而默认收录 |
| Python wheelhouse | 精确锁、wheel版本/hash、METADATA及所有LICENSE/NOTICE、原生库材料；可选知识依赖单独选择 |
| Node / gateway / Console | Node发行来源与hash、完整LICENSE；实际npm树版本/许可证/NOTICE；lib、source maps、嵌入sourcesContent及Console源/补丁均入清单 |
| controller / rollback | 独立代码版本/摘要/协议、历史payload及嵌套归档；不能恢复未选聊天或个人数据 |
| 安装树 / previous / 恢复树 | 10隔离输出的实际文件与发行映射，二次下载及字体路径；安装检查不能用源码清单代替 |
| 随行材料 | 来源/许可证/NOTICE/修改说明/源码或补丁提供方式；每项真实文件摘要；外部数据服务和宿主依赖单列 |

08须给固定SHA的符号级共享查询闭包、能力矩阵、聊天排除清单（含文档、编译代码、source maps）、所选P165入口与资源索引。00须给统一装配候选及依赖完整SHA。947、品牌、游戏素材、知识正文的保留/替换/排除均待产品选择；审计建议不是选择。个人缓存、真实账号/名片、日志、生产库不得进入发行。Haneoka是外部serverless数据服务；其R2/D1、采集状态、账号和部署均不打包。

Node完整许可证的交付位置为 `licenses/node/LICENSE`。具体来源、完整性及Console 5.30.11材料核对见 [发行材料](RELEASE_MATERIALS.md)。清单匹配只是字节证据，许可证内容充分性与物理裁剪语义仍需11审计。

### 08静态输入与精确selection门槛

固定08输入为 `509abc81efc2433cbc197050ddc5d64c40f48f5a` 的 `docs/TASK08_PRODUCT_BOUNDARY.md` 与 `docs/task08-product/assembly-input.json`，分析源码基于191 `e1a86a6db1f327a6dd0cc53fd860c4ababf50923`。`assembly_ready=false`；静态import图是保守上界，不是获批selection。本轮只读引用，不合入或拼接这些SHA的文件。

生成selection前依次需要：00固定能力/入口/区服/群与C2C、947/品牌/素材/知识及公告选择；08完成共享parser、followup、查询依赖与聊天运行路径拆分，给出聊天排除清单；05/07/02等接口在单一组合提交接通并验证；按该提交核完整Python包数据、npm/Console编译及source maps、controller/rollback与许可材料。随后从该**单一固定提交**生成精确文件/hash，重算backend_sources/组件摘要，两个通道分别构建。不能将212个静态输入文件直接当发行清单，也不能把多SHA摘取文件称作统一候选。物理裁剪、个人功能回归、11递归审计和10隔离安装均在真实工件上另验。

## 可执行基础与使用顺序

以下命令使用本checkout受信脚本，绝不加载候选内校验器：

```text
python -B scripts/product_release.py stage --checkout <repo> --selection <reviewed-selection.json> --output <new-tree>
python -B scripts/product_release.py inventory <artifact-or-install-tree> --output <inventory-outside-tree.json>
python -B scripts/product_release.py verify <artifact-or-install-tree> --inventory <reviewed-inventory.json> --inventory-sha256 <external-sha256>
python -B scripts/product_release.py identity --candidate <product-manifest.json> --expected <external-instance.json> --observed <controller-observations.json>
```

selection格式为 `{source_commit:完整SHA, files:{精确仓库POSIX路径:SHA256}}`。只从该提交的普通Git blob装配到新目录，拒绝链接/子模块、错hash、已有输出；不读工作区未提交文件，不按文件名猜测聊天边界。它是构建前的裁剪基础，尚不调用wheel/npm构建，也不自动产生最终产品selection。

inventory格式为 `{inventory_version:1,files:{路径:{size,sha256,parent_sha256}}}`；目录内文件使用相对路径，单工件保留文件名，归档子成员以 `!` 分隔。每个压缩层也记hash和父hash。包括source maps、源码包、wheel、许可证、rollback以及安装树中的普通文件；不会执行脚本或解析语义。单文件/每层解压256MiB，总读取2GiB，100000文件/归档条目，嵌套深度8；超限拒绝，不出部分通过结果。7z/rar与不可读的声明归档拒绝。空目录不构成文件证据，权限/可执行位和目标平台安装检查仍归10。

inventory生成是待审记录，不能自动批准自身；11审阅其全部字节和选中闭包后，由外部权威固定inventory原始文件SHA，并写入候选manifest的inventory_sha256，最终再固定manifest原始文件SHA。组合文件摘要不含manifest自身，避免循环摘要。组件分组及源SHA由08/09的最终装配清单给定，独立测量后传入observed；CLI只代为实算manifest文件摘要。此阶段不把人工provided components称为自动运行探测。

10应先隔离外发，按外部摘要验证inventory，再验证identity，然后检查05当前范围权威、07当前删除权威与存储、实际安装/protocol握手，最后按10激活门决定。任一reject/manual保持隔离。检查顺序属于10，09不另建恢复框架。

## 05 opaque binding接线（唯一接口）

以下函数/字段为09唯一调用合同；05新版适配仍消费同一合同，不再定义另一份跨语言身份算法。本轮未改05文件。下述05提交只作为已核调用边界的版本依据，不能代表05移动分支的最新实现。08固定输入509abc81efc2433cbc197050ddc5d64c40f48f5a也确认此调用顺序，实际集成须再固定05最终SHA。

已核对05提交 `6b64e027b25d197981f456d6c87f32db54da6c51` 的 `koishi/src/scope-authority.ts`：`ScopeAuthority.identity(binding)` 是同步回调，`ScopeStore.inspect()` 每次先调用identity，只有allow才调用独立approval。05只把binding当对象，尚无09接线；本分支没有叠加05。

09定义binding为严格对象 `{contract_version:1, manifest_sha256:64位小写hex}`。它只引用当前经外部批准的发行manifest，不携带可信status、expected或observed。具体适配入口 `check_scope_binding(binding, candidate, expected, observed)` 每次先调用check_identity，再核binding，返回05需要的 `{outcome,reason,evidence}`；reject映射deny，manual保留，allow仍只表示身份预检。新原因 `scope_binding_malformed` / `scope_binding_mismatch` 均deny。换发行manifest必须显式重新绑定；这不决定范围批准如何获得。

check_identity对格式合法的三份输入生成evidence：`SHA256(ASCII JSON({contract_version:1,candidate,expected,observed}, sort_keys=True, separators=(',', ':'), ensure_ascii=True))`。字段不合法时evidence为空。它绑定证据快照，不是签名、授权token或可信来源证明，不能用候选自带evidence或上次allow绕过重验。check_scope_binding的evidence指向当前C1输入，binding失败由reason说明；完整binding保留在05文档及其独立摘要中，不回显AppID。

08/10接线顺序：受信控制器先读取恢复树外的当前expected，实测candidate原始文件摘要与组件/控制器/schema，再运行09预检。05的同步回调必须由受信组合根注入；不能直接执行候选内Python，也不能从approved-scope恢复expected。若网关与Python分进程，08/10先在现有受信启动/管理边界取得当前会话的预检快照，再由同步回调核binding并映射结果；不得每次查询spawn Python或新增通用RPC框架。快照只在对应组件、实例配置、控制器、存储版本和外部预期不变的当前会话有效；变化/恢复/重启先隔离并重新预检，缺失或无法确定新鲜性返回manual/deny。这个生命周期需要08/10实际接线与验证，本包不宣称已实现网关信任传递。

直接Python调用示意（可信输入来源由调用方负责）：

```python
identity = check_scope_binding(scope_document['binding'], candidate, external_expected, measured)
# identity['outcome'] != 'allow' 时保持隔离；allow后仍须05当前approval和07恢复检查。
```

05现有接口无需修改，独立批准回调仍是必需项；本包未选择每次启动人工批准或最新批准锚点。恢复旧scope的合法binding不能批准旧宽范围，个人模式也不能作为身份失败的回退。
