# L2 Application Runtime — 启动与运行支撑

## Purpose

定义一个 Taki 进程如何选择模式、获得配置并进入业务/平台运行，以及共享运行工具的使用边界。它是应用外壳的地图，不是业务剩余文件的收纳域。

## Responsibilities

CLI 模式与组合、配置根/数据路径、包身份；为多个系统域提供无业务反向依赖的 AI 传输和文案查找叶子。组合根与共享叶子的调用方向必须分开描述。

## Non-Responsibilities

不拥有领域模型、查询结果、查询额度、渲染原语或发布通知。业务兼容入口仍归原域；`bot_info.py` 只被直接命令使用，归 Query。没有独立“Shared Boundary” L2 或通用服务容器。

## Inputs / Outputs

输入为 CLI 参数、环境/配置模板规定的值；输出为 Settings、运行路径、所选模式和进程输出。
共享 AI 客户端把提示转为受限 JSON 响应与 usage；文案工具把键/locale/参数转为文本，均不决定业务事实。

## Core Contracts

- [main.py](ournotes_bot/main.py)：`build_parser/main`；命令入口由 [pyproject.toml](../pyproject.toml) 声明。
- [config.py](ournotes_bot/config.py)：`Settings.from_env`、`CONFIG_ROOT/SOURCE_ROOT/runtime_data_dir`；配置项只以代码和 [.env.example](../.env.example) 为准。
- [ai_client.py](ournotes_bot/ai_client.py)：`AIClient.request → ModelResponse` 与客户端异常；调用策略分别归 Query 和 [运行时通知专题](../docs/更新通知机制.md)。
- [i18n.py](ournotes_bot/i18n.py)：`tr`，支持已有语言表，缺 locale 退中文。
- [__init__.py](ournotes_bot/__init__.py)：包版本身份。

- [application/reply_preparation.py](ournotes_bot/application/reply_preparation.py)：捕获同次歌曲／谱面资料与原回退文字；结果仅含领域对象、绘图输入及文案，不含 QQ 身份或交付状态。测试见 [test_reply_preparation.py](../tests/test_reply_preparation.py)。
- [application/replies.py](ournotes_bot/application/replies.py)：共享只读命令识别、完整回复准备、QueryGate、分页和 DeliveryCursor；旧 QQ 导出兼容名称，后端直接调用公开入口，不导入 botpy。图文与备用文字复用同次结果；日志在后端模式隐藏正文。
- [application/backend.py](ournotes_bot/application/backend.py)、[backend_bootstrap.py](ournotes_bot/application/backend_bootstrap.py)：M2/M3 常驻实例、loopback HTTP、代次撤销与卸载 ACK 门禁、接纳高水位、准备到 ACK 的续查租约；[backend_protocol.py](ournotes_bot/application/backend_protocol.py) 校验线协议，[delivery_ledger.py](ournotes_bot/application/delivery_ledger.py) 负责独占 SQLite 意图/回执事实。详见 [后端协议与阶段边界](../docs/KOISHI_BACKEND_V1.md)。

## Internal Subsystems

组合根与四个运行支撑叶子直接 L2 → L3。没有足够复杂度和统一新生命周期来为每个叶子建立 L2-2；共享并不意味着相互调用。

## Dependencies

`main → Query/Data/QQ`；其他域可依赖 config/ai_client/i18n 叶子，但不导入 main。
因此域级缩略图可能画出双向箭头，不能误判为模块循环，也不能把整个 Application 包作为下层服务依赖。
共享工具不反向引用业务域。

## State / Side Effects


配置加载读取 dotenv 并通过 `setdefault` 补入进程环境，不能描述为完全无写状态。
除 setup-menu 外，模式都会构造仓库/AI 门面并调用 `load`，可能网络与缓存写入；setup-menu 提前分流但有远端管理副作用。
bot 模式在 `qq.run_bot` 内重新组装实际使用的 AI/限流/通知组件，Current 并非单一集中组合根。
backend 在公共仓库初始化前独立分流，只组装一次业务实例；异步初始化后 ready。M2 尚未接管周期刷新/采样/通知，不能作为生产切换完成的依据。

## Failure / Degradation Boundaries

资料初始化失败终止相应启动；缺 QQ 凭据阻止相关模式；配置缺 AI 不等于本地查询不可用。客户端只检查传输/JSON，业务 schema/实体验证在 Query，发布摘要验证见 [更新通知机制](../docs/更新通知机制.md#触发条件与处理流程)。

## Navigation to L2-2 / L3

本页 Core Contracts 是完整源码范围，直接看文件头 L3 对应运行支撑与回复准备；CLI 与安装声明见 [pyproject.toml](../pyproject.toml)，源码包内容规则见 [MANIFEST.in](../MANIFEST.in)。
不把 `data.py/commands.py/structured_query.py/ai_query.py/visuals.py` 的根路径当作本域归属依据。

## Relevant Tests

[test_install_paths.py](../tests/test_install_paths.py) 验证安装与相对路径；
[test_menu.py](../tests/test_menu.py) 的 `test_menu_setup_does_not_load_game_data_or_ai` 验证提前分流。
模型解析/错误行为分别由 [Query 测试入口](L2-Query.md) 与 [通知测试入口](../docs/更新通知机制.md#文件与测试导航) 覆盖；没有发现独立的 AIClient 传输测试文件，不声称其全部传输边界已单测覆盖。

## 当前实现与边界

五个文件均保留包根；业务门面按各自业务域导航。config 的 CONFIG_ROOT/SOURCE_ROOT 与 update_notice 的 RELEASE_SOURCE 原位保留。CLI 名称和 main 入口不变；新增的非空 application 包仅负责歌曲／谱面回复准备，不扩展通用容器。

向上阅读：[仓库 L1](../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。

- M4 运行叶子：`application/runtime_tasks.py` 管理后台任务，`data_owner.py` 在写入前锁定业务目录，`notice_reports.py` 复用原报告/账本，`notice_gateway.py` 实现群版本与主动领取/意图/ACK。旧 QQ 通知模块保留运输适配，后台不导入 botpy。
- Console 管理读取：`application/admin.py` 投影既有账本事实；`diagnostics.py` 独立、有界、可降级地观察元数据和单调耗时，不授予发送许可。运行与组合构建共用 `backend_sources.py` 摘要清单。接口、隐私及缺失口径见 [Console 契约](../docs/koishi/CONSOLE_V1.md)。

## 管理任务

Backend 的 operations.py 拥有独立有界运维任务库与单执行器；与发送/通知账本隔离。Admin 提供脱敏导出。Console RPC 与网关管理审计契约见 [运维控制台](../docs/koishi/OPERATIONS.md)。
`operations_sources.py` 管理快照绑定的资料／素材任务；`operations_runtime.py` 提供固定的任务恢复、活动、采样和连接检查。`runtime_tasks.py` 统一手动／定时资源锁与进程内观测，不提供系统进程控制。
