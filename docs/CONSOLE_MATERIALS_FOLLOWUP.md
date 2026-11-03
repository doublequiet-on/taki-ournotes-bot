# Console 材料增量（2026-11-04）

本轮只补材料，基于固定装配 `a8bdc799c81ce655b76d45ff5a0b13e9b923c5bf`，沿用 PR201。未改组件、依赖锁、builder、recipe、网关或控制器；不重复后台接入和整套安装测试。原组合与材料保持原路径、字节及来源，10/11可继续按固定 hash 验收。

## 交付及版本绑定

- 新增量：`runtime/task09/materials-20261104/console-materials-20261104-increment.zip`，21,732,186 bytes，SHA256 `d5aabd69f1837f32f5b3301713195cdd92f713adffe59142e9bec90f8f8ba922`。
- 137项文件及 manifest；manifest SHA256 `0ac30f86345670de4412c37d46beb89916e0ecfc4d6cae69a9b0288a06a9fc73`。逐成员大小/hash/CRC、唯一安全路径检查及外部回执见同目录 `delivery.json`、`verification.json`。
- 组件来源仍 `91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f`；tgz SHA256仍 `72c286da0c53feb6820c54e4b283d2c752b3b4d7198db1b6f7d38e405b495612`。新材料提交只绑定材料增量，不充当组件构建来源。最终提交与下载索引绑定在原 HANDOFF 和本轮 `publication-index.json`。
- 父组合 SHA256 `8810db8ebaad54db68da1d2dc7dcbc9edb96fdc7322bf38406f6bcac4d2e0b12`；父重建输入 `7440ab10e2df8556ec065c17d1aac9f5b46fde199b18bf57a37c1673e1d1a5ef`；父源码补充 `4a1e255b950bf534c930d6246f6c6e593d0374937e106a809783661126160894`。新包是补充，不替换这三包。
- `gateway-notices.json`仅更新未来封装时使用的随行原文/来源说明；未把旧组合内文件改成新版本。新材料既不重封装 tgz，也不替换原16/16复建记录。

## 五个运行依赖

| 准确依赖 | 新证据与结论 |
|---|---|
| @koishijs/plugin-http 0.6.3 | 元数据 gitHead `d06e1e9b2ad1d7ae7e31efc7f11249b1133d9450`实际位于 koishijs/koishi；`plugins/http`的全部运行包装 JS/声明与 npm 同字节，根 LICENSE取得。源码package版本0.6.1与npm版本不同，明确保留，不改版本冒称重建。 |
| @koishijs/plugin-proxy-agent 0.3.3 | gitHead `b2462893f99a78e7e98bd0ef110f253eebf0a337`同样位于 koishijs/koishi；`plugins/proxy-agent`包装源码与npm同字节，根 LICENSE取得。源码package版本0.3.2差异保留。两个包装均直接委托 Cordis，分别保留被委托包许可。 |
| only 0.0.2 | 准确npm的 Readme.md已有完整MIT原文；官方0.0.2 tag固定为 `61a61534ffadb26ac6171d604a6774d2420d3010`，五个非package.json文件全部同字节。JS本身是首选修改形式；缺gitHead不等于缺源码。 |
| koa-compose 4.1.0 | npm与准确树声明MIT，全文由官方后补LICENSE提交 `903480c36957776f8561a33a840cdd0a06ad611f`取得；相对所选index.js仅移除一个分号，diff保留。收口的是同项目原文及适用代码关系，不称原npm已携带该文件；11仍可复核这条后补来源。 |
| cache-content-type 1.0.1 | npm JS、README及准确gitHead已查。后补LICENSE提交 `919c1f36b9873b6a3606b90ac49f4e4462b3a55c`同时转TS并改ylru，不能直接证明旧1.0.1适用原文。仍阻塞；不借mime-types许可覆盖。 |

上述官方固定源、registry integrity、npm/cache对应、原文和失败404记录均保留在本轮证据目录。404根因已沿准确提交定位，未进行历史Console版本组合搜索。

