# Console第七轮后构建修复（candidate.2）

本轮基于09 `07ad693f7a32b5c3d63e313514ec5c1144c6747c`，独立构建候选；不合入08移动分支、不改网关package-lock或业务。旧组件、组合包与旧材料完整保留。新组件待08定向回归和11材料复核，`complete_corresponding_source=false`、`product_release=false`。

## 固定输入与结果

| 对象 | 大小 bytes | SHA-256 |
| --- | ---: | --- |
| 原缩小输入console-reduced-rebuild-inputs.zip | 128972431 | 7440ab10e2df8556ec065c17d1aac9f5b46fde199b18bf57a37c1673e1d1a5ef |
| 新Rollup-only输入rollup-only-inputs.zip | 128358138 | b6ba8f4199f2545d8dfda94e53e8baeebbba44ceb957f9de55f5d83e2fcc5666 |
| 新候选输入console-candidate2-inputs.zip | 128358736 | 04e7bb6a403b28e33eb4cc278734720dab2ebec091a1841450bb214f6b16275f |
| taki-console-0.1.0-candidate.2.tgz | 634437 | 5b73df05ece95041f0586177bd077e72cb4bdd0df7c996c5ad7017909bad62dc |
| candidate.2 component-manifest.json | 见交付索引 | ac16d5e340153559f9ae4eb72d50e3910c504abf1badbcfe0145438ae9bc8b1e |

新组件包版本为`@koishijs/plugin-console@5.30.10-taki.2`，基础webui固定`896882b50cc91d16f522a52ded9644045d461c26`。原candidate.1源码来源仍91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f、tgz72c286…；不能替换其来源标签。

本轮实际文件位于装配工作区`runtime/task09/build-repair-20261104/`。输入ZIP、组件、材料ZIP与源码提交绑定见原HANDOFF/artifact-index及本轮delivery.json；只提供本地材料，未发布获取入口。

## 构建专用Rollup元数据

`prepare_console_build_inputs.py prepare`只接受上表原输入摘要，在新Linux目录解开原包。从准确Rollup4.67.4缓存复制完整包到`source/taki-tools/rollup`，**package.json唯一语义差异**是删除`optionalDependencies.@napi-rs/lzma-linux-x64-gnu=1.5.1`。包原name/version、JS、LICENSE、native平台依赖和平台选择保留；下游副本用`rollup@portal:./taki-tools/rollup` locator和`rollup-metadata.json`前后hash标识，不冒称新的上游发布。

根构建专用resolution指向该副本。Yarn4.10.3在`YARN_ENABLE_NETWORK=0`、新HOME、本地缓存模式下重新解析；原500 ZIP变为498，删除的两项是lzma缓存和已完整复制到本地目录的Rollup缓存。498项保留缓存逐字节相同。`lock.diff`记录lzma条目/optional边移除及Rollup hard→portal soft locator，其余解析保持不变。未改supportedArchitectures，也未删旧ZIP文件。portal软链接只存在于隔离构建安装树；输入ZIP和产品tgz仍拒绝链接，未放宽产品安装规则。

新目录immutable/immutable-cache安装成功，无lzma锁项/缓存/安装目录；完整types/lib/patch/client成功，16/16旧组件输出同字节。新输入包括原始许可、全部非元数据工具字节和独立修改记录；这证明本Console构建不需要lzma，不适用于将来重编Rollup native交叉工具链。

```sh
python3 scripts/prepare_console_build_inputs.py prepare --inputs /absolute/console-reduced-rebuild-inputs.zip --work /absolute/new-input-work
python3 scripts/prepare_console_build_inputs.py pack --work /absolute/new-input-work --output /absolute/rollup-only-inputs.zip
python3 scripts/rebuild_console_sources.py --inputs /absolute/rollup-only-inputs.zip --inputs-sha256 <本次实际摘要> --work /absolute/new-proof-work --output /absolute/new-proof-output
python3 scripts/prepare_console_build_inputs.py pack --candidate --work /absolute/new-input-work --output /absolute/console-candidate2-inputs.zip
python3 scripts/build_downstream_console.py --inputs /absolute/console-candidate2-inputs.zip --work /absolute/new-candidate-work --output /absolute/new-candidate-output
```

重锁日志时间与ZIP封装时间可能导致重新派生输入归档摘要不同，必须评审并更新固定规格；不能绕过builder的精确输入hash。已交固定候选输入可直接用最后一条命令独立复建。builder另核输入中的recipe/patch与当前文件同字节，拒绝lzma回流和重复源码补丁。

## 两个源码生成入口

