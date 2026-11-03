# 产品身份与发行合同 v1（任务09）

本合同供05/08/10接线；本阶段只提供离线校验和精确文件装配基础，未接入现有控制器激活路径。个人版现有 schema=1 组合包流程保持原行为，不因此获得定制交付资格。新产品合同缺失时不能降级到旧验包器继续激活。

## C1：外部预期与自报

入口 `deploy/product_identity.py::check_identity(candidate, expected, observed)`，三个参数均为 JSON object，返回 `{status, reason, contract_version}`；status 为 `allow/reject/manual`，allow 只表示本项匹配，10仍须组合05/07和实际安装检查。错误不包含AppID、路径或输入值。读取JSON使用同模块 `read_json`，拒绝重复键和非有限数值。

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
