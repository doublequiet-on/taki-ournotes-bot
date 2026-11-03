# 任务08第二阶段：查询闭包与产品装配输入

> 历史版本记录：本文及 task08-product/assembly-input.json 绑定旧191源码。新共用源码组合和共享拆分以 [TASK08_INTEGRATION.md](TASK08_INTEGRATION.md) 及其绑定版本清单为准；不得将本文旧依赖SHA或静态闭包当作新组合验证。

日期：2026-11-03，Asia/Hong_Kong。本文是固定源码的分析与接线要求，**不是最终产品选择、组合候选或裁剪通过证明**。本轮不修改运行代码，不执行产品装配。机器清单见 [assembly-input.json](task08-product/assembly-input.json)。总计划以本轮用户明确确认的“同仓独立发行、定制工件物理裁剪聊天、保留规则查询”为准；任务卡旧复制提示中“不要把物理裁剪当已决定”已被新确认覆盖。

## 版本、归属与消费方式

| 对象 | 完整SHA | 本轮用途 |
|---|---|---|
| main B0 | `8d9e7301392ad548fe9554c4f4b09a0d53f615fa` | 远端核实，未合并第二阶段依赖 |
| 原165 | `5e88749ea2aacca19e82af490acdc8c43449fef4` | 历史来源，保留原PR |
| 191兼容候选 | `e1a86a6db1f327a6dd0cc53fd860c4ababf50923` | 本分支代码基线；以下符号/路径均绑定此版本，另有注明者除外 |
| 02 / PR192 | `c6048b58ad054979be29542176776bd0cbe3551a` | 新head；相对旧登记仅测试修复，投递合同仍在 |
| 03 / PR196 | `17212e5df76cf948aa990af571ed6daf5c66ec25` | 数据合同，基于191 |
| 04 / PR193 | `101e148523b3087386f46946369b2ec9ec895ce5` | 图片候选，基于191；尚未把03组合验完 |
| 05 / PR195 | `6b64e027b25d197981f456d6c87f32db54da6c51` | 范围合同；base仍为02旧head `d9240dffecee0f0a0066d5f70c8357fc9a398632` |
| 09 / PR194 | `3dda6d29955328777f7e526c21bfd2828dbbea8a` | 新增scope binding适配；替代本轮初读旧登记 `b29d69fd3d76b5be81610228ec3cfbb65fd044bf` |

独立目录：`E:\CodexData\Codex\taki\work\taki-dev\runtime\plan-20261103-08-product`；分支 `codex/plan-20261103-08-product`。从191已提交对象建立独立clone，origin为目标GitHub仓库。没有修改191分支、原165或其他任务工作树；对其他任务源码仅使用固定Git对象。定位目录的AGENTS、开发指南、图片说明及未跟踪目录保持原状。

11只读审计：`E:\CodexData\.codex\visualizations\2026\11\02\01a19e8f-71e1-7682-88a3-80ae5d3c7799\task11-audit-20261103\审计报告.md`及`artifact-checklist.md`。其源码证据为B0、原165、旧190 `12f621de13a897e395e38e332fa4b0fbb8cfa442`，不是本轮工件审计。使用其字体/素材/包层清单，不继承“当前版本”结论。

09可消费JSON中的精确文件摘要、符号职责、阻断项和入口验收；不能把它直接传给`product_release.py stage`。09的selection要求同一已审核提交的完整`files`映射，而当前共享文件含聊天、产品选项未定。先按下文完成拆分和选择，再从最终组合commit生成selection。禁止把多SHA文件拷贝称为通过同一候选验证。

完整静态证据在本工作区`runtime/task08-product/query-import-graph.json`：167个Python模块、2074个符号（文件/行/作用域）、704条静态import边；四个根的展开文件集合分别为commands 57、replies 102、backend_bootstrap 154、on_query 58。逐文件摘要清单覆盖212个已提交源文件/资源/构建声明。图与JSON都读取191提交blob，不读取其他任务未提交文件；符号行号绑定191，不绑定未来组合版本。完整图为本地分析产物，两个docs文件为可通过PR获取的交付资料。