| 模块 | 首选输入 | 生成步骤及边界 |
| --- | --- | --- |
| marked-vue | 官方固定树86f4fa602bc4ad588661bbddcc0fee5126c295b7的src/index.ts，树package版本1.2.3；不冒称npm1.3.0原字节来源 | esbuild0.23.1 API，bundle=true、format=esm、platform=browser、target=es2022、keepNames=true、sourcemap/sourcesContent/metafile=true、legalComments=eof。Vue/marked/xss显式external，再由固定Vite5.4.21装配；Vue继续由Console外部模块提供。未改类型或运行源码。 |
| reggol | 准确npm1.7.1随包src/browser.ts及shared.ts；npm tgz9486eb19c2924ad805e2baaea4f44b1a696f54a1211aabdb971ba0ce94b0e688 | 同上显式ESM及名称策略，cosmokit/supports-color/object-inspect外部化，再由Vite解析。export=按ESM默认导出编译。没有沿用旧树实验、没有追加类型修改。 |

精确编译flags、输入TS hash和metafile在`candidate2-a/module-evidence/generate-{marked-vue,reggol}.json`；生成ESM与内嵌首选源码的map在generated-sources。既有devtools三项类型适配保留，运行表达式不变。

旧reggol Git实验与准确npm shared.ts有三处类型差异：`Logger.Target→Target`、`Logger.Type→Type`、`Logger.LevelConfig→LevelConfig`，见reggol-types.diff。browser/index/node TS一致。这些类型擦除差异不解释旧IIFE/ESM、名称辅助函数或object-inspect闭包差异；本轮显式确定ESM、keepNames及external，不再猜版本。

两个独立目录/HOME完整构建的tgz和component manifest同字节。16项输出只改变dist/client.js，其余15项（含lib、CSS、Element、Vue/Router/VueUse）不变；原输出摘要与新摘要保存在candidate-comparison.json。新组件的reconnect源码仍只应用一次原固定patch。

## 自查与尚未接通的验收

- 固定Vite浏览器打包后的marked-vue：Markdown标题/强调、危险script/事件/javascript链接过滤、安全链接属性、Vue自定义renderer中响应式source/inline更新通过。保留unsafe显式能力，没有改变业务策略。
- Logger新旧浏览器模块默认导出、类名、format/color/getLevel、info/warn/子logger、结构化record及JSON导出字段对照通过。未把这项当成浏览器日志下载按钮验收。
- 168个组件文件、135条许可索引校验；真正npm pack后所有成员字节相同（tgz封装hash不同且单列）。files白名单显式包含licenses、LICENSE与两份TAKI材料，避免后续封装丢材料。
- 缺LICENSE、client篡改、未知身份、未批准输入、重复源码patch、旧网关拒绝新绑定负例通过。负例脚本在本轮材料中，未新增提交测试文件。
- 直接Node ESM导入marked-vue最初失败：xss.parseAttr不能从CommonJS赋值导出自动识别。失败记录保留；本模块目标为Vite浏览器装配，随后按真实打包边界自查通过，没有改源码迎合Node直载。
- 完整构建保留原peer警告、CJS import.meta警告、Vite大chunk警告。没有跳过types、改门禁或删除失败记录。

08最小接入：在其独立工作区把vendor、console-candidate绑定及gateway package/lock显式改为本候选版本/摘要，重新核完整外部依赖树；09当前console_release.py仍只接受原candidate.1，待00登记08固定SHA后09更新准入与发行装配，不能只替换tgz求通过。

08定向回归：`koishi/console.cjs`插件加载、Console HTTP/WS、RPC/认证/撤权；`koishi/console/index.js`等插件页面中的Markdown说明/帮助/日志，验证安全链接、XSS、props更新、默认/命名导入；日志record消费、过滤、下载文件内容及中文/多行/error对象；断线重连回原URL且不叠旧字节补丁；现有个人页面/表单保存和scope撤回行为。cache-content-type与网关锁仍归08，本轮未碰。真实浏览器/网关、10恢复安装和11材料复核均待后续，不记通过。

## 工具来源与有界材料边界

`esbuild-resolved-parents.json`根据新yarn.lock的完整descriptor查resolution：root→0.23.1、Vite5.4.21→0.21.5、importx0.5.2→0.25.12、tsx4.23.15→0.28.2；每个esbuild版本只连到同版本`@esbuild/linux-x64`。旧按包名匹配所得另外12条父边是误报，保留旧证据并由此记录更正。

本次携带的esbuild0.28.2：准确npm原生包SHA9573bb2233aab0f9ea7647d5cca9726113cc1768de61d66b17267f4db84488f6、binary SHAe1698a3d5c6c0798fee4fd3b5cc816651f460c63d390a7a26ea4beb0b1884100；固定源码609683d892977362a0f99026cb74b96263d728a9，源码归档SHA04cb2d38fb6af093577aa8bf174add7286099b65ef7854480cbbb8ff061cef1f，MIT原文随材料。准确scripts/esbuild.js给出`go build -ldflags=-s -w -buildid= -buildvcs=false -trimpath ./cmd/esbuild`；go.mod/go.sum锁定x/sys `v0.0.0-20220715151400-c0bba94af5f8`。**该原生文件缺可解内联Go buildinfo，go1.26.5字符串仍不是完整构建来源证明**；没有声称得到原发布工具链/全部标准库与依赖的精确字节对应。前三版esbuild已关闭证据不重开；0.28.2运行工具材料完整性仍交11判断，未伪装为自建工具。

