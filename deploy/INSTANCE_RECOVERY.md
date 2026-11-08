# 独立实例计划与恢复预检（任务10）

部署侧提供纯计划、恢复检查编排、文件测量、独立首装离线运行环境安装，以及受信控制器进程内的隔离夹具启动会话。**没有生产自动激活或数据恢复执行器**。`checked` 只表示此次受信回调检查完成；所有回执均为 `activation_permitted=false`。缺候选、缺政策、缺当前权威均不会补成批准。隔离夹具进程运行与真实产品激活分别记账，最新接线边界见文末。

## 已核对入口与副作用

以下结论绑定 main `8d9e7301392ad548fe9554c4f4b09a0d53f615fa`：

| 入口 | 真实行为 |
|---|---|
| `linux_backend.py check` | 构造 `LinuxBackend` 创建 releases/logs/backups 目录；可发布 notice，进入 `Engine.run`，获取候选、安装、备份、停服务、切 current、启动和清理。不是只读。 |
| `linux_backend.py recover` | 恢复持久事务；`recover_engine` 可能重新启动已知版本。不是数据恢复 dry-run。 |
| `status` / `cleanup-plan` | 已有只读分支，不构造 backend；分别读状态、读回收计划。不能证明新实例可安装或可恢复。 |
| `LinuxBackend.verify_compatibility` | 检查清单/策略/配对；可运行隔离兼容沙箱。不是纯函数。 |
| `backup` / `_snapshot_stopped_data` | 分别保存代码环境与停写后的 data 快照。代码回退不自动恢复共享数据；现有预算/摘要/路径保护继续有效。 |
| `koishi_control` / launcher | 固定 `taki-gateway.service`、组合目录、运行 permit 等个人实例路径。launcher 检查当前 boot permit、群策略，再 `execv` 应用。 |
| `TrustedActions` | 构造时取得锁，恢复未决操作并写 unknown；prepare 委托 adapter，execute 可启动线程执行操作。不能拿它包装“无副作用预检”。 |
| Windows setup/start | 可建 venv、复制配置、同步数据或进入受管恢复/启动。不是本轮 Linux 计划入口。 |

现有 updater 初始化依赖一个健康 baseline，不是空机首装器。仓库内没有可据此宣称完成的通用空机安装/业务数据恢复命令；私有运维脚本未提供固定提交或交接路径，本轮不猜测其接口。现有 service/launcher/config 仍固定个人实例；本轮示例清单不能直接输入这些执行器。没有放宽 `validate_config`、事务 version、备份保护、forward-only 或旧版 reader 拒绝规则。

## 纯计划入口

```text
python -B deploy/instance_preflight.py --inventory deploy/instance-plan.example.json
# 在拟用 Linux 主机，仅追加只读路径元数据检查：
python3 -B deploy/instance_preflight.py --inventory /review/instance-plan.json --local-paths
```

退出码：1 拒绝，2 待人工/缺门禁；没有 0/激活成功状态。不创建目标目录，不读环境文件内容，不执行 systemctl、安装命令或候选 Python。输出 `operations=[]`。`plan_instances(document, observations=None)` 是纯函数；`inspect_local_paths` 只读 Linux realpath/stat，输入文件解析拒绝重复 JSON key 和非有限数。

清单 version=1，必须显式给出 `host` 和非空 `instances`。每实例严格字段：

- `instance/product/app_id/channel`：非秘密声明；产品/通道组合做声明检查，真正身份仍交09与外部 expected 核验。不能从该清单证明实际 AppID。
- `user/uid/controller_uid`：应用 UID 必须非 root，控制器 UID 必须不同；实例间不得共用应用用户/UID。应用所有权不等于控制器所有权。
- `paths`：base、config、environment、data、console_state、ledger、history、controller、controller_state、runtime、combinations、backups、recovery、authority 全部显式绝对 Linux 路径，不提供生产默认值。目录子树代表该角色的完整范围；外置额外账本/历史须纳入实际清单审阅，不可省略后宣称覆盖完整。
- `units`：service/timer/socket 三类名称数组；没有 socket 时显式 `[]`。服务和定时器至少各一项。应列出 recover/updater/采样/维护等所有触发者；此处不自动发现 systemd alias/override。
- `ports`：角色到端口号的映射；同宿主任何重复端口保守拒绝，不假定不同绑定地址能够安全复用。

检查实例 ID、AppID、用户、UID、unit、端口冲突，以及任意跨实例路径相等/包含关系；authority/controller/config 不得落在应用可写、备份、恢复或候选组合树；console/ledger/history 不得互相覆盖。`--local-paths` 还检查 realpath 别名、共享 inode 及其祖先（覆盖硬链接、bind mount 别名和不存在叶路径），检查已存在的受信路径祖先属主及 group/world write、已存在数据路径属主。别名必须先改为可审阅的规范路径。

这只是声明与路径预检。当前账号组/ACL、挂载权限、systemd 实际 alias/FragmentPath/User/ExecStart/EnvironmentFile、全部 timer/socket、监听进程、其他未登记实例与跨主机接收者尚须独立宿主盘点。`host_inventory_and_ownership` 始终待决；未存在目录的最终权限必须在创建后复验。合成清单不证明目标宿主或同 AppID 旧实例已停止。

## 恢复检查接口及顺序

`recovery_preflight.py::recovery_preflight(candidate, *, read_isolation, check_identity, check_scope, check_deletion, current_authority)` 由未来受信部署组合根调用，不能从候选/RPC/备份内容选择 Python 模块或任意 callable。

1. `read_isolation()` 读取当前控制器的独立证据。四个事实必须均为 true：`outbound_isolated`、`writers_stopped`、`triggers_inhibited`、`controller_exclusive`。这不是停写实现；传入 true 不能代替实际隔离。
2. `check_identity()`：受信09实现 `check_identity(candidate,expected,observed)` 的无参闭包；manifest、外部 expected、实测组件、当前 schema/协议/控制器由同次受信快照提供。身份非 allow 立即停止，不调用范围或删除检查。
3. `check_scope()`：05 `ScopeStore.recovery()` 的受信同步桥接回执 `{outcome,reason,evidence}`，其中 deny 映射 reject；缺接口/未选独立批准政策返回 manual。必须包含当前身份校验及独立 approval，不得拿旧备份的 scope 自证批准。本轮已用固定05真实适配器/ScopeStore和09/07真实函数验证；生产跨进程可信通道仍由08/10组合根接线，离线测试输入输出不是该通道。
4. `check_deletion(candidate,current_authority)`：注入07原始 `check_recovery`；candidate 是07规定的测量结果，而 `current_authority` 原样传为**无参回调**，在检查时读取备份和恢复树之外的当前权威。不可传预先读出的 dict、从备份闭包读取的权威或把旧 journal 当最新记录。
5. 每个检查前、全部检查后重读隔离与修订证据；变化返回 `manual/preflight_evidence_invalidated`。全部通过返回 `checked/preflight_only_activation_pending`，不是锁、permit 或实际激活决定。

09/07要求 `contract_version=1,status=allow|reject|manual,reason,evidence`；05使用 `outcome=allow|deny|manual`，allow还须携带 `identity_evidence`，与本次09检查的evidence完全一致，否则 `reject/scope_identity_evidence_mismatch`。回执保留该字段。allow 必须有非空 evidence；拒绝/待人工允许空证据。缺回调、隔离未证明或 OSError/TimeoutError 为 manual；坏判定结构为 reject；编程异常向调用者抛出，不能吞成成功。任何失败都不得产生外发动作。

`read_isolation().revisions` 严格包含 session、inventory、candidate、components、controller、storage、expected、scope、deletion 九个 SHA256。它们由当前独立权威快照/测量生成，不能从恢复树提交。这些字段是任务10的失效输入，不重定义09身份或07删除语义。结果保留本次 revisions 和有序 steps，供审计。它们不提供身份认证或锁；宿主 adapter 的实现与来源验证仍是待接线门禁。

## 停写、测量、恢复后重验与激活时序

以下是后续执行器必须实现的顺序，不能将本轮回调测试称为已执行恢复：