## 规则查询的实际调用闭包

“规则查询”在此指确定性命令/本地语法到事实结果，不等于开放百科、攻略生成或模型解释。没有命中本地语法时应明确不支持/待澄清；不能为了保留规则查询而隐式启用模型。`/查`、`/问`、`/ask`共享本地解析，但实际已支持别名以`query_agent.is_ai_request/query_text`为准；`/問`仍是产品待定合同，不能用简体通过代替繁体入口通过。

| 层/入口 | 精确文件与符号（路径前缀src/ournotes_bot/） | 必须保留的行为/下一跳 |
|---|---|---|
| 当前组合根 | `main.py::main/build_parser` → `application/backend_bootstrap.py::build_backend` | backend模式清除QQ密钥副本、构造DataOwner、SongRepository、来源、Backend；当前还无条件初始化KnowledgeService/ModelRuntime，必须拆分 |
| 网关接入 | 仓库`koishi/src/index.ts`、`ingress.ts`、`receiver.ts`、`backend.ts` | 解析群/C2C、身份/权限/范围、识别、提交；HTTP鉴权不能由菜单代替；02拥有投递，05拥有范围 |
| 后端 | `application/backend.py::Backend.submit/prepare_command`、`create_app` | direct走QueryGate；自然入口优先ModelRuntime.prepare（有实例时），否则parser.prepare_local。DeferredModel之后是受限可选模型分支，不属于规则必需项 |
| 业务闸门 | `application/replies.py::QueryGate/prepare_reply/_prepare_reply` | 工作线程边界、捕获同次query view，直接`resolve_command/handle_command`或接收AgentOutcome，不重查绘图事实 |
| 自然规则 | `ai_query.py::AIQueryParser.prepare_local/prepare_native_local` → `natural_query/query_agent.py::QueryAgent._run` | 续查解析、榜线、meta、字段、public_information_command、本地卡/曲条件、information_command；保留缓存来源、scope、失效与OutcomeCode，不能整删query_agent |
| P165规则 | `natural_query/on_query.py::public_information_command/information_command` → `commands.py::resolve_command/handle_command` | 完整问题匹配：玩家精确ID、竞技场乐队/模式、本期活动四子项；不是从任意文本猜意图 |
| P165事实 | `query/public_queries.py::parse_arena/execute_arena/parse_player/execute_player`、`query/activity_query.py::execute_activity/detail_rows/overview_rows` | `domain/public_queries.py`请求/失败、`query/contracts/info.py`的InfoAnswer/InfoView/InfoRow；活动投影来自`domain/info_catalog.py`与`source`（下行） |
| 来源 | `data.py::SongRepository.capture_query_view/current_query_view/capture_catalog`、`sources/haneoka/public_queries.py::PublicQueries`、`sources/haneoka/activity_details.py::project_details`、catalog/transport/catalog_assets | 当前release与区域身份；公开记录pending/429/404/失败独立；profileId不可经JS number；名片页码、下载路径及原始字节绑定同次结果 |
| 通用查询 | `commands.py`、`structured_query.py`、`natural_query/local_query.py/on_query.py/query_validation.py/query_terms.py/query_capabilities.py`、`query/`各已选业务executor | QuerySpec/QueryResult/CommandResult共用；query_capabilities既有本地路由也有模型提示，不按文件名全删。未选业务入口后续单独收窄 |
| 图文准备 | `application/replies.py::render_prepared/render_prepared_inner/complete_fallback/visual_asset/bind_info_expiry`、`reply_preparation.py` | 捕获完整正文、captured_images与original_image，保留活动分组、区域素材准入、失效时间；归04，本轮只登记 |
| 图片 | `rendering/info_layout.py/rotating_layout.py/haneoka_theme.py/readable_pages.py/image_output.py`及已选卡/曲/谱/榜renderer、`visuals.py` | 全文回退、分块/分页、字体测量、编码限制；文字失败不能误变成空成功；名片原尺寸与通用图缩放规则分开 |
| 投递与续查 | `replies.py::PreparedReply/DeliveryCursor/expand_replies/prepare_followup`，`query/continuation.py::QueryContext/capture_context/execute_followup/parse_operation`，`application/conversation_state.py::Conversations/DisplaySnapshot/Anchor` | 成功ACK才绑定作用域/消息锚点；冻结续页与当前目录详情不同；02账本、unknown、grant和allow_context语义不改 |

