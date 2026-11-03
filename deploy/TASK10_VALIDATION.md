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