1. 核对宿主/实例归属，取得既有控制器互斥，保留事务与失败状态；停止新接纳和紧急停发分别操作。阻止 recover/updater/timer/socket/自动重启重新拉起进程；关网关外发并核对 PID/cgroup 已停。仅停止新接纳不足以处理旧 grant/排队公告。跨主机同 AppID 必须提供旧接收者停止或网络隔离证据。
2. 停止所有 writer：后端、网关/Console、采样、账本、历史归档/压缩/清理、备份维护。记录 boot/session、服务状态、配置、组件与权威代际；拿不到完整停写证明保持 manual。unknown 与投递事实保留；不得为“健康”删除它们。
3. 使用既有预算与备份/事务门禁，保全恢复前快照。在独立恢复树解包，核验完整文件清单、SQLite 主库及适用 WAL/journal、一致性、schema、删除覆盖与备份责任。不能覆盖当前05/07权威。
4. 09先按外部批准的 inventory 原始摘要验证实际工件；从**同次读取的 manifest 原始字节**计算 SHA 并严格解析。`measure_components({受审组件名:绝对普通文件路径})` 只独立哈希明确文件，拒绝符号链接组件与多硬链接文件，比较读取前/后文件身份、大小、mtime、Linux ctime，变化抛出 `component_changed`。不执行文件、不把候选内的 hash 当测量。目录/多文件组件聚合算法必须来自09最终映射；本轮不发明新的摘要算法。上级路径信任和停写由部署侧证明。
5. 在隔离恢复树上按前述 C1→C5→C6 预检。07 candidate 中 replicas/generation/journal 必须由实际恢复结果生成，不能只读一份声称已应用删除的清单。缺副本、未知时间、未传播代际都保持07失败语义。
6. 经授权的恢复执行后，再对**实际安装树**重新测量与执行全部检查，复验 settings/group-policies、实际 protocol 握手与 schema。恢复、重启、换包、切 current、控制器/配置/范围/删除政策/存储变化、锁丢失或无法确认新鲜性均使旧证据无效。不能凭先前 allow 跳过重验。
7. 只有政策、实际候选和宿主 adapter 完成后才设计/接通最终激活：同一互斥与隔离窗口内复核当前权威，持久记录阶段，签发当前 boot/会话绑定启动许可，启动后端及本地握手，再按最终范围许可开放网关；完成版本/PID/稳定窗口与范围检查后，最后恢复批准的 timer/socket。现阶段此步骤未实现，不能用当前个人 launcher 消费本轮 `checked`。

首装 previous 必须为空，中断留下可诊断 intent/阶段，不伪造 baseline；没有 previous 时失败应停住。升级失败回到本次升级前 current，不固定回历史基线。代码回退、控制器回退、数据恢复三个流程分开；发生单向格式写入后保持 stop-and-repair-forward，不启动旧 reader。控制器回退同时核对配置/事务格式，数据不会随代码自动倒退。

## 隔离 Linux 故障矩阵

```text
python3 -B deploy/recovery_matrix.py
python3 -B deploy/recovery_matrix.py --parameters /review/task10-acceptance-inputs.json
```

只输出18项可复核的注入/预期/`not_run`矩阵；不执行故障，退出码始终2。未提交参数时逐项列 missing；填齐参数也不代表验证通过。实际候选执行器尚未提供，不能用矩阵生成替代演练。

参数必须有 `host_image_digest`、`controller_commit/controller_sha256`、个人与定制各自 `*_candidate_commit/*_manifest_sha256`、`artifact_files`（逐路径/SHA/大小）、`reviewed_component_mapping`、`instance_inventory`、`rollback_manifest_sha256`、`scope_policy_reference`、`deletion_authority_reference`、`backup_fixture_inventory`、`isolated_harness_commit`。提供的是受审引用/文件映射，不是凭据；具体值仍须执行器校验。分别在干净 VM/容器快照上执行，每次保存输入摘要、故障注入点、返回值、事务/服务/PID、current、数据与 unknown 前后摘要、另一实例的不变证据；不能注入生产。

覆盖两实例、路径别名/端口、错身份工件、首装中断/半启动、停网关失败、迁移不兼容、备份缺项、切 current 失败、低盘、旧宽范围、旧删除 journal、权威离线/变化、代码/控制器回退、恢复后失效、跨主机 AppID 切换和上游故障。Linux合成 realpath/硬链接和离线事务单测不代替以上实际工件矩阵。

## 给08：真实05会话边界

固定05已删除完整candidate旧binding。唯一链路为09 `check_scope_binding` → `TrustedScopeBindingCheck` → `ProductIdentitySession` → `productScopeAuthority` → `ScopeStore.recovery()` → 独立approval。不再把“05新版接口未提供”列为阻塞。

受信启动/管理组合根持有会话对象，`beginCheck()`清空旧快照并返回不透明ticket；传入 `publish(ticket,{binding,decision,checkerCommit})` 的decision必须是受信09原样结果。发布一次即消费ticket。`invalidate()`同时清空快照与所有在途ticket；旧ticket、跨会话ticket、重复发布不能重开，新会话从空开始。发布/读取均复制，外部对象变更不改活动快照。身份allow后仍调用独立approval；未注入政策为manual。

版本细节：05固定代码仍要求 `checkerCommit=3dda6d29955328777f7e526c21bfd2828dbbea8a`。此commit与最新09 `9a3eca0b21dd7a7724143f988def59a5fc558dca` 的 `deploy/product_identity.py` Git blob均为 `8be5187d99bcd4b1cb04ed6dc45c2f9c726d0139`。本轮分别加载两份固定blob，验证判定完全一致；可接受的会话使用实际从3dda6d2提取的校验器和真实来源标签。直接标9a3eca0会被当前05拒绝，不能把新来源伪标成旧来源，也没有修改05的版本白名单。若实际安装校验器按9a3eca0登记，应由05负责人更新接受版本并重新验证。

跨进程传递必须沿现有受信启动/管理边界，受信控制器独立读取expected、实测observed和逐库存储，网关不能接受来自Console客户端、候选、旧备份或任意磁盘JSON的allow。ticket由网关会话本身创建，不从传输反序列化。控制器身份、文件所有权和通道认证仍需实际部署证明；SHA/evidence仅用于审计与关联，不是签名。

变化**之前**由08关闭接纳/停发并invalidate：manifest/绑定、expected权限或下限、observed组件/控制器/protocol、逐库存储集合/格式、范围或删除权威、安装/恢复/回退、控制通道失联和新鲜性未知。10重读隔离证据并重测，08再用新ticket发布。未收到可靠变更通知时不能保留旧allow。无需逐查询spawn Python，不新增通用RPC；业务组合根、Console/receiver的实际接线仍归08。

## 逐库只读观察

```text
python3 -B deploy/storage_observation.py --inventory /review/history-copies.json --isolated-checkpointed-copy
```

清单严格为 `{version:1,roots:[绝对专用历史目录],replicas:{不含用户信息的副本ID:{path:绝对库路径,partition:null或[provider,normalization,time_kind]}}}`。清单由恢复树之外的当前管理者提供，path与partition共同绑定副本；不能从文件名推断provider。旧1/2/3为null/unknown，新4和107/logical4必须有外部预期分区三元组。

只在显式已隔离、停写、完成checkpoint的副本上使用。工具不做checkpoint，不复制/迁移数据库，也不打开业务store。扫描专用roots的全部普通文件，逐一与受信副本集合比较，拒绝漏列/缺失、副本别名、符号链接/多硬链接和特殊文件；WAL/SHM/journal即使为空也使覆盖不一致，不能被immutable静默忽略。未知目录、未登记主机/离线备份/导出不会自动发现，必须先补完整外部清单。

SQLite固定 `mode=ro&immutable=1`、`query_only=ON`、`trusted_schema=OFF`，检查SQLite完整性、基础表/列、实际user_version；107另核 `store_meta.legacy_version/state=complete/players_codec/source_sha/schema`，不把107当逻辑版本。schema4核singleton分区身份及每条observations的provenance provider/normalization/time_kind，缺失或错分区保持manual。格式完整性不是业务语义和压缩payload全量解码证明；后者仍需07正式恢复检查/实际候选回归。观察查询有VM预算，超限按读取失败报告。

读前/后复核全部文件集合和摘要；任何库失败、漂移或覆盖不完整，都保持 `schemas={}`，不给09部分成功的投影。成功 `status=observed` 仅产生 `cutoff_history.<replica_id>.physical/.logical` 和原始文件摘要，`coverage_complete`只针对登记roots；不生成provider能力标记、删除代际、当前批准或07的verified许可。`cutoff_history_provider=4`属于候选代码能力，需要08/09验证后明确登记，不能从一个数据库反推。

09的expected.schemas须组合这些实测键与经过验证的其他存储/能力键；不得复制candidate区间成为观测。07恢复candidate的删除代际/应用状态还须独立验证，不能把此处的库SHA或observed直接升级为07 verified。存储evidence用于 `read_isolation.revisions.storage`；恢复/变更后重新采集，完整集合变化须重新审核manifest绑定。CLI观察完成返回0、manual返回2，但所有结果 `activation_permitted=false`，0不代表允许启动。

