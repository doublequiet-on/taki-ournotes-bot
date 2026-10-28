# Taki Koishi 迁移候选（M0–M4）

控制台增加模型／额度设置、真实 AI 调度和用量诊断，以及默认不可用的受信重启／更新接入。客户端、协议核心、生产适配器安装边界见 [包 4](../docs/QUERY_CHAT_PACKAGE4.md)。

查询聊天包 1 将网关／后端载荷升级为 2.0，接入每次模型尝试的当前权限核验。实现与生产能力边界见 [包 1](../docs/QUERY_CHAT_PACKAGE1.md)；真实 QQ 引用／按钮 profile 尚未启用。

M3 已接通查询、续查与逐片段交付；`enabled` 默认 false，仅注册运输服务。启用后连接同机 Python 后端，按原始事件身份、Koishi 作用域/权限和后端发送账本处理消息。

配置、生命周期、行为差异和验收范围见 [M3 接线说明](../docs/KOISHI_MIGRATION_M3.md)，HTTP 见 [后端协议](../docs/KOISHI_BACKEND_V1.md)。生产切换另行确认；后台任务、主动通知和组合发布见 [M4 运行准备](../docs/KOISHI_MIGRATION_M4.md)。现有 Python bot 入口保留，本目录不进入 Python 离线包。

## 重现离线探针

使用 `.node-version` 的 Node 24.19.0 和 `packageManager` 的 npm 12.2.0，在本目录执行：

```sh
npm ci --ignore-scripts
npm run build
npm run probe -- probe-output.json
```

报告输出路径可以省略。探针在进程内禁止真实 socket、HTTP 和 fetch；固定原始事件经过实际安装的 QQ adapter，上传和发送调用其实际 internal API，仅 HTTP 返回值由夹具提供。启动故障场景使用隔离的系统临时目录并在结束时清理，不要求源码目录可写；探针不加载 `.env`、不登录 QQ。高层发送的部分成功测试会打印预期的 `fixture explicit rejection` 警告，最终报告必须 `failed: 0`。

HTTP 双端样例位于 `scripts/fixtures/http-v2.json`，随组合包的 scripts 一起交付。Python 的 `test_console_admin.HTTPV2ContractTests` 对照实际 snapshot／事件／模型许可输出并验证 TS 入站样例；probe 验证实际 TS 编码、响应解码与 Receiver 绑定。管理 CJS 的原始 JSON 透传仍保留，不属于本次静态类型迁移。

安装允许访问 npm 官方 registry，但禁止依赖安装脚本；编译和探针不需要联网。升级任意锁定包后必须重跑，不能沿用旧版本结论。完整依赖树和 integrity 在 `package-lock.json`，生成的 `lib/` 与 `node_modules/` 不提交。

## 模块与调用约束

接纳容量按唯一原消息计量，默认 `maxPending=8`，从等待接纳到交付结束只占一份名额；重复原事件复用原任务，冲突继续拒绝。满载发生在识别前，保持静默并计入 `capacity_rejected_unclassified`。`activity()` 提供互斥的接纳／已受理占用、总占用、上限与当前进程累计原因计数，经现有 overview 读取。三个后端账本预算、缓存与隔离基准见 [查询性能与容量](../docs/PERFORMANCE_FOUNDATION.md)。

| 文件 | 职责 |
|---|---|
| `src/ingress.ts` | 原始身份与正文门禁、legacy 字段转换、真实 adaptSession、稳定 requestId。 |
| `src/materials.ts` | 官方图片素材上传、私有所有权记录、内容复用与有界回收。 |
| `src/delivery.ts` | 每条原消息一个 QQDelivery；逐片段 intent、发送、ACK、预算与卸载。 |
| `src/wire.ts` | HTTP 2.0 JSON 的 unknown 解码、事实／可发计划与回执类型；不授予权限。 |
| `src/backend.ts` | 认证 loopback 客户端、能力握手、受限图片、幂等重试与 ACK 提交门禁。 |
| `src/receiver.ts` | 原始事件捕获、标准化作用域/权限、串行接纳、同消息 delivery 所有权。 |
| `src/gateway.ts` | 每 bot 的窄 discovery 主机修正，保留路径/参数并随卸载恢复。 |
| `src/notices.ts` | 群许可事件、独立主动通知、逐次许可/intent/ACK，使用后端唯一账本。 |
| `run.cjs` | 双进程网关入口，显式启用、受认证 loopback 健康、顺序卸载。 |
| `console.cjs`、`console/` | 官方 Koishi 控制台接入、独立管理员会话与管理页面。 |
| `src/management.ts` | 接收/通知范围设置校验、原子持久保存和管理记录。 |
| `src/group-policies.ts` | 实例绑定群策略、持久 revision、有效规则、通知 all/selected/none。 |
| `src/telemetry.ts`、`health.cjs` | 有界旁路耗时采集、共享实时健康读取。 |
| `src/index.ts` | 默认关闭的配置、运输服务与受管 receiver 生命周期。 |
| `scripts/probe.cjs` | 已安装包的离线行为验证与故障夹具。 |

