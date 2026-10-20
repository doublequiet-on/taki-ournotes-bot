# Console V1 验收与交付

基线 main `b3d7c4267f8dd304ff0ce59dde2d9e3ff287c35a`。两期均为草稿：V1a分支 `codex/console-v1a` / [PR #100](https://github.com/doublequiet-on/taki-ournotes-bot/pull/100)，V1b叠加分支 `codex/console-v1b`，目标为V1a。源码交付不代表生产采用。

## 执行入口与证据层

- Python：`PYTHONPATH=src;tests python -B -m unittest test_console_admin test_console_policies test_continuation test_reply_pipeline test_updater test_linux_backend -q`；完整回归使用 `python -B -m unittest discover -s tests -q`。本地凭据清空、资料/模型/QQ均夹具化。
- 网关：固定Node24.19.0、锁文件依赖、TypeScript编译，`node scripts/probe.cjs` 与 `node scripts/console-rpc-probe.cjs`；后者真实本机WebSocket，不接QQ。
- 架构：`python -B scripts/check_architecture.py`，通过，无新增例外。
- 浏览器：`npm pack ./console --ignore-scripts --offline` 的六项资源安装到node_modules，官方Console接真实隔离Python管理接口；全新无头Edge阻断外部页面请求。覆盖桌面1280、320px、深浅色、键盘/触摸、导航/筛选/详情、双会话保存冲突、15秒轮询草稿保护、后端超时及恢复。证据在本工作区 `runtime/console-v1b-installed/evidence`。
- 正式产物：Linux CI生成同提交Python离线包与Koishi组合，禁网执行安装/实际嵌入运行时探针；发布前另下载该组合逐文件核验并使用其未修改JS、资源及Python应用wheel做隔离浏览器检查。Windows上的浏览器检查使用固定Windows Node，不能替代Linux ELF执行。最终提交、CI运行号及产物摘要写入交付回执。

V1a `d11561747d2aee8aebb6b94bed54594be7105f4a` 的 Checks运行39397253372全部通过。最初离线测试因禁网namespace中loopback未启用失败，现仅启用隔离lo，仍无外部网络。V1b每次CI绑定其实际提交，不沿用V1a结论。 V1b初轮组合检查曾因检查器import产生额外pyc而失败；改用run_path且新增清单不变回归，不跳过清单校验。

本地V1b已执行完整回归及目标回归、61项网关探针、9种受保护RPC、资源包浏览器检查；最终计数与命令退出值以工作区交付回执及对应CI为准。Windows跳过POSIX/目标Linux测试；Linux CI必须执行相应覆盖。测试中的预期磁盘故障、模型禁止调用及发送失败日志不作为实机故障。

## 附件验收矩阵

下表“通过”只指列出的隔离证据层。B1最终正式组合结果及R2具体限制须与交付回执一并阅读。

| ID | 执行结果及对应证据 |
|---|---|
| A1 | 通过：RPC探针对9方法逐一验证未登录与过期会话拒绝 |
| A2 | 通过：非法Origin、未知方法、无效/超大帧、参数与签名cursor；错误只含受控码 |
| A3 | 通过：安装包浏览器模拟Python响应超时，保留最近数据、登录与脏草稿，恢复后重读 |
| A4 | 通过：冻结夹具反复管理读取，账本/通知SQL dump不变，无claims、指标写入或来源刷新 |
| D1 | 通过：prepared与非零全成功区分，统一projection用于汇总/列表/详情 |
| D2 | 通过：成功+uncertain+过期保留partial及未确认数 |
| D3 | 通过：failure与uncertain分开；失败生命周期保留 |
| D4 | 通过：finished零片段为closed_unsent及completed_without_output，不算完整交付 |
| D5 | 通过：实际合法晚到ACK更新片段事实而不重开生命周期 |
| D6 | 通过：旧元数据缺失为null/unknown，不从正文或primary推断 |
| D7 | 通过：24小时外保留的未决请求可从attention列表找到 |
| D8 | 通过：固定首次时间窗口和排序点，插入新请求不重入旧页，cursor绑定过滤条件 |
| D9 | 通过：实际多命令结果为mixed，片段缺类型保持未知；命令归属来自准备结果 |
| O1 | 通过：诊断损坏、只读、SQLite实际满额、容量、关闭、遥测断连/队列满不改变业务许可 |
| O2 | 通过：重复及乱序事件幂等；同ID不同内容、错误epoch/关联拒绝；核心账本不变 |
| O3 | 通过：上传失败与文字回退仍按同position关联，保留失败上传跨度 |
| O4 | 通过：阶段使用各进程单调时钟；拒绝负值/非有限值，不做跨进程时间相减 |
| O5 | 通过：后台刷新失败快照与可用旧缓存并列；线程失败时仍正确取消关闭 |
| O6 | 通过：跨日/缺日、无文件、损坏快照及匿名工作计数分母；缺失不填零 |
| O7 | 通过：实际prepare调用与排序/发送等待隔离，SDK夹具延迟分别进入upload/send/receipt |
| P1 | 通过：双页面相同持久revision仅一方成功，冲突保留甲草稿并显示乙服务器值 |
| P2 | 通过：前进程全局epoch guard被新进程拒绝 |
| P2b | 通过：旧taki/settings返回upgrade_required，文件未写入 |
| P3 | 通过：原子写前失败保持有效值；丢失rename结果核对持久内容；浏览器不盲重试 |
| P4 | 通过：损坏及实例不符限制新群查询/通知，不重置为全开放 |
| P5 | 通过：allow不越过全局关闭/allowlist；同一有效规则返回解释原因 |
| P6 | 通过：所有候选暂停为none，不领取、不生成报告；selected空数组拒绝 |
| P7 | 通过：暂停后撤权继续同步，intent后撤权禁止发送且记录aborted ACK；在途ACK继续 |
| P8 | 通过：真实Receiver已接纳工作在暂停后继续完成且不重置序号 |
| N1 | 通过：模型/额度spy验证off/local正常、失败、缓存、修复禁止路径调用为零；local不读model/unknown缓存 |
| N2 | 通过：local/model/off并发在线程上下文隔离，不改共享ai_enabled |
| N3 | 通过：混合直接指令+/问依原顺序产出，既有运输防重复探针继续通过 |
| N4 | 通过：model/unknown旧上下文受限，混合续查保留直接内容而不泄漏模型内容 |
| N5 | 通过：重试复用原策略快照，改变快照的同请求返回409不重新执行；Python/网关/控制器拒绝布尔版本与额外协议字段 |
| B1 | npm安装布局通过；正式同提交组合逐文件及浏览器结果见交付回执，不以源码预览替代 |
| B2 | 通过：登录/导航/筛选/详情/全局和群保存/冲突/草稿轮询，浏览器无模块404或异常 |
| B3 | 通过：1280桌面/320px、深浅色、键盘焦点和触摸；状态有文字，不只依颜色 |
| R1 | 通过：实际网关入口关闭网页仍执行分群策略；关闭诊断仍接纳业务；原健康门禁不变 |
| R2 | 限制：首次1.1尚无前一份已上线且支持策略的生产组合。实际V1a诊断模块旧写→新读→旧写往返通过，原核心账本schema不变；相同支持版策略保留由隔离guard覆盖。未声称两个生产1.1版本实机回退 |
| R3 | 通过：旧版冻结前后摘要核对、磁盘失败保持停止、未知旧SHA停止、launcher两角色启动前复核；POSIX属主/权限由Linux测试覆盖 |
| R4 | 通过：原文件精确恢复，人工修改/策略变化保留现状并报告冲突；账本与群策略未回退 |

## 资源测量

`scripts/benchmark_console.py`：Windows11 / CPython3.12，本地合成4096请求、每请求31跨度；读取10样本，写入20样本。开启诊断的总览中位84.100ms（最大110.243），25条列表51.777ms（68.884），详情1.617ms（2.358），跨度写入0.861ms（1.926）。关闭诊断总览44.226ms（65.722）、no-op写入0.005ms；账本基础列表13.368ms。126996行跨度，丢弃0；DB+WAL约54.1MB，一次总览Python分配峰值约9.81MB，不含解释器/原生分配。

该测量是本机接近容量上限的读取/旁路写入对照，不是生产延迟、CPU/RSS或吞吐承诺。轮询15/5/60秒，隐藏暂停，重复读取5秒合并。旧版初次逐条总览曾中位2553ms，批量只读后降至约100ms；保留该失败记录，不以跳过采集实现性能目标。

## 交付清单与后续

三页、管理契约、字段来源/缺失口径、诊断旁路、群策略/通知/每请求问答、配对1.1、受信冻结恢复及必要回归均随两份草稿PR交付。浏览器/性能/兼容证据不进入生产数据，夹具仅测试入口引用。

未执行生产采集、控制器安装、真实QQ客户端验收、切换或重启。自动更新未恢复。部署前仍须独立批准，预置配对组合、最新备份和受信控制器，核对实际前后产物兼容门禁；不能只合并源码后假定服务器已有新保护。V1a获准合并后将main正常合入V1b并调整PR目标，不强推。
