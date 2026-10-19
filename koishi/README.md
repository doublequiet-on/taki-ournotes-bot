# Taki Koishi 迁移候选（M0 / M1）

本目录交付锁定依赖、原始事件探针和逐片段 QQ 发送适配。`apply()` 只注册有作用域和卸载生命周期的 `takiTransport` 服务；尚未安装消息接收器、业务命令或 HTTP 后端，加载插件不会启动查询或发送消息。现有 Python bot 入口继续使用原实现。

阶段记录见 [M0/M1 基线](../docs/KOISHI_MIGRATION_M0_M1.md)，完整要求见 [迁移方案 v1.1](../docs/KOISHI_MIGRATION_PLAN.txt)。本目录未进入 Python 离线发布包。

## 重现离线探针

使用 `.node-version` 的 Node 24.19.0 和 `packageManager` 的 npm 12.2.0，在本目录执行：

```sh
npm ci --ignore-scripts
npm run build
npm run probe -- probe-output.json
```

报告输出路径可以省略。探针在进程内禁止真实 socket、HTTP 和 fetch；固定原始事件经过实际安装的 QQ adapter，上传和发送调用其实际 internal API，仅 HTTP 返回值由夹具提供。探针不启动 Koishi 应用、不加载 `.env`、不登录 QQ。高层发送的部分成功测试会打印预期的 `fixture explicit rejection` 警告，最终报告必须 `failed: 0`。

安装允许访问 npm 官方 registry，但禁止依赖安装脚本；编译和探针不需要联网。升级任意锁定包后必须重跑，不能沿用旧版本结论。完整依赖树和 integrity 在 `package-lock.json`，生成的 `lib/` 与 `node_modules/` 不提交。

## 模块与调用约束

| 文件 | 职责 |
|---|---|
| `src/ingress.ts` | 校验 AppID/selfId、会话/发送者/消息身份、时间；窄转换 legacy 字段后调用真实 `adaptSession`；生成稳定 requestId；群许可事件转换。 |
| `src/delivery.ts` | 同一原消息逐片段上传/发送、msg_seq、发送前 intent、回执 ACK、预算及卸载检查；上传失败由调用方显式选用已捕获备用文本。 |
| `src/index.ts` | Koishi 作用域过滤、受管 delivery 的销毁；调用方在批次结束时释放所有权，最多持有 64 个 delivery。 |
| `scripts/probe.cjs` | M1 版本行为测试/运行探针；固定夹具、故障注入、JSON 证据。 |

`decodeIncoming` 的业务识别函数由后续共享入口提供；探针只识别 `/查曲`、`/继续` 的固定样本，不是完整命令解析器。对 GROUP_MESSAGE_CREATE 要求自身 @；GROUP_AT_MESSAGE_CREATE/C2C 本身为定向入口。使用原始正文，避免把 Koishi 转义后的元素序列化当作业务文本。raw→Session 调用返回适配器对象；Koishi 实际事件监听器通过 Cordis trace 取得会话扩展，探针的高层发送也走这一步。

`QQDelivery` 是**每条原入站消息**的序号所有者；同一消息的多命令/多片段必须复用同一实例。`deliver(partId, index, part)` 的 index 从 0 开始；成功 ACK 后才能前进。重复相同 partId 返回原结果，内容或 index 改变则拒绝。图像上传失败未消耗发送槽位，可用不同 partId、相同 index 发送预先准备的备用文本。

`DeliveryEvents` 是 M2 待实现的持久化事件端口；当前没有 HTTP、落盘账本、重启去重或跨进程事务。只有 intent 的明确 `granted` 才授权一次发送；`existing` 不等于可重发。ACK 丢失时 `acknowledgementPending` 为 true，仍保留平台真实结果；调用方须等待 `retryAcknowledgement()` 确认，不能据 success 直接提交业务上下文。超时、空回执、审核中都停止后续片段。插件卸载后已在途发送仍记录回执，不再启动后续发送。

图片字节、文本与片段集合均有界；图片解码/像素限制由后端复用现有 `image_output`，尚未在本目录实现。当前 `candidateLimits` 是源码与夹具下的保守候选，不是实测平台额度。后续后端还必须校验 request digest、gateway epoch、准入顺序、权限和提交代次。

## 接入前的门禁

安装包已复现：旧身份字段不会自动映射；旧 `api.sgroup.qq.com` 网关下发地址不会被默认替换；高层发送会丢失部分成功返回。身份转换与窄发送适配已有实现；实际 receiver、基于真实 discovery 保留路径/参数的网关配置、完整命令及通知接线属于 M2/M3/M4。

必须在另行授权的测试环境验证真实事件、权限、回复窗口、群/C2C 额度、图片及客户端送达。未经验证和生产切换确认，不启用新网关接管现有机器人。
