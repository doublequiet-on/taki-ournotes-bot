# 架构门禁与渐进迁移

Taki 保持模块化单体。新业务沿既有 Query、Data、Rendering、Natural、QQ 边界开发；只在真实复用或测试需要时提取契约、编排和组件。歌曲分数表已删除，其切片迁移计划取消。

## 执行与规则

在 checkout 根目录执行 `python -B scripts/check_architecture.py`。检查使用 Python 3.10 以上的标准库 AST，不执行或导入业务源码，无新增运行依赖。`tests/test_architecture.py` 包含源码变异负例；已有 `test_platform_boundary.py` 继续在新解释器中禁止 QQ 导入与真实网络。

| 规则 | 当前约束 |
|---|---|
| `core-no-qq` | QQ 适配、发布通知适配与组合入口之外，禁止导入 `botpy`、`platforms` 或 `update_notice` |
| `no-bootstrap-backedge` | 核心不能反向导入 `main` / `bootstrap` |
| `pure-contract` | `domain/**`、Query/Rendering 的 `contracts`（含专项子目录）、`query/pagination`、现有纯 JSON/异常/支援摘要工具只依赖纯模块与标准库，禁止直接网络库 |
| `source-foundation` | `sources/haneoka/transport` / `identity` 只能依赖纯模块与同类公共设施，不能借用具体资源适配器 |
| `source-direction` | 来源不依赖查询、绘图、QQ 或业务门面；保留配置读取与已登记的领域记录债务 |
| `query-direction` | 确定性查询不新增具体来源、网络库、绘图、Natural 或旧命令入口依赖 |
| `rendering-direction` | 绘图不新增具体来源或查询执行模块依赖；新视图契约可放在纯 contracts 中 |
| `private-boundary` | 跨职责、跨旧门面及来源适配器之间的私有符号逐项登记；同一 Query/Rendering 子系统的旧兄弟组件暂不全面收紧 |
| `dynamic-import` | 可解析的动态导入使用同一规则；不能解析目标的动态导入失败 |

普通、相对、函数内及 TYPE_CHECKING 导入都参与检查。识别 `import_module` / `__import__` 常量目标、模块别名、私有属性与对模块的常量 `getattr`。扫描所有新增源码文件，没有目录 ignore；纯契约和公共设施规则在文件实际出现时生效，不预建空目录。

这是一版直接导入和可解析私有访问门禁。纯模块链中的每个反向导入都会被扫描，但旧查询经 `data` 门面的传递依赖尚未清理。任意反射、对象方法调用、别名重新赋值、外部模块内部行为以及运行时业务 IO 不由此证明。绘图切片宣布纯绘制前，仍须提供禁网和“来源调用即失败”的真实入口探针；现有旧成员列表不是全面纯渲染。

## 存量债务

唯一清单为 [architecture_exceptions.json](../architecture_exceptions.json)。每行 `[rule, source, target, symbol, debt]`，模块名相对 `ournotes_bot`，`symbol` 仅私有访问填写。`debt` 指向同一文件中的理由、职责负责人及移除批次/条件。角色负责人表示代码职责，不虚构 GitHub 审批者。

清单按删除分数表后的实际调用逐项核对，每项是具体导入边或私有符号，不能放宽成目录/通配符。旧模块获准导入一个门面，不代表可新增私有符号。核心/QQ、纯契约、公共来源设施与组合反向依赖不接受例外。

| 债务 | 退出条件 |
|---|---|
| 领域记录仍在 `data.py` | 真实迁出单份类型并验证缓存兼容后替换来源依赖 |
| 查询借用入口分页、匹配和结果 | 触及该能力时迁至纯契约/语法模块；不重建已删除的分数表 |
| 查询/绘图消费来源 DTO | 迁移相关快照、视图和素材准备后删边，保留失败回退语义 |
| Rendering 借用 `visuals.py` 私有组件 | 实际迁出共用组件时逐符号删除 |
| 共享谱面解析、榜线校验与缓存 | 相关适配迁移时验证旧缓存和历史语义，再删除私有借用 |

新违规失败，已消失的例外也失败，要求清理清单。工具没有生成/重建清单命令。增补例外须解释触发场景和退出条件，并审查 diff；不能以“使 CI 变绿”为理由。检查无法阻止有权限的人同时改规则与清单，仓库审查和保护仍是独立控制。

## CI 与发布

`Checks` 的 `architecture (3.10)` / `architecture (3.12)` 执行门禁及负例。正式 `publish-linux-bundle` 的 `needs` 包含 architecture、原 Python 测试/打包和 Linux 离线验收；架构失败时不会正式发布。

Windows `ci_passed` 和 Linux `check_ci` 都先要求目标提交的整个 Checks 工作流成功；Linux 随后检查原固定任务名单。因此新增检查通过整体工作流状态接入两个更新器，本批不改控制器或生产配置。实际已安装控制器版本和 CI 运行结果仍需发布时验证。

门禁与清单不进入 bot wheel/sdist 的安装资源，也不新增 Linux 离线依赖。Linux 离线包的提交源码归档仍包含仓库工具与测试，供断网源码回归；应用运行不会加载门禁。

本批修改源码及 CI 定义，没有设置 GitHub 保护、rulesets 或审查者。建议 PR 必需检查包含两项 architecture、两项 test-and-package 及 offline-linux-bundle；仅 main push 执行的 publish job 不适合作为合并前必需检查。CI结果绑定提交确认；PR合并是否被强制阻止，仍取决于另行授权的仓库保护规则。