## 交00与下一包

本轮 base 为 main，无代码堆叠；05/07/09/08仅固定接口参考，未复制其业务实现：

| 包 | 固定参考 SHA | 仍需接口 |
|---|---|---|
| 05 / #195 | `a9c27d7eb94833df713995b8e6f9a85fc45ea0d5` | 新binding、会话/ticket及真实ScopeStore已验证。仍需独立批准模式/权威、跨进程通知与受信来源接线。 |
| 07 / #198 | `a63de717df487f896ff2182ea5bf6008672db4ad` | 当前删除权威实现/政策、完整副本清单与实际测量、schema 到09契约映射。入口 `check_recovery(candidate,current_authority)` 已做独立函数组合验证。 |
| 09 / #194 | `9a3eca0b21dd7a7724143f988def59a5fc558dca` | 两产品实际工件、组件精确映射、inventory/manifest、外部 expected、回退目标及最低控制器；固定 binding 为 `{contract_version,manifest_sha256}`。 |
| 08 / #197 | `509abc81efc2433cbc197050ddc5d64c40f48f5a` | 最终组合根与能力选择、实际接纳/外发门。10不修改其装配文件。 |

00还需确认目标宿主、同AppID全局接收者、安装/备份责任和恢复目标；本轮不联系其他对话。Haneoka为外部 serverless 数据服务，Taki宿主待定；不假定 localhost、共享磁盘或可直接读数据库。Haneoka R2/D1/采集状态不归本包恢复；连接端点/权限/上游失败语义由03/09实际合同提供。

验证与完整文件清单见 [任务10自查记录](TASK10_VALIDATION.md)。

## Manual reconcile 最小接口提案（2026-11-04，未实现）

本接口只解释组合暂存中断的现存证据，不接管服务、不修写ownership/事务、不清理stage。适用于 `koishi_control.stage_combination` 的发布后回执失败，以及提取时SIGKILL。它不是 `Engine.run(recover_only=True)`：后者可能执行事务恢复、启动进程，不能用作只读检查。历史故障实物上轮已清理，本轮只参照 `retained-fault-records.json` 与日志，不能把旧路径当成当前仍存在的孤儿目录；本轮没有再制造或删除失败状态。

### 输入与只读证据

由既有受信控制器配置提供base/state/combinations根、实例标识和控制器代码完整SHA/hash，不接受候选指定控制根。每次观察须记录采集时间、可用性和来源；缺项写unknown，不补默认停止或批准。

1. 外部固定候选源码SHA、归档大小/hash、Python配对hash、manifest原始字节hash。ownership中的 `manifest_sha256` 是 `release_retention.manifest_digest` 的规范JSON摘要，必须另列 `manifest_canonical_sha256`；不能拿包内字节hash或外置清单hash直接比较。
2. ownership原始文件字节hash、schema/owner/phase/commit/destination/stage；可信读取校验使用现有 `release_retention.read_record` 的权限与所有者要求，再补严格键/重复键/类型/大小、单硬链接、祖先路径无别名检查。不能仅凭文件名或相同SHA目录认领。
3. stage/destination的存在性、真实路径、lstat(dev,ino,mode,uid,nlink,ctime/mtime)、挂载身份；不跟随符号链接，不打开FIFO/device，不遍历配置根之外。缺权限不等于不存在。逐文件清单、大小/hash、Python配对、Node目标和组合profile均从内容核验；复用受信版本 `inspect_combination_archive`/`verify_combination`，不执行包内控制器或应用。
4. `state.json`、release manifest、cleanup intent及失败记录的原始hash和current/previous/candidate/repair_from/phase引用；current链接本体与解析目标分别记录。任何引用缺失、漂移或未知都要显式保留。`release_retention.plan` 只提供保留引用信息，不是“可接管”或“可删除”结论。
5. 现有锁持有者/控制器会话可用性，以及目标实例service/timer/socket与PID、cwd/exe引用的只读快照；不可读取密钥/进程环境。目标宿主或进程引用覆盖不足标unknown。本接口不创建锁文件；不以“未发现PID”等同停写证明。未来执行者需在既有FileLock内重新采集完整证据并验证实际停写，检查报告不能代替锁。
6. 采集开始/结束对记录和目录身份/摘要重验，任何变化返回unstable并使先前报告失效。报告绑定实例、控制器、候选、根目录/挂载、记录、树及引用集合；稳定两次仍只证明观测窗口，不证明未来不会变化或历史fsync成功。

### 状态与返回边界

提议输出普通本地报告字段：`schema=task10-manual-reconcile/1`、`mode=read_only`、`classification`、`evidence`（分项来源/hash/可用性）、`conflicts`、`missing`、`next_review`、`activation_permitted=false`。不输出allow、执行token或能直接被Console提交的动作计划；无需通用RPC。缺真实宿主/停写证明时仅给artifact-level分类，必须同时返回runtime状态unknown。

| ownership / 目录观测 | 分类 | 必须保留的结论 |
|---|---|---|
| staging；stage缺失；destination完整且配对/规范摘要匹配 | published_receipt_unconfirmed | 符合rename后回执丢失；不证明Python prepared、事务完成或可运行。人工核对关联事务后，另行审阅是否补发独立回执；本轮不写 |
| staging；stage存在且部分内容；destination缺失 | staging_interrupted | 不发布、不续写、不删除；保留intent及文件，待审批独立重试位置 |
| staging；stage完整；destination缺失 | verified_stage_unpublished | 完整字节也不能推定rename许可或停写；仍人工处理 |
| stage与destination同时存在 | ambiguous_multiple_trees | 即使内容相同也拒绝自动认领/覆盖；需查并发控制器/重复尝试与目录身份 |
| 两者均缺失；但记录仍存在 | recorded_paths_missing | 保留记录；不能改成成功或删除记录“修复” |
| 无owner但有目录、owner损坏/越界、phase未知、digest不符、目录被替换 | ownership_unproven_or_conflict | 拒绝认领；不按目录名创建ownership |
| published但destination不完整、stage仍存在，或current/txn/cleanup引用冲突 | inconsistent_published_state | 不降级为普通缓存；保护所有可能被引用路径，交人工审查 |
| 读取失败、进程/锁未知、两次采集漂移 | evidence_incomplete_or_unstable | 不推断任何否定事实；仅报告缺证据，保持隔离 |

### 归属与下一步

- 10负责本提案、暂存证据与纯观察输出边界；未来若实施，优先接在现有 `koishi_control`/`release_retention` 的只读面，不另建框架。
- 既有 `linux_updater.Engine`/`linux_backend` 的受信控制器维护者负责事务关联、FileLock和任何后续修写；具体执行改动由00确认归属后单独审阅。不能由08 Console补写ownership。
- 08只展示分类、缺证据和人工交接；不能把 `published_receipt_unconfirmed` 映射成“恢复/启动”。09负责发行/组件来源与清单解释；05的范围批准和07的当前删除权威仍独立，reconcile不能代签。
- 本轮没有选定真实批准、删除政策或宿主，也没有实现任何修写、重命名、删除、自动接管或激活端点。

未来实现前必须验证：两类原始中断快照；完整stage未发布；双目录/双控制器；缺owner/坏JSON/重复键/未知phase；符号链接、硬链接、挂载别名、dev/ino替换、读取失败；全部摘要类型错配和缺/多成员；current/previous/txn/cleanup引用冲突；PID和锁覆盖未知；采集中漂移；同一报告重放、重启/锁切换失效。纯观察调用前后所有树与记录hash/文件集合必须不变，禁止mkdir/atomic_json/rename/unlink/rmtree/systemctl；未来执行验证还须覆盖断电及回执重复，不能沿用只读报告作为授权。

## 现有服务事务入口定位（2026-11-04增量）

“缺服务事务适配器”不能理解为没有停启/回退代码。`LinuxBackend.stop/activate/start`、`KoishiControl.stop/start`、`Engine._switch/_rollback/run(recover_only=True)`已有实际实现；本轮从固定0026159 Git源码使用这些方法完成隔离子进程验证，不改生产默认配置。