### 必须从ModelRuntime保住的详情路径

`Backend`选择native_detail的条件是：有model_runtime、operation不是continue、origin为local/model、query_context.meta为空。接着调用`ModelRuntime.followup`：

1. `previous(work)`读取已ACK显示快照和evidence leases；不把聊天history当事实。
2. `check(work, content_deadline, leases)`检查work active、deadline、content deadline、anchor仍在snapshots、context lease仍有效、knowledge evidence未撤回；该函数还混有fallback配置及聊天专属有效性检查。
3. `current_repository()`经gate执行`SongRepository.current_query_view`，失败映射source_unavailable，繁忙不能当有效仓库。
4. 再检查有效性，然后`gate.prepare_followup(context, operation, repository)`，最终走确定性`replies.prepare_followup`。

此路径**没有模型请求**。只把`backend.model_runtime=None`会改变native详情的当前目录检查；整删ModelRuntime也会令组合根import失败。最小拆分应由08在02固定后，把当前view与上述通用有效性检查移到已有查询应用层，聊天验证留个人分支；两者委托同一份实现。不能简单拷出followup而漏掉两次校验、evidence撤回、错误码或查询busy处理，也不应为纯查询创建ChatAgent/UsageLedger。

### 知识、上下文不是同一项功能

- `conversation_state.ReadOnlyContext`的text/origin/facts/evidence/topic/knowledge_scope/focus/history/chat_topic混合查询和模型上下文；`DisplaySnapshot`/`Anchor`/Conversations及`query.continuation.ContextKey/QueryContext`属于续查共享闭包。禁自由聊天不等于删除已ACK查询上下文。
- `domain/knowledge.py::KnowledgeScope`被conversation_state直接导入；即使不选知识检索也不能先删该文件。可先保留无I/O值类型，或在最小拆分时移入共享类型并检查全部调用者，不创建另一个知识权威。
- `knowledge_service.py`、`query/knowledge_retrieval.py`、`sources/knowledge_files.py`是受审资料/撤回/检索功能；BGE在`sources/knowledge_embeddings.py`是独立可选编码器。规则命令不需要权重或正文。若选本地知识，应增加确定性QQ检索入口和来源/撤回展示；当前原生工具中的模型knowledge_search不等于已存在该入口。
- `ModelRuntime.run`含query_only工具选择，也含chat_response、conversation_reply、知识证据表达及站内查证；`query_only=True`不能证明零模型或没有聊天字节。远程参数提取与证据表达须分开选择，保留所选用途的权限/限额/核算，不能为了删聊天把这些用途误标成已批准。
- 剧情服务/检索/文件读取供ChatAgent使用；未获独立产品用途前从定制运行闭包排除。正式知识正文、剧情正文与批准清单不从个人实例复制。

## 数据、依赖与资源

运行闭包不仅是Python import。`query_aliases.json`是实体规则；`sources/haneoka/visual_skill_contracts.json`是视觉技能/字段合同；`sources/haneoka/game_assets.json`是固定release/摘要素材索引。它们不是可随意删除的研究夹具。SongRepository依赖已选源的目录、谱面、榜单适配器与缓存；不把Haneoka serverless的R2/D1/采集进程打包。

图像保留方案需要Pillow及随包`rendering/assets/NotoSansSC.ttf`、`Roboto.ttf`和两个OFL文本；字体用于中文/拉丁数字的测量和绘制，不是聊天素材。04新head还修复visuals优先读取随包字体；05的安全下载校验与04字体改动由04顺序组合。P165使用原生段位/任务/属性图标、活动banner、卡面、玩家最爱卡及下载名片，资源与游戏区服须各自校验。预置这些游戏素材是否交付仍待选择及11材料核验；保留查询支持不等于允许预装真实名片、昵称、ID、缓存或冻结输出。

