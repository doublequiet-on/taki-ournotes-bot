# Console 验收记录

独立审查修复：指标写线程持锁时，读取立即返回 `metrics_snapshot_busy`，事件循环心跳和后续写入回归通过；保存响应只确认提交时快照，期间的新编辑保留，失效响应不恢复管理数据。`node koishi/scripts/console-state-probe.cjs` 使用官方 Console 随包 Vue 验证迟到响应；管理与指标目标回归22项通过。修复已独立复查，完整回归以修复提交的 CI 为准。

任务基线：main `b3d7c4267f8dd304ff0ce59dde2d9e3ff287c35a`，开发分支 `codex/console-v1a`。本记录按 V1a、V1b 分期更新，草稿 PR 不代表已部署。

## V1a：批次 0–3

已实现：目录资源入口与原生 ESM 分模块页面、真实健康读取、只读请求/详情/群候选/通知/来源/匿名指标、独立诊断存储、准备与运输阶段耗时、全局 epoch/revision 保存保护。群策略控件保持只读。

实际执行：

| 检查 | 结果与范围 |
|---|---|
| `python -B -m unittest discover -s tests -q` | 开发过程中全套1035项通过，9项跳过；日志 `runtime/console-v1a-python.log`。此后新增的历史未决、晚到ACK、排序耗时等用例另跑目标回归；提交版本以 CI 回读为准 |
| `python -B -m unittest test_console_admin test_continuation -q`，PYTHONPATH=src;tests | 初轮32项通过；后续扩展管理用例单独复测 |
| `python -B scripts/check_architecture.py` | 通过，无新增架构例外 |
| 锁定 Node24.19.0 / TypeScript 编译与 `scripts/probe.cjs` | 既有运输探针与新增 guard、队列、阶段计时探针通过；记录 `runtime/console-v1a-probe.json` |
| `node scripts/console-rpc-probe.cjs` | 8个新方法均拒绝未登录/过期访问；非法Origin、参数、旧写接口和保存冲突验证通过；只返回受控错误码 |
| `npm pack ./console --ignore-scripts --offline` | 6项资源进入正式 npm 包；在解包后的 node_modules 布局加载，不使用源码预览入口 |
| 隔离 Python + 官方 Console + 全新无头 Edge | 登录、总览真实夹具、诊断详情、只读群入口、15秒轮询草稿保护、保存、320px/桌面、深色、键盘及触摸检查。外部页面请求阻断，QQ与付费模型未加载；截图和结果在 `runtime/console-v1a-installed/evidence` |

浏览器使用合成请求，生产页面不内置该数据。首次测试把资源放在 node_modules 外，被官方静态资源边界403拒绝；改用实际安装布局后通过。Windows 沙箱临时目录的 rename EPERM 通过正常文件系统权限下复测区分，未修改业务代码规避原子保存。

4096请求夹具，Windows11 / CPython3.12，10次读取样本：总览中位101.145ms、最大137.311ms；25条列表69.063ms、最大114.247ms；详情1.205ms、最大1.466ms。账本基础列表14.959ms；诊断单跨度写入20样本中位0.011ms、最大2.643ms；诊断文件与WAL约1.60MB。这是本机合成读取与写入测量，不是生产延迟或整机CPU/RSS结论。首次逐条总览中位2553ms，改为批量只读快照后复测；两次都保留实际结果。

## 验收矩阵状态

V1a 的 A1–A2/A4、D1–D8、O1–O4/O6–O7、P2/P2b、R1 已有相应目标回归或探针；A3/B1–B3 有安装布局的浏览器基础检查。D9 的命令归属由实际准备结果采集，异常/旧数据保留unknown。O5 当前提供目录cache_state与成功同步时间；其他来源未采集快照明确unsupported。源文件、claims及指标只读边界不通过业务lookup实现。

仍需 V1b 完成或扩大：群配置并发与损坏/实例校验、通知none与发送前复查、每群/问与续查、协议1.1、冻结回退/冲突恢复、完整浏览器故障及冲突矩阵、正式Linux组合包验证、资源开销对照。以上不按通过计入。

## 生产与交付边界

本轮未合并、部署、重启、发送QQ或恢复自动更新。旧业务账本schema未修改。正式Linux组合包需要对应提交的Linux CI产物，Windows本机 npm 资源验收不能替代该层。
