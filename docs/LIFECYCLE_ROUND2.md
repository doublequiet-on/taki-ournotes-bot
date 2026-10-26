# 请求生命周期与素材所有权（第二轮）

本批基于 `05eb115102f5a36d33ecb896fb2fd2f450b8acbd`，保持协议 2.0、单网关／单业务状态所有者及原并发预算。请求核心与素材兼容 A 一起交付，素材启用 B 单独交付，版本回收属于独立受信控制器变更。

## 请求核心

- QueryGate 与后台线程共用抗重复取消的排空等待。调用者在真实线程结束后才传播取消；排队取消不启动线程，工作槽不提前释放。HTTP 关闭也由独立收尾任务负责，先等待维护与初始化，再关闭后端存储和 DataOwner。
- Haneoka 的每个直接逻辑命令捕获本地目录、metadata、对应来源及一份别名快照。解析、筛选、文字、上下文和绘图准备使用同一视图；不新增上游身份探测，不持锁绘图。普通续查也使用该边界；固定页和 Meta 捕获详情保持绝对 TTL。Yume 仍遵循既有刷新与详情策略。
- 持久接纳后立即登记最小 Work。终态保存失败时先撤销发送许可，再保留所有者与固定目标终态；reaper 按 1／2／4 秒追加最多三次幂等保存。未决期间不接纳新请求，耗尽后保留故障，修复存储并重启时由原恢复流程处理，不重执行业务或补扣额。
- `/v1/ready` 新增 `maintenance`，报告故障原因、待保存数量／年龄、重试耗尽及 reaper 存活。未决终态或维护失败返回 503；普通诊断库既有降级保持。交付账本格式不变。

## 素材 A／B 与恢复边界

`ImageMaterials.acquire(bytes, {signal, deadline})` 支持取消和毫秒绝对截止时间。未启动的冷队列项和等待状态队列的初始检查均可移除并释放预算；同内容其他调用独立继续。已启动的外部动作仍由 store 收尾；close 停止接纳并排空。实际启动脚本显式等待 Receiver 收尾，再卸载 QQ 适配器；重复卸载复用同一任务，包含通知与遥测收尾。冷并发仍为 1，最多 8 个调用／24 MB 输入，热命中不等待无关冷上传。

schema 2 显式区分 `owned`、`completion_unknown`、`deletion_unknown`。未知项不发 lease、不参与普通回收，仍占用容量。DELETE 前先保存不可租用状态，只有明确成功或校验过的 40035008 回执才能移除；complete 晚到仍记录所有权。每次完整快照（含 used 更新）执行文件同步和原子替换，Linux 还同步目录；失败后阻止后续外部动作与 lease。现有 overview 增加素材状态、队列、容量及未知项年龄诊断，不新增强清或猜测恢复操作。

A 双读 1／2，读到 1 仍写 1，读到 2 保持写 2 并执行完整状态约束。**A 是兼容桥，旧 schema 1 的删除未知缺口直到 B 才全面修复。** B 仅将最低写入格式设为 2；不改变线上消息协议。

启用顺序必须通过受信兼容门禁：先批准旧版→A；核实 A 实际运行、双进程健康且事务为空后，再以实际编译产物的回退证据批准 A／B 新契约。素材读写源码加入 `compatibility_paths`；旧版仍属旧契约，不能跳过 A 直接切 B。步骤见 [独立控制器与两阶段门禁说明](https://github.com/doublequiet-on/taki-ournotes-bot/pull/134)。控制器尚未更新时，按同样的受信策略执行，不靠发布间隔保证顺序。

实际编译回退已验证：B 生成三种状态，A 读取后拒租未知、继续新增 owned 且保留 schema 2；旧 05 拒读且不修改账本。正常回退下限为 A。未知结果缺少官方可核验事实时继续保留并降级，不能承诺自动解堵。

## 验证入口与性能证据

使用现有 `test_stage5_limits.py`、`test_catalog_concurrency.py`、`test_alias_loading.py`、`test_console_admin.py`、`test_linux_backend.py`、`test_offline_bundle.py` 及 Koishi `scripts/probe.cjs` 固化回归。核心覆盖重复取消、真实 HTTP 关闭、后台刷新穿插、终态保存失败／耗尽／重启、DELETE 未知、取消队列、晚到 complete、错误码两种 HTTP 形态、配对目录保护与中断续清。

```text
python -B scripts/check_architecture.py
python -B -m unittest discover -s tests -q
# koishi 目录，按 package-lock.json 安装本目录依赖后：
node node_modules/typescript/bin/tsc -p tsconfig.json
node scripts/probe.cjs
```

局部强同步测量：Windows、Node 24.19.0、固定 16 项素材账本，冷 prepare 暂停，5 轮独立进程 ABBA、每版本 400 次热命中。最终实现复测的基线 p50／p95 为 3.63／5.55 ms，A 为 9.45／14.32 ms；增加约 5.8／8.8 ms。800 次热命中均在冷上传仍暂停时完成，0 失败，冷并发 1。峰值 RSS 中位 41598→41964 KiB，增加约 0.9%。初次测量 p95 成本为 6.9 ms；两次原始数据均保留。本次保留明确的持久性成本，不声称整体提速；这些数字不代表 Linux 生产性能。

合成性能／跨版本脚本与编译哈希保留于本任务 `runtime/round2/materials` 与 `runtime/round2/materials-final`。全量回归、Linux 目录同步、离线安装和组合包检查绑定各交付 PR 的当前 SHA；CI 通过、服务器运行版本、QQ 平台回执与客户端显示分别验收。应用合并不自动安装版本回收控制器，本批不执行生产清理或素材格式启用。