基础Python依赖pyproject声明aiohttp/Pillow/qq-botpy。Koishi后端HTTP需要aiohttp，绘图需要Pillow；qq-botpy被legacy bot和menu Token引用，只有同时处理旧入口与菜单工具后才能评估从定制锁移除。BGE的onnxruntime/tokenizers/numpy及知识锁只在选中时加入。Node/Koishi/QQ adapter/HTTP/Console是接入/管理依赖，不能当聊天包全删；精确版本及完整许可由09/11按实际工件核对，不升级依赖。

## 排除与待拆分清单

清单中的“排除”表示**定制最终工件必须不含对应聊天实现**，不授权删除个人源码；现有import断开前不能执行删文件。JSON列出精确现有路径/摘要，避免目录通配误删。

| 分类 | 文件/符号 | 装配动作与阻断 |
|---|---|---|
| 聊天专属 | application的chat_agent/chat_admin/chat_capabilities/chat_context/chat_followup/chat_tasks/chat_store/chat_recovery/chat_proactive_runtime/chat_jobs/proactive_chat/persona；domain的chat/chat_memory/chat_images；sources/chat_images | 定制排除；个人保留。移除自由聊天、观察/候选、合并窗口、总结、记忆与图片分析后台任务及持久writer。先解除ModelRuntime/backend引用 |
| 剧情聊天 | application/story_service/story_cli、query/story_retrieval、sources/story_files、domain/story | 定制当前无批准用途，排除聊天闭包；未来独立知识用途需另审，不能以剧情可检索扩大首版 |
| Python组合根 | main、backend_bootstrap、backend、model_runtime | 必须拆分。build_backend按受信产品合同装配；custom无条件不得构造ChatAgent，不接受环境变量提升。backend删除/隔离chat observations/candidates/admin路由；直接HTTP、重启、旧配置均受同一上限 |
| 自然查询与模型 | ai_query、query_agent、query_capabilities、native_tools、ai_client、model_provider、model_config/admin/execution、usage_ledger、site_lookup | 本地AgentOutcome/语法/校验留；requester/run_model、模型工具、站内查证、预算配置按用途拆分；未选远程不是自由聊天替代入口 |
| 共享查询回复 | replies的explicit_chat/named_chat/chat_text与recognizes_message/command_kind | 聊天识别/润色移出；规则识别保留。947由easter_egg_reply提前截获，须独立选项；不能静默回落普通卡ID947造成含义改变。04持有渲染段，08后顺序接线 |
| 上下文/后台 | conversation_state、runtime_tasks、operations_runtime/sources、admin/library_admin | 查询快照/目录与traits刷新保留；知识watch/refresh按选择；历史sampler交07；不得把RuntimeTasks整体当聊天任务。Console数据运维不能因有模型栏目全删 |
| 网关混合模块 | koishi/src/receiver.ts、ingress.ts、backend.ts、index.ts、management.ts/group-policies.ts | 删除定制观察/候选调度和chat API客户端，保留群/C2C、身份权限、请求、ACK、上传与限流。具体receiver改动先交02/05确认接口，不改delivery.ts/wire.ts的投递语义 |
| Console专属 | koishi/console/chat.js | 排除定制；不能仅隐藏tab |
| Console混合 | koishi/console.cjs、console/index.js/state.js/views.js/model.js/costs.js/library.js/ops.js | console.cjs注册RPC并new Management；index/state含聊天轮询、草稿恢复、主动/模型设置；按能力移除相关import/RPC/轮询/sessionStorage恢复字段。model/costs依远程/公告用途，library依本地知识；共有登录/群范围/运维保留 |
| 旧入口 | main的bot/query/repl/setup-menu；platforms/qq/qq.py/menu.py；update_notice.py | bot是另一条可绕过新组合根的运行入口；是否提供CLI/菜单工具待选。选中时同样检查产品上限；定制不得留下可启动自由聊天的legacy路径。菜单实际发布本轮未执行 |
| 公告 | application/notice_reports、notice_gateway；koishi/src/notices.ts；update_notice.py；AIClient | 查询“活动公告事实”与机器人“更新公告生成/发送”不同。生成、发送分别选；只关生成不能放行旧ready稿。四组合都需入口/队列/恢复测试 |
| 工件层 | pyproject、MANIFEST、build_offline_release/build_koishi_release、backend_sources、Console编译/映射 | 归09。pyproject find全src、data-files显式带聊天文档，MANIFEST/source archive也可带；需覆盖wheel/sdist/source归档、lib、source maps/sourcesContent、依赖、controller/rollback/安装及恢复树 |

