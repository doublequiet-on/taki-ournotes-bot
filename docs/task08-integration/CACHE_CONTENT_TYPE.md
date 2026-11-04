# 任务08：cache-content-type 2.1.0 窄CJS适配

起点 `8d57eebb7efa279279166be53a6d9c102e6d90d1`，沿用PR200和08集成工作区/分支。
方案依据09固定 `07ad693f7a32b5c3d63e313514ec5c1144c6747c` 的 `docs/CONSOLE_MATERIALS_FOLLOWUP.md`。
本轮只改运行依赖与窄适配，不接收09新Console组件、装配代码或移动分支。

## 调用合同与实现

实际消费者是Koa2.16.4 `lib/response.js:9` 的require，`response.type` setter（329行）调用函数；结果为假时移除Content-Type。
2.1.0的require导出对象含default/getType，同指向函数；直接升级会破坏Koa调用。
独立MIT包 [@taki/cache-content-type-cjs](../../koishi/compat/cache-content-type/README.md) 只执行
`module.exports = require('cache-content-type-upstream').getType`。
根file依赖与npm override接管同名依赖，上游通过独立alias精确锁2.1.0；无全局猴子补丁、安装脚本、node_modules修改或Koa源码补丁。

转换仍使用mime-types2.1.35/mime-db1.52.0。LRU由ylru1.4.0变为2.0.0；缓存容量仍每代100，两代轮换，1000个唯一输入观察峰值199项。
false结果现在缓存，旧包装重算；淘汰后重新计算。类型字符串、已有charset/其他参数、未知类型返回false及清头行为保持。

## 固定输入及材料

| 包 | 版本 / 精确源码 | npm tgz SHA256 |
|---|---|---|
| cache-content-type | 2.1.0 / b4d59700bf3f0d4aad25db504cb316526fc029fa | 2ee7f6c454996e6780d41eb31309910cbe6cde522bd939aeb77ef5d3beb4315f |
| ylru | 2.0.0 / dafd9b0a90e6ac912a6d8ec90fb82ada7ff1602b | 51e41cf4f814bb3d7aee0760546e0e6091239e42649930e5f4f754096de45da5 |

机器索引 [upstream-materials.json](../../koishi/compat/cache-content-type/upstream-materials.json) 含tgz integrity、源archive、许可hash及固定URL。
两个包的npm首选src/index.ts和LICENSE与固定源码树逐字节一致；干净安装后上游11文件/ylru10文件逐字节等于tgz。
准确材料在本工作区 `runtime/task08-cache/materials/`，仅获取上述已选版本，没有继续搜索旧版历史许可。
未证明独立上游构建逐字节复现。原mime-types/mime-db锁未变。
锁文件差异只有根依赖、compat包、cache-content-type本地链接、upstream alias和ylru记录；没有其他依赖升级。

## 定向自查

Node24.22.1/npm12.2.0，干净 `npm ci --ignore-scripts` 完成。证据在 `runtime/task08-cache/`：

- `baseline.json` 在修改前从原1.0.1采集17输入；`probe.cjs` / `probe.json` 比较html/.html/HTML、json、完整MIME、显式charset/其他参数、png/css/js、未知扩展、空/null/false/0，并另查undefined；结果相同。
- VM局部注入真实ylru2以观察缓存，未修改全局require或安装文件：重复命中、false命中、每代100、1000输入有界、旧条目淘汰后重算通过。
- 实际Koa HTTP服务器并发执行上述setter；先设body再清头，结果无Content-Type。JSON 200、显式400错误正文与MIME通过；夹具nosniff头保持。
- `browser.py` / `browser.cjs`：真实隔离Python HTTP18765→Console HTTP/WS15142→Chrome，登录、overview RPC、退出后unauthorized；HTML、JS、CSS、PNG均200且MIME正确，无pageerror。截图 `console.png` 已复核。实际loopback Console未配置nosniff头，如实记录，不冒称已启用。
- TypeScript构建、现有console-rpc-probe的46受保护方法及会话过期/来源/大小边界、架构门禁通过；未手工重跑无关查询/历史全套。
- `install-verification.json`：两个上游包安装字节、锁差异范围及解引用后的最小依赖树迁移验证通过；无个人绝对路径依赖。

本地新增定向探针按AGENTS仅保留runtime，不纳入提交。初版探针变量与CJS内置exports重名，修正夹具后通过；npm沙箱缓存symlink EPERM后同一授权命令成功。没有变更业务预期。

## 给09的装配接口与边界

1. 取本轮固定提交的 `koishi/package.json`、`package-lock.json` 及整个 `koishi/compat/cache-content-type/`。本地包源、README、MIT LICENSE、来源索引必须同行；仅lock或仅node_modules链接不足以重装。
2. 安装树名称：`node_modules/cache-content-type` 是Taki适配（package.name为 `@taki/cache-content-type-cjs`）；`node_modules/cache-content-type-upstream` 是原npm2.1.0；ylru2.0.0无运行依赖。更新材料扫描/来源映射，不能把适配当旧1.0.1。
3. npm本地file目录生成相对链接；发行封装解引用为实体，或保留正确相对目标。当前Python copytree默认解引用能迁移，最小树已验证；完整新组合验包仍归09/11，本轮不改发行脚本。
4. 将两准确版本的源码archive/tgz/LICENSE索引纳入新运行材料；原来源记录不能继续声称最终树含旧1.0.1/ylru1.4.0。这里只交新输入，不回写09冻结包。
5. Console仍为final4，SHA256 `72c286da0c53feb6820c54e4b283d2c752b3b4d7198db1b6f7d38e405b495612`；未接收新组件。当前08分支的旧组合发行器仍有已登记final4字节补丁门禁阻塞，09新装配另行接入。

没有范围批准、最终产品选择、公开发布、QQ、付费模型或生产操作。
