# Taki Koishi 迁移候选（M0–M4）

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

安装允许访问 npm 官方 registry，但禁止依赖安装脚本；编译和探针不需要联网。升级任意锁定包后必须重跑，不能沿用旧版本结论。完整依赖树和 integrity 在 `package-lock.json`，生成的 `lib/` 与 `node_modules/` 不提交。

## 模块与调用约束

| 文件 | 职责 |
|---|---|
| `src/ingress.ts` | 原始身份与正文门禁、legacy 字段转换、真实 adaptSession、稳定 requestId。 |
| `src/delivery.ts` | 每条原消息一个 QQDelivery；逐片段 intent、发送、ACK、预算与卸载。 |
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

M1 候选额度不是实测平台额度。真实事件、群/C2C 权限、回复窗口及客户端送达仍需另行授权的测试；不得据离线通过接管生产机器人。历史基线见 [M0/M1](../docs/KOISHI_MIGRATION_M0_M1.md)，要求见 [方案 v1.1](../docs/KOISHI_MIGRATION_PLAN.txt)。

## 可视化管理 V1

入口为官方 Console 的「Taki 管理」页 `/taki`，提供总览、查询诊断、分群管理；契约与缺失数据口径见 [Console V1](../docs/koishi/CONSOLE_V1.md)，验收见 [执行矩阵](../docs/koishi/CONSOLE_ACCEPTANCE.md)。

运输协议1.1要求绝对路径 `TAKI_CONSOLE_STATE_DIR`，即使 `TAKI_CONSOLE_ENABLED=0` 也加载设置及群策略；网页开关只控制管理入口。受管启动固定使用 `<base>/console-state`，不能换目录绕过已存策略。此目录须为服务用户可写，不能放进只读组合包。网页启用另需独立管理密码；不暴露后端token。

`node scripts/console-rpc-probe.cjs` 使用本机隔离 WebSocket。`scripts/console-server.cjs`、`console-browser.cjs` 及 Python `tests/fixtures/console_backend.py` 只作隔离浏览器验收，不进入生产启动链。浏览器证据必须使用打包后的模块，不能以源码预览替代。