## 24项缓存：许可、角色和 native 分开

`cache-24-review.json`逐项记录准确版本、父依赖边、缓存路径、原文及源码。24个准确npm包的全部非package.json文件与冻结Yarn缓存逐字节一致；Yarn规范化的package.json不作字节等同断言。依赖边说明解析关系，不自动证明执行或进入运行chunk。

22项取得原文：@cordisjs/plugin-server-proxy0.2.0；@esbuild/linux-x64的0.21.5/0.23.1/0.25.12/0.28.2；@koishijs/plugin-http0.6.3、plugin-proxy-agent0.3.3、plugin-server-proxy1.2.0；@maikolib/vite-plugin-yaml1.1.1；@polka/url1.0.0-next.29；@rollup/rollup-linux-x64-gnu4.67.4；@satorijs/core4.6.1；@vue/devtools-api6.6.4；dumble0.2.3；duplexer0.1.2；element-plus2.7.7；koa-compose4.1.0；only0.0.2；sirv3.0.2；yakumo/yakumo-esbuild/yakumo-tsc1.0.0。

- duplexer使用`LICENCE`，此前扫描拼写遗漏；YAML插件后补LICENSE提交 `2273c3f46fdfdb4de2dc9a273e8ee062e54dff35`的首选`src/index.ts`与所选树相同。
- server-proxy的gitHead实际在koishijs/upstream，原文取得，但源码版本/TS差异不据此消失。devtools缓存6.6.4与实际自建6.6.3-taki.1分别记录；Element Plus缓存与实际TSX/Vue生成输入分别记录。
- esbuild四版本均取得固定Go源码；前三版ELF内嵌build info绑定Go1.20.12/1.22.5/1.23.12、对应干净VCS提交。已补准确Go LICENSE，x/sys固定源码的Go h1校验一致。0.28.2没有同样的内嵌头，仅有go1.26.5字符串，不能单凭字符串证齐工具链。
- Rollup取得完整bundled-dependency LICENSE、固定Rust源码和200项Cargo.lock清单；200不是已链接数量，Linux目标实际native依赖及随行条款覆盖仍待核。
- 余两项：cache-content-type如上；@napi-rs/lzma-linux-x64-gnu1.5.1准确Rust源码有Cargo.toml/build.rs，但无Cargo.lock及已找到的项目许可全文。MIT声明不能代替原文，`.node`不是首选源码；native闭包仍阻塞。

因此“22项已有原文”不是“22项全部native/生成材料放行”。没有笼统采用系统工具例外。

## 22项实际编译输入

`matrix-22-followup.json`保留原逐输出hash、源码树、map和生成证据，新增15项准确配置→入口→输出选择说明及原文件hash：Vue/Router/I18n/Intlify、VueUse、Floating UI、dayjs、memoize-one、Popper及Element icons。包括Icons的SVG→Vue生成、VueUse的metadata/catalog/import生成和Floating UI共享config依赖。小片段只是导航；完整固定源与锁在父材料及新增source-archives中。

- 复用已成立证据：tinycolor11项实际JS同字节、async-validator/normalize-wheel map内容与首选源相同、marked TS→map中间JS比较；未重跑它们。
- 新增cosmokit1.8.1定向生成：固定TS及原命令，用已有固定esbuild0.28.2生成实际lib/index.mjs，逐字节相同，输出SHA256 `8c493c6ab7e44ab7608e407e84a79e86dfb6c17b8067cbccfcf29c011285f72a`。
- marked-vue1.3.0：所指源码package1.2.3，原命令`--external=`语法被工具拒绝；修正命令语法后仍缺Vue解析。需准确生成recipe/外部Vue策略，尚未证明实际输出来源。
- reggol1.7.1：准确npm已含首选TS；固定树package1.7.0，按登记命令及确切object-inspect补输入后产生不同格式/闭包，完整diff保留。需核实际生成recipe，不把自建不同输出替换当前组件。