业务识别调用 Python 共享入口；GROUP_MESSAGE_CREATE 要求自身 @，GROUP_AT_MESSAGE_CREATE/C2C 为定向入口。直接使用原始正文，不把 Koishi 转义元素当业务文本。

同一原消息的多命令/多片段必须复用一个 QQDelivery，成功 ACK 后才能推进。上传失败由后端明确选择同次捕获的文字分支；intent 的 existing 不授权重发。未知回执停止后续片段。卸载后在途回执只记事实，后续发送被禁止。

指令群回复使用 `msg_type=2`：文字正文前加入入站作者的 `<qqbot-at-user id="…" />`；图片先经官方素材 `prepare → 无凭据 COS PUT → complete`，再把显式宽高的 `qqbot-media://` 图片和真实提及放在同一条 Markdown。图片请求启用 `force_verify_image_resource`，避免转存失败时仅发出占位文字。它们共用原消息 `msg_id`、递增 `msg_seq`、intent 和 ACK，不另发「查询结果：」。私聊保留原生图片路径。单轮五条，真实慢提示占一条，其余由 `/继续` 读取已捕获结果。

`src/materials.ts` 使用 SDK 当前认证 HTTP 客户端调用官方元数据接口，COS PUT 独立、限时、禁止重定向且不带 QQ 凭据。`run.cjs` 将素材登记放在 `TAKI_CONSOLE_STATE_DIR/qq-materials`，按 App ID 分文件；同一 App 由单个受管网关拥有此目录。保存内容哈希、已获得的素材 ID、引用及最近使用时间，不保存密钥或预签名 URL。最多登记 64 项，为其他用途留余量；只回收本功能已登记、空闲超过五分钟且无在途引用的素材，不枚举／删除账号其他素材。完成响应丢失的条目保留占位并停止同内容重传，须人工核对，不凭文件名猜测所有权。损坏或持久保存失败时停止使用素材链并走既有文字回退。

平台新返回的 upload_id、image_id 和素材 URL 必须为合法字符串，避免写入后重启无法读取。schema 1 历史数字 ID 仅在非零安全整数时无损转为字符串，保留原有 owned／未决所有权；其他异常类型或精度不明的 ID 保留原文件并停止素材链，不能猜测或清空。schema 2 始终严格校验字符串。

素材生命周期加固分两阶段发布。兼容 A 读取 schema 1/2，原 schema 1 继续写 1，读到 2 后始终写 2；启用 B 才将新建与旧账本的后续写入升级到 2。schema 2 将 `owned`、`completion_unknown`、`deletion_unknown` 明确分开，DELETE 前持久登记未知状态；未知项占用容量但不发 lease、不参与普通回收。只有删除成功或官方明确不存在码 `40035008` 可移除记录。A 读取旧格式时仍保留旧删除行为，完整删除保护随 B 启用。

每次完整快照（包括 `used`）先同步文件，再 rename；Linux 还同步父目录，Windows 沿用平台支持范围。所有步骤保持在单一提交队列内，保存故障后停止租用。冷上传并发仍为 1，最多 8 个调用／24 MB 输入；有效热命中可绕过冷网络请求。交付取消或期限到达时移除未启动的排队调用，包括被其他 DELETE 阻塞的初始缓存检查，并及时释放对应输入与预算；已启动的状态写入和外部动作继续由 store 收尾并保存晚到所有权。网关卸载取消两个队列中的等待项并等待真实在途动作结束。运行入口在撤下 QQ 适配器前显式等待 receiver 排空；重复卸载复用同一收尾任务，包含通知及发送遥测收尾。

