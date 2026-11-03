# 任务08：共用源码组合与查询共享拆分

本轮交付独立源码候选，保持个人入口，并让确定性查询不再初始化聊天运行时。不是最终定制 selection、范围批准、物理裁剪工件或上线证明。旧191静态清单只作历史记录。

工作区：`E:/CodexData/Codex/taki/work/taki-dev/runtime/plan-20261103-08-integration`；分支 `codex/plan-20261103-08-integration`。原191、197分支和其他任务工作树不改。进程占用枚举受环境权限限制，因此从已提交对象建立独立 clone；不复用原第二阶段目录。只消费以下固定提交。

## 组合检查点与输入

未加新拆分的检查点：`12637558b1b3da8c38b1ca3596cc281f5b2f9dc4`。由 B0 开始依次 merge 04、05、07、01、09、08资料。02、03、191已在输入祖先内，没有重复 cherry-pick。

| 输入 | 固定完整 SHA | 祖先关系/处理 |
|---|---|---|
| B0 main | `8d9e7301392ad548fe9554c4f4b09a0d53f615fa` | 指定基础，不移动 main |
| 01/#190 | `7ae92a28cfa2c01b4fa8ae42207b61ae0afe7f48` | 平台边界检查；与02测试自动合并 |
| P165/#191 | `e1a86a6db1f327a6dd0cc53fd860c4ababf50923` | 已在03/04祖先内，原165也已包含 |
| 02/#192 | `c6048b58ad054979be29542176776bd0cbe3551a` | 已在05祖先内 |
| 03/#196 | `4d754dfaf10a2e598532c3d4dd291a26f6fa6627` | 已在04祖先内 |
| 04/#193 | `f31722d9913491855ffd47b33556553542941da1` | 包含03新投影修复、字体/区域素材及05固定图标安全段 |
| 05/#195 | `a9c27d7eb94833df713995b8e6f9a85fc45ea0d5` | 保留完整范围/撤权接线，未生成真实批准 |
| 07/#198 | `a63de717df487f896ff2182ea5bf6008672db4ad` | 分叉于旧03 `17212e5df76cf948aa990af571ed6daf5c66ec25`；保留provider历史分区及03新投影 |
| 09/#194 | `9a3eca0b21dd7a7724143f988def59a5fc558dca` | 身份和发行工具；未 stage 最终产品 |
| 08资料/#197 | `509abc81efc2433cbc197050ddc5d64c40f48f5a` | 保留历史分析，加新版本索引 |
| 10/#199 | `cab373c65a98424f37b817c22c7837e53155893b` | 仅阅读部署合同，未合入执行接线 |

显式冲突只有两处文件：

- `src/ournotes_bot/visuals.py`：合05时保留04的受控 fetch 参数、区域素材读取和回退保护；05固定图标下载安全逻辑已由04包含。解决后与04该文件逐字节一致，保留字体、素材区域和安全段，没有整文件取05覆盖。
- `docs/L2.md`：同时保留03数据合同和09产品发行导航。backend/replies/receiver/commands及历史源的自动合并仍接受交叉回归，不以无文本冲突推断语义通过。

## 本轮新增共享边界

| 文件/符号（src/ournotes_bot 下） | 变化与保持的合同 |
|---|---|
| `natural_query/parser.py::LocalQueryParser` | 共享解析门面；QueryAgent保持唯一语法实现，local/off上限，零模型、无额度账构造 |
| `ai_query.py::AIQueryParser` | 继承共享门面，个人 run/resume/native、AIClient和配额行为保留 |
| `application/query_runtime.py::QueryRuntime` | 当前目录读取、确定性详情、前后有效性检查；检查 active/deadline/anchor/context lease/evidence，保留 source_unavailable/query_busy |
| `application/model_runtime.py::followup/check/current_repository` | 委托共享实现；fallback/chat配置、人格和能力有效性仍由个人 runtime.check 检查；个人错误映射保留 |
| `domain/query_context.py::ReadOnlyContext` | 共享不可变值类型；conversation_state兼容导出，保留所有字段与ACK锚点格式，不改已有持久合同 |
| `application/backend_bootstrap.py` | 原 `build_backend/run_backend` 仍个人默认；新显式 `build_query_backend` 仅开发验证。清除模型key，禁公告初始化，使用LocalQueryParser/QueryRuntime |
| `application/personal_runtime.py::configure_personal` | 原知识、BGE、ModelRuntime、可选公告初始化集中至个人组合叶子；查询根不导入 |
| `application/backend.py` | 共用原submit/ledger/grant/ACK/unknown；查询根详情走QueryRuntime；不注册聊天、模型、资料库和公告路由，不构造LibraryAdmin |
| `application/backend_sources.py` | QUERY_CODE_FILES是开发查询运行摘要，不是递归闭包或发行selection；个人CODE_FILES添加新共享模块 |