15项静态recipe追溯不声称历史发布字节重现或全部生成闭包证明；尚缺的生成器依赖及必要输入逐项登记。没有机械重建全部上游，也未为求同字节删差异。

## 拟公开集合、验证与剩余决定

新ZIP包含原文、定向源码/配置、增量source archives、索引及复现说明，不带旧wrapper缓存或新安装树；原始下载、失败实验和未放行lzma参考材料保留内部研究目录。源码archive中的链接原字节保留，`source-archives.json`登记；未来提取须核目标，产品安装包禁止链接的规则不变。父重建输入仍冻结，未重新删缓存或生成一个未经复建的新输入集。

本轮自查：旧工件及组件hash不变、新137文件清单/CRC/hash、24缓存绑定、原文hash、22源码archive/配置hash、定向生成日志及完整diff。没有重跑无变化的后台/整套回归；a8bdc799的CI只证明原代码。新材料提交CI另登记。

未放行项：cache-content-type旧版适用原文、lzma许可/锁与native输入、其他native传递条款覆盖、marked-vue/reggol及其余未证生成关系、11增量复核与源码托管。若最终必须替换cache包装器或排除lzma缓存，应先明确最小代码/构建集合变更与相应兼容、隔离复建影响，另交候选；本轮不执行替换。保持`complete_corresponding_source=false`、`product_release=false`、`hosting_url=null`、`published=false`，不生成假公开链接。

## 第六轮审计后的有界收口

本节以18bcba9为固定输入，旧材料ZIP `d5aabd69…`及所有旧安装包保持不变。新增记录在`runtime/task09/closure-20261104/`，不写11审计目录。18bcba9的Checks41708122418已回读success，未发现需修复的本任务CI失败；新文档提交CI另登记。

### 原始字节与规范化比较纠正

| 对象 | 原始bytes / SHA256 | 比较结论 |
|---|---|---|
| 组合内`combination.json` | 414953 / `64582024fd05e89551f7a8196647bd0dd7ca23fc350374cb1b2769d545473a15` | 比外部文件多一个末尾LF；JSON相等，原字节不等 |
| 外部`combination-manifest.json` | 414952 / `93f1b9f623c2bcb63bde5df5c66f7f8b422cf4b5beb3f692cf8054b9e331121d` | 不可用此hash验证组合内成员 |
| 组合内`licenses/koishi-console/gateway-notices.json` | 97318 / `96eaf3104122a1bcdac462502702b92d2d1a3dc4290ad2e653bd46d2826eb904` | LF文本，等于a8固定Git内容 |
| 原补充ZIP内`gateway-notices.json` | 98002 / `751c87c5121ea939fb77f34829395bfd2672b5b2bfb2a757ebc5566c17efdd31` | CRLF文本；只在CRLF→LF后与组合成员相同 |

`raw-byte-corrections.json`由两冻结ZIP和外部文件重新计算，分别保留原始比较、JSON比较和换行规范化比较。原HANDOFF已纠正，不为修正文案重造组件；18bc许可增量又是另一版本，不能替换上述旧成员摘要。

### 固定生成输入的入口、格式、flags和命令

以下是从**已取得固定源码**补齐的生成说明，不是重新搜索版本或证明所有原npm发布字节相同。`generation-input-contract.json`逐22项绑定实际输入摘要、准确源码archive/commit、根和包级package.json、工具依赖声明及锁/配置hash。执行上游命令前须按该源码自己的包管理器/锁准备工具；不能把Taki的500缓存当全部上游工具锁。动态banner年份、COMMIT及发布环境覆盖值不补造为历史事实。

