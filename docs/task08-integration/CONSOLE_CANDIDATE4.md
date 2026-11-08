# candidate.4 B2 实际网关接入自查

本轮只接入09固定B2，保留Backend撤权生命周期和知识权限夹具修复，不接10协议。组件可用于隔离验证，未获得最终材料、整包可复现或产品发行放行。

## 固定输入

- 基线：`d1e53b0c09c856857bdde9766a31056833feab67`；原08工作区 `runtime/plan-20261103-08-integration`、分支 `codex/plan-20261103-08-integration`、草稿PR200。
- 09固定提交：`a526abab715989145ed1368ce1610413213a1a8c`；读取其Git源索引与配方摘要，没有合入移动分支或读取未提交业务文件。
- 唯一原始B2：`taki-console-0.1.0-candidate.4.tgz`，npm版本 `5.30.10-taki.4`，635218字节；SHA256 `1668e57b5b387814aab60f79f2d157f7017299088b5413e49690ef8aa5b5f0c9`。
- 固定源索引所列原包、component-manifest、输入ZIP及native材料增量的实际size/hash，以及四个配方Git blob和构建规格均核同。包内输入/native绑定与源索引一致。
- npm12.2.0干净ci安装183包；实际安装的168文件与原始tgz逐字节相同，135项许可摘要逐项核同。锁文件仅根组件引用及Console条目改变，其余依赖完全相同。
- 与原candidate.3对照仅 `TAKI-CONSOLE.json`、`TAKI-MODIFICATIONS.txt`、`package.json` 不同；16个lib/dist运行文件相同。这是B2与旧包对照，不是A2/B2可复现证明。旧包及证据保留。

## 本轮实际执行

证据根 `runtime/task08-candidate4`。本轮使用新安装B2重跑，旧结果仅用于对照。浏览器为本机Chrome，测试限制loopback；实际Console、BackendClient和Python管理HTTP通过隔离数据库运行，模型为离线替身，无QQ适配器。测试驱动放在runtime，不纳入PR新增测试文件。

- `run.py extra reconnect conversation full`：实际HTTP/WS+浏览器原链接属性注入复验，onclick=null、点击executed=false；旧组件正对照产生onclick。正常Markdown、危险协议过滤、props与自定义Vue renderer响应式更新通过。
- 实际Logger records经隔离插件消费，中文多行、循环对象、等级/UI过滤、下载按钮和文件通过；插件加载/卸载/重载、错误密码、登录与退出后RPC拒绝通过。日志页是验收插件，不宣称生产已有独立日志页。
- 连续两次连接失败后恢复首次完整URL、重新认证、全局/群/模型草稿保持、无草稿重连通过。个人交流设置真实保存/回读、冲突草稿、用户去重、桌面/移动端及群/请求日志通过。
- 全页面设置、群冲突、请求诊断导出、后端断线草稿恢复和移动端交互通过。浏览器错误和失败响应列表为空；已查看关键截图。
- `browser.py`：实际HTML/JS/CSS/图片资源与MIME、登录撤权通过。Koa cache探针覆盖17输入、false缓存、容量/淘汰、清头、并发响应、JSON/错误；原CJS适配保持。
- TypeScript编译、151网关探针、46受保护RPC及Console状态探针通过。RPC覆盖个人chat/model/library/notice管理接口与权限投影，使用合成上游；不当作真实模型或QQ投递验收。

个人策略和知识权限定向Python回归129项通过（222.355秒），覆盖QueryCompositionTests、NativeRuntimeTests、ModelAdminTests及KnowledgeAuthorityTests；架构门禁通过。完整CI绑定提交后回读，不使用旧head绿色代替。无关查询/历史全套未在本地重跑。

## 给09/12的接口

09消费本轮固定组合SHA的vendor原始B2、`console-candidate.json`、package/lock，保留既有compat目录和后续Backend生命周期。源码材料按元数据中的固定索引提交及hash获取；无需个人绝对路径安装，未编造公开下载地址。

09唯一负责发行器准入、冲突与组合装包。当前08旧发行器仍无条件执行旧client字节补丁，将报 `Koishi Console client differs from reviewed 5.30.11 asset`；本轮没有修改发行脚本、削弱门禁或改压缩产物。新准入须绑定B2身份，不能重复应用源码已完成的重连修复。

A2/B2仅dist/index.js不同的原因仍交12独立审查，`console_tgz_byte_reproducible=false`；08作者接入自查不代替12结论。完整对应源码、product_release、published均为false。本轮没有最终能力选择、范围批准、物理裁剪、恢复协议接线、合并或生产操作。
