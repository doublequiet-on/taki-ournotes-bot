# Console有限追溯：第五轮

## 先登记的候选与停止条件

保持原Console npm5.30.11及其固定hash；基准是第四轮材料v2及11第三轮复核。本轮不搜索历史锁、不改版本或组件保护hash、不自动选下游版本。

| 输出/输入 | 固定候选 | 排除与停止规则 |
| --- | --- | --- |
| vue.js | Vue3.5.14官方tag/源码与生成脚本 | 11已关闭二进制复制识别；只补源码/许可，不重复比较二进制 |
| vue-router.js | vue-router4.5.1官方npm/gitHead | 原横幅仅作定位；检查浏览器入口、map及源码/构建变换；缺传递输入则保留缺口，不换猜版本 |
| client.js、element.js、index.js、style.css | I18n9.14.4、Element Plus2.7.7、既定webui896882b源码 | 核固定源码、生成输入及与实际chunk关系；不以单组件横幅推出整chunk闭包；仅一次有依据的对照，不做工具版本组合搜索 |
| vueuse.js | @vueuse/core11.1.0、11.2.0、11.3.0 | 上游范围^11.1.0；registry显示截止2025-11-17发布时恰有这三个稳定候选。先以完整导出/API及源码指纹排除；仅对未排除者做同配置有限构建。若仍多义或缺传递闭包，停止并交00，不扩展范围或逐版本猜测 |

固定原输出始终保留。字节不同不等同法律结论，字节相同也不自动补齐第三方首选源码、许可和取得方式。任何未证事实保持unknown。A研究止于本表候选；B自建、C固定替代、D排除均由用户决定。

## 材料线A的验收口径

12项chunk参与者按准确npm版本、官方gitHead/tag、实际包目录记录根/包级许可原文、版权、例外、缓存对应关系及首选修改源码。lodash-unified单独检查原wrapper，不把Lodash/Element Plus许可移植给它。其余53项按实际构建命令、lib外部import和缓存交付范围分类；研究材料外发也须处理所携内容，不把缓存上界当runtime。

本轮结果、源码archive/hash、逐包表和六输出表随原HANDOFF及增量材料索引交11；complete_corresponding_source和product_release继续为false，除非后续实证支持改变。

## 材料线A结果

12项官方npm tarball均通过registry integrity校验，与v2材料中的对应Yarn缓存**全部文件同字节**。新增材料保存准确npm、registry记录、官方固定Git源码archive及原许可全文；逐文件来源和hash见增量包 `twelve-materials.json/csv`、`source-bindings.json`、`source-licenses-and-links.json`。后者枚举源码归档中的许可/NOTICE和链接；原归档完整保留，只读普通文件，不静默丢弃链接，也不改变产品安装包拒绝链接规则。

