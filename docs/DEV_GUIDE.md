# Taki 开发导航

本指南导航到代码和已有专项说明，不替代实现核实，也不是 bot 运行时提示词。按任务选一行，再补读相关调用方和测试即可。以下现状以 2026-09-27 本仓库 `eddee55` 为核实基线，不表示线上版本或上游数据已现场验证。

## 项目共同边界

- 本项目仅服务 Our Notes 日服；独立的官方 X 推文翻译机器人不在此仓库。其他游戏机制、研究假设和路线图不能直接当作已验证的 ON 规则或已实现功能。
- 直接查询不调用大模型；`/问` 本地优先，模型只提取受限条件，事实由数据检索产生。攻略资料须审核后才能进入正式资料库。
- 新功能尽量让游戏业务使用明确输入和结果结构，QQ 接入层负责事件、平台权限、上传和发送；按实际任务小步调整，不为设想中的其他平台全仓重构。
- Windows 自动更新跟随 `main`，合并可能触发部署；版本共享缓存和额度，代码回退不回退数据。发布前依照[自动更新说明](../自动更新说明.md)验证兼容性及回退。

## 调用链

路径均相对仓库根目录；下表模块名位于 `src/ournotes_bot/`，测试位于 `tests/`。

- 启动：`main.py::main` → `Settings.from_env` → `SongRepository.load` → `qq.py::run_bot`。命令行 `query` 走文字分支，不能验证 QQ 图文发送。
- QQ：`run_bot` 内 `OurNotesClient.on_group_at_message_create` / `on_c2c_message_create` → `commands.py::split_commands` → `_reply_commands` → `prepare_commands` / `QueryGate.prepare` → `_prepare_reply`。群聊仅明确 @ 触发；兼容事件入口见 `register_group_message_parser`。
- 直接查询：`commands.py::resolve_command` / `handle_command` → `SongRepository`；`_prepare_reply` 将同份结果交给 `_image_from_result` → `visuals.py` → `_deliver_reply` 上传发送。绘图失败退文字，发送结果不明不自动重发。
- `/问`：`AIQueryParser.answer_with_plan` → `QueryAgent.run` → 缓存及本地命令/`parse_local_query`；未命中才走能力路由、模型结构化动作、`query_validation.py` 校验 → `structured_query.py::resolve_query`。模型自由文本不能成为事实答案。

## 任务导航