| 类别 | 真实入口与现状 | 下一步/归属 |
|---|---|---|
| 已实现，原来缺隔离驱动 | linux_backend.py:stop先停gateway再backend并核PID；activate创建临时链接→os.replace→目录fsync；start先写permit再启动两进程 | 10本轮用局部systemctl解释驱动＋专属HTTP子进程验证，后续真实systemd沙箱仍需独立宿主/完整服务夹具 |
| 已实现，原来缺隔离驱动 | linux_updater.py:_switch先持久phase；_rollback使用transaction.previous；recover处理未完成事务 | 本轮真实状态文件、FileLock、文件系统和子进程验证半启动/超时/切换中断，未重跑已绿套件 |
| 已实现的控制器格式保护 | load_state只认可支持的版本；单向迁移采用version=2，旧0359控制器拒绝 | 本轮直接加载候选包内旧控制器比对固定Git字节，验证拒绝，未替换任何已安装控制器 |
| 真正缺独立实例正式实现 | validate_config硬绑定/home/ubuntu/taki、既有baseline、/var/lib/taki-updater；_service/unit、KoishiControl unit、runtime_directory和launcher均固定taki名称/路径 | 10需在独立实例接口确定后接线配置、unit模板、permit与launcher；本轮没有放宽白名单或修改固定生产路径 |
| 真正缺独立首装事务 | Engine无state时要求current_release及健康baseline；空recover返回uninitialized；CLI check/migrate要求installation.json installed | 10需设计/实现previous=null的独立首装状态与失败恢复。现有baseline模式不能补成首装成功，也不能假造健康baseline |
| 真正缺实际恢复/激活接线 | recovery_preflight纯函数已有05/07/09顺序，但linux_backend/launcher未调用此链；现有prepare_start仅group_policy_guard | 10负责受信执行边界，08只提供既定快照注入/管理接线；需实际external expected/批准与删除权威来源，不能持久allow自证 |
| 已有但受具体审查阻塞 | _check_reviewed_fingerprints核critical_paths、指纹和双向兼容批准；migrate显式target并用version=2保护 | 准备与候选绑定的兼容证据后由有权人员批准；10不为测试生成真实policy，不因CI成功跳过 |
| 缺真实运行证据 | systemd/cgroup、unit/timer/socket、实例用户、开机恢复、真实应用健康、跨机停写 | 需专属测试宿主或受控服务环境及真实参数；局部进程驱动不代表这些验收完成 |

本轮驱动只替换systemctl命令执行、runtime目录、manifest、健康、数据快照边界和预批准输入。`compatible=true`、A/B release ID、permit中的policy_guard均明确SYNTHETIC，不是05或用户批准；通过直接调用已批准事务阶段测试控制流，未调用生产CLI或跳过其校验后上线。子进程只提供loopback合成HTTP，未运行QQ或真实业务。双实例测试证明独立目录/端口/进程/permit不串写控，未证明不同Linux用户/cgroup隔离。`LinuxBackend.activate`确实执行于专属合成目录；报告activation_permitted=false仍表示没有真实产品激活许可。

### 需要00/用户作出的具体决定（本轮未选择）

| 决定 | 可选方式 | 对实施/验收的影响 |
|---|---|---|
| 升级兼容模式 | 经指纹与数据证据审查的双向兼容升级；或显式单向migrate＋前向修复 | 前者才可保留previous并验代码回退；后者不得启动旧读者，需version=2及前向修复目标。两者均需固定候选、critical_paths、schema及兼容审查，不仅是批准一句“升级” |
| 可接受回退版本 | 仅经过当前安全/数据兼容审核的旧完整组合；或暂无安全旧版本，使用停机＋前向修复 | 8d9旧包能读管理文件不代表已获候选3安全批准；候选3修复前版本不得由10默认作为可上线退路 |
| 范围恢复批准 | 每次启动/恢复由当前管理员批准；或备份树外独立最新批准锚点（实例、范围摘要、代际及撤销） | 前者必须有值守，重启不能静默批准；后者需明确权威存储、可用性、可信读取和撤销传播，离线未知保持manual。05定义范围，08/10接现有边界 |
| 删除权威来源 | 独立服务/外部账本按代际提供当前副本清单；或每次恢复经人工核对的当前外部快照 | 前者依赖服务可用及所有副本传播；后者需值守与新鲜度核对。二者都不能取自旧备份自身，也不由10代定保留期限/删除范围，07负责合同，用户确认策略 |
| 宿主与运行隔离 | 专属Linux测试VM/容器内完整systemd环境；或现有宿主上明确分配的独立用户、unit/端口/路径 | 前者更易验证完整生命周期且不触及真实实例；后者必须先盘点资源和权限、证明与既有控制器隔离。跨机迁移还要旧实例停止/隔离凭据。当前只运行了普通隔离子进程 |
| 产品/数据范围 | 个人完整能力选择；或定制选择矩阵及物理裁剪后候选 | 决定需安装组件、数据副本与恢复覆盖，09负责实物，10只消费已冻结清单；不按当前含聊天整包代定定制产品 |

以上记录该轮待审选项，不是生产授权。后续选择和实现见下节；仍不实现manual reconcile接管、补发ownership或清理失败状态。

## 独立首装准备与人工恢复会话（2026-11-04）

已选择的流程是恢复前人工核对当前权限和删除记录，未知即不恢复；完整服务验收使用专用Linux VM或完整systemd容器。这不是任何具体恢复、启动或生产宿主的授权。当前机器只发现共享Ubuntu-24.04 WSL（PID1为systemd）；未发现Docker、Podman、Multipass、VirtualBox、systemd-nspawn或现成machine目录。共享WSL不能当作专属服务验收宿主，未安装虚拟化工具或修改全局配置。

`instance_install.layout(settings)`接受精确字段：`version=1`、`instance`、`parent`、非root `user`、三个互异的`backend_port/console_port/health_port`（1024–65535）。生成独立目录和unit名称；它不证明宿主端口空闲、用户存在、AppID或范围合规，仍需instance_preflight实际盘点。端口只是待接线配置，不表示应用已经采用这些端口。

`unit_drafts(settings)`只返回backend/gateway文本。gateway依赖并绑定backend，均使用实例专属`--instance-config`入口；不写入systemd、不enable、不创建用户。`koishi_launcher.py --instance-config <json> backend|gateway`目前只检查配置并返回manual/退出2。原`--base`入口行为保留。草稿不能作为可运行安装成果：Python环境、配置权限、真实端口接线及受信启动许可尚未完成。

`prepare_first_install(settings, archive, commit, archive_sha256, python_sha256)`是显式有副作用的暂存API，不是check。只在已经存在、控制器拥有且所有祖先无链接/组写/其他写权限的Linux父目录中运行；不接受已有内容的实例。复用现有组合校验、FileLock、atomic_json和stage_combination。状态写入`<parent>/<instance>/controller-state/first-install.json`，固定`previous=null,current=null,activation_permitted=false`，先持久`staging`，成功暂存后变为`staged_pending_manual_review`。不构造假baseline、不写current/permit、不安装依赖、不启动服务；任何异常保留intent和既有stage/ownership，重复运行拒绝。该记录是独立准备事务，不是Engine已安装状态，也不能传给旧Engine当作state.json。

`inspect_first_install(settings)`只读取准备记录并给manual。它不重新核包、不证明ownership或停写，因此不能代替manual reconcile或后续激活预检。失败状态、孤儿目录和旧记录均保留。

`ManualRecoveryReview(check_current)`接受组合根注入的受信无参回调，回调须完整运行既有`recovery_preflight`隔离→09身份→05独立范围批准→07恢复检查，且当前权威来自备份树外。`begin()`仅为checked报告建立内存ticket；`confirm(ticket,current_permissions_confirmed=True,current_deletions_confirmed=True)`消费ticket并重跑完整检查，报告任何变化即manual。确认缺一项即reject，invalidate、再次begin、重启均使旧ticket失效；重复/迟到提交manual。成功只返回reviewed且activation_permitted=false，不代表范围批准、恢复执行或启动许可。

上述确认参数只能来自受信人工操作流程，不可直接映射Console/RPC传入布尔值。05会话/09 checker来源/07外部权威约束不变；08负责业务组合根和Console身份接线，10负责受信执行边界。真实权威/停写证明缺失时，人工选择流程也不能补成checked。剩余实施需要具体宿主和独立实例参数、用户/unit/cgroup隔离、完整Python与Node运行环境、当前范围与删除证据、兼容批准，以及锁内恢复后重验和短期启动许可；本轮未接这些实际执行动作。

人工复核并发约束补充：每次begin/confirm绑定进程内失效代际；短临界区只保护代际与ticket，check_current在锁外。invalidate或第二次begin发生在回调期间时，旧操作不得发布ticket/reviewed；confirm原子消费防止并发重复确认。此同步仅属该会话，不替代FileLock、跨进程权威或激活前重验。

## 离线运行环境与锁内隔离执行接线（2026-11-04）