| 实际参与包 | 官方固定Git输入、实际目录 | 许可/首选修改源码结果 |
| --- | --- | --- |
| @cordisjs/core 3.18.1 | cordiverse/cordis `4658414e9414464ff1d08d076882fb66c1a3bb1c`，packages/core | 根MIT原文，Shigma 2021-present；npm含TS，8份中7份与树相同，reflect.ts存在实际差异，附patch/hash；树版本3.18.0 |
| @cordisjs/logger 0.3.3 | cordiverse/cordis `a2ea5b1c94971440942878aaa35ce165e45352b8`，packages/logger | 同仓根MIT；npm的src/index.ts不同于Git，附完整diff；树版本0.3.2 |
| @cordisjs/schema 0.1.1 | 同上a2ea5b1，packages/schema | 根MIT；npm TS与Git同字节；树版本0.1.0，发布过程未证明 |
| @cordisjs/timer 0.3.2 | cordiverse/cordis `6327f6f43e95aef85420479f23f793fb8755e7c2`，packages/timer | 根MIT；npm TS使用Context.current，Git为Context.trace，附diff；树版本0.3.1 |
| @satorijs/element 3.2.0 | satorijs/satori `0c6a7ddd9a1979c4ccc43b3145b11faf9d2ad7fd`，packages/element | 根MIT，Shigma 2019-present；TS同字节；registry旧directory写packages/message，实际按包名定位 |
| @satorijs/protocol 1.7.0 | 同上0c6a7dd，packages/protocol | 根MIT，TS与树同字节 |
| @vue/devtools-api 6.6.4 | vuejs/vue-devtools `df6ab6bb7791a7a525a97990de73b3ea5e9a1941`，packages/api | 根MIT，Evan You 2014-present；首选TS树已附，树版本6.6.3；npm编译JS不冒充首选TS，对应变换仍未证明 |
| cordis 3.18.1 | 同上4658414，packages/cordis | 根MIT，10份npm TS均对应树；registry旧directory写packages/cli，树版本3.18.0 |
| element-plus 2.7.7 | element-plus/element-plus `8c0180c37ae4741f40969a26a9704c1068be4d93` | 根MIT，Element Plus 2020-PRESENT；wrapper目录packages/element-plus版本0.0.0-dev.1，实际源码在components/hooks/utils/locale/theme-chalk及internal/build。实际参与模块map的675份sourcesContent中673份与树同字节；另2份是版本生成文件及Vue导出helper虚拟输入，保留内容与待验证生成关系 |
| lodash-unified 1.0.3 | npm未给repository/gitHead；四文件原包 | 三个普通re-export wrapper及package.json完整保存；无文件头版权或许可全文，只有MIT声明，**许可缺口未关闭**。不以Lodash许可替代wrapper许可 |
| schemastery 3.18.0 | shigma/schemastery `9e1f54f8ff785a4e51a022922999634205b61e1f`，packages/core | 根MIT，Shigma 2021-present；npm TS同字节 |
| schemastery-vue 7.3.15 | shigma/schemastery `a7a6d26683129ac1b847dcf26ca3abaaf525b165`，packages/form | 根MIT；39份npm TS/Vue与树同字节；树版本7.3.14，发布过程未证明 |

根/包子树未发现额外命名许可文件的结果只表示本次文件扫描，不推定全仓/所有传递组件均为同一许可。Element Plus未使用的整包browser map含其他捆绑版本，单列为npm材料交付范围；未把其4224份全部sourcesContent冒充实际chunk闭包。实际重建使用lodash/lodash-es **4.18.1**，其各自官方gitHead源码和原LICENSE（含Lodash及Underscore版权）另附，不能由wrapper的通配peer推断历史版本。最初下载的4.17.21不进入绑定输入和增量包。

其余9工具、8外部运行候选、36参与未明项全部列在 `all65-scope.json/csv`：保留原分类、每个缓存精确版本、workspace依赖边、缓存package.json入边、lib直接import以及本次CJS加载/子进程证据。`koishi`是lib直接外部入口；其余7项只是该依赖链候选，未启动服务验证，不据此自动纳入最终运行树。工具按实际命令/加载记录细分，未命中CJS记录不能排除ESM使用；36项的开发/测试/服务声明与运行实证分开。v2仍携带全部缓存，外部分发该研究包时53项材料义务不能因未入chunk而忽略。

9项工具中，实录命中@esbuild/linux-x64 0.23.1/0.28.2启动、@maikolib/vite-plugin-yaml 1.1.1与@rollup/rollup-linux-x64-gnu 4.67.4加载；0.21.5/0.25.12未由该追踪器观测。yakumo/yakumo-esbuild/yakumo-tsc 1.0.0由真实build命令、yakumo.yml的tsc/esbuild/client管线及日志绑定，但CJS追踪未命中其ESM加载，不写成逐模块执行证明。native二进制仍非首选修改源码；这9项的许可/源码补全没有被本轮12项工作自动覆盖。

## 材料线B：六输出有限追溯

VueUse11.1.0的联合导出287项，缺原输出288项中的breakpointsElement，排除。11.2/11.3导出集合均相同；原输出包含11.3新增的onClickOutside多根shapeFlag===16和joinPaths重复斜线处理，另有useMouse滚动差值/URL search保留指纹。独立指纹排除11.2，11.3为本次范围内唯一保留输入；证据在 `vueuse-api-screen.json`、`vueuse-fingerprints.json` 和官方11.2→11.3源码diff，不能据此宣称整个发布闭包已证。