| 实际输入 | 入口与生成命令 | format、flags和必要依赖 |
|---|---|---|
| marked `lib/marked.esm.js` | `src/marked.ts`；根`rollup -c rollup.config.js` | `format=esm`、sourcemap=true；`@rollup/plugin-typescript`按tsconfig擦除TS并打包。terser只用于`marked.min.js`分支，不应用于本项；版本来自package/SEMANTIC_RELEASE_NEXT_VERSION，年份动态。保留rules.ts的非同字节中间比较 |
| Vue `vue.runtime.esm-browser.prod.js` | `packages/vue/src/runtime.ts`；根Rollup配置，`TARGET=vue FORMATS=esm-browser-runtime NODE_ENV=production` | ES、runtime-only、生产压缩；DEV=false、BROWSER=true、ESM_BROWSER=true、ESM_BUNDLER/GLOBAL/CJS/COMPAT/TEST=false、SSR=true；SUSPENSE/OPTIONS_API=true，PROD_DEVTOOLS/PROD_HYDRATION_MISMATCH_DETAILS=false。runtime-dom/runtime-core/reactivity/shared及根alias/replace/esbuild配置一并保留 |
| Vue shared `shared.esm-bundler.js` | `packages/shared/src/index.ts`；`TARGET=shared FORMATS=esm-bundler` | ES bundler分支，保留交给消费bundler处理的DEV/feature标识；不能误用上一行browser常量。工具和workspace别名使用同一固定Vue树 |
| Router `vue-router.esm-browser.js` | `packages/router/src/index.ts`；该包`tsdown`读取`tsdown.config.ts`的esmBrowser对象 | ESM、非prod文件，DEV=true、TEST=false、BROWSER=true、FEATURE_PROD_DEVTOOLS=true；vue external。不得因为Console最终是production就把此已选上游入口改成prod版本 |
| I18n `vue-i18n.esm-browser.prod.js`、Intlify core-base browser prod | 各`packages/<target>/src/index.ts`；根Rollup，`TARGET=<target> FORMATS=browser NODE_ENV=production` | ES browser生产分支；DEV/TEST/GLOBAL/RUNTIME/ESM_BUNDLER/NODE_JS/LITE/BRIDGE=false，BROWSER/ESM_BROWSER=true；FULL_INSTALL/LEGACY_API=true，PROD_VUE_DEVTOOLS/PROD_INTLIFY_DEVTOOLS/JIT_COMPILATION/DROP_MESSAGE_COMPILER=false。TS/replace/json/terser及shared/message-compiler workspace输入按固定配置 |
| Intlify `core-base.mjs`、`shared.mjs` | 对应target，根Rollup `FORMATS=mjs` | ES bundler分支，DEV保留`process.env.NODE_ENV !== 'production'`表达式；I18n功能标识保留给消费bundler。不能套用browser产物的已固化flags |
| Floating core/dom1.8.0、utils0.2.12 `.mjs` | 各固定包`src/index.ts`，utils另有`src/dom.ts`；先构建workspace `config`（`tsc -b`），再包内`rollup -c` | `config/src/defineRollupConfig.mts`的mjs分支，format=esm，DEV替换为`process.env.NODE_ENV !== "production"`，不强制false；external由各包globals键生成。node-resolve、Babel TS/TSX及annotate-pure-calls、根Babel配置和workspace源码不可遗漏；core/dom只用修正tag树 |
| VueUse core/shared `index.mjs` | `pnpm run build`：metadata子包update → scripts/update.ts → scripts/build-run.ts/build.ts → updateImport → Rollup →输出目录整理 | metadata、meta/packages.ts及utils生成exports/包声明；core/shared为ES、默认target=es2018，非minified `.mjs`；esbuild/json/pure插件，vue-demi/@vueuse/shared/core/metadata external。没有凭空添加production flag；minifier位于另一个iife.min分支，NETLIFY贡献者更新不是此构建必需输入 |
| dayjs核心与8插件 | `BABEL_ENV=build node build`；core `src/index.js`，插件`src/plugin/<name>/index.js` | UMD、compact=true，Babel preset-env modules=false/loose=true及terser；dayjs external/global。实际8插件是advancedFormat、customParseFormat、dayOfYear、isSameOrAfter、isSameOrBefore、localeData、weekOfYear、weekYear；本组实际输入**没有locale文件**，不沿用含混的“locale/plugin”统计 |
| Element icons `dist/index.js` | 固定SVG → `packages/vue/build/generate.ts`生成Vue/index → `build/build.ts` | esbuild ESM、neutral、es2018，unplugin-vue production、hoistStatic=false，vue external，minifySyntax=true/minify=false；保留SVG和生成器，不能只交生成Vue |
| Popper ES / memoize-one | `tsx build/build.mts` / `rollup -c`，分别`src/index.js` / `src/memoize-one.ts` | Popper：Flow/Babel→esbuild target es6、minify=true、NODE_ENV=production→ESM dist/index.mjs；memoize：Rollup TypeScript→ESM dist/memoize-one.esm.js，非UMD压缩分支 |
| tinycolor / async-validator / normalize-wheel / cosmokit | 复用已经核实的TS转换、map源对应或定向字节生成记录；分别原tsconfig.module、pika-pack pipeline、tsup、固定esbuild ESM命令 | 不重跑已有证明；保留相应package配置和编译工具输入，map对应不升级成完整bundle重建。cosmokit实际输出已同字节 |
| marked-vue / reggol | 本节下方的新下游构建提案 | 原有命令失败/输出差异保持失败；本轮没有再换工具版本试配 |

