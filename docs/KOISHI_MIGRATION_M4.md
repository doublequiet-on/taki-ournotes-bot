# M4：后台任务、发布组合与切换准备

本阶段延续 M3 `0359a8d515db5cd32a66617852edb29b25862580`。新增后端运行任务、主动通知接线、双进程启动器、可选受信控制器补丁及组合产物 CI。所有生产安装、服务替换、真实 QQ 测试与切换属于 M5，仍须另行确认。旧 `bot` 默认入口及旧控制器运行模式不变。

## 运行与配置归属

后端初始化资料成功后，只启动一组主资料刷新（Unix 半小时对齐）、歌曲属性刷新（300 秒）和按原配置启用的 HistorySampler。停止会先关闭接纳，等待查询与后台线程实际结束，再释放数据锁；取消线程等待不会提前释放所有权。网关重载不重复启动这些后台任务。`/v1/ready` 分别报告资料就绪、后台任务、通知配置、进程 PID 与代码摘要，不冒充 QQ 在线。

Python 持有主资料、来源、渲染、查询模型/额度、采样和唯一 NoticeStore。Koishi 只持有 QQ 凭据、原始事件、权限核验和发送。两个进程共享独立的 `OURNOTES_BACKEND_TOKEN`，通过环境注入；不得写 URL、命令行或日志。`QQ_SELF_ID` 为真实 bot 身份，不能用 AppID 代替。新增 `OURNOTES_RUNTIME_DATA_DIR` 明确控制素材/谱面缓存路径，避免仅隔离主资料仍写到公共缓存。

配置模板见 [runtime.env.example](../deploy/koishi/runtime.env.example)。`TAKI_KOISHI_ENABLED=0` 默认禁止网关启动；插件直接加载时也保留 `enabled: false`。后端启用哪种采样、来源和通知由已有 Settings 决定。模板的关闭值是隔离测试默认值，切换时必须逐项与已批准生产配置对照。

| 数据 | 路径规则 | M4 写入者与回退 |
|---|---|---|
| 主资料、来源专项缓存 | `OURNOTES_CACHE_FILE` 及其目录 | Python；沿用原格式及既有兼容门禁 |
| 素材、谱面、渲染缓存 | `OURNOTES_RUNTIME_DATA_DIR` | Python；同一目录加独占锁 |
| AI 额度与查询指标 | 原显式路径或主缓存同目录 | Python；通知报告使用独立 AIClient，不计查询额度 |
| 三类历史库/来源状态 | 原配置路径及主缓存目录 | Python；不转换历史格式，不移除时钟隔离记录 |
| 续查与余图 | Python 内存 | 重启失效；不从旧 ACK 重建 |
| 被动交付账本 | `--state-dir/deliveries-v1.sqlite3` | Python；保留 intent/unknown，不能删除来重发 |
| 通知、报告、群许可 | 主缓存旁 `update-notices.sqlite3` | Python 唯一账本；追加网关表，旧 notices/groups/release_reports 格式不变 |
| 更新器事务、版本指针 | 既有受信控制器路径 | 控制器；候选应用不可覆盖，回退不恢复共享数据 |

`DataOwner` 在初始化前锁定主缓存、runtime cache 和显式额度/历史文件所在目录。相同配置的新 backend、bot、query、sync、repl 不可同时写。M3 及更旧程序不认识这个新增锁，因此锁不能替代切换前停止旧进程的验收。实际生产路径未在 M4 读取或改动；M5 必须把上表每条解析后的绝对路径、原管理者与备份位置填入切换记录。

## 主动通知与兼容

Python 复用报告生成、已提交部署校验和 NoticeStore。网关领取同一部署/群的任务，核验 bot_state 后申请独占 intent，再主动发送纯文字，最后幂等 ACK。主动发送没有被动 msg_id/msg_seq。每次 QQ 请求间隔至少 2.1 秒；每轮最多领取一条，轮询间隔 30 秒。bot_state 的白名单失败保留原行为，由 POST 执行平台权限检查；明确拒绝/离群会阻止发送。

群发现不授予主动许可。群事件带时间、接纳序号与稳定事件键；权限回报绑定领取时 group_version。许可查询期间的新拒绝事件不可被旧 GET 覆盖。网关先在本地标记变动，再串行同步后端，并在发送前再次检查。群同步失败或队列满时停止本次网关通知，修复后重载。

`gateway_notice_claims/requests/events/group_versions` 是追加表，完成事实仍只存旧 `notices`。intent 在发送前持久占位；空回执、审核中、异常和重启后的 sending 不重新领取。ACK 丢失只重交同事件；旧代次迟到 ACK 可补事实，不能新发消息。尚无 intent 的过期/旧代次 claim 可重新领取。元数据每类上限 4096，原始群同步队列最多 128；满时停止准入，不自动清理未决通知。后续运维需按账本保留策略人工审阅容量，不能清库解锁。

旧入口仍可读取并保留新写入的发送占位；回退不应造成公告重发。添加表不会删除、重建旧表。旧版本实际回读、并发/撤权/重载/ACK 失败由本阶段隔离验收覆盖。

Windows 旧部署状态规则继续保留。Linux 可选控制器仅在 Engine 已提交成功事务后发布 `/run/taki-updater/notice.json`，含当前/前一提交、源码路径与公开更新日志差异；应用以 `OURNOTES_DEPLOYMENT_STATE` 读取。事务进行中、回退、暂停、来源路径不符均不公告；重启不重复生成报告或发送。

## 双进程启动与健康

开发环境先启动 `ournotes-bot backend --instance-id ... --app-id ... --state-dir ...`，再启动组合包的 `bin/node gateway/run.cjs`。二者必须属于同一个组合清单。停止顺序为网关（撤销代次并收尾在途 ACK）→ Python（停止后台并释放文件锁）。不能以同 AppID 双开旧/new bot 做灰度。