现有 overview 的 `activity.materials` 提供格式、占用、未知状态数量、最早未知条目的登记年龄、冷阶段与保存故障诊断，不展示素材 URL 或凭据。未知结果走文字回退；当前没有经核验的素材查询接口，未取得与 uploadId/imageId 对应的官方事实前保持未知，不自动重传、不强清账本。本批不提供解锁按钮或新增管理页面。

素材上传失败发生在 intent 前，可由后端选择同次完整文字回退；发送／审核回执不明时停止后续发送，不自动改格式重发。平台接收与客户端显示分别验收；本次接线仍需新版真实指令验收。此前原生图片绑定 `msg_id` 不会自动显示 @，已由 PR #125 客户端反馈证伪。官方 Markdown 不提供 @ 字体、颜色或气泡留白控制，不能以此承诺复刻另一机器人的外观。

身份在构造时捕获，正文内 `<@…>`／`<qqbot-…>` 转为普通文字，不能引入额外提及。包含提及后的完整文本仍限 1800 字符；配套后端的群正文限 1500、分块 1400，余量留给提及和分页说明。私聊保持原预算与发送类型，独立通知链不追加指令用户。实际回复链仍需候选版本上线后验收，独立探针不等于该版本已经部署。

网关与后端须配套更新／回退：旧后端可能生成超过新群正文预算的页面，不能只部署新网关。素材登记独立于批准／撤销、发送账本和额度，代码回退时保留。启用 schema 2 前必须先验证兼容 A 实际运行，并将素材读写模块加入更新器受审指纹及持久契约；更新器跟踪最新 main，不能靠两次合并的时间间隔保证经过 A。B 的最低可用回退版本为 A；此前只读 schema 1 的网关会拒绝 schema 2 并退回文字，这不属于正常图像可用回退。不能删除未知记录来恢复容量或回退。

M1 候选额度不是实测平台额度。真实事件、群/C2C 权限、回复窗口及客户端送达仍需另行授权的测试；不得据离线通过接管生产机器人。历史基线见 [M0/M1](../docs/KOISHI_MIGRATION_M0_M1.md)，要求见 [方案 v1.1](../docs/KOISHI_MIGRATION_PLAN.txt)。

## 可视化管理 V1

独立的 Astro 可点击设计稿见 [Console 设计预览](preview/README.md)。预览仅使用演示数据，不进入现有运行或组合发布链。

域名入口准备见 [借用子域名接入](../docs/koishi/CONSOLE_DOMAIN.md)：同机 HTTPS 代理先校验外部 Host/Origin，再转发本机控制台；默认监听与 SSH 入口保持不变。

入口为官方 Console 的「Taki 管理」页 `/taki`，提供总览、查询记录、群管理、数据来源和设置；契约与缺失数据口径见 [Console V1](../docs/koishi/CONSOLE_V1.md)，验收见 [执行矩阵](../docs/koishi/CONSOLE_ACCEPTANCE.md)。

运输协议1.1要求绝对路径 `TAKI_CONSOLE_STATE_DIR`，即使 `TAKI_CONSOLE_ENABLED=0` 也加载设置及群策略；网页开关只控制管理入口。受管启动固定使用 `<base>/console-state`，不能换目录绕过已存策略。此目录须为服务用户可写，不能放进只读组合包。网页启用另需独立管理密码；不暴露后端token。

组合包构建时只在暂存目录修正 Koishi 5.30.11 客户端重连刷新回调；先校验原始文件 SHA-256 和唯一匹配，未知版本拒绝打包，不修改已安装的 npm 依赖。

`node scripts/console-rpc-probe.cjs` 使用本机隔离 WebSocket。`scripts/console-server.cjs`、`console-browser.cjs` 及 Python `tests/fixtures/console_backend.py` 只作隔离浏览器验收，不进入生产启动链。浏览器证据必须使用打包后的模块，不能以源码预览替代。设置 TAKI_SCROLL_ONLY=1 可单独检查桌面滚轮、手机触摸滚动及模拟设置保存。
