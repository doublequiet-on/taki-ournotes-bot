# candidate.2 接入与阻塞复现

**结论：固定组件已接入隔离组合，但XSS验收失败，不放行。**
起点 `e5e532a29cff792d1a7f2a17f704917f96580e79`，原08工作区/分支/PR200。
仅消费09源码 `e4c0cebade52272e4e2b2f249d1d66d5ce27f722` 的原始candidate2-a组件，没有整体合入09分支。

## P1：默认安全模式下链接属性注入

真实浏览器的已安装 `k-markdown`，默认 `unsafe=false`，输入：

```html
<a href='https://example.com/" onclick="window.fixtureXss=1'>引号链接</a>
```

实际输出含：

```html
<a href="https://example.com/" onclick="window.fixtureXss=1" rel="noopener noreferrer" target="_blank">引号链接</a>
```

点击会执行内联事件，隔离页 `window.fixtureXss === 1`；测试通过capture阶段preventDefault阻止导航，未发外部请求。
严格断言 `locator('[onclick]').count() === 0` 得到1，exit1，未降低断言。
相同Vue/Element资源下另加载原candidate.1的实际 `k-markdown` 渲染相同输入，也产生onclick；这是继承缺陷，不称candidate.2新增回归。不能用退回candidate.1作为安全修复。

归属09组件构建：固定输入 `source/taki-sources/marked-vue/src/index.ts`（86f4fa602bc4ad588661bbddcc0fee5126c295b7）第53行只检查href协议，第61行将原值直接拼入双引号HTML属性；第55行的title已有escapeAttrValue，而href没有。
建议09在其源码构建范围正确编码href属性并交新hash，覆盖原始单双引号、实体和控制字符及合法相对/http/https/mailto/tel链接；08只接新固定包复验，不直接改此组件或安装文件。
本轮未证明生产页面存在可触达的不可信Markdown输入；已证明默认安全组件合同失效，因此候选验收阻塞。

证据：本工作区 `runtime/task08-candidate2/extra/xss-reproduction.json`（unsafe:false、executed:true、currentHtml、oldHtml）；`extra.log` 的失败断言；`extra/quoted-link.html`、截图 `extra/last.png`。
复现驱动 `runtime/task08-candidate2/run.py extra`，会预期失败并自动停止合成本地服务器；浏览器只允许loopback。旧包对照读取仓库保留的原始candidate.1 client，不使用npm-repack。

## 完整性与其余定向结果

原始tgz634437字节，SHA256 `5b73df05ece95041f0586177bd077e72cb4bdd0df7c996c5ad7017909bad62dc`，包版本5.30.10-taki.2。
干净npm ci后168成员逐字节一致；135项许可索引逐项hash通过。固定Git中的builder、recipe、重连源码patch与包内TAKI-CONSOLE.json一致；没有再应用旧字节补丁。
16项运行输出仅client.js改变，其他15项与candidate.1相同；其余包级变化是身份/修改说明/许可索引/package元数据及新增marked-vue源码许可。
输入包04e7bb6a…和source-support包3762c214…完整hash已核，见 [版本绑定](../../koishi/vendor/console-candidate.json)。公开地址仍null，完整源码/发行放行标记仍false。

以下证据均绑定当前安装组件，不能抵消上述失败：

- Markdown标题/列表/表格/代码、普通协议链接与javascript拒绝、script/img/svg/事件属性过滤、空/长中文输入通过；引号href的更强边界随后失败。`extra/ordinary-cases.json`只表示先前普通用例，不是总体通过。
- source/inline/tag/unsafe props更新通过；同一已安装Markdown组件经浏览器中的第二个Vue createRenderer宿主更新后，标题/链接DOM正确变化。
- 新client的 `ctx.logger()` 实际产生records；隔离动态插件注册target消费，真实UI显示、级别过滤、中文多行、循环对象、子logger及下载按钮→浏览器下载文件通过。debug被target等级过滤；warn下载不含info；全量下载含中文换行。证据 `extra/download.txt` 与 `extra/markdown-logger.png`。
- 网关没有独立生产Logger下载页面。本项验证的是实际Logger→隔离插件消费→浏览器按钮/文件的链路，不冒称新增或验证了生产日志管理页。
- 错误密码、登录、退出后unauthorized、动态插件加载/卸载/重载通过；`reconnect/reconnect.json`：连续两次失败后恢复首次完整URL、重新认证、草稿恢复及保存、随后无草稿重连通过。
- `browser.json`：真实Console HTML/JS/CSS/PNG 200与MIME正确，HTTP/WS读取通过；实际loopback资源未配置nosniff，如实记录。新cache适配的17输入、真实Koa清头/JSON/错误及有界缓存定向回归通过。
- TypeScript、46受保护方法Console RPC、架构门禁通过。未手工重跑无关查询/历史全套，无QQ或模型供应商请求。

## 给09的固定组合接口

取本轮最终SHA的package/lock、新原始tgz、vendor绑定及现有cache适配整体；cache-content-type2.1.0/ylru2.0.0不回退。外部依赖树未变。
`console-candidate.json.acceptance.status=blocked` 标记上述源码问题。09唯一负责修复组件、发行准入/冲突/装配；新组件hash必须重新核对相关证据。
本轮不改旧发行器；其拒绝final4及candidate.2仍是已登记装配依赖，不能放宽旧字节门禁求绿。旧组件和历史证据保留，不发布源码下载地址，不宣称完整组合CI或发行通过。
