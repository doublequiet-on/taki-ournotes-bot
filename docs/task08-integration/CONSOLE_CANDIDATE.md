# 任务08：final4 Console 隔离接入

起点 `eed7e70fa8b5194befc379a9929ce72e7617707a`，沿用 PR200 / `codex/plan-20261103-08-integration`。
工作区 `runtime/plan-20261103-08-integration`；本轮没有合入09红灯分支。
只消费09 `91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f` 的 final4，完整 hash、包版本、外部依赖与源码材料绑定见
[console-candidate.json](../../koishi/vendor/console-candidate.json)。用户已选择保留网页后台并维护下游构建。

## 接线与版本

- 整包 tgz 使用仓库相对 file 依赖，npm lock 验证 SHA512；167 个安装文件与 tgz 成员逐字节一致。
- 外部依赖保持原锁：`@koishijs/console 5.30.11`、`koishi 4.18.11`、`open 8.4.2`、`uuid 8.3.2`、`ws 8.22.0`。不是09合成 smoke 的 5.30.10 / ws8.22.1 环境；本记录绑定本轮实际安装树。
- `console.cjs::install` 默认导出类、`ctx.plugin`、`ctx.inject(['console','server'])`、`addEntry` / `addListener` 兼容，无需改网关业务源码。`/taki` 页面、`/status` WS、30分钟会话、authority、CAS及撤权语义保持。
- 查询根、个人组合根、02投递、03数据、04图片、05范围和07历史源码均未改变。
- 浏览器回归脚本的旧“查询记录”导航改为实际“请求记录”；维护入口改为实际“运行维护”。保留完整业务断言。

## 实际验收

证据在工作区 `runtime/task08-console/`。Node 24.22.1、npm12.2.0、Windows Chrome headless；真实本地 Python HTTP 18765、Koishi Console HTTP/WS15142。
`tests/fixtures/console_backend.py` 提供真实管理 API 和合成数据；模型客户端为 AsyncMock，Python审计钩子拒绝外部网络，浏览器只放行127.0.0.1。没有 QQ adapter、真实服务或付费调用。

| 验证 | 结果与证据 |
|---|---|
| TypeScript、状态模块 | 构建通过；console-state-probe 的保存竞态、冲突、epoch失效、恢复草稿、密钥清空、退出清理通过 |
| 真实 WS RPC | console-rpc-probe 46受保护方法未登录拒绝；错误密码、连接独立限流、过期撤权、来源拒绝、超大响应边界通过 |
| 连续断线 | reconnect/reconnect.json：2次失败后恢复首次断线完整URL；新连接重新登录，global/group/model草稿保留、密钥不持久化，恢复后保存；随后无草稿重连通过 |
| 个人交流设置 | conversation/conversation.json：选择近期用户、手动ID、去重、保存重读、双会话冲突、刷新、移动端移除、交流日志通过 |
| 页面完整回归 | full/browser.json：筛选、详情导出、群策略冲突、后台停滞保留草稿、恢复提示、320px焦点/导航/保存通过 |
| 动态插件与组件 | extra/result.json：运行中addEntry→加载→dispose→重载；真实Element表单1/表单项3/按钮3；输入、选择、日期日历、弹窗交互通过，截图人工复核 |
| 个人管理接口 | chat/status、model/settings、library/status、notices/list 经浏览器WS→网关→HTTP读取；模型探测经相同链路调用离线替身；退出后chat/status拒绝 |

页面无未捕获JS异常、无HTTP4xx/5xx。额外日志保留两个动态路由注册前 `/taki` 匹配警告，及本机安全软件注入的 kaspersky-labs.com 脚本被浏览器离线白名单阻断；不是产品外部请求成功或完整零控制台警告。
知识库未配置状态、公告无合成投递内容均如实展示；本轮不证明实际资料发布、公告送达或模型供应商可用。

首次浏览器跨沙箱本机连接超时，改同一进程组隔离运行后通过。新增组件夹具曾有旧提示词、缺少library参数及模型ID字段对应错误，均按真实合同修正；动态插件必须安装在允许的node_modules路径。截图等待动画结束后复核，没有修改组件字节。

## 给09的最小装配接口

1. 必须携带 `koishi/vendor/*.tgz` 与 `console-candidate.json`，保留相对依赖及 lock 完整性；不能只打 dist。源码安装与材料收录需覆盖新 vendor 输入。
2. 发行器显式识别该候选版本、tgz SHA256/lock SHA512、`TAKI-CONSOLE.json`；此分支跳过**旧字节补丁**，仍拒绝其他未知组件。现有 build_koishi_release.py 无条件旧patch会拒绝新client，本轮不改其门禁。
3. 完整外部 runtime 树采用本轮 lock；按此树重新绑定来源、许可和验包，不能沿用09 smoke依赖树。现有管理代码和 assets 均须同行。
4. 对应源码入口只交组件hash→源码ZIP hash→固定源码SHA→相对路径绑定；hosting_url仍null。公开地址、材料放行、页面取得入口及最终发行验包继续由09/11处理，不虚构下载链接。
5. 09 CI修复、下游装配分支、源码包收录/许可复核完成后才能证明完整组合发行通过；本轮不是新的全部集成通过候选。不得追加final2/final3或再次patch final4。

给06/12：旧查询/握手证据仍绑定原版本；本轮只替换Console组件并修正浏览器测试定位。请从本轮最终提交和固定tgz重验真实Console链，不能将开发夹具配置当作最终产品selection。