`instance_install.install_runtime(settings)`要求实际Linux root、Python3.12/x86_64和只有lo的调用者网络命名空间。它在现有FileLock内消费已暂存固定组合，写installed.json的installing意图，复用unpack_bundle/extract_archive、venv --copies、pip --no-index --require-hashes及pip check，最后用原tree_digest/environment_digest与freeze_release记录installed。previous为空；这个installed只代表依赖/源码准备完成，不代表Engine active/current或产品批准。失败保留意图、安装日志与目录，存在旧记录/目录时拒绝自动续装。没有改变旧LinuxBackend、迁移v2、备份或代码回退语义，也不安装QQ凭据。

`isolated_runtime.IsolatedRuntime(settings,check_current,stop_writers,entries)`是受信Python控制器内的特定隔离验收接口，不是RPC或可持久化授权框架。check_current必须每次调用现有recovery_preflight及真实05/07/09；stop_writers必须覆盖当前实例所有登记writer和trigger，且在FileLock内执行，随后由read_isolation证实停写。调用者不能传Console JSON或备份中的allow替代可信回调。entries精确为backend/gateway两个已审核的合成入口文件，位于实例root拥有的controller目录；不支持任意产品启动命令或默认批准。

会话进入后保持实例FileLock直到所有直接子进程停止。运行时重新校验installed设置摘要、整包清单、代码/venv摘要、实际解释器及入口文件；不使用包内控制器替换受信模块。approve(confirm_current)先取得新报告，由受信人工回调明确返回current_permissions_confirmed/current_deletions_confirmed，再通过ManualRecoveryReview复查。只有reviewed才生成两张独立角色、不透明、30秒有效、仅内存存在的一次性许可。start消费后重新检查当前报告及安装测量，最后核代际/期限，再以目标UID/GID、空附加组、最小环境启动实际Python或Node进程。实例/端口/Console-state从settings传给离线夹具，不能从继承环境带入真实token。

invalidate可以跨线程撤销在途操作和未用许可；执行/测量/恢复后重验限定创建会话的控制线程。回调不持短线程锁；同一实例FileLock仍保护完整操作窗口。第二次approve、重启、新会话、角色错配、重复消费、超时、组件/范围/删除证据变化均不重用旧票。许可是本控制器会话的隔离夹具启动许可，所有结果仍activation_permitted=false，不能序列化交给独立launcher或恢复为真实批准。

after_restore不执行数据拷贝：先撤销许可、停止本会话子进程并调用stop_writers，再测量和执行当前检查；即使checked也须重新人工复核。停止不能确认时不释放writer锁；外层必须由专属systemd控制组保证控制器异常退出后的子进程清理。本接口不是通用后台进程监护器，入口必须是已审核的离线夹具；生产运行中的持续撤权/禁写仍由08业务生命周期和未来受信执行边界负责。

### 本轮真正运行的范围

专属Taki-Task10-2404、实际systemd控制器unit、显式NetworkNamespacePath只有lo，gamma/delta两个root安装树、UID999/997，固定候选0026159包各自离线安装venv/Node。后台是已安装Python包导入后运行的合成HTTP后端；网关是包内真实Console/Koishi/BackendClient，用合成密码和仅loopback后端，没有加载QQ adapter。两对实际进程分别使用29100/29101/29102和29200/29201/29202。管理状态为合成数据。

检查函数使用固定可信09源码3dda6d29955328777f7e526c21bfd2828dbbea8a和包内05 ProductIdentitySession/ScopeStore、07 check_recovery；SQLite physical/logical3经只读观察器生成schemas。产品身份、独立范围approval、当前删除authority、人工确认和部分隔离修订值明确是SYNTHETIC fixture，不构成生产权威。两实例为全新对象，stop_writers实际确认对应writer units不存在且FileLock已持有；恢复后测试则真实停止本会话子进程。未把这个全新夹具的stop回调宣称为通用生产停写适配器。

### 仍缺的具体接线，交00分配

| 接口 | 当前边界及需要的精确输入 | 负责人 |
|---|---|---|
| 独立launcher CLI | --instance-config仍manual/退出2；本轮已运行的是控制器内IsolatedRuntime路径。需要固定可信控制器会话入口、实例/角色与调用者绑定、失联/重启撤销、与FileLock一致的进程生命周期。不能用磁盘allow替代；本轮不创建通用RPC | 10受信执行边界；08提供现有组合根入口 |
| 当前身份和范围 | check_current每次需要同次candidate/expected/observed、原始manifest摘要、组件映射、schema、checker来源；05需要可信ProductIdentitySession、check_scope_binding原样结果、独立approval及代际/撤销来源 | 09合同和映射；05会话合同；08注入可信当前快照；10消费并重验 |
| 当前删除权威 | 备份树外无参读取器，绑定binding/policy_generation/deletion_generation/journal_sha256/evidence_version与全部replicas；candidate须来自实际恢复副本测量。人工确认不能生成缺失字段 | 07语义；00/08确定实际权威源；10复测 |
| 生产停写/双进程启动隔离 | 明确全部writer/timer/socket/controller清单、cgroup/PID/锁/触发器停止证明及恢复顺序；第一进程启动后第二次检查如何维持禁止业务写入的状态，需要真实运行门。不能把夹具只读后端当作生产backend的禁写能力 | 10宿主适配，08业务接纳/写入门 |
| 恢复执行与持续撤权 | 现有备份/事务保护内实际恢复动作、恢复后实际全量副本测量；运行中撤销的停写/停发时序。本轮after_restore只是执行后重验入口，不恢复数据，也不监控未来所有变更 | 10事务边界，07删除覆盖，08持续业务执行 |

这些是未实现/未接线的具体部分；专属环境已存在，不能再记成缺环境，也不能用缺真实生产批准阻止独立实现和合成隔离验收。最终产品selection、安全旧回退版本和具体生产恢复仍未批准。

## 继承FD可信会话与双角色屏障（10交08，2026-11-04）

消费08固定 `3d51c9335cf9b65b78d99de4d034728b74a694dc` 的 `RECOVERY_BUSINESS_GATE.md` 与 `RECOVERY_LIFECYCLE.md`。新增控制器模块 `recovery_session.py`、Node等待入口 `recovery_session.cjs`；不修改08业务文件、不替换冻结应用包。原未提交草稿归本任务，其副本保存在本地验收目录。独立launcher CLI仍manual，这不是生产启动批准。

### 控制器入口与信任边界

在已进入的 `IsolatedRuntime` 持锁会话内：

1. 构造 `RecoverySession(runtime, read_running=..., read_stopped=..., drain_timeout=10, startup_timeout=120)`，完成安装/包装器测量。两个读取器无默认值；需当前安装测量一致、无已启动子进程、此前未创建过RecoverySession。包装器本身须root所有、路径无链接/组写/其他写，并测量hash；每次准备子进程重新核对。
2. 调用 `session.approve_start(confirm_current)` 显式运行停写预检及人工双项确认，获得原有30秒、两角色一次性短期票；不续期旧票。
3. 分别 `runtime.start('backend', ticket, session=session)`、gateway同理。每个角色只有一对socketpair；通过Popen pass_fds继承，不监听端口、不写授权文件、不接受Console JSON。旧fixture入口未提供session时仍为旧路径，不能用于新的恢复组合根。
4. 第一进程只执行Python/Node等待器，业务entry在双方ready、重新测量/身份/范围/删除/人工检查通过后才import/require。`session.commit(confirm_current)` 在双方等待期间重审；缺一角色返回manual。提交还要求运行权威九项revision与该次人工复核快照完全一致，清除额外票后才放行。等待默认120秒，受信构造参数startup_timeout仅接受1–300的整数；预算绑定私有FD帧，Python/Node均严格校验。启动票仍30秒，已运行断言的读写超时仍1秒；失败/取消/超时撤销双方。
5. 所有报告仍 `activation_permitted=false`。`isolated_active` 只表示隔离夹具屏障已提交，不能转换为产品激活或范围批准。

每个私有端点绑定随机会话、实例、角色、boot_id和启动代际，帧序号严格递增。启动代际在该端点内固定；提交后控制器另外持有当前runtime代际，持续比较，不接受旧代际重开。控制器存活以私有通道存活确认，另核FileLock fd/dev/ino及两进程；恢复/显式invalidate/进程退出/通道断开/当前权威变化都会粘滞撤销。此信任依赖root控制器和已测量受信entry，不是抵抗已失陷应用进程伪造自身生命周期事实的证明；宿主停止仍由独立读取器确认。

