# 继承FD会话到真实业务生命周期

本轮基线 `a7ca40d1c50d2a4d1a216f21cb3c8e87c2bd511f`，保留candidate.4 B2和d1知识权限夹具修复。只消费10固定 `a6e102a5b94be6af65652985fe0063491352cbf5` 的ChildSession、屏障与回执合同；10文件没有复制为第二套协议或修改。原PR200继续，未合入PR202或移动分支。

## 已测量入口如何调用

10的Python/Node包装器仍是唯一先执行的入口：继承私有FD和binding，wait双方ready并重新核验后才import/require业务entry。第一进程等待时不可构造Settings持久对象、DataOwner、Backend、Management或QQ adapter。以下调用仅位于已测量entry的 `start(session)` 内，参数来自受信组合配置，不来自Console布尔批准。

- Python：`asyncio.run(ournotes_bot.application.recovery_entry.serve_recovery_backend(session, args, settings))`。验证backend角色、实例和当前session，再调用原个人 `build_backend(..., assert_recovery_current=session.assert_current)`。角色/实例不符在创建存储前拒绝；build_backend继续验证返回必须为None。
- Node：`await require('<gateway>/run.cjs').startRecovery(session, scope, env)`。scope必须是05既有ScopeStore，保留其独立身份/批准判断；session不扩大scope。入口验证gateway角色和实例，原start继续在构造前/启动前/握手后检查。旧 `start(env)` 个人入口不变。
- `koishi/recovery.cjs::serveRecoveryGateway`只编排该网关生命周期，受信createGateway返回同一个实际runtime；没有网络路由、序列化许可或通用方法分发。startRecovery是实际发行入口的接线，测试的createGateway装入真实Receiver/Management/ScopeStore和假QQ平台。

两端使用10原session.state/report。当前性失败仍由既有Backend HTTP/initializer/后台检查与Management所有接纳/发送/Console检查拒绝。新入口50ms调度观察停止阶段；既有Backend100ms观察保持。FD读写有10定义的1秒界限，但调度与同步调用不是实时停机上界。

## 停止顺序及失败

1. 控制器撤销后，Backend调用revoke_recovery并await quiesce_recovery，原样报告 `quiesced` 的business_writers_stopped、receipt_tail_open、pending_terminations。
2. 网关在quiesce阶段拒绝新业务但保持进程，收到drain后调用原close。Receiver保持原retire→在途发送/ACK排空顺序；只新增retire成功及本地排空完成两个事实，`recoveryDrained()`同时为真才报告 `drained,{gateway_drained:true}`。旧dispose仍按原合同记录retire未确认，不修改grant/ACK/unknown或重试语义。
3. Backend收到close后才close_recovery，封ACK尾窗、排空HTTP、关闭存储，原样报告closed，再runner.cleanup。网关保留进程至stopped，避免提前退出被误判失联。
4. 控制器失联、错误阶段、启动失败或取消时，业务仍执行本地排空/清理，但不伪造有序回执。Python停止任务由drain_task持有，连续取消不能分离真实线程。排空异常保持错误，交宿主有界强停和独立停写证明。
5. `confirm_stopped`、FileLock、cgroup和全writer/trigger核验仍完全归10。未知read_stopped、pending_terminations或终止异常不得放锁；进程退出或本地close完成不等于当前宿主停止证明。强停也不等于orderly。

## 实际验证与证据层级

证据根：原08工作区 `runtime/task08-session`。从固定Git导出10 deploy源码到该目录，仅本地验收使用，不消费其他任务未提交文件。普通Ubuntu-24.04中使用08独立用户venv，未进入10专属WSL、未修改unit/用户/系统配置。Python3.12、aiohttp3.13.3、Pillow12.1.0、qq-botpy1.2.1；测试Node24.18.0来自该普通Ubuntu既有安装，低于发行要求24.19.0，因此不把本轮当发行Node版本验收。Windows现有Node24.22.1另执行编译/网关/RPC回归。