定制随行材料排除聊天说明、故事/persona研究和夹具中的聊天实现/正文；不是删掉仓库开发测试。README/帮助/三语菜单应根据最终能力重建，不能夹带`/聊`或不可用入口。品牌图、947、研究资料的许可与产品选择分开，11的建议不是授权。

## 首版功能选择矩阵（交00）

个人列是191现有能力/需回归，不等于批准未来所有新功能。定制除“规则查询必须保留、自由/主动聊天必须裁剪”外，**下列具体入口默认都是待选择**。选中一个入口时，仍要分别选择群/C2C、区服、数据历史、图片与素材方案；未选择不能由Q0验证配置推定全部开或全不交付。

| 建议能力名（供09核名，尚非已批准manifest） | 个人191实现/入口 | 数据与限制 | 定制首版待00选择 |
|---|---|---|---|
| query.local | `/查`、`/问`、`/ask`本地语法；直接命令 | 确定性事实查询零模型；别名/三语帮助不同于数据三语完整 | 必保规则能力；具体alias含`/問`、命名/无前缀入口、群/C2C待选 |
| query.arena.overview | `/查竞技场 [region] [band] 总览` | Haneoka arena release目录，JP/intl资源身份 | 是否开放、群/C2C、jp/hk(tw)/kr/en分别选 |
| query.arena.schedule | 同上`日程 [赛季=N]` | 已发布赛季，UTC+8显示；不是全历史保证 | 同上；指定旧赛季范围单选 |
| query.arena.songs | `曲池` | 已到公开时间歌曲，属性/mission条件；未知不补公式 | 同上 |
| query.arena.ranks | `段位` | 原生段位资源/目录 | 同上；素材交付选择独立 |
| query.arena.rewards | `奖励` | 段位/奖励目录事实 | 同上 |
| query.arena.standings | `榜单 [排名=1-20]` | 上游公开records；范围上限100，精确profileId；不承诺完整终榜或边界并列 | 同上；排名/更新时间语义须消费03 |
| query.arena.population | `人数` | 上游summary，不从榜单长度推总人数 | 同上 |
| query.player.profile | `/查玩家 ID [region]` | JP最多19位且有效范围；国际服前缀/长度检查；等级可由同区progression推导 | 是否开放、群/C2C/四服；留存交07 |
| query.player.namecard | `/查玩家 ID [region] 名片 [页N]` | 页码可能稀疏；missing与empty不同；原尺寸；202/429不忙轮询、不负缓存成不存在 | 同上；图片、玩家内容材料独立选；不是账号操作 |
| query.activity.overview | `/查活动 [region]`当前/最近、ID/名称、列表 | 当前四服事件元数据；静态ID/名称/历史JP；非JP只当前/最近。资源intl不能当所有区域数据相同 | 当前入口、群/C2C/区服分别选 |
| query.activity.bonus | `/查活动 [ID/region] 加成` | project_details分组、档位与条件保留，不只摘要百分比 | 独立选 |
| query.activity.rewards | 同上`奖励` | 数量与条件成对，未知rewardID保留未知 | 独立选 |
| query.activity.missions | 同上`任务` | 仅上游明确投影，不猜缺任务 | 独立选 |
| query.activity.recruitments | 同上`卡池` | 仅明确关联；不能按时间邻近拼接 | 独立选 |
| query.activity.calendar | `日程/日程开始/下期`及本地时间问法 | JP/intl日历身份与四服排名region不是一套；不能借JP时间填intl缺口 | 独立选支持范围 |
| query.activity.history | `/查活动 历史 [名称]` | JP静态活动目录；不是榜线历史数据库 | 独立选 |
| query.public.grammar | 玩家ID/竞技场模式/本期活动加成奖励任务卡池的问法 | on_query整句语法转同一直接命令；零模型，不另造事实 | 随所选底层能力开放，不能绕过能力上限 |
| query.song/card/support/character/gacha/chart | 既有查曲/查卡/留影/角色/卡池/谱面 | 来源与支持区域各不相同，不能以P165四服推出全部四服 | 首版列表、详情、图片与群/C2C各选 |
| query.cutoff/history | 四服活动歌曲Top100、Tn、趋势/回看 | 03位置/显示rank/缺值质量合同；07 provider/2历史仍有门禁；非积分预测 | 当前榜/历史/后台采样分开选 |
| query.total/points | 总榜/积分相关能力 | 未批准；不能从已有歌曲榜推定 | 明确选或不选，后续再核实现与数据限制 |
| query.continuation | 继续、页、详情、引用锚点 | 同一作用域/有效快照/成功ACK、完整新问不消费旧话题；不保证跨服复用 | 所选查询应保留其续查；群/C2C分别验 |
| easter.card947 | 精确`/查卡 947`静态截图拦截 | 不来自卡目录；`platforms/qq/assets/card-947.jpg` | 保留/替换/排除与普通卡ID947语义必须明确 |
| knowledge.local | 受审资料检索服务 | 当前缺独立确定性QQ入口；正文/批准/撤回/再分发需单独版本 | 选或不选；不由“规则查询保留”推导 |
| knowledge.bge | 本地语义嵌入 | 独立权重/依赖锁；不是规则查询必需 | 与knowledge.local独立选，需实际权重摘要 |
| model.query_parse | 远程条件/参数提取 | 限定QuerySpec/工具、当前权限/限额、事实来自executor | 选或不选，群/C2C/额度待定 |
| model.query_explain | 对已捕获证据表达/站内辅助 | 与纯参数解析不同；有效evidence/撤回/来源/范围约束 | 不附带随意聊天，单独选用途 |
| notice.generate / notice.send | 更新公告生成/批准/发送链 | 两开关四组合；旧ready稿、恢复队列也须受发送门 | 各自选；范围与业务查询群/C2C独立 |
| chat.free / chat.proactive | 个人自由/主动聊天、记忆/观察候选 | 个人按原配置继续回归 | 已决定：定制禁止且物理裁剪 |