`read_running()` 每次新读、线程安全，严格返回 `{status:'current', revisions:{session,inventory,candidate,components,controller,storage,expected,scope,deletion}}`，每项小写64位摘要。它不返回停写报告，不含writers_stopped；运行后approve和再次start均拒绝，不再重放停写事实。各摘要应绑定已审权威/安装/格式/副本覆盖代际；不能把正常运行期间可变的数据库内容摘要当恒定当前权威。生产端如何生成这些代际仍须相应所有者提供，不能用固定字符串顶替。读取器必须有界；通道1秒读写超时会拒绝，不能把超时补成成功。

### 给08的子进程接口与生命周期

包装器先wait再加载已测量entry。Python entry导出 `start(session)`，Node entry导出 `exports.start = async session => ...`。这里的session由包装器在进程内构造，环境中的FD/binding随后删除；不允许业务从磁盘重新构造许可。Python `session.assert_current()` 成功只返回None；Node `session.assertCurrent()` 成功只返回undefined，分别注入08既有组合根。任一次断言失败后，该子进程永不重新授权，但仍保留生命周期回执通道。

- `session.state()` 同步查询阶段：waiting/preparing/active/quiesce/drain/close/stopped。Python `session.report(stage, facts)` 与Node同名；只有三个固定阶段回执，无通用方法分发或RPC路由。
- 撤销先进入quiesce，双方assert拒绝新业务。08在Backend事件循环控制协程中 `revoke_recovery()`，等待 `quiesce_recovery()`，然后backend报告 `quiesced`，原样字段为 `business_writers_stopped:true, receipt_tail_open:true, pending_terminations:非负整数`。
- 控制器收到有效quiesced才进入drain。网关已关闭接纳，需按08现有retire/发送/ACK排空顺序完成，报告 `drained,{gateway_drained:true}`。该布尔必须来自真实close完成；10不制造它。若08内部close已经自动完成，可在drain阶段回报真实已完成事实。
- 然后进入close，Backend才调用 `close_recovery()`，报告 `closed,{stores_closed:true,pending_terminations:非负整数}`，再runner.cleanup并退出。重复/错角色/越序回执拒绝，不自动跳过阶段。不能在被跟踪HTTP handler里等待自身排空。
- 失联时断言抛错；08仍须本地撤销/排空，无法回报时不得视为干净关闭。控制器保留隔离和锁，在有界等待后SIGTERM→等待5秒→SIGKILL→等待5秒停止直接进程组。不合作线程/孙进程最终仍需宿主cgroup与全writer证明；强停不等于排空成功。

`session.confirm_stopped()` 仅控制线程调用，先撤销，等待生命周期/退出，再确认子进程停止，最后调用无参 `read_stopped()`；后者必须来自当前宿主独立观察，严格返回 `{writers_stopped:true,evidence:非空证据标识}`。缺失、异常、pending_terminations非零或终止异常返回manual。`IsolatedRuntime.__exit__`/`after_restore` 遇manual抛错并保留FileLock，不写“已停止”回执、不继续恢复。成功回执同时区分 `orderly` 与 `forced`；强停后即使宿主确证停止，orderly仍false，不能声称数据干净恢复。控制器进程自身死亡会由OS释放锁；此时必须由宿主隔离/cgroup接管停止核实，下一控制器不可凭旧锁消失恢复或启动。

### 尚缺生产输入，交00逐字段确认

| 所有者 | 必需输入 | 当前处理 |
|---|---|---|
| 09/00 | 固定candidate、独立expected、实际observed、原始manifest摘要、逐组件映射/来源、physical/logical schemas、可信checker完整SHA、产品selection | 不由组合SHA代签checker，不选最终产品；缺项manual |
| 05/08 | 固定真实ProductIdentitySession→TrustedScopeBindingCheck/ScopeStore接线、binding原样decision与checkerCommit、独立approval及当前撤销代际 | 本接口只承载当前性，不传磁盘allow，不实现第二套批准；08负责本地真实ScopeStore |
| 07/00 | 备份树外current_authority来源；contract_version/binding/policy_generation/deletion_generation/journal_sha256/evidence_version/全部replicas，恢复候选逐副本verified证据 | 无当前可信来源或漏副本manual，不补合成政策 |
| 10/宿主所有者 | 全writer/trigger清单、实际停写及进程组/cgroup观察、锁/挂载/实例归属、read_running九项revision生产者、read_stopped证据标识 | 专属离线夹具已验；生产读取器未提供，保持manual |
| 08 | 已测量entry对上述session接口、真实Backend lifecycle及网关drain完成事件的组合 | 本轮交固定宿主接口；未把源码08接口冒充已进入冻结组合包 |

验收脚本/失败日志见 `runtime/task10/trusted-session-20261104`，测试新增文件按AGENTS不提交。整个接口仍不执行备份恢复、不接管服务/开机任务、不删除任何失败或事务状态。


## 2026-11-08 新整包首装等待与初审时序

固定602a95f49fd059deb7a59377be74becb4d55e008新包在专属双实例实测暴露两个时间窗口：a6固定30秒等待不足以完成真实完整树测量及提交前重验；先签票再构造会话又会让构造测量占用原30秒票的有效期。本增量只将无业务屏障预算改为默认120秒、有界1–300秒，并提供approve_start将会话构造测量移到显式初审之前。它不延长启动票、不自动重签、不省略commit重验、不允许运行中重放停写事实。

正确顺序为进入IsolatedRuntime持锁→构造RecoverySession→approve_start人工初审→两个start(session=...)→commit人工重审。若角色已创建，approve_start拒绝；测量/人工回调期间invalidate仍无票。受信预算只控制无业务等待，不能来自Console批准或产品候选。宿主Python/Node包装器与父会话须成组安装并测量；新包装器拒绝缺失或非法startup_timeout的旧binding，不提供默认放行兼容。实际08 a384335f69e0514190c51c5dfce5808b55a19f60入口仍消费version1角色/实例及state/report，不修改其业务接口。


## 2026-11-08 宿主受信配置约定（待08唯一注入；本节为方案）

消费固定05 `054a101276f5ca451ad78d9397531534a2ce0249` 的ManualScopeApproval及07 `8bdde8392ab79180ebf32a11fc05ed0a5190fd1e` 的read_current_authority；独立宿主代码仍为23839bc05850009131f9c73311b1e6ba42295e38。本节没有实现配置加载器、持久批准、代际写入或新权限服务，也没有声称这两个新接口已经进入602/85bace冻结包。待08固定唯一组合接线后，仅增量验收其实际装配；12反馈前不重复旧工件全套。

已确认产品政策：个人版只有用户指定管理员，定制版只有接收方指定管理员可批准/撤销。Console登录或操作系统root不是产品批准资格。现有“人工核当前记录，未知不恢复”保持；不可改写旧备份隔离、禁止恢复，按既定留存期限到期处理，本轮不执行删除。管理员名单尚无实际ID，因此配置状态为unconfigured；禁止提供默认管理员、按首次登录自动认领或把空名单解释为所有人。

### 具体路径、权限与配置输入

建议部署路径如下，由10安装器的受信操作者明确配置，不从候选、Console参数、应用配置、备份内容或环境变量推导；下列绝对位置是技术选型，不需要用户决定chmod或目录布局。本轮不创建这些生产目录。

| 用途 | 具体路径 | owner/mode与用途 |
|---|---|---|
| 当前宿主配置 | `/etc/taki/instances/<instance>/host-control.json` | root:root 0600；实例目录0700，受信祖先root所有且不可组/其他写；只在管理员受信安装/配置流程更新 |
| 树外控制状态根 | `/var/lib/taki-control/<instance>` | root:root 0700；必须与该实例全部application/config/console/data/combination/backup/recovery树不相交，禁止随业务备份恢复 |
| 当前07记录 | 上述根下 `deletion/current.json` | root:root 0600，父目录0700；原样07 `{authority,journal}`，不是批准文件 |
| 唯一撤销记录 | 上述根下 `revocation.json` | root:root 0600；仅现有持锁控制器写，记录实例、连续代际及本次事件ID；不存allow |
| 人工审阅留痕 | 上述根下 `review-events.jsonl` | root:root 0600；记录操作者不透明ID/证据摘要/批准或撤销事件；日志不是可重载的批准 |
| 当前有效批准 | 现有root控制器内存；05当前网关对象内存 | 无批准文件；重启、通道失联、锁切换或源变化即清空，07回调不读取历史审阅日志作为当前批准 |

应用UID没有控制状态读写权限。08跨包注入只接收经审核的当前输出，不把root文件开放为应用组可读或可写。不沿用现有 `layout(settings).state` 作为07权威根：它仍管安装/更新事务，不更名、不搬迁、不清理旧事务。继续持有该实例既有 `FileLock(state/update.lock)`，树外状态写入也必须在同一实例控制器锁内；无第二套锁协议。

