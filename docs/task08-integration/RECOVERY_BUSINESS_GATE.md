# 恢复组合根：业务拒绝点与待接接口

后续Backend进程内排空接口已实现，见[撤权生命周期](RECOVERY_LIFECYCLE.md)。下文是37af641阶段的固定范围；跨进程会话/双角色屏障仍未完成。

本轮基线 `982f042889d9de84e13aa2aedaca51faeea5bd5b`，原08工作区、分支及PR200。只读10固定提交 `573c7586c3e05d2699c31e6523e68c70521a7df1` 的INSTANCE_RECOVERY、TASK10_VALIDATION、isolated_runtime及recovery_preflight。未合入10执行代码，PR202排除。

**交付是可接线的拒绝点，不是已完成的恢复运行链。** 用户已选人工核对当前权限和删除记录、未知不恢复。专用WSL已存在，本轮没有占用它；缺口是业务/控制器接口，不是环境或用户还没批准人工模式。

## 已实现的08边界

`backend_bootstrap.build_backend/build_query_backend(..., assert_recovery_current=callback)` 在DataOwner、SQLite账本、知识/用量与repository创建之前调用受信同步断言。callback须返回None；布尔值不是批准，未知必须抛错。调用方必须在两个角色都完成核验前保持断言拒绝，不能提前构造真实Backend再称为停写。此参数不是环境变量、HTTP或Console输入。默认个人CLI保持原行为，不应被恢复launcher直接使用。

`koishi/run.cjs::start(env, recovery)` 可由受信本地组合根传入 `{assertCurrent, scope}`；scope必须是既有05 ScopeStore，assertCurrent为同步函数，成功只返回undefined。构造前、启动前和握手后重验。未传recovery仍为原个人入口，不构成恢复入口。当前代码没有将Console配置或磁盘allow转换成此对象的路径。

同一assertCurrent注入Management以及Console：

- 接纳、发送、群/C2C、公告和模型许可复用既有Management检查点；独立05范围判断仍继续执行，恢复断言不能扩大范围。
- 任一次检查失效后Management不可在原进程内重新打开，即使读取器恢复、旧快照重放或Console操作controls也无效。
- Console所有RPC在执行回调前检查，失效后不再转发后台调用；定时journal prune也停。不是新增远程批准入口。
- 运行根每100ms调度一次检查，失效后调用既有close→Receiver.dispose→retire→排空→停止adapter/Console。该间隔是事件循环调度，不是实时停机上界。既有unknown、迟到ACK事实和不复活上下文语义未改。

已经发起的上传、后台RPC、线程写入不能被同步断言倒撤销。允许完成必要的ACK事实/撤销审计与排空；不能把上述机制写成“撤权时刻之后所有字节写入为零”。停止完成前，10必须保持writer锁及网络隔离，不能据此恢复数据或启动旧读者。

## 给10的生命周期与精确缺口

1. 控制器持有既有FileLock，停全部writer/trigger；从备份树外读取当前05/07/09权威，以实际恢复副本测量运行既有recovery_preflight和ManualRecoveryReview。现有IsolatedRuntime.approve(confirm_current)是受信进程内人工入口，普通Console登录不拥有这一身份。
2. 第一角色进程只能运行10测量过的无业务writer等待入口；尚不能构造真实Backend或调用网关start。待两个角色、同一控制器会话、角色/实例/代际及当前权威都完成核验后，受信入口才调用上述组合根。当前IsolatedRuntime.start只传环境并启动fixture，没有向子进程传递可验证的实时会话，也没有双角色提交屏障；这两个接口由10定义/实现，08不新增RPC或磁盘授权格式。
3. assertCurrent必须从这个受信会话检查当前角色、实例、控制器存活、锁所有权、双角色状态及当前证据代际。任何失联、重启、invalidate、恢复、代际变化均抛错；不得固定返回成功，也不能以“上次checked”代替当前权威。它是断言注入点，不是批准算法。
4. 网关按上述机制关闭新接纳/外发，10撤销整个会话并停两进程。**Backend运行中的持续禁写尚未接线**：真实backend的load、RuntimeTasks、model任务、operations、reaper和线程须共同排空；仅HTTP 503或ready=false不够。当前Backend.close已经有排空/存储关闭路径，但启动lifetime另持initializer/reaper/observation，必须经aiohttp生命周期关闭，不能单独并发调用close后仍让后台任务访问已关闭存储。
5. 10核实两进程、线程/宿主writer都停止后才能恢复数据；恢复后重新全量测量全部副本和权威，再人工审阅。旧会话永不恢复。这是后续要验证的真实恢复链，本轮未运行恢复/生产launcher/双进程提交屏障。

