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

组件摘要由所用归档字节或规范文件清单生成：各文件以POSIX相对路径排序，每行 `path\0sha256\n`，UTF-8无BOM后SHA-256；不能复用未包含全部选中模块的旧backend digest。现有 `backend_sources.CODE_FILES/TRANSPORT_PROTOCOL` 仍是个人版运行协议来源；08固定共享闭包后才能提供定制inventory。不得按model/knowledge/query_agent文件名整删。源commit与component摘要分别记录；不同依赖SHA可以是经审核的明确装配，未登记混搭拒绝。

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

## 05 opaque binding接线（固定接口）

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
