# 查询聊天 v3：包 5 工程集成验收

本轮完成工程验收与迁移／回退核对，未改变业务代码。离线回归和实际双进程入口的隔离联调通过；公开源冷查询在本机网络路径上超时，保留为真实环境验收未通过项。正式百科、真实模型、QQ 能力和上线仍分别等待对应材料、验证与授权。

## 版本与结论

- 验收源码：包 4 `1b86c0cc38696dba49509f285c962648ed1138af`，基于 [草稿 PR #116](https://github.com/doublequiet-on/taki-ournotes-bot/pull/116) 继续，分支 `codex/query-chat-package5`。本包增量仅为验收文档和导航。
- 工作区：`E:\CodexData\.codex\worktrees\7868\taki-dev`。本地完整 SHA、源码／证据摘要、远端核对和 PR 回执集中在 `runtime/package5/verification.json`。
- 包 4 [Checks #522](https://github.com/doublequiet-on/taki-ournotes-bot/actions/runs/39968954928) 的 7 项检查全部成功：Python 3.10／3.12 回归与安装、两版架构、Koishi 探针、断网 Linux bundle、双进程组合产物。发布 job 按条件跳过。它绑定上述源码版本，不冒充包 5 文档提交的 CI。

| 层级 | 本轮结果 |
|---|---|
| 工程回归与隔离集成 | 通过；入口、原生工具、当前许可、来源快照、上下文、用量及 ACK 一起工作 |
| 当前公开来源 | 版本及原始资源已读到；本机冷查询的 18 秒总预算耗尽，端到端可用性尚未通过 |
| 内容交付／核验 | 尚无用户确认的正式百科交付包；没有新增正式正文或批准记录 |
| 真实内容与接入联调 | 未执行正式百科问答、真实模型能力／价格／语言质量、QQ 引用／按钮／统一身份与客户端送达 |
| 整版上线 | 未放行、未合并、未部署；本地通过不替代上述条件 |

## 执行证据

本次自查使用 Python 3.12、Node v24.19.0 与现有锁文件。临时数据库和合成审批仅存在于测试临时目录，结束后关闭进程和连接并删除；本地验收程序按仓库约定不纳入提交。

| 编号 | 检查与实际结果 | 本地记录 |
|---|---|---|
| E1 | 全量 1,173 项：1,163 通过、10 项因 Windows 缺 POSIX 所有权／进程与 GNU tar 行为跳过，548.589 秒；没有失败 | `runtime/package5/full-suite.log` |
| E2 | 固定包 0 原始数据合同 20 项通过：未知／空集／错误、跨难度时长、稳定序、筛选、真实图片及分页 | `runtime/package5/meta-contract.log` |
| E3 | 双进程入口 18 个场景通过；另验证 SQL／诊断正文隐私和重启保留用量、清除上下文 | `runtime/package5/integration.json`、`integration-node.json` |
| E4 | TypeScript 编译、已安装 Koishi／QQ 适配器探针 68 项、架构门禁均通过 | `runtime/package5/typescript.log`、`koishi-probe.json`、`architecture.log` |
| E5 | 复用同源码包 4 的 Console 26 个认证方法、实际 npm 资产桌面／320px 深色交互与回退兼容证据；本轮未重复启动 Console 预览 | `runtime/package4/browser.json`、`rollback-check.json`、`verification.json` |
| E6 | 当前 JP／intl 公开源查询均到达 18 秒总预算；精确失败 URL 另读返回 HTTP 200，分别耗时 22.351／24.901 秒 | `runtime/package5/current-sources.json`、`raw-resource-check.json` |

E3 实际调用 `koishi/run.cjs::start`、已安装 QQ `WsClient.accept` 的原始事件分发、Receiver、BackendClient、`build_backend/create_app`、RuntimeTasks、NativeClient 的 HTTP 编解码和 SQL 账本。只替换外部边界：QQ 登录／上传／发送回执为合成传输；歌曲和封面为隔离输入；原生模型 HTTP 指向回环提供方；知识向量为确定性合成编码。出站连接被限制为回环。启动健康中的 QQ online 因此也是合成状态，不是平台连通证明。

联调确认：传统查询及合法本地 `/问` 零模型调用；旧列表在来源版本变化／失败后仍从原快照翻页并选出原第 3 首；同工具双组查询只取一份来源；错误参数停止且已知用量结算；HTTP 500 不重试；缺 usage 标记 unknown；空知识库不进入二次生成；有隔离批准资料时二次生成取得独立许可且不携带工具；生成中删除资料丢弃答案但结算两次用量；local 策略禁止 AI；另一用户直接查询先于慢 AI 交付；上传失败使用原结果文字，保持消息序号。

最终隔离账本共 11 次尝试：10 次已知消耗合计 1,400 加权 Token，1 次 unknown 未伪造扣款；均无在途预留，1 次 `provider_error` 可见。使用的是合成价格和响应，不代表供应商账单。重启后账本摘要相同、锚点为零。

### 数据版本与显示合同

本次公开读取确认 JP `r-60335f9615398d015cd6`、intl `r-efa95c9026ea09777a20`；每个资源的 URL、SHA-256、release/source 响应头见 E6。两个查询在最后歌曲批次读取时超时；补读该确切 URL 为 200，不能将其写成文件缺失，也不能将补读成功写成运行时整条查询通过。18 秒上限、完整性检查和禁止旧数据兜底均保持原值。正式环境须复核这条路径在原预算内的冷查询及同版复用表现。

固定数据合同继续采用 [包 2A](QUERY_CHAT_PACKAGE2A.md)：`song-meta/<song>/<difficulty>/chart/time` 为各难度共享的歌曲最大末判定时刻，单位秒；不是完整音源／演奏结束，也不是 EX 独有时长。EX 条件只限定谱面字段；NPS 使用已核定歌曲时长分母。谱面等级、Note、NPS、BPM 查询必须明确难度。该限制沿用包 0／2A 合同，未增加默认难度或补造未知值。

实际渲染查看了缺封面、多行长标题、七列指标和连续编号的 1080px 排行图；20 条拆成固定页面，图片尺寸／字节预算通过。代表图保存在 `runtime/package5/meta-page.png`、`ranking-first.jpg`、`ranking-last.jpg`、`single.jpg`。这些是离线渲染，不是 QQ 手机客户端验收。

## v3 §12 验证矩阵

以下测试文件均位于 `tests/`，包含于本次 E1；表内“待实测”不计为已经验证的真实接入。E2/E3 是本地验收程序，E4 是仓库现有网关探针。

| 场景 | 工程证据与结果 | 真实边界／待项 |
|---|---|---|
| T01 旧指令与合法 `/问` | E1 `test_natural_query_eval`、`test_query_refactor`、`test_query_spec`；E3 直接／本地调用数为零 | QQ 客户端送达待实测 |
| T02 家团最短 | E2 `test_package0_samples`，实体与末判定时长固定 | 当前源可用性见 E6 |
| T03 EX 27+ 最短三首 | E2 同样本仅返回真实匹配的 1 首；E3 AND 条件 | 不足不凑数 |
| T04 分组与跨工具 | `NativeRuntimeTests` grouped／two_business_tools；E3 双组共用一次来源及一次调用 | 真实模型提取质量待实测 |
| T05 空集、错误、未知 | E2 empty/no_stale/all_metrics_unknown；E3 来源失败与文字回退 | E6 确认实际超时不返回旧榜 |
| T06 同值、BPM、单位 | E2 stable_numeric_tie、bpm_min_filter、fractional_level | 数字 ID 稳定序，未知不填零 |
| T06a 时长层级 | E2 原始样本、inconsistent_duration 与歌曲级合同 | 不扩大到玩法结束时间 |
| T07 显示 | E2 实际渲染、尺寸与编号；E3 长标题缺图 | 手机 QQ 缩放／送达待实测 |
| T08 翻页／第三首 | E3 source_version 改变且来源报错时仍选原值，零 AI／零重取；`BackendContinuationTests` | 未启用真实引用画像 |
| T09 翻页中更新 | E3 原 `synthetic-r1` 及第 3 首 92 秒保留；E1 snapshot_paging | 新完整查询才取新版 |
| T10 过期回复 | `ContinuationCoreTests` TTL 与 `AbsoluteLifetimeTests` | 过期提示重新说明 |
| T10a 所有正文到期 | `AbsoluteLifetimeTests` 三项；网关 raw sweep 探针 | 600 秒绝对期限，不续期 |
| T11 同群跨用户引用 | `BackendContinuationTests.quote_shares_only_confirmed_same_group_context` | 合成画像通过；QQ 正式画像未启用 |
| T12 可见对象 | `ContinuationDeliveryTests`、`BackendContinuationTests` 的成功／丢失 ACK、fallback、visible_indices | 未发片段不成为锚点 |
| T13 多话题／迟到 | 两组 continuation 测试的 topics、generation、late、pending、retire | 无引用且多话题不猜测 |
| T13a 同句跨上下文 | `AbsoluteLifetimeTests.runtime_scopes...`、`NaturalPolicyTests` 缓存来源／策略隔离 | 后端上下文计划缓存禁用 |
| T14 未授权入口 | E3 普通群聊／仅提及闲聊无发送和模型；E4 raw 过滤 | 不主动接管群聊天 |
| T15 按钮不可用 | E3 真实 HTTP 拒绝未验证 interaction；文字分页正常 | 本版只使用文字指令，真实按钮未验收 |
| T16 调度限额 | `test_stage5_limits` 的全局 5+20、用户 1+3、等待／执行超时；实现默认 60／30 秒 | 时限测试使用缩短时钟，不称真实压力测试 |
| T16a AI 不阻塞直查 | E3 慢 HTTP 期间另一用户帮助先交付；E1 hung_model_permission、QueryGate | 保留渲染／发送容量 |
| T16b 依赖与片段顺序 | E3 每批 msg_seq；`test_reply_order`、`test_reply_pipeline`、`test_multi_command`、continuation | 独立结果不受全局票号限制 |
| T17 超时／晚到 usage | `NativeRuntimeTests.late_uncancellable_usage...`、调度取消测试 | 无迟到答案／重复工具；已知用量幂等 |
| T18 原子额度 | `WeightedUsageTests` 原子群预占与重复结算；local/direct 测试 | 不沿用旧按次账作为加权账证明 |
| T19 群／私聊身份 | `WeightedUsageTests.shared_trusted_subject...`、unknown_identity 拒绝 | 合成绑定共享额度通过；真实映射证据未交付 |
| T20 失败及未知 usage | E3 HTTP 500 实扣、缺失 unknown、实际 admin HTTP；WeightedUsageTests | 合成提供方；真实 usage 待实测 |
| T21 价格／币种 | `WeightedUsageTests` immutable、openrouter_price、currency_guard；`ModelAdminTests` reference_unit | 不追溯重算；真实报价与估算状态待实测 |
| T22 off/local/撤权 | E3 local 零调用；`NaturalPolicyTests` off/local/continuation；E4 | 排队时不扩大入场权限 |
| T22a 当前许可 | continuation 绑定／过期／退役；NativeRuntimeTests 二次许可；E3 两次不同 challenge | 已知 usage 和 ACK 仍是事实 |
| T23 参数／注入 | `NativeTransportTests`、`WeightedUsageTests` 原生结构与闭合参数、native 工具执行；E3 错参数一次即停 | 工具无管理员／任意 URL 能力 |
| T24 资料范围／纠错 | `KnowledgeAuthorityTests` current_scope；NativeRuntimeTests 受证据约束生成 | 真正文稿正确性与纠错回答待实测 |
| T25 更新／索引失败 | `KnowledgeAuthorityTests`、`RuntimeRecoveryTests.knowledge_watch...`；包 2B 不变源码的增量索引／导入证据 | 候选构建成功才发布 |
| T25a 无正式百科 | E3 空目录只调用检索路由，不生成；E1 未确认文件不入索引 | 正式交付仍为空 |
| T25b 夹具／导入 | 临时目录及合成标记；[包 2B](QUERY_CHAT_PACKAGE2B.md) 交付格式／受控 import | 正式确认稿导入未执行 |
| T25c 撤审优先 | E3 二次 HTTP 生成时删除，答案丢弃、两笔结算；E1 删除＋索引失败＋重启及撤审时点 | 恢复旧文件不恢复已撤审许可 |
| T26 人格／简洁 | E3 轻量聊天通道；persona、工具事实边界和长度检查 | 自然度／事实遵从需真实选定模型评测 |
| T27 隐私／重启 | E3 SQL／诊断无合成正文／密钥，重启清锚点保留 11 笔；`test_console_admin` | 配置密钥文件单独受保护，不属于导出 |
| T28 运维与回退 | `FreezeTests`、`test_linux_backend`、`test_linux_updater`；E5 控制器缺失不可用、旧读新账 | 生产控制器安装／root socket／实际恢复未执行 |

知识检索的真实本地编码器证据沿用包 2B：固定 BGE small zh v1.5 ONNX int8，16 个合成样本命中 16，另 8 个留出问句命中 7。它不能作为正式内容召回率；本次没有调低阈值来消除未命中。

## 迁移与回退顺序

本节是后续经授权执行的准备说明，本轮未进行生产切换。双进程流程沿用 [M4 runbook](KOISHI_MIGRATION_M4.md)、[自动更新说明](../自动更新说明.md) 与 [包 4](QUERY_CHAT_PACKAGE4.md)。

1. **先核实目标**：记录实际运行版本、当前 controller、服务 UID/GID、管理／业务目录、数据库所有者与兼容门禁；源码 Worktree 的状态不能代替生产状态。取得同 SHA 的 Linux Python bundle、Koishi 组合包及 `combination.json`，核对锁文件、模块摘要、载荷 2.0 和两项能力。应用更新不自动安装受信控制器。
2. **补齐接入材料**：确认正式文稿及清单、固定本地向量模型、真实身份映射证据、供应商 wire profile／tools／usage／价格与额度参数、QQ 引用画像及文字分页实测。按批准内容导入；不把测试 `fixture://`、测试审批或合成身份复制过去。将 E6 冷查询在目标环境的原预算内完成作为实源验收项。
3. **维护与可恢复备份**：授权切换时先进入维护并停止新接收，按既有 drain／停止流程核实在途归零及数据所有权释放，再对受保护配置和 SQLite 做一致备份。包含模型配置、group/settings、usage、delivery、knowledge authority/index、model-admin、diagnostics、operations、通知及更新器恢复状态；保持 ACL／所有者，不能只复制一个仍在写的主库文件。诊断导出不包含密钥，不能代替备份。
4. **配置与账务**：新版读 `taki-model-config/1`，首次管理保存为 v2；包 3 不能读 v2。使用受保护的既定文件路径，身份 binding 保留原证据，网页不推导身份。`usage-v1.sqlite3` 为新独立加权账，旧次数账不转换成 Token、不开空库抹账；历史价格／币种／参考单位保持原快照。新增 observation 表不改变原账字段。知识撤审下限随共享状态保留。
5. **启动与实际核验**：由已批准控制器启动匹配的两进程，核对实际 SHA、后端摘要、双方 epoch、背景任务和两进程 PID，再在授权范围进行直接查询、分页、模型／额度、管理和平台交付验收。健康接口通过不等于 QQ 客户端已收到。
6. **失败回退**：停止新入口并保留 failed/pending/unknown 与审计记录，先核对回执，结果未知不换 operation ID 重跑。旧版本不具备当前策略／v2 能力时，由受信 guard 先冻结；无法确认冻结或兼容性则保持停止。旧启动器必须通过 `verify_start`，应用不得自行解除维护。只有部署适配器已审阅、账务／权限兼容和恢复门禁通过后才恢复旧运行。
7. **回退范围**：代码回退不回退共享用量、发送事实、知识撤审记录或恢复事务。确需恢复旧秘密配置时使用受保护备份和经过核对的配置版本，不能删库或静默落回旧按次账以求启动。旧版读新 usage 的 known／partial／unknown 已在 E5 验证；“能读账”不代表“能执行新版额度政策”。

## 后续放行材料

当前可交付工程验收结果。后续依次是：公开源目标环境时限复核；用户确认百科后的真实内容检索与问答；选定模型及计费合同的真实能力验证；QQ 引用、按钮替代路径、统一身份及客户端送达；需要运维网页动作时的受信控制器独立安装审查。满足这些条件后，再按用户授权决定合并、切换和上线验收。本轮不触发其中任何生产动作。
