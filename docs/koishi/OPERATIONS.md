# Taki 运维控制台

起点：main `124176052a7977b7ba15718687e6f3a8ea90a25a`，设计 PR #106 head `b237775c0df5cecb3205884f6f162f4f229115a0`。#106 保持草稿，运维分支叠加交付。运输协议保持 1.1，独立管理能力 `ops_v1`。

## 字段与所有权

| 内容 | 来源 | 行为与缺失 |
|---|---|---|
| 查询与交付事实 | 现有 Admin 投影、发送账本 | 只读；partial/uncertain 不变成成功 |
| 来源与同步 | 已发布 Catalog 快照、实际刷新观测 | 读取不联网；历史不反推 |
| 修复任务 | Backend operations-v1.sqlite3 | 实例绑定；7 日、200 条；重启中断、不重放 |
| 管理审计 | 网关 console-state/operations.v1.json | 30 日、1000 条；意图先落盘、结果回读 |
| 运行身份 | 网关健康、后端启动摘要、组合清单 | 不使用 current 链接替代进程身份 |
| 主机与备份 | 单独审阅安装的只读采集器 | 未安装、陈旧和缺乏恢复证据分别显示 |
| 通知 | 通知、领取、群许可的只读投影 | 不领取、不重发；旧时间未知 |

浏览器只使用 authority 5 的 Console RPC，不获得后端令牌。新增 ops/jobs/list、ops/jobs/detail、ops/jobs/start、ops/export、ops/audit；Python 对应 /v1/admin/ops/jobs、/jobs/{id}、/export[/{request_id}]。提交另需活跃网关 epoch。

提交体固定为 operation_id（32 位小写十六进制）、action 和受限 params。同 ID 不同参数冲突；同类正在执行的操作合并。一次只执行一个重型任务，队列最多 8 项，提交别名最多 2000 项。读取预算沿用 2 秒，执行不占用 Console 请求。

状态为 queued/running/succeeded/partial/failed/interrupted。列表显示最近 25 项摘要，详情包含有界结果。存储故障只禁用对应管理能力，不改业务账本或发送许可。关闭网页不取消任务。
网关审计记录逐项校验，损坏成员不使 Console／网关启动失败。结果落盘失败时保持未确认并禁用后续管理写入，不从内存中的变更结果伪报持久成功；重新启动后只读核对已经保存的意图，不重放操作。

### 资料与素材动作

Haneoka 实例提供 catalog_sync/catalog_reload/assets_verify/assets_repair/assets_retry。请求参数为空，由服务端在受理时捕获 JP/intl 版本；原操作 ID 重试复用原快照，快照变化不会重绑或覆盖较新资料。普通同步以小型身份读取比较两个版本，完整重新获取复用既有下载与验证预算。

结果区分 upstream_unchanged/synchronized/updated_unsaved/refresh_failed_cached/no_available_data，并分别返回本次发布、持久保存、资料可用性及本次新增数量。未保存的同版本快照可以重试保存，不伪报同步成功。

素材任务范围为捕获的 Catalog 快照引用和内置游戏素材；不声称覆盖未收集的其他来源、谱面或历史版本。逐文件解码／摘要检查，最多两个下载线程，20 分钟后停止发起新文件；有效文件保留，文件原子替换。修复清除对应运行时图片和固定素材的负缓存；本地文件核验有效并复用时也清除旧失败记录，保留已加载的有效图片。失败项按同快照重试，清单保存失败单独报告，不掩盖已经修复的文件。

### 主机采集与恢复证据

单独审阅安装 deploy/ops_snapshot.py 到受信控制器目录，并安装 taki-ops-snapshot.service/timer 模板；不能从应用自动安装。该 timer 与自动更新 timer 独立，不恢复自动更新。oneshot 不依赖、不启动或重启机器人，只执行固定 systemctl show、控制器 status 投影和有界文件统计；不读环境凭据，不联网，不调用 check/recover/resume/rollback。

输出 /run/taki-ops/host-v1.json，root:ubuntu、0640，目录 0750 不允许应用写入。30 秒采集，超过 90 秒标记陈旧，未来时间标记时钟变化。目录大小最多每 10 分钟统计，扫描限 2 秒；complete=false 的字节数只是已扫描部分。business_data 包含 assets，不可相加。磁盘门槛按 releases 和 backups 所在文件系统分别报告。

控制器记录、进程 cwd 线索和网关/后端启动身份分开显示。cwd 不等于模块摘要；控制器目标只代表最近观察，非实时 main。仅在采集代次和 PID 对得上时比较运行版本。

现有备份清单只能证明相应归档存在，不能证明摘要或恢复成功。可读取独立批准流程产生、root 所有的 /var/lib/taki-updater/ops-recovery-proof.v1.json：schema=1，objects 以 code_environment/business_data/console_settings/trusted_configuration/isolated_restore/offsite_copy 为键，每项包含 artifact_sha256、verified_at，可选 restore_verified。页面标注为外部受信记录，不声称本采集器执行了核验，也不据此推断归档当前存在。

### 通知只读投影

既有 notices 列表保留插入窗口分页，额外返回最多当前 limit 个已观察目标和候选总数。单群通过 ops/notice-target（Python /v1/admin/ops/notice-targets/{group_id}）读取，合并当前已发布通知目标、许可、领取及账本状态。读取不执行 claims、pending 或报告生成；策略限制仍由网关有效规则解释。

新增时间放在独立 operations 库的 notice_observations 表，仅记录 claimed/sending/ack 等实际处理时的服务器观测。通知路径只入有界队列（256），独立任务每批最多 32 条落盘；写入失败与丢弃只影响观测，不改变许可或 ACK。保留 24 小时、最多 16384 条，历史、丢失和未覆盖时间保持未知，不作为客户端送达时间。

导出包含实例摘要或单条请求，不含密码、令牌、原问句、用户或平台消息标识、完整配置及日志。每份导出使用临时群代号；实例导出最多含前 25 条需要关注记录，不代表完整消息流量。

## 批次验收矩阵

| 批次 | 必需检查 |
|---|---|
| 0–1 | 认证、非法参数、重复提交、冲突、中断、损坏存储隔离、导出脱敏、草稿并发、正式包五页 |
| 2 | JP/intl 版本变化、无更新、旧缓存、无缓存、保存失败、坏图、部分失败、负缓存失效 |
| 3 | 采集缺失/过期、进程版本不一致、备份分层、通知未知/未决、读取无副作用 |
| 4–5 | 后台任务去重、采样退避、维护及 local 冲突恢复、跨重启、原业务回归、安装包及性能 |

每批记录适用提交、实际命令、结果及证据。离线夹具、CI、生产与真实 QQ 验收分别报告。自动更新继续暂停；网页不执行发布、回退、主机备份、系统命令或重启。