上述flags是所选固定配置分支的默认含义；配置支持环境覆盖的地方仍显式保留，不将未知历史环境写成已证。源码说明闭合与最终对应源码/公开材料整体放行仍分开。

### lzma：实际用途已验证，排除方案待决定

冻结Rollup4.67.4的package.json把`@napi-rs/lzma-linux-x64-gnu@1.5.1`列为optional，并注明用途是上游`napi build --use-napi-cross`交叉工具链。其已发布JS中lzma文本命中是扩展名表，不是加载该包的调用。当前Console使用预编译Rollup，未重编上游native产物。

新增定向证明：原7440ab…输入、新隔离目录/HOME、原immutable安装与types/lib/patch/client命令，预加载探针同时禁止lzma的require、import和process.dlopen。9个Node进程启用探针，26次Rollup解析可见，构建中被阻止尝试为0；16/16输出仍等于原组件。require/import两个强制加载对照均收到指定拒绝码。`lzma-build-proof.json`、trace与五阶段日志保留。未修改锁、删除缓存或运行后台/整套业务测试；不是OS禁网证明。

可搬迁复核命令（使用Linux绝对路径，work/output必须全新；探针与脚本同目录）：

```sh
python3 probe_build.py --repository /absolute/task09-console-assembly \
  --work /absolute/new-lzma-probe-work --output /absolute/new-lzma-probe-evidence
```

**建议下一包只排除构建工具的该optional边，不补一个本构建不用的Rust工具链。** 最小可审方案：对固定Rollup工具做独立标识的本地构建副本，仅修改package元数据去掉此optional依赖，保留dist/原许可及全部非lzma依赖；为该副本记录补丁和完整hash，重解构建专用锁和缓存。不要用仅能增加依赖的配置冒称删除边，也不通过改supportedArchitectures误删实际所需Rollup/esbuild native包。运行网关lock和组件先不动。

实施门槛：新目录immutable安装不索取lzma；实际build同样16/16；新锁/缓存差异只含该边及必要工具身份变化；原文/修改说明/manifest齐备，再以新路径交11。当前仅证明**执行不需要**，尚未生成或宣称“排除后集合可重建”。若将来启用Rollup native交叉编译，该结论不适用；那时须补lzma项目原文、缺失Cargo.lock的精确Rust解析、目标工具链及传递源码/条款。

### cache-content-type：两条具体最小方案