## 05/07/09快照与交00事项

| 来源 | 已有接口/所需原样数据 | 仍缺的组合输入 |
|---|---|---|
| 09 | check_identity、check_scope_binding；同次candidate/expected/observed，原始manifest摘要、明确组件映射和schemas。05 TS适配器固定checkerCommit为 `3dda6d29955328777f7e526c21bfd2828dbbea8a` | 当前08并未整体接09新安装包。需要00固定实际安装候选与可信checker、组件映射，不能拿候选自报hash或包SHA冒充checker来源；selection不由08选择 |
| 05 | ProductIdentitySession、productScopeAuthority、ScopeStore；binding及check_scope_binding原样decision、checkerCommit；独立approval(candidate,digest)每次核当前代际/撤销 | 本08树只有TS会话/ScopeStore，没有10所用安装包中的Python同名类。需要05/00给固定Python合同来源；不能复制10未提交夹具或另写一套批准逻辑。默认approval为manual，恢复回调成功不能替代scope批准 |
| 07 | history_retention.check_recovery(candidate,current_authority)；无参读取器必须在备份树外。字段contract_version、binding、policy_generation、deletion_generation、journal_sha256、evidence_version、完整replicas；candidate副本还须state=verified | 现有合同没有选择实际权威存储。人工核对不能制造缺字段，需00/07固定当前记录的可信读取来源、全副本覆盖和更新通知；未知继续manual |
| 10 | 隔离/锁与会话代际、人工双项确认、实际测量、角色生命周期 | 新增assertCurrent的跨进程可信实现、双角色提交屏障、运行中撤销通知及停止确认；不是创建新通用RPC的请求 |

recovery_preflight要求writers_stopped等四项隔离事实以及session/inventory/candidate/components/controller/storage/expected/scope/deletion九项revision。它的checked仍是activation_permitted=false。运行后不能伪造writers_stopped=true继续重放该报告；运行中当前权威复核与停写事实需要10拆清时序。

## 验证与限制

本地证据 `runtime/task08-recovery`，身份/权威均显式SYNTHETIC，不选产品能力：

- Python两组合根8项拒绝用例，确认DataOwner从未创建；网关9项未知/布尔/缺scope负例，未建目录或初始化QQ。
- 真实loopback Console/WebSocket：既有46受保护RPC合同通过，随后撤销合成权威，46方法全部拒绝，后台调用计数不变；恢复旧读数仍拒绝。群/C2C接纳/发送及公告检查关闭。RPC使用离线后端替身，不是完整Python恢复服务。
- 现有151网关探针通过（含unknown/ACK相关原合同）；11项个人/查询组合根及恢复边界回归通过；TypeScript和架构门禁通过。
- 未变化的candidate.3浏览器安全与页面验收沿用 `982f042…` 固定证据，没有重跑；该证据不能代替本轮恢复链验证。
- 未验证生产启动、当前真实权威、完整运行中Backend停写、首次进程等待/第二次核验、跨进程失联与宿主cgroup清理。新增拒绝点可独立交付，整体恢复准入仍未完成。没有修改10宿主/permit/launcher、05/07/09合同、最终selection或生产数据。