配置字段建议固定为：`schema=task10-host-control/1`、`instance`、`product`（仅比对09已验证身份，不选择/降级产品）、`authority_root`、`deletion_record`、`revocation_record`、`review_audit`、`excluded_roots`、`administrators`、`identity_source`、`scope_source_commit`、`deletion_source_commit`。`identity_source`固定09 checker完整SHA及代码摘要；另两source固定本节05/07SHA，候选不能选择解释器。`administrators`必须为非空的 `{issuer,subject}` 精确列表，issuer是08已核对的认证体系标识，subject为稳定管理员ID；不按显示名、群角色、客户端自报ID或通用Console密码授权，不包含凭据。每个实例名单独立，不继承另一产品管理员。ID未知时没有可用配置，不能落一个可通过校验的示例。

excluded_roots由宿主完整登记：实例base、外置数据、所有组合/候选、全部本地与挂载备份、恢复工作树；不得只列本次候选路径。配置加载须验证严格字段/类型、大小/重复键、正规范绝对路径、实例/09产品绑定、root身份、文件0600与单硬链接、目录权限、全部祖先无符号链接、相同inode/挂载别名及包含冲突。缺文件/缺管理员/不可信路径/不可枚举覆盖均输出 `manual/host_authority_unconfigured` 或具体拒绝原因，应用准入保持关闭。不得由检查自动mkdir、chmod、创建初始generation或恢复最后一次成功配置。读取前后比较配置与文件身份/摘要，漂移失效；host root被攻破不在此隔离保证内。

### 唯一撤销生产者与当前人工批准

唯一宿主 `revocation_generation` 生产者是持有该实例FileLock的现有root控制器，由08已认证管理员动作请求它执行；05的进程内generation和10的runtime.generation是各自票据失效计数，不能反写或替代宿主代际。宿主代际为1至9007199254740991的整数；任何撤销、管理员列表/认证来源改变、身份/删除权威替换或新安装/恢复，必须先关闭准入、撤销05批准及09会话、invalidate10票，再持锁递增并fsync记录和父目录，然后才能发布新证据。溢出、写失败、代际不连续、并发写入或来源不可证均保持关闭；不得回绕、自动补1或从备份还原。控制器不能承诺没有08确认回执的旧活动会话已停止，失联按现有revoke/停止确认处理。

同一进程保留已观察最高代际和事件ID，不接受倒退或同代际不同事件；禁止绕过唯一写入者的A→B→A文件替换。每次控制器启动产生新的随机controller_session及boot绑定，05和07批准均为空；即使磁盘代际相同也不沿用旧批准。离线宿主状态丢失或可能整体回滚时，单个本地计数/日志不能证明最新；没有独立可核对的当前记录及连续性证据就人工阻塞，不把文件hash或本方案称为外部防回滚锚点。未来管理员名单更换须由个人用户/定制接收方通过受信配置流程重新指定，既有管理员不能借批准动作扩大自身名单。

人工动作在08认证边界先解析真实 `{issuer,subject}`，核对该实例当前名单，再为具体动作建立当前认证会话绑定；10仅消费该受信边界的结果，不接受浏览器JSON中的actor或approved=true。begin/confirm/revoke均重新核对管理员、实例、会话仍有效及配置摘要。日志中的管理员ID不构成认证。系统安全失效可以自动关闭准入，但不冒充管理员批准/撤销决定。08负责其认证实现及唯一跨进程绑定证明；本节不新增监听端口或通用RPC，也不假定现有只承载state/report的私有FD已经传送这些新字段。

07独立批准的内存条目应包含 `instance,controller_session,boot_id,actor:{issuer,subject},auth_session_revision,config_sha256,revocation_generation,review_nonce,authority_file_sha256,identity_evidence`，以及本次人工核验的journal/replica清单摘要。它绑定具体管理员的本次动作与07文件精确原始字节，不能由scope批准或C6 allow生成。历史审阅只留审计；重启必须新审。`read_approved_sha256()`每次重验条目当前性、管理员仍在名单、认证动作的受信关联、配置/代际、锁及当前文件身份；不匹配即撤销并返回None，只在匹配时返回authority_file_sha256。07本身再次前后双读并比对文件摘要；独立批准摘要不可写进current.json自证，不从恢复树、启动常量或最后成功缓存获得。

05 begin/confirm的进程内ticket由08保留，绑定发起管理员、认证会话、实例、controller_session和host代际；浏览器只携带08自己的短期请求关联，不序列化05 ticket。confirm只有同一经核验管理员会话可消费；另一位管理员需要自己的新审阅。任何有权管理员可请求撤销，但须重新核名单并先使旧票失效。10 ManualRecoveryReview的双项确认仅在上述身份和两项独立审阅均成立后执行，不能反过来批准07文件或05范围。

### 10与08/05/07所需、所供字段

| 方向 | 固定值/语义 | 缺项行为 |
|---|---|---|
| 08→10 | 经受信边界验证的actor、认证会话revision、实例、动作与审阅关联；不可用JSON声明代替认证 | 拒绝人工动作，不产生票或批准 |
| 10→08 | 当前controller_session/boot、配置摘要、唯一revocation_generation、管理员精确匹配结果、独立07批准revision/摘要；事件发生前必须撤销通知并确认隔离 | 未配置/失联无可用快照 |
| 08→05 | `readCurrent()`严格仅document/digest/revocation_generation/identity_evidence/deletion_evidence；document/digest用scope.readCandidate，identity来自真实09，不能把host字段追加进05严格对象 | 返回undefined，05 manual |
| 10→07 | `read_current_authority(绝对deletion_record, excluded_roots=完整宿主清单, read_approved_sha256=当前内存批准读取器)`；外层包装为无参current_authority | None/异常沿07 manual，不缓存成功 |
| 07→08/10 | 当前check_recovery完整结果；只有allow且独立批准仍当前可产生下述deletion_evidence | reject/manual不签发05输入 |
| 05→10 | scope.recovery原样决策；manual.read().revision作为revisions.scope，approved仅诊断 | 不能单用approved或revision当许可 |
| 10→既有屏障 | 九项revisions；deletion绑定下述证据，session绑定boot/controller_session/代际，controller绑定独立宿主代码和配置摘要 | 任一变化invalidate；停写未知不commit |

固定 `deletion_evidence` 算法由10现有 `instance_preflight.digest` 计算：对严格对象 `{kind:'task10-deletion-review/1',instance,controller_session,revocation_generation,approval_revision,authority_file_sha256,identity_evidence,candidate_evidence_sha256,c6}` 按sort_keys=True/separators=(',',':')/ensure_ascii=True/allow_nan=False的UTF-8 JSON取SHA256。candidate_evidence_sha256是实际07候选完整对象（含逐副本state/代际/独立摘要）的同算法digest，c6为本次07原样返回；只有c6.status=allow、全部输入当前且本次人工批准有效时才计算。approval_revision是上述独立07内存审阅条目的同算法digest（不包含凭据），不是authority文件自身hash。身份证据必须与本次09相同。07 evidence_version原样留在c6，不强行当64位hash。

这个证据只在隔离、停写及复核期绑定具体恢复候选。正常业务写入不能把活跃DB的每次内容变化当作撤销；运行期跟踪已审发布的权威/格式/覆盖及批准revision，恢复前重新停写并测量所有副本，不复用旧候选快照。先取得独立07审阅及C6结果，再05人工审阅，最后10完整preflight/人工复核/双角色屏障；05 readCurrent不回调完整preflight，避免循环。每个确认点均重新读取，allow始终不是激活令牌。

### 不可改写备份与待验门禁

不可改写旧备份在宿主完整清单中保留稳定ID、实物位置/hash、原期限起点/到期点、隔离与禁止恢复标记；这些标记不是07的verified。恢复选择器必须拒绝该实物，不能因改名、复制或过期自动变成可恢复；期限未知保持隔离人工处理，不能重新起算。到期处理仍独立授权执行，本轮不删除文件或记录。不能为使07通过而从replicas清单静默省略旧副本，也不能给07严格七字段对象额外塞quarantined。07当前合同如何表达“仍存在但永远禁止恢复”的副本覆盖，须由07/00明确适配；在固定8bdde83语义未能证明完整覆盖前，相关恢复保持manual，不自行缩小范围。