| 任务 | 入口文件＋关键符号 | 相关测试文件 | 按需补读 |
|---|---|---|---|
| 成员卡、支援卡、稀有度 | `commands.py::card_matches`、`support_card_matches`、`split_card_rarity`；`data.py::card_with_detail`、`support_card_with_detail`；`visuals.py::render_card`、`render_support_card` | `test_query.py`、`test_support_data.py`、`test_reply_pipeline.py` | [卡牌详情说明](../更新说明-成员卡与支援卡查询.md) |
| 成员技能反查 | `local_query.py::local_skill_question`；`structured_query.py::matching_skills`、`cards_for`；`yatta.py::build_skills`、`skill_description`；`SongRepository.member_skill_index_ready` | `test_support_data.py`、`test_ai_lexicon.py` | 同上；[昵称规范](../昵称词表维护规范.md) |
| 歌曲条件、谱面与绘图 | `commands.py::song_matches`；`structured_query.py::songs_for`、`chart_for`；`qq.py::_chart_image`；`chart_data.py::load_chart_score`；`visuals.py::_draw_score`、`render_chart` | `test_query.py`、`test_chart_data.py`、`test_reply_pipeline.py` | [README 谱面说明](../README.md#谱面怎么看)、[第三方资料](../THIRD_PARTY.md) |
| 自然语言、同义问法 | `local_query.py::parse_local_query`；`query_agent.py::QueryAgent.run`；`query_capabilities.py::local_route`；`query_validation.py::validate_capability_action`；`structured_query.py::QuerySpec`、`QueryResult` | `test_query.py`、`test_ai_lexicon.py`、`test_query_refactor.py` | [Agent 专项方案](../自然语言查询Agent重构方案.md)（含重构前背景，以代码确认现状） |
| AI 额度、统计、超时 | `ai_client.py::AIClient.request`；`ai_quota.py::DailyQuota.reserve`；`query_metrics.py::QueryMetrics`；`query_debug.py::QueryDebugCounters` | `test_stage5_limits.py`、`test_query_refactor.py`、`test_query_debug.py`、`test_observability.py` | [README 自然语言与设置](../README.md#自然语言数据与运行设置) |
| 数据接入、缓存、别名 | `yatta.py::fetch_json`、`build_data`、`build_support_cards`；`data.py::SongRepository.load`、`refresh`、`_save_cache`、`_load_cache`；`entity_lexicon.py::resolve_exact_alias`；`query_aliases.json` | `test_query.py`、`test_support_data.py`、`test_status.py`、`test_ai_lexicon.py` | [第三方资料](../THIRD_PARTY.md)、[昵称规范](../昵称词表维护规范.md) |
| QQ 触发、回复顺序、媒体 | `qq.py::register_group_message_parser`、`QueryGate`、`ReplySequencer`、`_prepare_reply`、`_deliver_reply`；`menu.py::setup_menu` | `test_media.py`、`test_multi_command.py`、`test_reply_order.py`、`test_reply_pipeline.py`、`test_menu.py` | [QQ 接入与验收](../LOCAL_QQ_TEST.md) |
| 游戏业务与平台边界 | `commands.py::CommandResult`；`structured_query.py::QuerySpec`、`QueryResult`；`ai_query.py::AIQueryParser`；`visuals.py::render_song_list` | `test_platform_boundary.py`（独立进程禁止导入 QQ SDK/接入模块，使用临时数据验证查询与绘图） | [项目共同边界](#项目共同边界)；`qq.py::_prepare_reply` 不接收消息对象，但导入所在模块仍加载 QQ SDK；核心业务不依赖它，暂留原位，有实际复用需要时再局部提取 |
| 配置与安装路径 | `config.py::Settings.from_env`、`CONFIG_ROOT`、`runtime_data_dir`；`main.py::build_parser`；`.env.example`、`pyproject.toml` | `test_install_paths.py`、`test_stage5_limits.py` | [README 快速开始](../README.md#快速开始) |
| Windows 更新、回退、群通知 | `scripts/update_bot.py::Updater`、`ci_passed`、`validation_env`；`scripts/install-update-task.ps1`、`start-bot.ps1`；`update_notice.py::NoticeStore`、`UpdateNotifier` | `test_updater.py`、`test_reply_pipeline.py` | [自动更新说明](../自动更新说明.md)；`deploy/README.md` 仅通用模板，不能代替 Windows 流程 |

## 数据、配置与易错点

- 基础歌曲/卡牌资料：`yatta.py` 的 `BASE`、`MASTER`；`SongRepository.refresh` 校验 Project Yume 来源，并构建成员技能索引。完整音符另由 `chart_data.py::CHART_BASE` 读取 MoeNotes 公共资源，`score_name` 限定已知 ID/难度映射。本次不扩展来源，不把外部研究目录当正式接口。
- 主缓存由 `OURNOTES_CACHE_FILE` 指定；`CACHE_SCHEMA`、旧字段兼容、详情失败回退见 `data.py`。TTL 由 `OURNOTES_CACHE_TTL_HOURS` 控制；QQ 后台刷新间隔目前在 `qq.py::refresh_loop` 固定为六小时，不由此变量控制。
- 谱面缓存默认 `runtime_data_dir()/chart-cache`，不一定随自定义主缓存路径移动；图片素材缓存见 `visuals.py::_asset`，字体选择见 `_font`。长图自下向上、各栏从左向右；节点数不能直接当判定数或算分公式依据。
- 卡牌映射 SSR=四星、SR=三星、R=二星，数字 ID 不当星级；技能按 `yatta.py::skill_description` 的 Lv.5 默认值展示，不代表玩家培养状态。技能索引不完整须保留提示；支援详情按需获取，不等于已有支援技能反查索引。
- `.env.example` 列出变量用途；`QQ_APP_ID`/`QQ_APP_SECRET` 用于 QQ，`AI_API_KEY`/`AI_BASE_URL`/`AI_MODEL` 为可选模型设置。源码配置根与普通安装包启动目录有区别，见 `CONFIG_ROOT`。不要为文档读取真实 `.env`。
- `OURNOTES_AI_QUOTA_FILE` 是持久额度，按北京时间自然日、每次真实模型请求前计数；`OURNOTES_AI_METRICS_FILE` 是匿名分类统计；调试计数仅进程内。三者不可混淆，也不可删记录来“修复额度”。依据：`DailyQuota.reserve`、`QueryMetrics`、`QueryDebugCounters`。
- 更新器共享配置、主缓存、额度及通知数据库；代码回退不会还原这些数据。状态记录不能证明进程在线或 QQ 送达；详见自动更新说明，真实验收只在获得对应授权后执行。

## 功能状态与产品要求

| 范围 | 当前状态与依据 |
|---|---|
| 卡牌/技能查询 | 已实现成员/支援卡详情与稀有度、成员技能反查；支援技能反查未实现。见 `structured_query.py::cards_for`、`support_cards_for` 与数据索引。 |
| 谱面 | 已实现静态完整音符绘图和缺失回退；局部放大、播放模拟未实现。见 `render_chart`。 |
| 直接查询与 AI 分工 | 已实现直接查询不调用模型、本地优先的受限 `/问`；不是自由问答。见 `_prepare_reply`、`QueryAgent.run`。 |
| 档线、预测、活动/卡池 | 当前/历史档线与预测未实现；活动、卡池、预测命令为未开放占位，见 `commands.py::UNAVAILABLE_COMMANDS`。 |
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

使用已安装项目依赖和 `setuptools>=68` 的 Python；命令生成 `dist` 等构建产物，不属于普通文档检查。`MANIFEST.in` 当前未将本开发指南纳入安装包；本指南面向 Git 源码开发。如将来要求随包分发，再单独调整打包规则。

纯 Markdown 变更检查链接、路径、符号及 `git diff --check`；新文件还要看 `git status --short`，因为普通 `git diff` 不显示未跟踪内容。不为文档跑生产服务、同步、更新器或 QQ 验收。功能变更先跑相关测试，共享接口、数据兼容或部署变更再扩大到完整测试与必要的真实链路。未执行项如实记录。

## 文档维护

本指南维护项目共同边界与任务映射；个人 Codex 工作约定由各自在本地配置；[README](../README.md) 维护用户能力与路线图；[交接文档](../交接文档.md) 保留交接入口；本机 `项目日志.md`（未随仓库发布）仅供需要历史证据且文件存在时查阅。不每次追加开发流水账，不复制专项规格。入口、接口、来源、关键约束或验证方式变化时，才更新对应段落。旧方案中的基线描述与代码不符应明确区分，产品要求继续有效。