没有改变02 receiver/delivery/wire或05范围授权语义，没有新增数据/视觉/历史业务规则。开发查询配置不通过环境变量或现有CLI隐式启用，也不自动批准任何群/C2C。

## 新调用闭包

1. 网关 `koishi/src/index.ts` → ingress/receiver → backend HTTP；05Management范围及撤权检查保留，02接纳、grant、ACK和unknown事实保留。
2. 开发组合 `backend_bootstrap.build_query_backend` → DataOwner、SongRepository/configure_sources、LocalQueryParser、Backend、QueryRuntime、RuntimeTasks、SourceOperations/RuntimeOperations → create_app。个人CLI仍 main → build_backend → configure_personal。
3. 直接命令 `Backend.prepare_command` → QueryGate → replies.prepare_reply → commands.resolve_command/handle_command → query执行器。`/查`、`/问`、`/ask` → QueryRuntime.prepare → current_query_view → LocalQueryParser → QueryAgent本地规则 → 同一确定性执行器。未命中规则返回本地终态，不调用模型解释。
4. P165：on_query.public_information_command/information_command → public_queries/activity_query → Haneoka public_queries/activity_details/catalog/transport。profileId为精确字符串，名片页码/原图、region、pending/429、活动分组/数据质量来自组合输入。
5. replies捕获同次事实与素材 → rendering/info_layout等布局 → visuals受控下载/字体 → PreparedReply/DeliveryCursor；失败完整文字回退。没有新增二次事实读取来绘图。
6. 成功ACK → Conversations/DisplaySnapshot/Anchor + query.continuation.QueryContext。续页保持冻结快照；详情非continue且origin local/model、meta为空时走 QueryRuntime.followup：确认显示页 → 通用校验 → 当前view → 再校验 → gate.prepare_followup。个人仍附加原聊天/fallback有效性检查。

知识值类型/evidence撤回合同不等于知识检索服务；query_context保留KnowledgeScope。`domain/chat_images.py` 是 backend_protocol 输入图片元数据校验的**共享依赖**，不能按chat文件名删除；`sources/chat_images.py` 才是聊天图片获取路径。`query_agent.py` 同时含本地语法与受限远程路由，必须保留共享部分，不能整删。

### 数据、字体及非Python依赖

保留已选查询的 `query_aliases.json`、Haneoka `game_assets.json`/`visual_skill_contracts.json`、受控素材下载和来源证明。图像使用 Pillow 与 `rendering/assets/NotoSansSC.ttf`、对应许可/THIRD_PARTY；04字体优先级保持。名片原图保留来源尺寸，字体不用于重绘其内容。目录/谱面/榜单网络与缓存是查询运行依赖，不包含Haneoka服务端采集系统。

规则查询不需要BGE权重、个人知识/剧情正文、模型密钥、UsageLedger或ChatStore。aiohttp是后端/来源依赖；qq-botpy只属旧个人QQ入口，最终包元数据须09按入口处理。`card-947.jpg`是独立未选用途，不能因为全量pyproject当前包含就认定首版批准。

## 给09的保留、排除与待拆分

机器清单 `task08-integration/assembly-input.json` 绑定拆分源码commit，包含完整输入、逐文件blob摘要和累计/增量文件清单。它是审查输入，**不能直接作为 product_release stage 的 selection**。最终制品须重新从实际commit生成完整files映射并经11实物检查。

| 类别 | 处理 |
|---|---|
| 必须保留共享闭包 | 本地parser/QueryAgent本地分支、commands/query/contracts、所选数据源/图文渲染、QueryRuntime/query_context/continuation、backend协议/账本/ACK、domain知识及图片元数据值类型 |
| 聊天专属运行叶子 | personal_runtime、chat_agent/context/store/capabilities/jobs/proactive_runtime/followup/tasks/recovery、proactive_chat、persona、story_service/cli、story检索及源、sources/chat_images；定制排除，但先消除共享文件残余引用 |
| 可选模型/知识/公告 | ai_query、model_runtime/config/admin、model_provider、usage_ledger、knowledge_service/cli/files/embeddings/retrieval、notice_gateway/reports、site_lookup：不一概称聊天；按独立用途决定，当前开发查询根均不初始化 |
| 仍混合的源码 | backend包含未注册个人处理函数和惰性导入；query_agent/query_capabilities有受限模型路由；replies有模型结果兼容；RuntimeTasks有可选knowledge分支；admin/operations有模型/公告投影；backend_sources保留个人摘要表。运行隔离不等于物理裁剪完成 |
| Console与网关 | console.cjs及client页面、RPC、轮询、恢复草稿、chat/model/library/notice接口须按最终选择一起裁。05scope-identity/Management共享权限合同保留，不能只隐藏菜单 |
| 旧入口/打包 | main、qq、platforms/qq、knowledge_cli、pyproject scripts/extras/data-files，以及全量发布脚本/资源包不能沿用为定制物理通过证据 |
| 后台任务 | 目录/traits按已选查询需要保留；历史采样配置与留存待决；knowledge观察、聊天主动任务/清理/研究、公告轮询不随规则保留自动启用 |

