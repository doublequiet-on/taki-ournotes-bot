# Taki 开发导航

本指南导航到代码和已有专项说明，不替代实现核实，也不是 bot 运行时提示词。按任务选一行，再补读相关调用方和测试即可。源码位置已按 2026-09-30 目录整理校准，主体业务实现沿用 `5ea0f08`；不表示线上版本或上游数据已现场验证。

架构阅读从 [仓库 L1](../L1.md) 进入 [Data](../src/L2-Data.md)、[Query](../src/L2-Query.md)、[Rendering](../src/L2-Rendering.md)、[QQ](../src/L2-QQ.md)、[Application](../src/L2-Application.md)，再到必要的 L2-2 和源码文件头 L3。测试命令和专项操作仍由本指南及对应说明维护。

本地准备与启动进入 [Deploy](../deploy/L2.md)，仓库级执行工具及部署状态协议进入 [Scripts](../scripts/L2.md)。跨目录运维操作由 [自动更新说明](../自动更新说明.md)维护，运行时公告的触发、账本与许可边界进入 [更新通知机制](更新通知机制.md)。

目录阅读入口：[Tests 验证导航](../tests/L2.md)、[Docs 文档导航](L2.md)。本指南继续维护具体任务与源码映射及验证命令。

后续更新时如何选择阅读范围、同步 L3／L2-2／L2／L1 及完成文档验收，见 [GEB 使用与维护指南](GEB使用与维护指南.md)；不要求每次改动都重写所有层级。

## 调用链

路径均相对仓库根目录；下表模块名位于 `src/ournotes_bot/`，测试位于 `tests/`。

- 启动：`main.py::main` → `Settings.from_env` → `SongRepository.load` → `platforms/qq/qq.py::run_bot`。命令行 `query` 走文字分支，不能验证 QQ 图文发送。
- QQ：`run_bot` 内 `OurNotesClient.on_group_at_message_create` / `on_c2c_message_create` → `commands.py::split_commands` → `_reply_commands` → `prepare_commands` / `QueryGate.prepare` → `_prepare_reply`。群聊仅明确 @ 触发；兼容事件入口见 `register_group_message_parser`。
- 直接查询：`commands.py::resolve_command` / `handle_command` → `SongRepository`；`_prepare_reply` 将同份结果交给 `_image_from_result` → `visuals.py` → `_deliver_reply` 上传发送。绘图失败退文字，发送结果不明不自动重发。
- `/问`：`AIQueryParser.answer_with_plan` → `QueryAgent.run` → 缓存及本地命令/`parse_local_query`；未命中才走能力路由、模型结构化动作、`natural_query/query_validation.py` 校验 → `structured_query.py::resolve_query`。歌曲等级与难度先由 `query/song_conditions.py` 从用户原文提取证据，再校验模型动作；模型自由文本不能成为事实答案。

## 任务导航

活动歌曲榜线：`sources/moenotes_events.py::EventCutoffRepository` → `query/event_cutoff_query.py::execute_cutoff` → `rendering/event_cutoff_visuals.py::render_cutoff`；直接入口与自然语言本地分支共享捕获结果，QQ `_expand_replies` 预先分配整批回复预算。目标测试 `test_event_cutoffs.py`；离线三视图脚本 `scripts/preview_event_cutoffs.py`，格式、边界和 A01–A22 对应见 [功能说明](EVENT_CUTOFFS.md)。修改这些共享契约后需要完整离线回归与实际查看样图；QQ 真机验收另行授权。