[Python service 模板](../deploy/koishi/taki.service.example) 和 [gateway 模板](../deploy/koishi/taki-gateway.service.example) 是安装前审阅材料，不会自行安装。网关依赖并绑定 Python 服务；launcher 要求本次开机的控制器启动许可和一致的 current 版本。Python 角色移除 QQ secret；网关角色移除 AI/来源密钥。

网关只在 `127.0.0.1:8766/health` 提供带 Bearer 认证的健康检查。200 要求：正确 QQ bot 在线、插件握手、后端资料/后台任务就绪、后端运行代次仍相同、后端代码摘要与组合清单一致。控制器另校验两个实际 PID、完整提交、服务 InvocationID/NRestarts 稳定窗口。不会伪造旧“机器人已上线”日志。真实图文及客户端送达仍是独立 M5 门禁。

启动器等待后端握手成功后才完成启动。握手拥有独立的 60 秒期限，暂时连接失败、服务端 5xx 或未就绪会每秒重试，注册始终使用同一个网关代次；协议不兼容、认证失败立即终止。期限到达会中止在途握手请求，启动器清理插件并以失败状态退出，由既有 `Restart=on-failure` 恢复。插件卸载也会中止握手等待。这不会改变业务发送的重试或防重规则。

## 发布组合、控制器补丁与恢复包

CI `koishi-combination` 依赖原 `offline-linux-bundle`，固定 Node 24.19.0、现有 npm lock 和适配器版本。构建时禁止依赖 lifecycle scripts；产物内含 Node 可执行文件、编译插件、安装后的锁定依赖、同 SHA 的 Python 离线包、逐文件 SHA-256 清单、协议/数据版本、控制器候选和 `0359a8d` 时的控制器恢复副本。安装时不运行 npm、不解析 latest。现有 Python 自动发布流程保持原行为；组合包当前作为 CI artifact 提供，尚未增加生产自动下载或发布权限。

构建入口：`scripts/build_koishi_release.py --node <locked-node> --python-artifact <same-commit-linux-zip> --output <zip>`。`scripts/check_koishi_release.py <extracted> <commit>` 校验所有文件和版本，并在产物内部跑实际安装包探针。Linux CI 在禁网命名空间执行这一步；本机 Windows 不冒充 Linux 产物验收。

`deploy/linux_backend.py` 的 `runtime_mode` 缺省仍为 bot；仅 root 配置明确选 koishi 时启用 [koishi_control.py](../deploy/koishi_control.py)。保留原兼容审查、事务、备份、失败和回退流程；停旧服务前要求前后两个已校验组合都可用。缺包/不兼容时不切换。组合固定在 root 拥有、应用不可写的 `/opt/taki-combinations/<sha>`，必须先以受信管理流程校验、解包、设定权限并保存摘要；候选代码无权改受信控制器。

root 配置新增项为 `runtime_mode=koishi`、`koishi_combinations=/opt/taki-combinations`、`koishi_health_port=8766`、`koishi_health_token_file=/etc/taki-backend-token`。token 文件须 root 保护并与进程环境令牌相同。不得只改此开关就转换现有单进程生产：先审阅并安装 launcher/service、保留原配置、建立可回退组合，再在 M5 执行首切。后续自动更新仅在组合已受信预置且前后版本兼容时执行。

恢复包中的旧控制器源码不含线上配置、密钥、systemd 实际单元或数据。M5 安装前必须另行备份这些真实受信文件及其权限/摘要。双进程版本之间回退仍由既有 Engine 事务执行；首次迁移退回旧 bot 需先停止两进程，再恢复原单元、launcher 和受信配置，核对旧代码与真实进程后启动。代码回退不回滚额度、历史或通知账本；共享数据恢复是另一项明确操作。

## M5 切换 runbook（本阶段不执行）

1. 固定拟切换和回退 SHA，确认两者 CI/组合校验、共享数据双向兼容证据，填完实际路径和配置表。保留原受信控制器、launcher、服务文件、配置和停止后的数据备份。
2. 在授权测试机器人/测试群验收原始群/C2C 事件、真实 QQ 图文回执、客户端图片、主动通知权限/撤权；窗口与额度仍按实机记录确认。
3. 暂停受管更新并确认事务状态；停止旧实例及其采样/通知，核对旧 PID 退出、文件写者停止。不得删除 transaction/failed/pending 记录。
4. 由管理员安装审阅后的控制器和双进程单元，预置匹配组合，核对 root 所有权/配置摘要/启动许可。先后端后网关，健康必须绑定两个 PID 与同一组合。
5. 单独进行授权的真实图文查询和通知验收，观察后台刷新/采样及账本。失败时按对应回退路径停止网关和后端，恢复旧受管运行；保留未知发送事实。
6. 记录代码、进程、后台任务、平台回执与用户可见结果各层证据，确认后恢复受管更新。没有实机门禁或生产授权，不执行以上外部动作。

## 本阶段验证记录

本机离线验证记录与声明夹具保存在工作区 `runtime/m4-*`；最终提交和各项计数见 `runtime/m4-verification.json`。实际后台启动/停止、等待刷新线程退出、单写者冲突、旧通知语义、撤权、重载、意图/ACK、跨进程查询通知、实际 Node 启动器和控制器故障回退均分别验证。新增本地验收脚本按约定不提交，现有测试文件中的回归和 CI 探针纳入交付。

实际 systemd 安装、Linux 服务切换、线上数据备份与真实 QQ 送达未执行；Linux 组合安装证据以本提交 CI 为准。Windows 跳过的 POSIX/GNU tar 测试由 Linux CI 覆盖。