| 输出 | 输入→变换 | 实证与剩余缺口 |
| --- | --- | --- |
| client.js | 固定webui packages/client/client、packages/components；I18n9.14.4 browser prod及@intlify；Vite/Rollup编译/别名/压缩 | 固定I18n及三包后输出hash变化，仍不同；workspace发布版本差异、其余历史传递输入/编译变换不明 |
| element.js | Element Plus2.7.7及传递组件；client构建manualChunks.element | 源码/map覆盖见A；固定本轮Vue/I18n不改变该实验chunk；原npm仍不同，原Lodash/其他传递闭包及生成器对应未证 |
| index.js | packages/client/app TS/Vue/YAML、schemas、共享依赖；Vue插件、UnoCSS、Vite modulepreload | I18n固定后输出改变但仍不同；原Vue编译器与传递工具/输入不全 |
| style.css | app/client/components SCSS、Element Plus主题、UnoCSS；先client CSS再append app CSS | 固定函数明确拼接顺序；实验输出不变，仍不同；原Sass/UnoCSS/样式传递输入未证 |
| vue-router.js | 官方vue-router4.5.1 dist/vue-router.esm-browser.js→固定client.ts的Vite/Rollup严格导出重打包，vue外部化 | npm、官方tag固定源码及MIT已附；用该精确入口实际构建仍不同，历史变换未证；不再猜别版Router |
| vueuse.js | 官方@vueuse/core/shared11.3.0 index.mjs→固定client.ts严格导出重打包，vue-demi/Vue外部化 | 原源码指纹限定11.3；实际输出仍不同，历史变换/工具链对应未证；停止候选研究，不扩展版本范围 |

六项原/基线/固定输入输出均保留，逐个size/hash与差异文件在 `finite-comparison.json`、`finite-output/`、`original-vs-fixed-*.diff`。本次一组有限覆盖构建没有替换产品组件或改保护hash。**六项均未达原发布字节一致，停止本分支版本研究。**

Vue3.5.14不再重做复制识别研究。已补官方tag固定提交 `d0253a0b7e8b356dc4c490dcb79fab49c1df6412` 的完整源码、MIT、pnpm锁和构建配置：packages/vue/src/runtime.ts→scripts/build.js与rollup.config.js的esm-browser-runtime production配置→npm runtime浏览器文件→webui client.ts copyFile。源码到官方npm的上游构建本轮未重放，复制结论沿用11已核证据。Router4.5.1 tag提交为 `14b87f603392e230cd333d3f5627f5e95451d4db`，I18n9.14.4为 `cffa3403a5b1d0aeaefb9bf769461fce5f781160`；tag对象链、固定源码hash和许可原文均附。

## 本次构建和交付门槛

隔离WSL目录中固定Node24.19.0/Yarn4.10.3及旧材料工具chain，Yarn禁网、immutable锁/缓存安装；先重放旧基线，再只在隔离node_modules覆盖八个已登记官方npm包，锁文件未冒充历史解析。全部精确输入及命令见 `build-finite.py`、`overlay-inputs.json`，环境、日志/exit、实际工具调用见 `build-logs/`。初始两次yarn build仍失败，直接client函数基线和有限构建成功；工具追踪的第三次yarn build仍失败并保存，不能报管线全绿。Yarn禁网不是OS禁网安装验收。

新增资料形成相对v2的增量包，manifest逐文件列size/hash、parent archive hash和false完整性标记；既有v2及失败日志不覆盖。材料仅供11复核，不是10可安装产品。源码取得渠道仍按[来源材料设计](CONSOLE_SOURCE_MATERIALS.md#源码下载入口设计未发布)交用户决定，未发布或建立服务。

08固定候选 `a78e8523691a3efc836a2fafc23683e8169907e0` 只读核对，未合入；较20b8cb2增加握手及07修复，不含本分支f1c12a9材料增量。07逐库physical4/107与logical4、代码回退不恢复数据的边界不变。05认可的checkerCommit仍为 `3dda6d29955328777f7e526c21bfd2828dbbea8a`，本轮未改checker blob/范围政策；不能把08组合SHA当checker来源。新版材料并不解决08混合代码裁剪、选择批准或10安装恢复/自动激活门槛。

交00停止项：lodash-unified原许可全文；Cordis三包实际TS与gitHead差异的发布对应、devtools等生成对应；原六输出完整闭包/变换；其余研究缓存随行材料；最终源码托管和维护责任。B自建需要新组件身份/hash、完整材料和RPC/认证/重连/个人行为回归；C固定替代版本需API/peer/Node兼容及同样材料；D排除Console需替代管理入口及嵌套/rollback清理证明。本轮未选择B/C/D，complete_corresponding_source=false、product_release=false，未生成最终定制selection。