| 任务 | 入口文件＋关键符号 | 相关测试文件 | 按需补读 |
|---|---|---|---|
| 成员卡、支援卡、稀有度 | `query/card_catalog.py::query_cards`、`parse_card_request`；`data.py::card_with_detail`、`support_card_with_detail`；`visuals.py::render_card`、`render_support_card` | `test_card_catalog.py`、`test_query.py`、`test_support_data.py`、`test_reply_pipeline.py` | [卡牌详情说明](更新说明-成员卡与支援卡查询.md) |
| 成员技能反查 | `natural_query/local_query.py::local_skill_question`；`structured_query.py::matching_skills`、`cards_for`；`sources/yatta.py::build_skills`、`skill_description`；`SongRepository.member_skill_index_ready` | `test_support_data.py`、`test_ai_lexicon.py` | 同上；[昵称规范](../昵称词表维护规范.md) |
| 歌曲条件、谱面与绘图 | `commands.py::song_matches`；`structured_query.py::songs_for`、`chart_for`；`platforms/qq/qq.py::_chart_image`；`sources/chart_data.py::load_chart_score`；`visuals.py::_draw_score`、`render_chart`；本地样图 `scripts/preview_visuals.py` | `test_query.py`、`test_chart_data.py`、`test_visuals.py`、`test_reply_pipeline.py` | [README 谱面说明](../README.md#谱面怎么看)、[第三方资料](../THIRD_PARTY.md) |
| Haneoka 歌曲分数表 | `sources/haneoka/song_meta.py::MetaRepository`、`parse_payload`；`query/efficiency_query.py::parse_efficiency`、`execute_efficiency`；共用 `QuerySpec` / `CommandResult` / `QueryResult` 和 `visuals.py::render_meta`、`_render_meta_table`（`MetaAnswer` 捕获数据，全难度逐行表格；默认前 30 条，同曲不同难度分别计数；基础行高紧凑，长标题或多段激奏按内容增高） | `test_song_meta.py`、`test_visuals.py`；修改共享路由后跑完整离线测试 | [README 歌曲效率](../README.md#歌曲效率怎么看)、[第三方资料](../THIRD_PARTY.md)；独立缓存，不改主缓存 schema |
| 自然语言、同义问法 | `natural_query/local_query.py::parse_local_query`；`natural_query/query_agent.py::QueryAgent.run`；`natural_query/query_capabilities.py::local_route`；`natural_query/query_validation.py::validate_capability_action`；`natural_query/query_terms.py::is_skill_placeholder`；`query/song_conditions.py::extract_song_conditions`；`structured_query.py::QuerySpec`、`QueryResult` | `test_query.py`、`test_ai_lexicon.py`、`test_query_refactor.py`、`test_natural_query_eval.py` | [Agent 专项方案](自然语言查询Agent重构方案.md)（含重构前背景，以代码确认现状） |
| AI 额度、统计、超时 | `ai_client.py::AIClient.request`；`natural_query/ai_quota.py::DailyQuota.reserve`；`natural_query/query_metrics.py::QueryMetrics`；`natural_query/query_debug.py::QueryDebugCounters` | `test_stage5_limits.py`、`test_query_refactor.py`、`test_query_debug.py`、`test_observability.py` | [README 自然语言与设置](../README.md#自然语言数据与运行设置) |
| 数据接入、缓存、别名 | `sources/yatta.py::fetch_json`、`build_data`、`build_support_cards`；`data.py::SongRepository.load`、`refresh`、`_save_cache`、`_load_cache`；`query/entity_lexicon.py::resolve_exact_alias`；`query_aliases.json` | `test_query.py`、`test_support_data.py`、`test_status.py`、`test_ai_lexicon.py` | [第三方资料](../THIRD_PARTY.md)、[昵称规范](../昵称词表维护规范.md) |
| QQ 触发、回复顺序、媒体 | `platforms/qq/qq.py::register_group_message_parser`、`QueryGate`、`ReplySequencer`、`_prepare_reply`、`_deliver_reply`；`platforms/qq/menu.py::setup_menu`、`PANEL_ITEMS`、`GROUP_PANEL_ITEMS` | `test_media.py`、`test_multi_command.py`、`test_reply_order.py`、`test_reply_pipeline.py`、`test_menu.py` | [QQ 接入与验收](../LOCAL_QQ_TEST.md)；单聊与群聊面板分别维护，群聊项按中文 `/帮助` 覆盖 |
| 游戏业务与平台边界 | `commands.py::CommandResult`；`structured_query.py::QuerySpec`、`QueryResult`；`ai_query.py::AIQueryParser`；`visuals.py::render_song_list` | `test_platform_boundary.py`（独立进程禁止导入 QQ SDK/接入模块，使用临时数据验证查询与绘图） | [开发边界约定](../AGENTS.md#边界与生产安全)；`platforms/qq/qq.py::_prepare_reply` 不接收消息对象，但导入所在模块仍加载 QQ SDK；核心业务不依赖它，暂留原位，有实际复用需要时再局部提取 |
| 配置与安装路径 | `config.py::Settings.from_env`、`CONFIG_ROOT`、`runtime_data_dir`；`main.py::build_parser`；`.env.example`、`pyproject.toml` | `test_install_paths.py`、`test_stage5_limits.py` | [README 快速开始](../README.md#快速开始) |
| Windows 更新、回退、群通知 | `scripts/update_bot.py::Updater`、`ci_passed`、`validation_env`；`scripts/install-update-task.ps1`、`deploy/start-bot.ps1`；`update_notice.py::NoticeStore`、`UpdateNotifier` | `test_updater.py`、`test_reply_pipeline.py` | 操作与排查：[自动更新说明](../自动更新说明.md)；工具与部署协议：[Scripts 地图](../scripts/L2.md)；启动分流：[Deploy 地图](../deploy/L2.md)；公告行为：[更新通知机制](更新通知机制.md)。`deploy/README.md` 说明本地入口，不能代替 Windows 更新流程 |

## 数据、配置与易错点

- 歌曲颜色／激奏扩展：`sources/haneoka/song_traits.py::SongTraitsRepository` 从 Haneoka 日服按release取完整详情，独立缓存，不改变旧主缓存歌曲行；`Song.traits` 是内存关联。`query/song_query.py::parse_filter/execute/local_query` 共用于直接命令和 `/问`；`QuerySpec.song_query` 仅本地解析产生，不向模型开放任意新字段。新语法经 `CommandResult/QueryResult.song_selection` 捕获同一份结果和覆盖提示，图文不二次查询。QQ在独立后台任务每5分钟检查、快照TTL24小时；来源故障不阻塞原有数据刷新或消息处理。`visuals.py::MISSION_ICON_URLS`、`_mission_marks` 运行时按需读取游戏原生 JUST／COMBO／LUCK 轮廓图标并复用普通素材缓存，缺图只退类型文字。相关测试 `test_song_traits.py`、`test_visuals.py`；事实样例、图标路径和字段依据见 THIRD_PARTY。
- `bot_info.py` 是消息内介绍文案，`/帮助` 在 commands.py，QQ面板描述在 platforms/qq/menu.py；修改源代码不会自动修改QQ平台资料页简介。`setup-menu` 不再初始化游戏缓存或 AI，仅在新版本已上线后分别核对并安装单聊、群聊面板；内容未变时不重复写入，面板发布仍属于真实 QQ 外部操作。

- 基础歌曲/卡牌资料：`sources/yatta.py` 的 `BASE`、`MASTER`；`SongRepository.refresh` 校验 Project Yume 来源，并构建成员技能索引。完整音符另由 `sources/chart_data.py::CHART_BASE` 读取 MoeNotes 公共资源，`score_name` 限定已知 ID/难度映射。本次不扩展来源，不把外部研究目录当正式接口。
- 主缓存由 `OURNOTES_CACHE_FILE` 指定；`CACHE_SCHEMA`、旧字段兼容、详情失败回退见 `data.py`。TTL 由 `OURNOTES_CACHE_TTL_HOURS` 控制；QQ 后台刷新间隔目前在 `platforms/qq/qq.py::refresh_loop` 固定为六小时，不由此变量控制。
- 谱面缓存默认 `runtime_data_dir()/chart-cache`，不一定随自定义主缓存路径移动；图片素材缓存见 `visuals.py::_asset`，字体选择见 `_font`。长图自下向上、各栏从左向右；节点数不能直接当判定数或算分公式依据。
- 卡牌映射 SSR=四星、SR=三星、R=二星，数字 ID 不当星级；技能按 `sources/yatta.py::skill_description` 的 Lv.5 默认值展示，不代表玩家培养状态。技能索引不完整须保留提示；两类详情同步构建分类索引，`card_catalog_version` 控制旧缓存升级，顶层 `card_catalog` 保持旧卡牌行兼容。
- `.env.example` 列出变量用途；`QQ_APP_ID`/`QQ_APP_SECRET` 用于 QQ，`AI_API_KEY`/`AI_BASE_URL`/`AI_MODEL` 为可选模型设置。源码配置根与普通安装包启动目录有区别，见 `CONFIG_ROOT`。不要为文档读取真实 `.env`。
- `OURNOTES_AI_QUOTA_FILE` 是持久额度，按北京时间自然日、每次真实模型请求前计数；`OURNOTES_AI_METRICS_FILE` 是匿名分类统计；调试计数仅进程内。三者不可混淆，也不可删记录来“修复额度”。依据：`DailyQuota.reserve`、`QueryMetrics`、`QueryDebugCounters`。
- 更新器共享配置、主缓存、额度及通知数据库；代码回退不会还原这些数据。状态记录不能证明进程在线或 QQ 送达；详见自动更新说明，真实验收只在获得对应授权后执行。

## 功能状态与产品要求

| 范围 | 当前状态与依据 |
|---|---|
| 卡牌/技能查询 | 已实现两类卡条件网格、精确ID详情、独立卡面及多维分类筛选；BD映射和原生框素材仍缺依据。见 `structured_query.py::cards_for`、`support_cards_for` 与数据索引。 |
| 谱面 | 已实现静态完整音符绘图和缺失回退；局部放大、播放模拟未实现。见 `render_chart`。 |
| 直接查询与 AI 分工 | 已实现直接查询不调用模型、本地优先的受限 `/问`；不是自由问答。见 `_prepare_reply`、`QueryAgent.run`。 |
| 活动歌曲榜线、预测、活动/卡池 | `/榜线` 已实现四服当前／最近活动挑战歌曲 Top 100 观测；不提供历史选择、积分档线或预测。活动、卡池、预测命令仍为未开放占位，见 `commands.py::UNAVAILABLE_COMMANDS`；榜线约束见 [专题](EVENT_CUTOFFS.md)。 |
| 算分、配队计算 | 未发现可用命令或计算模块；卡牌数值/技能展示不能视为计算器，也不能证明公式正确。候选规则与实测假设须另行验证，本次不研究。 |
| 攻略资料与知识库 | 审核后入库是产品约束；正式审核库、采集和审核流程未实现，见 README 长期方向。未来回答须用可靠数据及审核资料，不能把规划写成已落地。 |

## 开发与验证命令

在**目标仓库根目录的 Windows PowerShell** 执行。Python >=3.10，项目依赖见 `pyproject.toml`；已有环境优先复用。缺环境时按 README 创建 `.venv` 并 `pip install -e .`（会安装依赖，本轮未执行）。绘图测试需要可用 CJK 字体；CI 使用 Noto CJK。

```powershell
# 在专用测试终端设置，不改持久环境；空值阻止 dotenv 补入凭据
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -B -c "import os,unittest; os.environ.update(QQ_APP_ID='',QQ_APP_SECRET='',AI_API_KEY=''); suite=unittest.defaultTestLoader.discover('tests',pattern='test_query.py'); raise SystemExit(not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful())"
# 全量离线测试（需要扩大范围或准备发布时）
.\.venv\Scripts\python.exe -B -c "import os,unittest; os.environ.update(QQ_APP_ID='',QQ_APP_SECRET='',AI_API_KEY=''); suite=unittest.defaultTestLoader.discover('tests'); raise SystemExit(not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful())"
# CLI 参数冒烟：解析帮助后退出，不加载配置/数据
.\.venv\Scripts\python.exe -B -m ournotes_bot.main --help
```

单模块测试替换 `pattern` 为导航表对应文件；入口依据为 CI 的 `unittest discover -s tests -q`，这里用等价 loader 在 Python 进程内清空凭据，避免 Windows PowerShell 空环境变量处理差异。测试使用桩和临时数据；新增测试同样不得依赖生产缓存或收费服务。不要把 `query`/`repl` 当纯只读离线验证：启动会 `SongRepository.load`，可能刷新缓存；`/问` 还可能调用模型。

CI 完整配置在 [.github/workflows/checks.yml](../.github/workflows/checks.yml)：Python 3.10/3.12、离线测试、构建并检查包。涉及打包时，在隔离开发环境执行现有命令：

```powershell
python -m pip wheel . --no-deps --no-build-isolation -w dist
python -c "from setuptools.build_meta import build_sdist; build_sdist('dist')"
python scripts/check_release_artifact.py dist
```

使用已安装项目依赖和 `setuptools>=68` 的 Python；命令生成 `dist` 等构建产物，不属于普通文档检查。`MANIFEST.in` 当前未列入本开发指南和 AGENTS，它们面向 Git 源码开发；如将来要求随包分发，再单独调整打包规则。

图片样式检查可用 `PYTHONPATH=src` 执行 `python -B scripts/preview_visuals.py --cache-dir <公开缓存副本目录> --output runtime/visual-preview/round1 --stress`。输入需含 `ournotes-cache.json`，以及可选的 `asset-cache/`、`chart-cache/`、`haneoka-meta-jp.json`；不复制凭据、日志或额度。脚本只读缓存，阻止网络连接，不启动 QQ、不调用模型、不刷新资料；缺素材时保留占位。可选 `preview-details/support-<ID>.json` 是与该缓存支援卡 ID 对应的公开详情，用于离线预览。输出须在输入目录之外，含原尺寸 JPEG、430px 宽检查图、概览和输入哈希记录；`--baseline` 仅用 `git show HEAD:src/ournotes_bot/visuals.py` 读取已提交绘图代码作比较。自动检查不能代替逐张检查手机字号、长标题、分页说明及真实卡面。样图与缓存不提交仓库。

绘图布局采用逻辑像素，导出默认按 `RENDER_SCALE=2` 原生绘制字体、边线及图标；素材按导出尺寸从原缓存取样。圆角、斜线和素材裁切遮罩额外采用 3 倍局部采样抗锯齿，按覆盖面积缩回，避免模糊整张图片；长谱面斜线分段绘制以控制临时内存。JPEG 使用质量 95 和 4:4:4 色彩采样。验证布局时区分逻辑坐标与实际像素，`manifest.json` 记录的是实际导出尺寸；检查高清细节请打开原图，430px 预览仅用于手机布局检查。固定背景图块随渲染倍率绘制，图案相对布局的大小不随画布宽高变化。

纯 Markdown 变更检查链接、路径、符号及 `git diff --check`；新文件还要看 `git status --short`，因为普通 `git diff` 不显示未跟踪内容。不为文档跑生产服务、同步、更新器或 QQ 验收。功能变更先跑相关测试，共享接口、数据兼容或部署变更再扩大到完整测试与必要的真实链路。未执行项如实记录。

## 文档维护

根 [AGENTS.md](../AGENTS.md) 是短入口；本指南维护任务映射；[README](../README.md) 维护用户能力与路线图；[交接文档](../交接文档.md) 保留交接入口；本机 `项目日志.md`（未随仓库发布）仅供需要历史证据且文件存在时查阅。不每次追加开发流水账，不复制专项规格。入口、接口、来源、关键约束或验证方式变化时，才更新对应段落。旧方案中的基线描述与代码不符应明确区分，产品要求继续有效。

- 卡牌列表与详情通过 `rendering/card_visuals.py` 复用 `visuals.py::_canvas` 的统一布纹、圆角边框、配色与两倍分辨率绘制；不得另建独立主题。纯卡面输出保持原图比例。共享视觉基线来自主线 `6d7f489`（PR #18–20）；后续集成需保留主线样式更新。相关离线检查：`test_visuals.py`、`test_card_catalog.py`。

- 角色卡条件列表专用视觉位于 `rendering/member_list_visuals.py`（`card_visuals.grid` 的成员卡分支）：技能摘要为框内队长/演出/激奏三项渐变信息层，ID 为框外附属栏；稀有度边框是设计处理，并非已取得官方卡框素材。正式列表不放资源/实现说明；保留分页与旧缓存提示。`test_member_list_visuals.py` 验证信息完整、长条件换行及 SNAP 分流不变。数据入口 `sources/haneoka/haneoka_members.py` / 已核实机制指纹 `sources/haneoka/haneoka_member_contracts.py`，单独缓存 `haneoka-member-list-jp.json`；`test_haneoka_members.py` 验证数值、条件、映射及缓存故障。未知机制先核实再扩充指纹，不能直接接受新摘要。

- 成员ID详情图入口为 `rendering/member_detail_visuals.py::render`，仅由 `card_visuals.detail` 的成员卡分支调用。完整full卡面按比例放入列表共用的可变尺寸稀有度框，下方分区展示基本资料、已确认状态的属性条、三类完整技能和多语言标题；SNAP由 `rendering/support_visuals.py` 独立绘制列表/详情。详情仍使用现有Project Yume完整字段，不把列表Haneoka摘要当完整技能；`test_member_detail_visuals.py` 检查卡面四角不裁切、完整文本、未知值和SNAP分流。

- SNAP视觉入口 `rendering/support_visuals.py::render_list/render_detail`，由 `rendering/card_visuals.py` 按列表/详情分流；共用主题、条件标签、稀有度框及分区布局。保留EX重复演出支援、明确激奏不适用，不合计百分比属性为综合力。离线检查 `test_support_visuals.py`，本地QQ命令见验收文档。

- `rendering/support_summary.py` 从现有Lv.5中文技能效果做完整模板匹配，仅输出基础效果及触发/上限，条件加成留详情；新措辞必须先核实。`member_detail_visuals._panel` 是两类详情共用的白底/标题带绘制，不影响列表或数据逻辑。

- 所有渲染器经 `visuals._bytes` → `image_output.encode_image` 统一编码预算（1.5MB/8192边长/1200万像素），不在各命令复制压缩代码。`qq._upload_image` 单次上传30秒，HTTP临时副本必须解除共享会话引用；测试 `test_image_output.py`、`test_media.py`、`test_reply_pipeline.py`。发布依据与可溯源说明见 [2026-09-28报告](RELEASE_2026-09-28.md)。

歌曲信息图共用 `visuals._song_heading`（属性图标＋标题）、`_mission_marks` / `_mission_chip`（原生激奏图标、顺序与缓存提示），先测量再排版；缺图只退文字，不改变筛选。列表和谱面身份区置于左下角，分数表读取同一 `MetaAnswer.song_records` 快照，以 112 逻辑像素为基础行高并按标题、激奏内容自动增高。歌曲筛选与统一布局的发布依据见 [歌曲更新报告](RELEASE_2026-09-28_SONGS.md)；原生激奏图标、紧凑分数表与群聊面板的后续实现以 `5ea0f08`（PR #25）为准。
