# candidate.3 实际网关接入自查

原链接属性注入在干净安装的实际 Console 浏览器中复验通过。此结论是08作者自查，不替代12独立复核或09发行装配。

## 固定输入与范围

- 基线：`399cc8a47bf84aa550ff2eb3d1e3d640101c2033`，本地及远端开工一致、工作树干净。沿用08工作区 `runtime/plan-20261103-08-integration`、分支 `codex/plan-20261103-08-integration`、PR200。
- 09源码：`ca7a64175aa4c359c018b8a7e1dc6c506fbd6552`；只消费已提交绑定组件，未合入移动分支。
- 原始 `taki-console-0.1.0-candidate.3.tgz`，包版本 `5.30.10-taki.3`，634671字节；SHA256 `ea92ea91d816648023e61cf6ce7dfe2de98aece38e1ca8dd9292166f5537d1fb`。来源为09 `security-20261104/candidate3-final`，不是npm-repack。
- 核对09 delivery.json 后，Git builder、recipe、重连源码patch、安全patch的SHA256均与包内TAKI-CONSOLE.json相同；输入ZIP实际hash匹配。重连patch没有重复应用。
- npm12.2.0干净ci安装183包，168个组件文件逐字节相同，135项许可索引逐项核hash。锁文件仅根依赖引用及Console包条目变化，其他包完全相同。
- cache-content-type兼容层、上游2.1.0、ylru2.0.0和所有网关/业务源码、发行脚本均未改动。历史candidate.1/2及失败证据保留。

## 本轮实际重跑

本工作区证据根 `runtime/task08-candidate3`；驱动 `run.py extra reconnect`、`browser.py` 使用隔离真实HTTP/WS BackendClient和Chrome，模型为离线替身，无QQ适配器，浏览器只允许loopback；驱动结束自动停止子进程。

- 原candidate.2复现输入：`<a href='https://example.com/" onclick="window.fixtureXss=1'>引号链接</a>`。默认unsafe=false，实际DOM的引号编码为属性值，onclick=null、点击executed=false。相同浏览器内旧candidate.1实际组件对照仍产生onclick，验证用例能检出旧缺陷。证据 `extra/xss-reproduction.json`、`extra/quoted-link.html`。没有降低严格断言。
- 正常标题/列表/表格/代码；相对、http(s)、mailto、tel链接；javascript、script/img/svg及事件属性过滤；source/inline/tag/unsafe切换、空/长中文和自定义Vue renderer响应式更新均通过。`extra/result.json`，page errors为空。
- 实际Logger records经隔离插件target消费、中文多行、循环对象、子logger、debug等级过滤、UI筛选及浏览器下载按钮/文件通过。`extra/download.txt`、`extra/markdown-logger.png`。这是隔离插件的真实消费链，并非生产中已有独立日志下载页。
- 错误密码、登录、退出撤权后RPC unauthorized、动态插件加载/卸载/重载通过。连续两次连接失败后恢复首次完整URL、重新认证、全局/群/模型草稿及保存、无草稿重连通过；见 `reconnect/reconnect.json`。
- 实际HTML/JS/CSS/图片资源200及MIME、HTTP/WS读取通过；`browser.json`/`console.png`。资源nosniff为null，如实保留。
- 现有cache探针17输入、有界缓存/false命中/淘汰重算、实际Koa setter清头、JSON及错误响应通过；TypeScript、46受保护RPC、架构门禁通过。RPC首次在沙箱内原子日志写入失败，原样在授权隔离环境重跑通过；保留两个日志，不改预期。

09的26项安全变体是绑定ca7a641…的作者证据，未称本轮重复运行全部26项。本轮以实际安装组件复验原漏洞及上述交叉路径。个人chat/model/library/notice完整业务、查询/历史全套未重跑；沿用此前绑定版本的证据，本次未改变其源码或其他依赖。全量CI另绑定最终组合SHA记录。

## 给09/12的接口

取本轮最终组合SHA的package/lock、原始tgz及vendor JSON；保留现有compat目录。外部依赖与源码材料精确值见 [机器绑定](../../koishi/vendor/console-candidate.json)。安全输入ZIP替代candidate.2输入包，source-support仍为原固定hash；公开地址null，完整对应源码、product_release标记仍false。

09唯一负责新组件准入、发行冲突和装包；当前旧发行器仍无条件调用旧client字节patch，将拒绝新client（`Koishi Console client differs from reviewed 5.30.11 asset`）。需要按固定身份识别已完成源码重连修复的候选并完整验包，不能重复patch或降低未知包拒绝门禁。本轮不修改发行器。

12可从固定提交vendor包干净ci，使用上述证据与驱动进行独立复核。新hash需重新核对受影响证据；本次没有发布源码地址、合并、部署或生产操作。