实际10 RecoverySession/ChildSession的socketpair、继承FD、绑定、序号、双角色屏障、持续观察和阶段回执原样运行。宿主安装测量、人工approve及running/stopped权威读取器为显式SYNTHETIC替身，wrapper摘要核固定字节；没有执行root所有权/专属网络命名空间验证，不声称完整IsolatedRuntime宿主安装路径通过。真实FileLock路径/fd身份检查运行；未知停止场景直接调用10原IsolatedRuntime.__exit__并验证fd仍在；使用10原FileLock补跑有序链和未知停止，第二持有者实际得到LockBusy（lock-final.log）。

- 11项双进程场景：实际build_backend、aiohttp/SQLite/DataOwner和真实Receiver→假QQ→ACK；有序停止、阻塞发送/迟到ACK、持续代际撤销、后台取消、控制器通道断开、Backend先等待、未确认retire不报drained、未知宿主停止保留锁、错误角色/实例/代际/版本。
- 3项追加：Node错角色、Gateway先等待不建业务目录、放行后Backend端口绑定失败。合计14项通过，all-first.log与extra.log。
- 新组合协程两项：阻塞initializer线程时连续取消，writer-ended早于stores-closed且回执不提前；setup完成到监听前撤销也依序回报。见entry-final.log。实际startRecovery的错角色/实例/缺ScopeStore三项构造前拒绝见start-root.log。
- 既有4项Backend生命周期、22项HTTP合同/组合根/知识权限、151项网关和46项RPC回归；TypeScript、JS语法及架构门禁，结果绑定交付记录。平台调用和外部数据均为离线替身，没有QQ/供应商请求。

保留chain-first/second失败：首轮查询超时且/tmp跨调用消失，后将环境/日志改为持久任务目录；后续日志定位ScopeStore模式下夹具缺持久管理/群策略，补齐合成策略后通过。没有削弱生产scope检查。新增本地测试不纳入提交。生产整包、真实05/07/09当前权威、宿主cgroup/失联后接管、全writer证明及恢复数据均未执行。

## 交00及09/10的具体缺项

下表保留a384历史状态；05/07固定接口与批准政策已更新，当前读取组合和10新时序依赖见[恢复权威组合](RECOVERY_AUTHORITY_WIRING.md)。真实宿主来源与最终装配仍未完成。

| 归属 | 固定依赖与所需输入 | 本轮边界 |
|---|---|---|
| 05/00 | 当前TS ProductIdentitySession/ScopeStore来自 `a9c27d7eb94833df713995b8e6f9a85fc45ea0d5`；可信读取器严格提供binding、decision、checkerCommit，独立approval及当前撤销代际 | 本轮复用ScopeStore，未自动批准。实际恢复的独立批准读取器、Python对应身份合同来源仍需固定 |
| 07/00 | `history_retention.check_recovery`接口来自 `a63de717df487f896ff2182ea5bf6008672db4ad`，当前整树仍含后续07修复；外部current_authority提供contract_version/binding/policy_generation/deletion_generation/journal_sha256/evidence_version/全部replicas及候选verified证据 | 未选择删除/留存政策，备份树自身不能充当前权威 |
| 09/00 | 当前05适配器固定可信checker `3dda6d29955328777f7e526c21bfd2828dbbea8a`；实际candidate/独立expected/observed、原始manifest摘要、逐组件映射与physical/logical schemas，固定selection | B2来源 `a526abab715989145ed1368ce1610413213a1a8c` 只是组件来源，不冒充完整安装身份或checker；09负责固定整包 |
| 10/宿主 | a6e102a固定协议；read_running九摘要session/inventory/candidate/components/controller/storage/expected/scope/deletion；read_stopped严格writers_stopped与非空evidence | 需真实代际生产者、独立宿主全writer/trigger观察，不能用业务数据不断变化的内容hash或合成字符串代签 |
| 09装配/10测量 | wheel/sdist包含新增Python模块，个人CODE_FILES同步观测其摘要；网关必须携带新 `koishi/recovery.cjs` 并核其hash，启动entry须在屏障后调用上述接口 | 当前旧发行器固定文件表未携带recovery.cjs；需09更新装配并由10重新测量组合/entry。08未越界修改发行器或降低旧Console准入门禁 |

本提交是实际跨进程业务组合的作者自查候选，不表示最终产品恢复或生产批准。10已独立审过的协议与本轮08新增接线仍应分别复核。