## 产品决策矩阵（交00，不代决）

| 功能/维度 | 个人兼容候选 | 定制首版状态 |
|---|---|---|
| 规则查询共享能力 | 原行为及本地优先保持 | 已确认必须保留；具体菜单/别名组合仍待选 |
| P165竞技场列表、详情/乐队、模式 | 已有入口保持，依赖源支持 | 每个入口是否首发待选；不推断四服都有数据 |
| P165玩家公开资料、精确ID、名片页 | 已有入口保持，pending/限流/缺页明确 | 玩家/名片分别待选；不扩为账号操作 |
| P165活动概览、奖励、卡片、歌曲及详情 | 已有入口保持；字段完整性依03 | 四子项分别待选；原生素材/全文回退保持 |
| 群/C2C | 原路由、授权与投递隔离 | 哪些群/哪些私聊及权限待批准，开发测试不构成批准 |
| 区服 | 查询按来源区分；榜单provider区分 | 各入口逐服选，不能把别名/镜像当全部服务验证 |
| 历史榜单、采样、留存 | 07provider/schema4隔离保留 | 是否启用、采样范围及期限均待选 |
| 947固定图片 | 旧个人入口保持 | 单独待选，不能由通用查卡推定 |
| 本地知识/BGE | 个人配置保持 | 知识用途和BGE分别待选；规则零模型不需要它们 |
| 远程参数提取/证据表达/站内辅助 | 原个人权限与计量保持 | 每用途待选，均非规则查询必需 |
| 公告来源、摘要、发送 | 原个人可选配置保持 | 来源展示/模型摘要/主动发送分别待选 |
| 自由聊天代码 | 个人发行保持 | 用户已确认定制工件物理排除；本轮仅完成共享拆分前置 |

## 09/10/12接口及顺序

09先基于本轮固定源码与00选择生成最终入口/Console/包层selection，再更新实际组件摘要和身份。`build_query_backend(args, settings)`可作无聊天运行验证入口，不能当受信产品激活门。旧CLI默认个人，不能部署时靠是否有key推断产品。

05 `ProductIdentitySession` / `scopeIdentityAuthority` 已随整包进入组合；session beginCheck使旧结果失效，publish消费票据，read返回快照，invalidate清除。其 checkerCommit固定 `3dda6d29955328777f7e526c21bfd2828dbbea8a`；09的 `9a3eca0b21dd7a7724143f988def59a5fc558dca` 新增资料/测试不等于自动更换该权威。Console组合点仍是 `koishi/console.cjs` 的Management构造和第三参数ScopeStore。真实受信identity/scope选择、更换/撤权事件、恢复验证须由10按05/09合同接入，不能使用UI名称或开发allow夹具替代。

10只消费合同，不合入本轮执行接线。新产品摘要与包身份更新后再做启动/恢复检查；共享源码CI不证明安装或生产切换。07的历史schema/provider版本须09/10显式对照，不自动迁移生产。

12应独立审查两个阶段差异：组合点冲突处理、个人兼容、QueryRuntime前后校验、import隔离、02unknown/ACK交叉路径、03/04同次事实/图片、05撤权和07provider隔离。最终功能选择/真实资源/发行层验包仍分别由00、09、11负责。

## 验证与剩余验收

版本绑定的测试汇总、完整文件清单及最终SHA见机器清单和PR。组合检查点使用独立导出的固定源树跑回归，拆分版本另跑全量，避免在运行中混读修改。

新增验证写入已有测试文件，覆盖实际build_query_backend/create_app HTTP群/C2C规则入口；有key仍零模型、无聊天/知识/用量账；详情当前view前后失效；ACK→详情→unknown→迟到ACK；新进程阻断个人runtime/知识/剧情import并检查摘要不读聊天文件。个人NativeRuntime、续查、平台边界及图文流水线复用现有回归。

未执行：真实QQ、付费模型、生产缓存/历史迁移、安装部署重启、最终定制selection和各层物理裁剪、真实范围批准。合成源和本地HTTP测试不是真实上游/客户端送达验收。首版选项、批准权威及留存选择仍交00。