所有待选行还需00收口：帮助语言zh/en/ja与实际别名、命令面板容量；默认区服及可选区服；图片/原生素材/品牌；角色/谱面等既有入口是否首版；公告范围与approved-scope的announcements；每次启动批准还是独立最新批准锚点。本文不提交任何批准字段或生产范围。

## 05 / 09接口与接线顺序

09新head已提供`deploy/product_identity.py::check_scope_binding(binding,candidate,expected,observed)`，**不再把旧版三态映射缺失列成未解决**：`reject→deny`、manual保留、allow仅身份预检。binding严格为`{contract_version:1,manifest_sha256}`；evidence是C1输入摘要，不是签名或可复用授权。expected在恢复树/包外；observed由受信控制器独立测量；不能运行候选包内校验器证明自己。

05的`ScopeAuthority.identity(binding)`为同步回调；`approval(candidate,digest)`是另一独立当前批准权威。`ScopeStore.inspect/recovery`先identity，allow后才approval。`Management(directory,identity,scopeStore)`第三参数必须由受信产品身份决定注入；身份失败不能退回个人模式。groups/c2c/announcements三种范围不互代；空selected不是all。

建议接线顺序（待对应负责人实现，非本轮已完成）：

1. 02固定投递代码与CI；05适配该head及09新binding。03固定DTO，04消费03并组合05的visuals下载段。08不抢这些文件。
2. 10在受信启动/恢复边界取得当前expected、candidate摘要、组件/协议/schema测量，运行09；08消费当前会话预检快照。不要每个查询spawn Python或新增通用RPC框架。组件/实例/控制器/schema/外部期望变化、重启或恢复必须先隔离并重验，未知新鲜性manual/deny。
3. 08在`backend_bootstrap.build_backend`拆个人/定制装配，按所选能力构造纯查询依赖；先抽取共享followup/本地parser，后裁去聊天运行路径。现有Backend协议由02接线确认，不能顺手改wire/账本。
4. 网关实际Management构造在`koishi/console.cjs`，并通过共享management交receiver；在此注入ScopeStore，不能只在UI显示产品名。05提供真实approval权威接口，10掌握启动批准选择；05的setControls/reloadRecoveredConfiguration/stage须认证、CAS与现有epoch/revision guard。
5. Console同时裁import、RPC注册、菜单、轮询、恢复草稿与隐藏直达入口；后端产品上限不能被配置、旧agent key、Console RPC或恢复的旧settings提高。只有所选model/knowledge/notice页面能出现。
6. 09按最终固定commit生成精确selection，重新生成组件摘要/backend_sources和清单，构建两个独立工件；11逐层实物核验；10验证安装/恢复；最后由00决定后续集成。