待08固定接线后的定向验证：两个合成产品及不同管理员；空名单/错产品/未登录/同显示名不同subject/客户端伪造actor拒绝；begin与confirm间撤权、管理员替换、跨实例/跨会话/重复确认、重启空批准；非root或组写、软硬链接/挂载别名、权威落恢复树拒绝；单写者锁竞争、代际倒退/溢出/ABA、fsync失败；独立批准摘要错配/撤销/双读变化；实际05新接口与07读取器allow/manual/reject；12提出的宿主受影响项。全部仅在专属WSL新合成目录执行；不重复旧工件全套，不启动QQ。未完成这些接线验证前，本节是可审阅配置合同，生产字段与管理员未配置始终不获准入。


## 2026-11-08 宿主受信配置最小实现（取代上节方案中的待实现接口）

`host_authority.py::HostAuthority`现在提供配置加载、路径/文件权限与身份检查、唯一写入者撤销代际、独立07批准及实时08输入。消费08 `4fd680b9096ac7283facf8c9735aaa88b9147a6f`（内含05 054a101276f5ca451ad78d9397531534a2ce0249、07 8bdde8392ab79180ebf32a11fc05ed0a5190fd1e）；不修改08文件、不把这些新接口声称已装进602冻结组合。23839bc实际包验收及ea1ed方案历史保留。

**以08实际合同为准**：05输入的deletion_evidence使用08 `deletion_scope_review(...).review_sha256`，即其format=`taki-scope-deletion-review/1`的candidate/authority/decision摘要；上节拟定的task10-deletion-review/1不实现、不另算。08管理员subject为单个字符串，10用 `principal(issuer,subject)` 返回 `principal:`加现有canonical JSON SHA256，避免两个认证体系同名碰撞；操作者与管理员列表使用同一映射。这个字符串不是认证凭证。

实际配置严格八字段（不接受方案中尚未实现的source/audit字段）：

| 字段 | 内容 |
|---|---|
| schema | `task10-host-control/1` |
| instance / product | 实例名与personal或custom；必须匹配runtime设置和受信09映射所得当前身份，不用该字段选择个人降级模式 |
| authority_root | 例如`/var/lib/taki-control/alpha`，已存在root:root 0700 |
| deletion_record / revocation_record | 已存在的树外普通文件、root:root 0600、单硬链接；父目录0700；必须属于authority_root且彼此不同 |
| excluded_roots | 非空、完整的规范绝对目录列表，至少覆盖该实例整个base；额外外置数据/备份/恢复树必须显式登记。允许应用用户拥有排除树，但固定目录dev/ino/权限，拒绝别名与变化 |
| administrators | 非空且无重复的`{issuer,subject}`列表，必须来自用户/接收方指定；无默认值 |

配置建议路径仍为`/etc/taki/instances/alpha/host-control.json`，root:root 0600、直接父目录0700，所有受信路径祖先root所有且无组/其他写。文件打开使用O_NOFOLLOW及前后fstat/lstat核对；读失败、重复JSON键、非有限数字、文件超限、软/硬链接、绑定挂载别名、权威落入排除树拒绝。配置上限128KiB，07记录上限16MiB。目录内容变化不等于目录身份变化；应用可写排除树不会因正常文件增删被误作权威目录。配置/撤销文件每次读取均与会话开始时字节及inode/ctime等比较；已批准07文件每次实时读取均核对，A→B→A写回同字节仍失效。

撤销文件严格为`{schema:'task10-revocation/1',instance,generation,event_id}`，generation为1至9007199254740991整数，event_id为64位hex。配置、目录、初始记录的受信配置阶段须由宿主操作者明确完成，加载器不创建、不补默认代际、不恢复备份；缺任一项即异常/未配置拒绝。它不提供一个用候选JSON自助初始化生产的CLI。完整副本清单及不可改写备份语义仍由07/00明确；excluded_roots只排除权威来源，不代表副本可省略或已完成传播。

### 实际调用与认证边界

在现有IsolatedRuntime进入FileLock后、任何批准/子进程启动前，由受信组合根构造：

```python
host = HostAuthority(config_path, runtime,
    read_operator=authenticated_host_entry.read_operator,
    read_identity=verified_identity_mapping,
    invalidate_scope=invalidate_live_08_scope,
    read_deletion_review=read_fresh_08_deletion_review)
```

这些是受信本地callable，不来自配置或网络对象；代码来源由安装控制器固定，不动态import候选路径。`read_operator`只能返回 `AuthenticatedOperator(issuer,subject,session,product,instance)`；普通dict/客户端JSON拒绝。该类型是已认证宿主入口的交接对象，不负责认证本身，持有root代码执行权可以构造它，不能当密码/签名。当前没有生产管理员ID或认证入口配置，因此没有生产批准者；本轮测试只注入明确SYNTHETIC对象，没有把root UID或Console登录自动认作管理员。

`read_identity`每次返回严格`{product,instance,identity_evidence}`，来自受信组合根对实际09allow及产品映射的核对；hash形状检查不替代09。两个回调的会话/身份变化未经预先revoke会使HostAuthority粘滞阻塞，不能通过值恢复重开。正式受信认证入口及Python/Node跨包运行注入仍由08/宿主共同接线，本实现不新增RPC、监听器或认证服务。

独立07批准顺序：`begin_deletion_review()`先在owner线程调用revoke，立即invalidate10票、现有RecoverySession及08身份/范围，再递增并持久化宿主代际，然后才返回本进程不透明ticket及审阅副本。人工核对当前删除文件/来源及摘要后 `confirm_deletion_review(ticket)`重新读管理员会话、身份、配置、代际、07文件身份与精确摘要，原样一致才记录内存批准。票一次消费、不序列化；错票亦消费待审票，迟到/重复/跨实例拒绝。confirm不是完整恢复批准，也不批准05范围。新的05审阅必须在08重新校验/发布09身份后进行。

`revoke()`唯一在runtime.owner_thread、同一既有FileLock下写撤销记录：先清内存并通知08，后复用linux_updater.atomic_json进行文件fsync→replace→目录fsync，最后回读确认。一个runtime只绑定一个HostAuthority；另一个锁持有者及非owner线程拒绝。任何写入/回读/通知失败粘滞阻塞，不重试旧事件，不延续旧批准。目录fsync失败时文件可能已是新代际，但回执仍失败并关闭；不能把落盘的新字节当成功。重启创建新HostAuthority和随机controller_session，内存批准为空；不自动读取审计日志恢复批准。没有实现方案中的审计日志写入，审阅副本可由受信入口留痕但不能作为可重载许可。

### 给08的实时函数

| 10函数 | 08接点/语义 |
|---|---|
| read_administrators() | readAdministrators：精确product/instance/subjects/revision/identity_evidence；revision绑定配置、控制会话和宿主撤销代际 |
| read_operator() | readOperator：精确subject/session/product/instance；重新读取受信认证源并核指定名单 |
| read_revocation_generation() | readRevocationGeneration：当前持锁且未被外部替换的代际 |
| read_deletion_review() | readDeletionReview：每次调用注入的真实08 deletion_scope_review，不缓存review_sha256；期间失效不返回旧结果 |
| read_approved_sha256() | 07/08独立批准回调：当前管理员、配置、锁、代际、07字节与内存审阅均一致才返回原文件摘要，否则None |
| read_revision() | 10当前性证据，绑定会话/boot/配置/代际/身份/批准；仅摘要，不是allow或票 |

读取错误会清批准、通知失效并返回None，blocked保存诊断；不保留最后成功值。有意更换认证/身份/文件前先由仍有效指定管理员revoke，然后受信流程更新来源并重新审阅；配置改写/意外漂移须新的控制器会话，不自动接管。外部特权程序绕过唯一写入者会被观测到的inode/ctime/摘要变化拒绝；完整宿主离线回滚或root恶意伪造不由本地文件保证解决，当前真实记录无法独立核实时仍禁止批准。

本轮专属WSL定向证据在runtime/task10/host-authority-20261108。Python调用固定08组合与真实07/09；包内Node24.19.0运行固定08/05的TypeScript源（仅测试副本将相对import加.ts，Node原生transform-types），以本地测试stdio实时往返读取上述函数。该stdio桥只为验证，未交付为认证/生产传输。实例、管理员、身份候选、删除记录、副本摘要和隔离事实均明确合成；不以接口通过代替真实全副本测量、产品选择或生产激活。未重跑238/B2业务全套、旧工件或生产恢复。


个人版收尾范围更新：本模块独立待审，现有个人入口未引用host_authority，原--base不变；未接权威的--instance-config继续manual/退出2。本轮不再推进定制、C6 v2或生产认证传输；完整跨实例恢复平台不是现有个人版上线前置。新恢复入口缺管理员/配置继续拒绝，不影响或绕过既有个人安全门禁。