Rollup仅携带Linux x64 glibc native，包4.67.4、binary SHAa6f7167810c4a34d66083eb2c148878a0d44960b3ec204e88cdd37292a6d38fb，固定源码18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6。材料含完整固定source.tar.gz、LICENSE-CORE/总LICENSE、Cargo.lock及四个Cargo.toml。目标`x86_64-unknown-linux-gnu`，入口rust/bindings_napi、cdylib；上游`npm run build:napi -- --release --target x86_64-unknown-linux-gnu`展开为napi build对应cwd/platform/dts/no-js参数，release strip/lto=true、opt-level3、codegen-units1。

显式feature：napi禁默认、启napi4；Linux x64 glibc选mimalloc-safe/local_dynamic_tls；不启aarch64专用no_opt_arch；parse_ast的swc_common/parking_lot；xxhash-rust/xxh3。准确直接解析版本/checksum在native-materials/rollup-target.json；bindings_wasm不是该native入口。未重建200个Cargo包，也没有把Cargo.lock所有项当成实际Linux运行闭包。全传递feature/条款逐项对应和原native发布工具链证明仍未完成，记录false。删除本Console不用的lzma不能证明未来Rollup native交叉构建无需它。

新输入和材料仍是拟公开集合候选；完整来源/许可审查、公开托管位置/访问范围/维护责任及显著源码链接尚待11和用户决定，不提供虚构公开URL，不标最终个人/定制发行通过。

## P1安全修复：candidate.3（作者自查）

08固定399cc8a47bf84aa550ff2eb3d1e3d640101c2033发现默认unsafe=false时HTTPS href内的原始双引号越出属性，生成onclick并在真实点击时执行fixture标记。candidate.1/2均在真实浏览器复现，属于继承缺陷；此前普通XSS用例通过不能关闭此边界。

修复在首选marked-vue TS上应用`marked-vue-href.patch`，recipe核before/after/patch摘要并拒绝重复应用；没有修改压缩dist或安装文件。顺序是：先把原始引号编码后在惰性template中按浏览器**属性**规则解码一次，读取完整href值；用原有URL构造与http/https/mailto/tel协议清单校验；最后先编码amp，再编码引号/尖括号输出。这样协议检查与浏览器最终收到的值一致，双重实体不二次解码。template不插入DOM，不加载资源；这个Console浏览器组件的sanitize链接路径现在明确依赖document，未声称Node直载或SSR支持。相对链接、片段和四种协议保留，未扩大协议清单，title原编码路径保留。

源码before 810c43dcad4749eb6e1c00670d13f5929aac2252ba77f05b1cddc1307d8964a6；after 19e78466c78037a5a18e1dedc64f6420e90f895f587b6651e4b09e59e3104930。完整types/lib/client构建通过，16输出仅client.js改变。

最终输入console-candidate3-security-inputs-v2.zip 128359885 bytes / SHA256 c6261043446d4d717af86a3466733f3f284021dbfd996082464ec202b2a68e0b。相对candidate.2输入只更换recipe、增加安全patch，lock和498缓存不变，因此仍携带cache-content-type1.0.1材料缺口。初次构建输入及组件保留为中间证据；最终patch改用零上下文格式清除自身尾随空格，生成的首选TS与client.js运行字节不变，组件身份和输入摘要随后重新生成。安全修复没有以改运行依赖或重锁掩盖该缺口。

最终组件taki-console@0.1.0-candidate.3 / npm兼容版本5.30.10-taki.3，634671 bytes / SHA256 ea92ea91d816648023e61cf6ce7dfe2de98aece38e1ca8dd9292166f5537d1fb；manifest SHA256 ed8de5188b9a208266d7aaae18587d833fe3fbcb76336b857f6b8d6e8a95018d。初次安全构建组件保留为中间证据；最终组件的26个lib/dist/src运行文件与浏览器自查组件逐字节相同，差异仅为TAKI-CONSOLE.json中的最终输入/patch身份。原组件和旧失败证据保留。

作者Chrome156浏览器自查加载tgz的实际client.js注册的k-markdown（不是单独字符串函数）：26个链接/点击用例覆盖原08失败、单双引号、命名/数值/双重实体、tab/CRLF/NUL/前导控制字符、危险与未批准协议、合法相对/片段/http/https/mailto/tel、query amp及title；不得出现事件属性，点击fixture标记保持0。普通标题/列表/强调和props响应式更新通过。旧两组件同一原失败均产生onclick且标记1。浏览器只访问loopback，capture preventDefault仅阻止导航，不阻止事件传播。完整输入、DOM、点击结果及截图在本轮security-20261104证据中。

这是09作者自查通过，不是08网关接线或12独立审查关闭。新固定包交00供08接入、12复核；统一装包继续等待固定接线与安全验收。cache构建依赖处置、Rollup传递源码/原文、esbuild历史未知及公开托管仍单列，complete_corresponding_source/product_release/published继续false。安全修复单独提交，不被材料工作延迟。
