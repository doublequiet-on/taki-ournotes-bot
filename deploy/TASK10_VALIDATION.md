# 任务10独立部分自查回执

日期：2026-11-03，Asia/Hong_Kong。续轮基于 `cab373c65a98424f37b817c22c7837e53155893b`，继续PR #199。状态：真实05/07/09独立接口验证与逐库观察自查完成；实际产品集成及安装恢复验收待门禁。本记录不表示独立审查、发布或生产验证。

## 版本与范围

- 仓库：`doublequiet-on/taki-ournotes-bot`，origin=`https://github.com/doublequiet-on/taki-ournotes-bot.git`。
- 实际目录：`E:/CodexData/Codex/taki/work/taki-dev/.worktrees/plan-20261103-10`。
- 分支：`codex/plan-20261103-10`；base=`main`，基础完整 SHA：`8d9e7301392ad548fe9554c4f4b09a0d53f615fa`。开工成功 fetch，提交前 ls-remote 仍一致。
- 本轮是独立 deploy 模块，未导入未合并依赖到运行入口，故 PR base 为 main；05/07/09/08仅消费固定提交的合同与独立检查函数，不声称统一候选已组合。四份完整依赖 SHA 和接口缺口列于 [接口交接](INSTANCE_RECOVERY.md#交00与下一包)。
- 入口 checkout 位于另一分支，原有 AGENTS、DEV_GUIDE、IMAGE_OUTPUT、其他未跟踪成果均未变更；本任务未切换该分支。
- 最终交付 commit/PR 以本记录所在提交及PR回执为准，不在文件中嵌入自身提交号。

## 完整文件清单

| 文件 | 作用 |
|---|---|
| `deploy/instance_preflight.py` | 纯实例资源计划、严格输入、可选只读 Linux 路径/属主观察 |
| `deploy/instance-plan.example.json` | 两个独立合成实例，全部路径/服务/端口显式，无真实凭据 |
| `deploy/recovery_preflight.py` | 独立组件测量、隔离证据失效、C1→C5→C6顺序及05 identity_evidence关联 |
| `deploy/storage_observation.py` | 逐库physical/logical、provider/观测provenance、专用roots覆盖与只读CLI |
| `deploy/recovery_matrix.py` | 18项实际 Linux 工件故障矩阵与参数缺项；结果始终 not_run |
| `deploy/INSTANCE_RECOVERY.md` | 副作用盘点、接口、停写/恢复/激活时序及交00缺口 |
| `deploy/TASK10_VALIDATION.md` | 本自查回执 |
| `deploy/L2.md` | 部署入口导航 |
| `deploy/LINUX_OFFLINE.md` | 区分有副作用 check/recover 与纯计划入口 |

## 首轮已执行（cab373c，保留历史证据）

| 检查 | 实际结果与边界 |
|---|---|
| `python -B runtime/task10/test_preflight.py`，Windows CPython3.12 | 最终20项，19通过、1跳过Linux路径观测。覆盖纯函数不访问文件/进程、不改输入、全部路径角色、用户/UID/AppID/service/timer/socket/端口冲突、错误产品/通道、回调短路、缺政策、旧DB+journal、权威失联、每项证据失效、独立组件内容变化、CLI无目标写入、矩阵不伪造运行。 |
| 同一脚本，WSL Ubuntu24.04 Python3 | 20/20通过，包含真实临时目录上的 symlink/hardlink、两个实例哨兵内容保持不变；bind-mount 别名仅用 inode 观测夹具模拟，没有实际 mount。没有 systemd 双实例启动/停止验证。 |
| `python -B -m unittest discover -s tests -p test_linux_updater.py -q`，Windows | 55项通过，1项POSIX相关跳过；原有事务、回退/单向迁移等回归。 |
| `python -B -m unittest discover -s tests -p test_linux_backend.py -q`，Windows | 84项通过，8项平台相关跳过；未修改这些执行器。 |
| 上述两组，WSL Ubuntu24.04 | 合计139项通过，1项跳过；属于替身/临时文件离线回归，不是目标宿主恢复演练。 |
| 两实例 JSON 经实际 CLI | manual，conflicts=[]，operations=[]，activation_permitted=false；退出码2。 |
| `python -B deploy/recovery_matrix.py` | 输出18项not_run和15个待提供参数，退出码2。 |
| `python -B scripts/check_architecture.py` | 通过，无新增架构例外。 |
| `git diff --check`、文档相对链接与文件清单 | 通过。 |

09真实 `check_identity` 从固定 `3dda6d2…` 的 Git blob提取，07真实 `check_recovery` 从固定 `a63de71…` 提取；07模块在本基线的sources包上下文中加载，只调用无存储依赖的C6函数。合成权威函数用于证明07在检查期间调用无参回调以及旧journal被拒绝，**不是生产权威实现或完整07包集成**。首轮05仅判定形状替身；已由下述续轮真实适配器/ScopeStore验证替代，不再作为当前接口阻塞。

首次 Windows 既有回归在沙箱默认临时目录遇到 `os.replace` 的 WinError5；将当前测试进程 TMP/TEMP 指向本任务 `runtime/task10/tmp` 后通过，没有改全局环境或业务实现。初始组件测量自查发现 Windows3.12 stat/fstat 的 ctime 语义不同，已将 inode-change ctime 比较限于POSIX；最终两个平台重新通过。WSL最初只读列举受沙箱权限阻止，提升执行权限后确认可用；没有把访问失败写成“无Linux环境”。

## 复现与残留

本地新增验证脚本按AGENTS不纳入提交：`runtime/task10/test_preflight.py`。同目录保留两个固定Git blob `product_identity.py` / `history_retention.py` 供脚本加载，以及 `preflight-tests.log`、`updater-tests.log`、`backend-tests.log`。复现必须保留这些文件；CI默认不会运行未提交脚本，其通过不能由CI代证。运行前后合成临时目录由 TemporaryDirectory 清理；没有本任务预览服务或应用进程需要停止。

Windows复现先将进程级 TMP/TEMP 指向任务内 `runtime/task10/tmp`，再执行上表命令。Linux可从仓库根运行：

```text
python3 -B runtime/task10/test_preflight.py
python3 -B -m unittest discover -s tests -p test_linux_updater.py -q
python3 -B -m unittest discover -s tests -p test_linux_backend.py -q
python3 -B deploy/instance_preflight.py --inventory deploy/instance-plan.example.json
python3 -B deploy/recovery_matrix.py
```

最后两条预期退出码2，不能由通用 shell 的“非0即安装失败”误解为异常。可交付脚本/示例位于deploy；仅本地自查脚本与固定参考副本留在忽略目录，需后续复查时沿用。

## 续轮实际依赖与验证

| 依赖 | 完整SHA | 用法 |
|---|---|---|
| 05/#195 | `a9c27d7eb94833df713995b8e6f9a85fc45ea0d5` | 原始scope-identity.ts、scope-authority.ts，由Node原生TypeScript转换执行，未复制/改写业务算法 |
| 07/#198 | `a63de717df487f896ff2182ea5bf6008672db4ad` | 原始history_retention、archive、compression；用其真实migrate在临时目录制备107夹具，观察阶段绝不调用migrate |
| 09/#194 | `9a3eca0b21dd7a7724143f988def59a5fc558dca` | 最新逐库合同及真实check_identity/check_scope_binding |
| 05接受的校验器来源 | `3dda6d29955328777f7e526c21bfd2828dbbea8a` | 与最新09的校验器blob完全一致；实际分别加载并比较，接受会话使用这份真实提取来源，9a3eca0标签被05拒绝的边界也已验证 |

本轮相对cab373c只改五个文件：storage_observation.py（新增）、recovery_preflight.py、INSTANCE_RECOVERY.md、TASK10_VALIDATION.md、L2.md，全部在deploy。PR累计九文件。没有更换base、混入05/07/09业务分支、修改08组合根或跨仓文件。

- Windows CPython3.12 + Node24.22.1：合计32项，31通过、1项Linux路径检查跳过。
- WSL Ubuntu24.04 Python3 + **原生Linux Node24.22.1**：最终32项全部通过。首轮WSL未找到Node时跳过的接口测试已补执行，不保留为最终通过证据。
- 真实05会话/ScopeStore含27个断言：空会话、一次发布、invalidate、旧/重复/跨会话ticket、重启、复制隔离、expected/AppID变化、observed组件变化、逐库logical不兼容、无独立approval保持manual、旧宽范围拒绝、checkerCommit不匹配拒绝。
- 真正调用09→ScopeStore.recovery→07.check_recovery；07收到无参当前权威回调。范围approval和删除权威仍为明确标记的合成替身，不能称生产权威。scope identity_evidence错配在进入07前拒绝；少一个删除副本由07返回manual。
- 存储夹具包括1/2/3/4及真实07生成的107/logical1/2/3/4；含0/null与unknown合成观察。缺legacy_version、错误版本/codec/source_sha/schema、copying状态、缺/错分区、缺/错观测provenance、漏库/多库、别名、读取异常、坏文件、WAL/SHM/journal、观察期间增加库均不产生完整schemas。实际CLI及观察前后文件集合/SHA相等，验证没有创建数据库/sidecar或改写原文件。
- 架构门禁、diff和相对文档链接检查通过。既有139项事务/后端回归属于首轮记录；本轮没有修改其代码，不冒称重跑。18项实际工件矩阵仍全部not_run。

Node Linux运行时仅解压在本任务 `runtime/task10/node-linux/node-v24.22.1-linux-x64`，未全局安装。来源 `https://nodejs.org/dist/v24.22.1/`，归档 `node-v24.22.1-linux-x64.tar.xz` 的SHA256与同目录官方SHASUMS256.txt匹配：`6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6`。没有运行真实服务。首次新夹具的SQLite连接未关闭导致Windows清理失败，修正本地夹具后重跑通过；未改业务打开方式来掩盖。Git初次schannel握手失败，用单次命令OpenSSL后端成功fetch/ls-remote，未修改全局配置。

## 续轮复现与实际保留

以下本地文件按AGENTS不纳入PR，仍在原工作区：`runtime/task10/prepare_interfaces.py`、`test_interfaces_storage.py`、已更新的 `test_preflight.py`、`interfaces/scope-session.mjs`、`interfaces/`内固定依赖原始blob及source-manifest.json、最终Windows日志、原生Linux Node工具及校验摘要。没有其他任务的未提交业务文件。复现需要这些本地脚本；CI不包含它们，不能以CI结果代证。

从本工作区根目录执行（Windows先创建任务内临时目录，再只对当前进程设置TMP/TEMP）：

```text
python -B runtime/task10/prepare_interfaces.py
python -B -m unittest discover -s runtime/task10 -p "test*.py" -v
python -B scripts/check_architecture.py
```

原生Linux复现，在该工作区根目录、PATH中加入上述Linux Node的bin目录后：

```text
python3 -B -m unittest discover -s runtime/task10 -p 'test*.py' -v
```

可直接交付的只读观察命令与清单参数见 [逐库观察](INSTANCE_RECOVERY.md#逐库只读观察)。只读观察结果不含删除代际或批准；它不替代压缩payload全量语义验证、远端副本发现或真实主机锁/停写。临时合成数据库、失败夹具残留和已解压的下载归档在核对归属后清理，保留复现脚本/工具/结果；无预览或应用服务需停止。

## 未完成门禁与兼容边界

个人/定制统一候选、实际工件与hash、范围批准模式、删除权威与留存政策/实现、目标宿主与完整主机资源盘点均未齐。05新版binding已提供并完成本轮真实接口验证；仍需实际安装版本登记和跨进程受信接线。18项实际候选故障矩阵未执行；没有实际 installer/restore/activation adapter，没有双实例服务运行证明、QQ或目标机验收。应用wheel/sdist与产品发行构建未执行：本轮控制器工具独立留在Git/deploy，不改产品打包清单，不宣称已经随发行交付。head CI 状态在PR回执另报。

不写持久业务格式，不修改既有个人控制器运行路径、启动permit或事务。代码回退不回退共享数据；未来恢复/迁移仍必须经过当前备份、兼容、事务与forward-only门禁。缺政策不能激活；没有合并、部署、重启真实服务、QQ消息、付费调用、生产数据写入或Haneoka仓库改动。

## 2026-11-04：PR201实际组合包独立验证

本节更新上轮“尚无实际工件”的时点结论；旧记录中的未执行保持历史事实。本轮只验证并补充此记录，没有修改部署/业务代码。

- 任务10基线 `ca34d361b083626777927997f7379bb2ae71e063`，原目录 `.worktrees/plan-20261103-10`、分支 `codex/plan-20261103-10`、草稿PR199继续使用；base不变。
- 实际候选 `a8bdc799c81ce655b76d45ff5a0b13e9b923c5bf` / PR201；组合ZIP SHA256 `8810db8ebaad54db68da1d2dc7dcbc9edb96fdc7322bf38406f6bcac4d2e0b12`，100522262字节。交接9个文件大小/hash及组合3292个文件全部核验通过。
- 输入08 `8d57eebb7efa279279166be53a6d9c102e6d90d1`，09装配输入 `610f14f8fd6ad55368a73e7ac4afea5f29e347a9`，Console组件源 `91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f`。本轮05执行安装包的scope-identity/scope-authority编译模块，07执行安装venv内history_retention/archive。09 checker直接导出自 `3dda6d29955328777f7e526c21bfd2828dbbea8a`，文件SHA256 `736c983ae4b0d03e39c893a886df07d3839b434381dd6fbef406ab2143ca1d98`，保存在包外；未用组合SHA冒充来源。
- 外置清单SHA256 `93f1b9f623c2bcb63bde5df5c66f7f8b422cf4b5beb3f692cf8054b9e331121d`；包内原始清单SHA256 `64582024fd05e89551f7a8196647bd0dd7ca23fc350374cb1b2769d545473a15`。包内多一个末尾LF，JSON相同，不能宣称字节一致。身份测试绑定包内摘要。

Linux环境为Ubuntu24.04.5 / WSL2 Linux6.18.54.1 x86_64、Python3.12.3、包内Node24.19.0。独立安装位于 `/home/lyy/.cache/taki-task10-actual-20261104/installed`，venv同父目录。使用 `unshare --net` 仅开loopback；Console15143/15144及合成后端随机端口仅在该私有命名空间。首轮/tmp安装通过后目录在后续WSL调用消失，已保留日志并在持久专属目录重新完整安装通过。

已完成：真实stage_combination暂存、组合完整性/安装探针、离线require-hashes wheel安装/pip check、71已安装模块与资源检查、Python实际入口--help；安装后的Console RPC46个鉴权方法和前端状态探针；另独立HTTP200/WS登录、退出撤权、重连重新认证、撤销设置保持和旧guard拒绝，使用实际BackendClient连接合成离线HTTP后端。未启动run.cjs的真实QQ适配器。

真实安装05/07+可信09接口12项通过，ScopeStore/session27断言。C1产品、实例、AppID和政策仍是合成输入；source_commit、protocol/capabilities、清单摘要和7项组件绑定实际工件，expected来自已核验清单，observed另读安装文件。它不是正式产品C1签发或范围批准。实际07制备schema1/2/3/4及107/logical1/2/3/4合成库，只读观察和缺元数据/分区/副本/读取异常均验证；观察前后hash不变。旧历史行和synthetic-deleted-topic备份复制到隔离恢复目录，由备份树外当前合成权威拒绝；只改代际标签但保留旧内容也拒绝；当前合成快照allow仍不激活，无权威manual。没有实现或证明生产话题恢复适配器。

Windows控制器回归32项：31通过、1项Linux检查跳过；Linux32项全通过。实物暂存故障检查10条：错hash/commit/Python拒绝；提取/校验/rename异常清理未发布目录；最终ownership回执失败保留已发布目录和staging记录；真实SIGKILL提取保留intent与孤儿目录；已有安装拒绝覆盖且原树重验不变。后两类必须manual reconcile，不得按目录存在自动接管。

完整18项逐项结果及复现脚本/日志保留在本工作区忽略目录 `runtime/task10/actual-20261104/`：`REPORT.md`、`matrix-results.json`、`delivery-verification.json`、`retained-fault-records.json`、`environment-final.json`，以及install-linux、installed-smoke、interfaces-linux、staging-faults、restore-simulation、windows-regression、linux-regression等日志。新增本地测试按AGENTS不纳入PR。故障大副本已在保存状态记录后清理，最终安装/venv、少量合成恢复数据和复现脚本保留；无测试应用进程残留。

复现从本工作区运行 `python runtime/task10/actual-20261104/verify_input.py` 和 `verify_delivery.py`（后者同目录）。安装脚本默认拒绝已有目录；重复验收需在脚本中一致设置新的独立base。先执行prepare_actual_interfaces.py再执行measure_actual_components.py生成最终组件测量测试；Linux私有网络命名空间内运行install_linux.py、run_probes.py、run_restore.py。不得把源码测试当作安装后探针；这些日志明确记录实际installed/venv路径。

给00的未完成项：没有提供固定旧完整应用工件，升级及B→C失败回B仍blocked；本包3个旧控制器实物与 `0359a8d515db5cd32a66617852edb29b25862580` 逐字节相等，但不能代替旧应用，未执行其新事务回退。双实例实际服务、半启动/停写超时、current指针/fsync断电、真实磁盘耗尽、宿主移交和Haneoka故障语义仍未完成。需补旧完整ZIP/hash、正式C1/外部expected、可信快照所有者与失效接线、当前删除权威及逐副本清单、宿主停写证明和manual事务恢复入口。最终能力裁剪、源码托管、批准/删除/留存政策及生产宿主仍未定；本包仍含聊天代码且delivery标记product_release=false、complete_corresponding_source=false。

层级结论：控制器暂存与隔离Console进程验证通过；恢复是合成数据门禁模拟；生产激活未实施，`activation_permitted=false`。所有allow均不是锁、激活令牌或真实范围批准。没有合并、部署、真实服务重启、QQ、付费模型或生产数据写入。
| 原矩阵项 | 本轮结果 | 证据与边界 |
|---|---|---|
| two_instances | partial | 32项控制器回归中双实例计划隔离通过；未运行双实例服务 |
| aliases | partial | Linux真实软/硬链接只读拒绝通过；未做实际服务bind mount恢复 |
| wrong_package | passed_isolated | 实物wrong hash/commit/Python拒绝；09合成身份变更拒绝 |
| first_install | partial | 实物暂存、离线wheel安装、入口及组件探针通过；提取/校验/rename/回执/进程死亡注入完成；无生产事务安装 |
| half_started | blocked | 无固定旧完整工件及部署服务适配器；未启动QQ适配器 |
| gateway_stop | blocked | 未接入真实服务控制器/PID停写证明 |
| migration | partial | 真实07生成107与只读logical兼容检查通过；旧完整工件读新数据回退未执行 |
| backup_missing | passed_simulation | 漏库、多库、sidecar、缺元数据/分区/来源、读取失败均manual且无部分成功 |
| current_switch | partial | 实物stage rename失败清理；最终回执失败保留manual；current事务指针/fsync断电未执行 |
| low_disk | not_run | 未执行真实文件系统容量耗尽；提取OSError不能代替低磁盘验收 |
| old_scope | passed_simulation | 实际安装05 ScopeStore旧宽范围拒绝；Console旧guard不能恢复撤销权限 |
| old_deletions | passed_simulation | 实际安装07拒绝旧history/topics快照及伪造新代际标签；无权威manual |
| authority_down | passed_simulation | 合成权威缺失/异常与证据变化fail closed；无生产权威 |
| code_rollback | blocked | 未提供固定旧完整应用工件；不以本包旧控制器或源码代替 |
| controller_rollback | partial | 实物3个旧控制器文件逐字节匹配0359a8d；新事务格式兼容/回退执行未验 |
| restore_recheck | passed_simulation | 逐库观察与全部revision失效验证；不产生激活许可 |
| host_handoff | blocked | 未提供目标宿主及旧主机停写证明 |
| upstream_down | not_run | 使用合成离线HTTP后端；不代表真实Haneoka故障语义验收 |

## 2026-11-04续轮：旧发行查找与最小reconcile提案

按用户要求未重跑已通过的首装、RPC与合成恢复。新组合继续固定a8bdc799c81ce655b76d45ff5a0b13e9b923c5bf，ZIP SHA256 8810db8ebaad54db68da1d2dc7dcbc9edb96fdc7322bf38406f6bcac4d2e0b12，实际读取摘要未变；没有换包或改checker来源。

一次有边界的查找覆盖本项目deploy发行说明、Checks发布流程、任务10既有交付记录，以及 `E:/CodexData/Codex/taki/artifacts`、`dist` 的归档文件名和明确发行清单；没有读取生产目录/配置/数据库或其他任务未提交业务代码。pr37本地记录只有旧wheel/sdist，不是配对组合；controller-settings/controller-review/source ZIP均排除。本地cutoff-query-v2基础Python包manifest提交71160ad5572c948c4c125c225c5f723b6dcc4ade、14352589字节、SHA256 c6606de82d4d58039a9b6c975b582b0130092af183cac1c05ada21fa1c43b52e，没有网关组合，不选为本次回退基线。

GitHub读取第一页20个Release后定位基线的精确标签，不继续无界翻页：
- [旧完整发行](https://github.com/doublequiet-on/taki-ournotes-bot/releases/tag/linux-8d9e7301392ad548fe9554c4f4b09a0d53f615fa)，源码 `8d9e7301392ad548fe9554c4f4b09a0d53f615fa`，新组合的Git祖先；发布时标prerelease。它是Linux x86_64/CPython3.12/Node24.19.0、协议2.2基础组合，不含可选knowledge附加包或新的最终定制批准。
- Release asset ID702175395：taki-koishi-linux-x64.zip，98544160字节；GitHub公布及本地实测SHA256均 `3f2e1db6c5bffcc5da2147300a0746374ed139e5c83654baf4130e78ce2e52d9`。下载至本工作区 `runtime/task10/old-artifact-20261104/taki-koishi-linux-x64.zip`，3136个成员由真实暂存校验器全部核验，包内清单原始hash `4093139032e2d57154793033e3526be562530cd8f3f673788c77a9509230b16c`。嵌套Python摘要 `734b7aba196a4223d98ab17ab5433c7db668d301b1fb8d1e8241a1ccb8bbb719`，与同Release基础Python资产一致。
- [Checks push运行41468776232](https://github.com/doublequiet-on/taki-ournotes-bot/actions/runs/41468776232) head为同SHA且success。Actions中koishi-combination ID13154742488未过期，外层97809065字节/hash cb18fc21964ebe44ce73511f07c2d3ad7d912a65a533dbb446d0bb1ab75ef1f8；linux-offline-release ID13154198560亦未过期。Actions只是独立来源记录，没有把其包装ZIP摘要当Release ZIP摘要；实际下载使用正式Release资产。元数据存github-source.json，找到适用基线后停止搜索。

在新建 `/home/lyy/.cache/taki-task10-transition-20261104` 和私有离线网络空间，旧包暂存后，运行真实旧/新gateway/lib/management与group-policies：旧版本写入两份合成管理文件→固定新包读取并撤销查询/通知权限→返回真实旧包读取，新撤权状态保持。返回旧代码前后settings/group-policies两个文件的SHA256相同，未恢复旧数据。新包复用上轮本任务安装，只做本次所需完整性重验，没有重跑首装/RPC探针。测试目录中harness-code-pointer由测试脚本切换，不是LinuxBackend.activate或真实服务事务；不能记成完整升级/回退成功。

实际调用既有LinuxBackend._check_reviewed_fingerprints，在独立目录缺批准policy时返回compatibility_review_required。没有补默认批准或伪造prepared/停写记录。原矩阵code_rollback从“缺旧工件blocked”更新为“管理格式/代码返回部分通过；完整服务事务仍blocked”，half_started的旧工件缺口解除，服务适配器缺口保留。迁移、B→C失败回B、current fsync断电及完整应用健康仍未执行，不以两个管理文件的兼容代表全库兼容。

[Manual reconcile最小提案](INSTANCE_RECOVERY.md#manual-reconcile-最小接口提案2026-11-04未实现)已整理只读证据、摘要类型、状态区分、歧义拒绝、既有模块责任与待验证集合；未实现。历史故障目录上轮已清理，本轮只引用留存快照，不宣称仍有可接管实物；本轮未删除任何失败记录、孤儿目录或事务状态。

复现文件在 `runtime/task10/old-artifact-20261104/`：github-source.json、原ZIP、run_transition.py、transition.cjs、transition-linux.log、transition-results.json；脚本base要求新独立空目录，使用上轮本任务新包安装路径，在unshare --net仅loopback且env白名单下执行。本轮只改本文与INSTANCE_RECOVERY.md，代码仍ca34d361b083626777927997f7379bb2ae71e063；未新增实现包/PR。完整服务升级/回退仍须真实服务适配器、兼容批准、宿主停写证明和相关权威，不可由本次合成数据代替。activation_permitted=false。

| 原矩阵项 | 续轮最新状态 | 剩余门禁 |
|---|---|---|
| code_rollback | partial：真实旧→新→旧管理文件兼容及数据不回退通过 | 完整服务事务仍blocked；兼容批准/服务适配器/停写证明未齐 |
| half_started | blocked：旧完整工件已齐 | 尚无服务半启动与停写控制验证 |
| current_switch | partial：沿用上轮证据；新增manual reconcile方案，未执行 | 真正事务指针/fsync中断恢复未验，不以测试指针代证 |

## 2026-11-04：候选3整包独立验收（00转交）

本轮固定PR201候选 `0026159bc369c768e783294e8447de99a5f1973a`，整包SHA256 `4ce3e43013199145e19e3a13caa089dd7b826fd431c22182192ece182bf9ca32`、103111283字节；3317成员逐项核验。输入为09 `candidate3-assembly-20261104/final/` 中ZIP、artifact-manifest、source-material-binding和combination，4份outputs/2份附属manifest摘要通过。旧a8bd证据保留，不沿用旧通过结论。08输入982f042889d9de84e13aa2aedaca51faeea5bd5b，09安全来源ca7a64175aa4c359c018b8a7e1dc6c506fbd6552；12在candidate3-gateway/REVIEW.md已关闭08接入复核，09交接/绑定里的pending文字落后，归09更新，10未改其文件。未纳入PR202。

包内清单418369字节/hash `2b55d44febfcf7054de8a667b044f1aee90f253ac612ef46bbd069afb1399564`；外置421722字节/hash `b97ee009ebaf8ff850f3e3984f46bb5cf639f7750c774f8cfbedb9e05a20b6ba`。首次严格字节assert失败；查明3353处CRLF/LF差异（只转换CRLF即逐字节相同），JSON相同。后续逐成员/hash核验保持严格，身份合成检查绑定包内原始摘要，不混用ownership规范JSON摘要。

新独立Linux安装 `/home/lyy/.cache/taki-task10-candidate3-20261104/installed`，venv同父目录；旧/新管理格式演练位于 `/home/lyy/.cache/taki-task10-candidate3-transition-20261104`。Ubuntu24.04.5/WSL2、Python3.12.3、包内Node24.19.0，私有网络命名空间仅loopback和env白名单；15143/15144/15145无宿主服务接管。

实际结果：

- 真实暂存、组合校验、离线require-hashes安装/pip check、71已安装模块与资源、实际入口--help通过。安装包Console RPC46方法、状态探针、HTTP200/WS登录/退出撤权/重新认证、旧guard拒绝通过。
- HTTP实际返回client.js与安装文件逐字节相同，JS MIME正确，SHA256 `60cdbd1f96efaf4f252de51d9f3732f8739105a7c36e30abb3aa87b979dfc20b`匹配12已审candidate.3，安装版本5.30.10-taki.3。没有重称浏览器全安全矩阵。
- 实际安装05/07＋包外固定可信09的12项接口测试通过，27个ScopeStore/session断言。checker仍真实导出自3dda6d29955328777f7e526c21bfd2828dbbea8a，hash736c983ae4b0d03e39c893a886df07d3839b434381dd6fbef406ab2143ca1d98。源码逐字对比证明05两模块、09checker、07retention和stage controller与旧固定a8bd未变，仅复用驱动，结果均在候选3上重跑。
- 真实07生成schema1/2/3/4与107/logical1/2/3/4；逐库只读检查、错误分区/元数据/副本/读取失败、观察漂移通过预期。恢复模拟拒绝旧history/topics、伪造新代际的旧字节；缺当前权威manual。产品身份选择、approval和删除权威仍明确合成，不产生真实批准。
- 真实旧完整Release8d9e7301392ad548fe9554c4f4b09a0d53f615fa（ZIP hash3f2e1db6c5bffcc5da2147300a0746374ed139e5c83654baf4130e78ce2e52d9）的3136成员重新核验；旧→候选3→旧管理模块验证新撤权保持，返回旧代码前后两份管理文件hash不变。测试指针不等于LinuxBackend.activate，真实兼容门仍compatibility_review_required。旧Console未获候选3安全修复放行，此结果不能批准安全回退或全库兼容。
- 10条暂存故障检查按预期通过：错包拒绝，提取/校验/rename失败无发布，最终回执失败和SIGKILL保留manual状态，重复安装拒绝覆盖。两个故障的原ownership及目录均保留，没有自动接管/删除/重试；manual reconcile仍为方案。

完整本轮报告、18项矩阵、失败和复现记录：本工作区 `runtime/task10/candidate3-20261104/REPORT.md`、`matrix-results.json`、`input-verification.json`、`install-linux.log`、`interfaces-linux.log`、`installed-smoke.log`、`served-component.log`、`restore-simulation.log`、`transition-results.json`、`staging-faults.log`、`retained-fault-records.json`、`environment.json`及evidence-sha256.json。脚本仅本地按AGENTS不纳入PR。复现用固定Git源码与原ZIP，在新的独立base内依次运行install_linux.py、run_all.py、served-component.cjs；现有故障目录不可为重跑而删除。Windows仅做实际输入/成员核验，未重跑未变10全套32回归；不拿历史32项结果替代本包验收。

首装通过范围为组件安装和隔离进程，未安装真实用户/unit/timer/socket；完整服务升级/回退、半启动/停写超时、单向应用迁移、磁盘耗尽、current/fsync断电、跨宿主、真实上游故障仍未完成。缺兼容批准、服务适配器、宿主/停写证明、真实范围批准及删除权威，不能补成合成默认值。最终个人/定制选择、物理裁剪/完整源码/发布仍阻塞，delivery的product_release/complete_corresponding_source/published=false保持。activation_permitted=false；未合并、部署、真实服务重启、QQ、付费模型或生产数据操作。代码基线仍ca34d361b083626777927997f7379bb2ae71e063。
| 原矩阵项 | 本轮结果 | 证据与边界 |
|---|---|---|
| two_instances | not_run | 候选3双实例服务未执行；10既有纯计划检查不替代整包实例验收 |
| aliases | not_run | 候选3服务路径别名未执行；既有10控制器代码未变 |
| wrong_package | passed_isolated | 实际整包错hash/commit/Python拒绝；真实09 expected/observed变化拒绝 |
| first_install | partial | 新空目录暂存、离线wheel安装、71模块/入口、安装后HTTP/WS及46方法通过；无生产服务首装 |
| half_started | blocked | 缺隔离服务事务适配器；未启动QQ适配器 |
| gateway_stop | blocked | 无真实service/PID停写证明或超时场景 |
| migration | partial | 真实07生成schema1/2/3/4及107/logical1/2/3/4，观察与不兼容检查通过；应用跨版迁移未执行 |
| backup_missing | passed_simulation | 漏库/多库/sidecar/缺元数据及分区/读取失败无部分成功 |
| current_switch | partial | 实际stage rename异常清理、最终回执失败保留manual；版本测试指针不代证真实updater current/fsync |
| low_disk | not_run | 未执行真实磁盘耗尽，OSError注入不等于该场景 |
| old_scope | passed_simulation | 候选3实际05旧宽范围拒绝、无独立approval保持manual；Console旧guard拒绝 |
| old_deletions | passed_simulation | 真实07拒绝旧history/topics及伪改代际；当前合成快照allow不激活 |
| authority_down | partial | 缺删除权威manual、会话失效/重启拒绝；生产权威未选择 |
| code_rollback | partial | 真实8d9旧→候选3→旧管理格式与数据hash通过；完整服务回退blocked，旧Console未经候选3安全放行 |
| controller_rollback | not_run | 包内rollback成员完整性核验不等于旧控制器执行新事务 |
| restore_recheck | partial | 实际12项接口测试含独立expected/observed/schema变化和观察漂移；全服务重验未执行 |
| host_handoff | blocked | 无目标宿主及旧宿主停写证明 |
| upstream_down | not_run | 离线fixture后端不代表Haneoka故障验收 |

本轮回读Checks41738407065：push/head0026159bc369c768e783294e8447de99a5f1973a，7项验证success，publish-linux-bundle skipped。此CI仅说明固定候选检查通过，不是产品发布或任务10完整激活验收。

## 2026-11-04：服务事务边界增量（不重跑已绿矩阵）

开工head c3d782c90d24802ebfa057a151b60aaaf268c185，Checks41744977067本轮已确认success。固定候选0026159bc369c768e783294e8447de99a5f1973a/整包hash4ce3e43013199145e19e3a13caa089dd7b826fd431c22182192ece182bf9ca32未变；外层hash重新核对，包内0359旧控制器字节与Git固定对象一致。已有首装/RPC/逐库恢复测试未重复。

入口核对发现：服务停启、current切换和失败回退已实现，原先主要缺隔离驱动；独立实例配置/permit/launcher参数化、previous=null首装事务、05/07/09实际激活接线确实未实现。现有CLI配置白名单固定既有实例，未放宽。具体入口/责任及待决选项见INSTANCE_RECOVERY.md“现有服务事务入口定位”。

本轮从candidate3-20261104/candidate-source的固定Git代码导入LinuxBackend/KoishiControl/Engine；在 `/home/lyy/.cache/taki-task10-service-boundary-20261104`、私有仅loopback网络中执行六个新场景。运行真实控制器stop/activate/start、FileLock、状态phase持久化和恢复逻辑；systemctl仅由本地驱动解释为专属HTTP子进程启停，绝不调用宿主systemctl。manifest、健康、预批准、snapshot边界及A/B版本ID为SYNTHETIC，不是整包实际业务服务或生产政策。activation_permitted=false。

| 本轮新检查 | 结果 | 证据边界 |
|---|---|---|
| 两个同时运行的实例 | 4个实时端口不同；操作alpha后beta PID/permit/当前版本不变 | 独立进程/目录通过，不等于独立Linux用户/unit/cgroup验收 |
| 后端已启动、候选网关启动失败 | Engine真实_switch→_rollback回transaction.previous，旧合成双进程健康 | 模拟网关start错误，健康来自合成HTTP；不证明真实应用可回退 |
| 网关停写超时 | 实际子进程在限定等待后仍存活，暂停并保留事务；backend未被停、current未变 | 驱动缩短等待为0.15秒，不冒称实际systemd的90秒超时测试 |
| 实际activate的os.replace失败 | current仍指向旧目录 | 文件系统异常注入，不等同断电 |
| 实际activate目录fsync失败 | rename已可见、新指针保留，但没有后续启动或耐久成功结论 | 需要recover/人工核对，不能把可见性当持久化证明 |
| current切换后模拟进程中断 | 持久phase仍activating；Engine.run(recover_only=True)回事务previous；共享数据标记未变 | BaseException中断模拟，无真实机器断电；恢复动作仅专属合成进程 |
| 包内固定0359旧控制器读取version=2 | 拒绝state_invalid；当前控制器可读，文件字节未变 | 仅格式拒绝门，未安装/回滚真实控制器 |
| 空首装recover与独立配置 | uninitialized；controller_configuration_out_of_scope | 直接证明当前入口没有独立空机首装能力，不能用旧baseline冒充 |

所有新检查按预期完成，子进程finally全部退出；probe.log/service-results.json/controller-boundary.json含实际结果，未出现未解决测试失败。原候选3矩阵仅把two_instances、half_started、gateway_stop、current_switch、controller_rollback增加isolated_driver/格式拒绝证据，整体仍partial；不把服务真实环境的blocked消除。没有新增生产实现；测试驱动、固定旧控制器副本和合成状态按AGENTS保留在本工作区忽略目录 `runtime/task10/service-boundary-20261104/`，包括probe.py、controller_boundary.py、binding.json及日志。所有状态保留，不删除旧故障记录。

具体决定已整理为兼容双向升级/单向前向修复、安全回退版本名单、每次批准/独立范围锚点、外部删除服务/人工当前快照、专属systemd宿主/既有宿主明确隔离资源、个人/定制选择的选项与影响，均未代选。需实现的10接口和需00/用户/05/07/08/09提供的输入明确分开。本轮只改两份deploy文档；无PR202、合并部署、真实服务、QQ、付费模型、生产数据或其他对话操作。

## 2026-11-04：独立首装准备实现与人工复核会话

基于b07b75c1ceaacee2bf7eaf0ec4f11ec6a2b204f7继续原工作区/分支/PR199；固定运行候选仍0026159bc369c768e783294e8447de99a5f1973a，ZIP SHA256仍4ce3e43013199145e19e3a13caa089dd7b826fd431c22182192ece182bf9ca32。未以材料提交替换运行来源，未纳入PR202。

本次代码只在deploy：新增instance_install.py；修改koishi_launcher.py、recovery_preflight.py，文档更新INSTANCE_RECOVERY.md、L2.md及本记录。独立配置/unit草稿、previous=null暂存记录和一次性人工复核会话已实现；原生产配置/launcher、Engine状态与单向格式保护未修改。草稿launcher只返回manual/退出2，未实现完整独立安装或实际恢复/激活。activation_permitted始终false。

流程选择为每次恢复人工检查当前权限及删除记录，未知即不恢复；这不提供任何具体范围批准或当前删除证据。完整服务验收选择专用Linux VM/完整systemd容器。本机仅发现共享WSL Ubuntu-24.04，PID1 systemd；未发现Docker/Podman/Multipass/VirtualBox/systemd-nspawn或已配置machine。没有改全局虚拟化、装新服务或用共享WSL接管unit。

新验证目录为runtime/task10/first-install-20261104/，Linux实际暂存目录/var/lib/taki-task10-firstinstall-20261104。root拥有目录与祖先，在unshare --net、仅loopback环境运行；不调用宿主systemctl。alpha用固定实物完成暂存，previous/current为null，无current链接/permit；beta注入stage失败后保留staging intent，重复调用拒绝，alpha记录不变。这里不是首次应用安装/RPC重跑，而是新首装准备事务验证。所有失败状态保留。

验证结果：
- 新增4项本地测试：Windows 3通过、Linux实物项跳过；Linux4通过。最后人工报告错误状态调整后只重跑3项非实物检查，均通过。
- 原预检/逐库接口32项在Windows工作区临时目录运行，31通过、1项Linux检查跳过。Linux控制器/接口合计116项，115通过、1跳过；同一初次命令额外的Console测试模块因未设置tests导入路径失败，不能把整条命令称全绿。补PYTHONPATH=src:tests后单独FreezeTests 9项全部通过（含原launcher）。
- Windows默认沙箱TEMP的rename访问拒绝、初次合成identity/deletion夹具缺contract_version、测试导入路径和Windows缺PIL均留失败日志。TEMP改到本任务目录后接口通过；夹具补真实合同字段后通过；Console回归使用已有完整Linux venv通过。未放松实现或测试预期，未为Windows安装全局依赖。
- python -B scripts/check_architecture.py通过；git diff --check通过。旧实物RPC、HTTP/WS与合成恢复已绿套件未重复。

复现入口（在本worktree，日志同上述目录）：
1. Windows：python -B runtime/task10/first-install-20261104/test_prepare.py。接口测试需TEMP/TMP指向本任务可写目录。
2. Linux：unshare --net后ip link set lo up；使用现有candidate3隔离venv，PYTHONPATH=src:tests，执行python -B -m unittest runtime.task10.test_preflight runtime.task10.test_interfaces_storage tests.test_linux_backend，以及python -B -m unittest tests.test_console_policies.FreezeTests。
3. 实物暂存脚本test_prepare.py要求Linux root与全新专属父目录（默认/var/lib/taki-task10-firstinstall-20261104）。现目录已保留，重复运行必须拒绝；复现者在脚本settings中显式换新专属目录，不能删除旧intent求通过。工件参数完整写在脚本和first-install-result.json。

矩阵增量：first_install仍partial，仅新增真实包首装准备事务证据；two_instances新增配置/unit名称及失败不串写，不代证不同用户/cgroup；恢复人工会话的invalidate/重启/重复/当前证据变化按预期拒绝或manual。完整systemd首装、升级、回退、开机恢复仍blocked。其余18项矩阵维持原状态。

给00的下一包：提供专用完整systemd测试环境及实例用户/路径/端口；确认受信人工入口与备份外当前权限/删除证据来源；完成Python/Node环境、端口、permit/launcher和锁内恢复后重验接线。08负责组合根/Console，05/07/09接口与checker来源仍由各负责人维护。manual reconcile仍仅方案，不补写ownership、不删除孤儿目录、不实现接管。未合并、部署、操作真实服务、QQ、付费模型或生产数据。新增测试和全部日志依AGENTS仅保留在忽略runtime目录，不纳入PR。

## 2026-11-04：12复核P2—人工会话在途失效修复

被审f3faea3953c4c925b03dd0031ce695c5f4953242的begin/confirm在回调期间invalidate可复活ticket或返回reviewed，采纳任务12复现。recovery_preflight.py增加实例内Lock与单调代际：begin在锁内撤销旧票并捕获代际；confirm在锁内原子消费票并捕获新代际；回调与报告复制/比较在锁外；发布ticket或reviewed前锁内复核代际。invalidate和第二次begin可立即撤销在途检查，不等待长回调。重复确认仅一次消费；没有新RPC或持久批准，activation_permitted仍false。

本地独立事件屏障测试6项，覆盖begin/confirm期间invalidate、第二次begin分别越过两阶段、跨线程重复confirm及回调内直接invalidate。相同测试对旧Git固定源码5项失败、1通过；修复后Windows/Linux各6通过。原纯预检/串行人工会话/launcher拒绝与profile共23项，22通过、1 Linux路径检查跳过。架构门禁及diff检查通过。未重跑实物安装/RPC/恢复矩阵。测试和完整日志按AGENTS仅在runtime/task10/review-race-20261104保留；复现命令python -B runtime/task10/review-race-20261104/test_race.py，基线由git show f3faea3:deploy/recovery_preflight.py导出并以TASK10_REVIEW_SOURCE指定。

此修复只保证内存会话操作的同步和在途撤销，不是跨进程锁或启动许可；返回完成后发生的后续失效仍须实际执行边界重验。完整launcher、独立安装完成态及锁内05/07/09接线尚未实现。原head CI41752494642最终7项success、publish skipped；新head CI须另绑定。专属WSL环境创建已获用户单独授权，后续环境记录不得与本P2纯内存验证混为一项。

## 2026-11-04：独立离线安装与实际隔离进程接线

开工固定head d4be5dabb121c2758f378b812c0035da8e44b9b7；继续原工作区/分支/PR199。本轮新增deploy/isolated_runtime.py，扩展instance_install.py，并更新本记录、INSTANCE_RECOVERY.md和L2.md。未改旧生产launcher/Engine或05/07/08/09源码。

install_runtime现已实际建立Python venv、安装固定带hash离线wheel和验证依赖、使用组合内Node、提取并封印代码/环境，记录installing→installed及previous=null；installed不是激活态。新增IsolatedRuntime在既有FileLock内调用受信停写/身份/范围/删除检查和人工复核，按30秒、每角色一次的内存许可启动已测量隔离入口，组件或当前证据变化拒绝。执行线程固定，invalidate可跨线程；恢复后先撤销并停子进程再重验，新启动须重新批准。源码完整测量与原封印约定复用，未设计通用RPC或持久allow文件。

实际验证仅在已授权Taki-Task10-2404，systemd-run的NetworkNamespacePath指定仅lo。全新/var/lib/taki-task10/wiring/instances/gamma、delta各自安装固定候选0026159bc369c768e783294e8447de99a5f1973a/ZIP SHA256 4ce3e43013199145e19e3a13caa089dd7b826fd431c22182192ece182bf9ca32；Python配对hash da2e78d7c72dc40059cba678673c232ff366977a4bbbc79210fd5b24f814ca6b。可信09 checker字节再次比对Git3dda6d29955328777f7e526c21bfd2828dbbea8a:deploy/product_identity.py完全一致，hash736c983ae4b0d03e39c893a886df07d3839b434381dd6fbef406ab2143ca1d98，未用组合SHA代源。

本地证据runtime/task10/runtime-wiring-20261104/：两条installed回执、13项矩阵增量、最终5项回归均按预期；新进程真实运行包内Console/Koishi/BackendClient，Python为安装包导入后的合成HTTP后端。实际UID999/997，端口29100/29101/29102与29200/29201/29202，HTML及健康/后端HTTP通过。没有加载QQ适配器、真实账号或模型。

| 新场景 | 实际结果 |
|---|---|
| 两套运行环境安装与重复安装 | installed记录及代码/venv封印形成，重复安装拒绝，不覆盖原记录 |
| 09身份allow、05未注入approval | manual/scope_approval_policy_pending，未产生票 |
| 人工确认缺字段 | reject，未产生票 |
| 当前范围/删除代际/入口文件变化 | 启动前重验拒绝并撤销未用票 |
| invalidate、过期、第二次approve、重复消费 | 旧票拒绝；重复backend不能再次启动，独立gateway票仅正常消费一次 |
| 同时两实例真实进程 | 两个不同UID、不同端口，真实Console页面可访问，回执activation_permitted=false |
| 恢复后删除权威变更 | first.after_restore真实停止gamma子进程，07拒绝旧代际；delta PID继续存活且HTTP正常，未复活旧数据 |
| 最终并发/停止边界 | 人工回调期间invalidate无票；非控制线程测量拒绝；恢复后重验使旧票失效，全部直接子进程停止后释放锁 |

调用05的包内ProductIdentitySession/ScopeStore、09真实check_identity/check_scope_binding和07真实check_recovery；历史SQLite physical/logical3由只读观察器生成schemas。范围批准、当前删除权威、产品selection、人工确认与部分隔离修订值均明确合成。stop回调在FileLock下实际核对这些全新实例没有登记writer unit，恢复后真实停止本会话子进程；这不是生产全writer/timer/socket停写适配器。未运行真实数据恢复、完整生产双进程启动或不安全旧版回退。

失败日志保留：首次systemd退出225/NETWORK，原因是WSL空闲终止后/run/netns消失，未退回共享网络；改为同一调用内建立并核验namespace再启动unit。首次历史夹具缺boards/gaps/health被observer拒绝；在新合成authority目录补齐要求，不改观察器、不删除旧库或成功安装。一次本地驱动字符串替换误改日志目录日期，命令未启动；修正后继续。matrix-passed.log与systemd-run.log分别绑定13项和最后5项通过，namespace-missing.log/incomplete-fixture.log保留失败。

Windows原预检/串行会话/launcher拒绝/profile23项，22通过1项Linux检查跳过；架构门禁与diff检查通过。没有重跑原RPC、所有历史格式或旧完整故障矩阵。本次first_install从只有暂存推进到installed准备态与真实隔离双进程；生产安装/恢复仍partial，18项总矩阵没有改成全通过。

复现文件：transfer.py打包本任务deploy快照及固定工件到全新专属目录；exercise.py执行两套安装，run.py在同一WSL会话创建仅lo namespace并systemd-run；exercise_resume.py只重用已成功安装的封印环境，在新合成权威目录验证13项；exercise_final.py针对最后线程/停止保护跑5项。脚本绑定目录已存在时不可直接重装，需另选全新专属案例目录或显式核对已有installed记录，禁止删除意图求绿。binding.json记录两份源码文件hash及真实依赖源；evidence/保留每实例first-install/installed/session回执及install.log。新增验证脚本按AGENTS不纳入提交。

精确下一包已列INSTANCE_RECOVERY.md末表：独立launcher CLI跨进程可信会话入口与角色/实例绑定、真实全部writer/trigger隔离证明、第一进程启动到第二进程检查期间的业务禁写门、真实当前05/07权威注入及运行中撤权、实际恢复执行与恢复后副本全量测量。当前CLI仍manual；本轮实际运行通过受信进程内API，不把磁盘allow当可信桥。缺生产批准不阻挡上述独立实现；缺可信接口也不由10伪造。专属环境已就绪，最终产品selection/安全回退目标/具体生产恢复未授权。PR202排除，无合并部署、生产/QQ/付费模型或其他对话联系。

## 2026-11-04：12新增P2—首次测量前绑定取消代际

573c7586c3e05d2699c31e6523e68c70521a7df1的IsolatedRuntime.approve在首次_measure后才捕获代际，使该测量期间invalidate被覆盖。现从首次测量前原子建立操作代际并清旧票，测量后立即核代际再进入人工复核，最终签票检查仍保留。未修改已闭的ManualRecoveryReview修复。

复用12具名check_runtime.py，仅改源码定位、缺陷预期及新增第二次approve首次测量取消例；专属WSL仅loopback、测量/Popen明确替身，10场景通过。原ManualRecoveryReview六项竞态回归通过。证据runtime/task10/initial-measure-fix-20261104；未重跑实物安装/旧矩阵。独立P2提交后再处理08固定37af64177389c4faa944d4dd7a9360f73980aaa6合同，P2通过不等于整体恢复或生产激活通过。

## 2026-11-04：10可信子会话与双角色屏障

基线及独立P2修复 `fb1b3187586e64392d4f0022caa884f1e8c209c0`；消费08合同 `3d51c9335cf9b65b78d99de4d034728b74a694dc`，不修改08文件或替换冻结工件。实现仅deploy/isolated_runtime.py、recovery_session.py、recovery_session.cjs及对应文档。原未跟踪草稿归本任务，已保留副本，改为保留生命周期通道而非撤销即断开；无自动接管/通用RPC。

专属Taki-Task10-2404，root控制器/systemd、私有仅loopback netns、现有gamma安装的Python3.12与固定组合0026159包内Node：真实降权UID999的两个进程在首角色等待期间不import业务；双方ready和重审后才加载测量过的合成entry；运行阶段不再调用停写preflight；scope代际变化后quiesce→drain→close，双进程退出，停止确认前锁保持，最后context释放。5条实物检查通过。应用entry/批准/当前权威/宿主停止事实明确SYNTHETIC；不宣称运行了08真实业务lifecycle或验证生产全writer。

协议负例15项通过：旧停写报告不能充当running authority且撤权粘滞；运行snapshot必须匹配当次审阅；人工回调取消；读取器内invalidate；错角色；断联；越序关闭；pending终止记录保锁；缺宿主停止证明保锁；分段帧跨poll超时；不完整帧不阻止监测撤销；第二角色坏票撤销等待同伴；真实Python/Node等待进程半启动死亡；控制通道关闭；不合作进程有界强停。前12项使用真实私有socket与实际会话/人工检查逻辑，安装测量和进程观察为替身；后三项启动真实UID999等待/阻塞进程，不加载应用、不开放端口或发出网络请求。协议驱动运行于专属发行版，不声称它是systemd完整业务恢复测试。

新代码上首次_measure P2的10项回归（专属WSL unshare仅loopback）及原ManualRecoveryReview六项Windows竞态回归通过；Python语法、Node语法、架构门禁和diff检查通过。未重跑原首装安装、RPC、历史格式、旧包回退矩阵。

保留失败：首次Windows tar导出带组写权限，wrapper trusted校验按预期拒绝，未加载业务；第二次新目录将导出控制器固定root:root/0644后通过。协议结果第一次导出时Windows GBK控制台不能编码WSL提示，Linux测试exit0和原日志保留；修复仅日志输出后导出成功。没有放宽权限、生命周期或测试断言。

本地证据目录runtime/task10/trusted-session-20261104：exercise.py、protocol.py、run.py/run_protocol.py/run_p2.py、results.json、protocol-results.json、systemd.log、attempt1-permissions.log、p2-regression.log。Linux实物源/脚本/结果在 /var/lib/taki-task10/trusted-session-v3，协议负例在v2；首失败v1及首成功v2日志保留。复现run.py需新专属目录、唯一unit/entry/marker名称，拒绝覆写原证据。run_protocol.py只运行等待器与合成状态，不调用真实服务。最终实物正例已重验运行期守卫及独立监测；随后完善强停回执与串行stop，协议负例重验强停标记为true/orderly=false。三份控制器源码按本地binding.json核验。

下一包由08消费文档定义的start(session)、assert_current/assertCurrent、state/report；接真实Backend quiesce/close及网关drain。生产05/07/09及宿主读取器逐字段缺口见INSTANCE_RECOVERY.md，不把SYNTHETIC的current/stop回执当生产批准。activation_permitted始终false，独立CLI仍manual，未合并部署、生产/QQ/付费或PR202操作。
