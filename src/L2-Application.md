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

## Failure / Degradation Boundaries

资料初始化失败终止相应启动；缺 QQ 凭据阻止相关模式；配置缺 AI 不等于本地查询不可用。客户端只检查传输/JSON，业务 schema/实体验证在 Query，发布摘要验证见 [更新通知机制](../docs/更新通知机制.md#触发条件与处理流程)。

## Navigation to L2-2 / L3

本页 Core Contracts 是完整源码范围，直接看文件头 L3 对应五项；CLI 与安装声明见 [pyproject.toml](../pyproject.toml)，源码包内容规则见 [MANIFEST.in](../MANIFEST.in)。
不把 `data.py/commands.py/structured_query.py/ai_query.py/visuals.py` 的根路径当作本域归属依据。

## Relevant Tests

[test_install_paths.py](../tests/test_install_paths.py) 验证安装与相对路径；
[test_menu.py](../tests/test_menu.py) 的 `test_menu_setup_does_not_load_game_data_or_ai` 验证提前分流。
模型解析/错误行为分别由 [Query 测试入口](L2-Query.md) 与 [通知测试入口](../docs/更新通知机制.md#文件与测试导航) 覆盖；没有发现独立的 AIClient 传输测试文件，不声称其全部传输边界已单测覆盖。

## 当前实现与边界

五个文件均保留包根；业务门面按各自业务域导航。config 的 CONFIG_ROOT/SOURCE_ROOT 与 update_notice 的 RELEASE_SOURCE 原位保留。CLI 名称和 main 入口不变；不创建 application/shared/infrastructure 空目录。

向上阅读：[仓库 L1](../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。