固定组合唯一直接调用点是`gateway/node_modules/koa/lib/response.js:9`，329—335行的`response.type` setter将返回值写入Content-Type，假值则移除该头；这会影响HTML/JS/CSS、JSON、错误响应及静态资源。旧1.0.1返回CJS可调用函数，委托mime-types并以ylru缓存。

1. **可核版本方案：2.1.0+显式CJS适配。** 准确npm gitHead `b4d59700bf3f0d4aad25db504cb316526fc029fa`，tgz SHA256 `2ee7f6c454996e6780d41eb31309910cbe6cde522bd939aeb77ef5d3beb4315f`；npm LICENSE和src/index.ts与固定源同字节。Node要求>=18，依赖mime-types^2.1.35、ylru^2.0.0。它的require结果是带default/getType的对象，**不能仅改锁就替代Koa所需函数**；需窄CJS适配层或明确的Koa调用补丁及独立来源/身份。旧缓存遇false会重算，新版缓存false，需接受并验证这处缓存行为变化。2.0.0准确npm仍缺原文，已排除，不把源码树的占位版本误认成其发布包。
2. **独立实现方案（改动面较小的备选）：** 以公开接口行为为规格维护Taki CJS函数，复用当前固定mime-types，缓存实现明确有界；不复制旧包装器并自称授权已解决，也不必引入ylru2/ESM适配。新源码、许可、包身份和依赖映射独立登记。若改变缓存淘汰/假值缓存策略须明说，不能宣称内部策略逐步一致。

08需要验：require结果可调用；html/.html、json、完整MIME/charset、png/css/js、大小写、未知扩展与空值的结果及Content-Type删除行为；重复/超过缓存容量的请求、并发响应隔离；真实后台HTML/静态资源、JSON RPC/错误响应Content-Type与nosniff兼容。09在选定方案后锁准确依赖、附原文与源、检查安装树及组合包；10验安装更新/回退树。此处不改变认证、scope政策或响应业务。两方案本轮均未安装或应用。

### marked-vue / reggol：最小下游候选边界

- marked-vue：固定首选TS树package1.2.3与实际npm1.3.0有差异，不能静默降级。可在该固定TS基线上建立独立下游身份，先与现1.3.0对照公开props/渲染行为并补明确差异；构建显式`format=esm`、neutral、target es2022，Vue/marked/xss external，使用原已固定工具和依赖。原命令拼写问题与缺Vue解析仍保留，当前没有新成功输出。若首选TS无法在该边界保持能力，交00扩大或放弃该方案，不继续猜历史版本。
- reggol：以准确npm1.7.1内的TS为首选源，保留版权；显式browser/ESM格式，固定object-inspect及cosmokit/supports-color外部边界，名称保留策略写入recipe，输出另设下游身份。原登记命令的格式/闭包差异不通过改预期消除，不重跑版本试配。
- 两者都可能改变Console chunk字节，必须另交组件候选、来源/许可证索引和TAKI标识，不能复用72c286…验收。09负责构建差异；00安排08定向验证Markdown标题/列表/表格/代码/链接、HTML/XSS过滤、空/长/实时更新内容，以及浏览器日志级别/命名/循环对象检查、插件日志UI与无Node专属API报错；再验受影响动态插件和重连后恢复。网关业务本轮零改动。

本轮待决定项限于上述cache处理路径、构建专用lzma排除，以及两模块独立下游构建范围。公开托管和最终发行仍待原门禁，不新增材料搜索轮次作为默认下一步。

新增复核证据：`runtime/task09/closure-20261104/task09-closure-evidence-20261104.zip`，508417 bytes，SHA256 `2c36826db239036941b1b55db123128a7eb59a024fbd7f8805eb7845111bab16`；72文件+manifest，manifest SHA256 `dc4b36fc1ed9c9e7dc433968ed4880879d855b1e46afc18034c372ad3d938c71`。这是内部复核证据，不是新的组件或已放行公开材料包。包含原始hash纠正、可搬迁探针、构建日志、生成配置绑定及替代参考；旧四份冻结ZIP再次实测摘要不变。
