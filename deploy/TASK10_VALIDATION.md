# 任务10独立部分自查回执

日期：2026-11-03，Asia/Hong_Kong。状态：独立实现/自查完成；实际产品集成及安装恢复验收待门禁。本记录不表示独立审查、发布或生产验证。

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
| `deploy/recovery_preflight.py` | 独立组件测量、隔离证据失效、C1→C5→C6顺序及失败状态 |
| `deploy/recovery_matrix.py` | 18项实际 Linux 工件故障矩阵与参数缺项；结果始终 not_run |
| `deploy/INSTANCE_RECOVERY.md` | 副作用盘点、接口、停写/恢复/激活时序及交00缺口 |
| `deploy/TASK10_VALIDATION.md` | 本自查回执 |
| `deploy/L2.md` | 部署入口导航 |
| `deploy/LINUX_OFFLINE.md` | 区分有副作用 check/recover 与纯计划入口 |

## 已执行

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

09真实 `check_identity` 从固定 `3dda6d2…` 的 Git blob提取，07真实 `check_recovery` 从固定 `a63de71…` 提取；07模块在本基线的sources包上下文中加载，只调用无存储依赖的C6函数。合成权威函数用于证明07在检查期间调用无参回调以及旧journal被拒绝，**不是生产权威实现或完整07包集成**。05仅注入对应判定形状的替身，未伪称05/09新版桥已通过。

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

## 未完成门禁与兼容边界

个人/定制统一候选、实际工件与hash、05/09接口对齐、范围批准模式、当前删除权威政策/实现、目标宿主与完整主机资源盘点均未齐。18项实际候选故障矩阵未执行；没有实际 installer/restore/activation adapter，没有双实例服务运行证明、QQ或目标机验收。应用wheel/sdist与产品发行构建未执行：本轮控制器工具独立留在Git/deploy，不改产品打包清单，不宣称已经随发行交付。head CI 状态在PR回执另报。

不写持久业务格式，不修改既有个人控制器运行路径、启动permit或事务。代码回退不回退共享数据；未来恢复/迁移仍必须经过当前备份、兼容、事务与forward-only门禁。缺政策不能激活；没有合并、部署、重启真实服务、QQ消息、付费调用、生产数据写入或Haneoka仓库改动。
