# 独立实例计划与恢复预检（任务10）

本轮实现部署侧纯计划、恢复检查编排与文件测量。**没有安装器、数据恢复执行器或自动激活入口**。`checked` 只表示此次受信回调检查完成；所有输出均为 `activation_permitted=false`。缺候选、缺政策、缺当前权威均不会补成批准。

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
3. `check_scope()`：05 `ScopeStore.recovery()` 的受信同步桥接回执 `{outcome,reason,evidence}`，其中 deny 映射 reject；缺接口/未选独立批准政策返回 manual。必须包含当前身份校验及独立 approval，不得拿旧备份的 scope 自证批准。**本轮没有接通 Python/TS 桥，也没有解决05与09固定版本的 binding 差异。**
4. `check_deletion(candidate,current_authority)`：注入07原始 `check_recovery`；candidate 是07规定的测量结果，而 `current_authority` 原样传为**无参回调**，在检查时读取备份和恢复树之外的当前权威。不可传预先读出的 dict、从备份闭包读取的权威或把旧 journal 当最新记录。
5. 每个检查前、全部检查后重读隔离与修订证据；变化返回 `manual/preflight_evidence_invalidated`。全部通过返回 `checked/preflight_only_activation_pending`，不是锁、permit 或实际激活决定。

09/07要求 `contract_version=1,status=allow|reject|manual,reason,evidence`；05使用 `outcome=allow|deny|manual`。allow 必须有非空 evidence；拒绝/待人工允许空证据。缺回调、隔离未证明或 OSError/TimeoutError 为 manual；坏判定结构为 reject；编程异常向调用者抛出，不能吞成成功。任何失败都不得产生外发动作。

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

## 交00与下一包

本轮 base 为 main，无代码堆叠；05/07/09/08仅固定接口参考，未复制其业务实现：

| 包 | 固定参考 SHA | 仍需接口 |
|---|---|---|
| 05 / #195 | `226ecce11299be42c349cc815f35de12cf463eac` | 对齐09新版 binding 的已提交 head；独立批准模式、当前权威与同步桥接的失效生命周期。固定05仍描述完整 candidate binding，不能声称与09已组合通过。 |
| 07 / #198 | `a63de717df487f896ff2182ea5bf6008672db4ad` | 当前删除权威实现/政策、完整副本清单与实际测量、schema 到09契约映射。入口 `check_recovery(candidate,current_authority)` 已做独立函数组合验证。 |
| 09 / #194 | `3dda6d29955328777f7e526c21bfd2828dbbea8a` | 两产品实际工件、组件精确映射、inventory/manifest、外部 expected、回退目标及最低控制器；固定 binding 为 `{contract_version,manifest_sha256}`。 |
| 08 / #197 | `509abc81efc2433cbc197050ddc5d64c40f48f5a` | 最终组合根与能力选择、实际接纳/外发门。10不修改其装配文件。 |

00还需确认目标宿主、同AppID全局接收者、安装/备份责任和恢复目标；本轮不联系其他对话。Haneoka为外部 serverless 数据服务，Taki宿主待定；不假定 localhost、共享磁盘或可直接读数据库。Haneoka R2/D1/采集状态不归本包恢复；连接端点/权限/上游失败语义由03/09实际合同提供。

验证与完整文件清单见 [任务10自查记录](TASK10_VALIDATION.md)。