仍缺：05同步回调接收受信快照的实际生命周期及失效实现；真实独立approval权威及政策选择；03/04组合证据；07存储合同与09schemas映射；08最终能力名单/入口范围；09定制工件selection、实际组件和inventory；10激活门。09身份allow或合成CLI夹具不能填这些空白。

## 入口验收清单

| 编号 | 最终候选必须验证 | 通过条件 |
|---|---|---|
| Q1 | 直接命令与/查/问/ask及选中/問，经真实Backend/receiver群与C2C入口 | 用付费调用替身计数，所选规则全零模型；精确大ID、非法区服/混服拒绝；普通未识别文本不进入聊天 |
| Q2 | 所有P165选中子项成功、pending、429、404、坏schema、过期/缺字段 | 同次捕获事实与来源；202/429不伪装不存在；03质量/排名合同保持 |
| Q3 | 当前view切换、过期anchor/lease、撤回evidence；继续/页/详情/新完整问 | 原followup两次校验、版本语义/错误码不变；02零片段失效计划与允许上下文ACK语义保留 |
| Q4 | 真实准备→渲染→编码，名片稀疏页码/原尺寸、活动分组、多语言 | 图片和完整正文来自同一结果；禁用素材时仍完整文字；04图像限制通过；QQ实机另验 |
| C1 | 有效模型key、旧agent配置、/聊/点名/普通观察/候选/直达HTTP/RPC | 定制聊天调用、后台任务、聊天正文持久写入均0；不是只检查菜单隐藏 |
| C2 | 重启、旧配置/旧Console草稿、恢复个人包、旧scope及失效快照 | 不扩大产品上限；身份/范围manual/deny保持隔离；缺scope不能回落个人模式 |
| C3 | 公告生成/发送四组合、旧ready稿；知识撤回/BGE按选择 | 独立上限无旁路；不选的模型用途零调用 |
| P1 | 个人profile实际组合根 | 既有聊天/主动/记忆、规则和续查回归；不以定制裁剪删除个人源码 |
| A1 | 09所有归档/编译/映射/回滚/安装/恢复层 | 精确字节清单无聊天实现/正文；无个人缓存/真实玩家数据；必要查询模块/数据/字体仍存在；11实物审计 |

本轮已执行固定源码读取、AST静态import及符号索引、路径/摘要校验、文档自查；结果见本地`runtime/task08-product/verification.json`。静态图会包含函数内import及未执行分支，是保守依赖上界，**不是可达性证明或最终allowlist**；Node的动态RPC/编译/包数据人工补入上述清单。本轮未改运行代码，不重复把191旧测试标为新组合测试。未执行运行拆分、产品装配、上述Q/C/P/A最终验收、实际工件/QQ/安装/恢复验收。CI状态以交付回执绑定的head和时间为准。
