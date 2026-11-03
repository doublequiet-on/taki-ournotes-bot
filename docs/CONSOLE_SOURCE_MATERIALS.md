# Console 5.30.11：方案A来源与重建材料

本轮保持原npm组件和Taki兼容行为，只在隔离环境重建、比较和准备材料。材料包是研究/复核输入，不是个人或定制发行，不证明完整对应源码或安装验收通过。11第二轮意见是本轮验收依据。

## 固定输入与公开发布证据

- npm `@koishijs/plugin-console@5.30.11`：SHA-256 `bc524a7809114c7d1a2e525ff7bfc98ae55182a5c8989bd4a219b4c2f782e67e`；原registry integrity与27个成员保留。其[固定Git树](https://github.com/koishijs/webui/tree/896882b50cc91d16f522a52ded9644045d461c26)参考archive SHA-256 `85528dd1b9407b0f7158ca7c049156088c53de850dad8e3ef695f1d96198e1b9`。
- Git提交时间2025-11-17 14:18:38 UTC；[npm公开元数据](https://registry.npmjs.org/@koishijs/plugin-console)记录5.30.11发布于14:22:51.317 UTC。四处JSON差异仍是package.version及client的peer/dev、console的dependency版本。时间相邻不证明发布步骤。
- [公开Build run 19432757690](https://github.com/koishijs/webui/actions/runs/19432757690)的5个job均failure；固定树只有Build/stale工作流，Build执行未固定版本的setup-node、`yarn --no-immutable`和`yarn build`，不是发布工作流。[后续713ecad提交](https://github.com/koishijs/webui/commit/713ecad28e651ebfba98a4881d47afd101dfcaa1)在2026-04-11才记录5.30.10→5.30.11，且同时改其他依赖，不能替代原发布源码。
- 结论：未取得历史发布锁、发布日志或完整输入证明；版本差异产生机制仍unknown。未手改版本以伪装原发布，未联系第三方。

## 隔离重建及结果

环境为WSL Ubuntu24.04.5 x86_64、Linux6.18.54.1；任务独占目录，进程级HOME/PATH/Yarn缓存。Node官方v24.19.0 Linux发行archive核官方SHASUMS，实际node SHA-256 `bc17c508ffeed0ec622934f9b7fa72f8e78da65350e63c3eceb56fa688aa5e12`。Yarn4.10.3脚本SHA-256 `cd71753d68307301d21034efaf20bd8eee81f9450af75f068477bf9011bb9ad4`。无全局安装或服务。

首次持久目录执行 `yarn install --mode=skip-build` 生成本次新锁，取得749个缓存包，保留peer警告。**新锁不是历史发布锁**。本次Vite5.4.21、Rollup4.67.4、TypeScript5.9.3、tsx4.23.15、Yarn解析Yakumo/tsc/esbuild插件1.0.0；全部精确解析见材料内yarn.lock及dependencies.json。

原始 `yarn build` 首次缺自身client/lib构建插件，生成lib后再次执行，前端调度仍错误解析到packages/client/index.html。两次失败和exit保留。随后直接调用固定源码的 `packages/client/scripts/client.ts::default`，以 `node --require tsx/cjs` 执行，成功产生全部9个dist。没有把失败管线改记为成功。

| 发布输出 | 本次重建比较 | 输入/差异证据 |
| --- | --- | --- |
| lib/browser/index.mjs及map、lib/index.d.ts、lib/node/index.js及map、index.mjs及map | 7/7逐字节一致 | plugins/console/src、locale、workspace console API；原始tsc/esbuild输出 |
| dist/index.html、logo.png | 2/2逐字节一致 | packages/client/app/index.html、app/assets/logo.png |
| dist/vue.js | 不同 | 原资产与官方vue3.5.14 npm的vue.runtime.esm-browser.prod.js完全一致，SHA256 `2533b2f62272f737212f545a9ea23c3e3c365a915c1a5a5275d56bf1768fef18`；本次解析3.5.44 |
| dist/vue-router.js、vueuse.js | 不同 | router浏览器入口、VueUse index.mjs重打包；原router横幅4.5.1，本次4.6.4；VueUse历史解析未知 |
| dist/client.js、element.js、index.js、style.css | 不同 | client/client及app、components workspace、Element Plus2.7.7、I18n、Sass、UnoCSS与传递依赖；原I18n横幅9.14.4，本次9.14.5。尚未逐一归因全部字节差异 |

`provenance.json`覆盖全部16个lib/dist，包含原/重建size/hash、真实入口、构建命令和证据引用；`module-graphs.json`含4次Rollup构建及每chunk的模块，`module-hashes.json`含1702个模块/虚拟入口及包归属。仅记录模块的插桩patch另存，插桩前后9个dist全部同字节；Vue直接复制路径和CSS合并/资源另列，不能只靠chunk模块推定资产闭包。

本次解析的749个包是完整monorepo重建的缓存上界，**不是全部必需运行依赖**。实际前端参与关系由模块图确定，lib外部依赖与产品运行树仍须最终锁审查。缓存包逐个hash、package声明与内部LICENSE/NOTICE路径/hash已索引并随材料保留。65个缓存记录未找到许可证文件，列为review_required；包括构建依赖，不能直接推定65项均进入运行工件。npm缓存也不自动等于每个第三方组件完整首选修改源码，历史捆绑依赖和缺项须继续核对。

用材料目录、同一工具和新工作目录执行 `reproduce.sh <new-absolute-dir>`：Yarn网络关闭、immutable锁与缓存安装成功，重放两次原管线失败及直接client构建，16个输出全部与本次实验字节一致。这个验证证明本次材料可重放，不证明原npm的7个不同dist已追溯，也不是产品禁网安装测试。

## Taki修改与源码链接

`deploy/licenses/koishi-console/taki-reconnect-source.patch`为现有三个字节替换的首选源码表达：connect增加可选URL参数；首次断线在清空store前捕获location.href；递归重连传递URL，成功后location.replace。源码位置是 `packages/client/client/data.ts`。材料保存原源码/patch及前后hash、原npm字节补丁前后、实验重建前后输出；没有更改生产builder的固定hash/替换锚点。

原npm client hash `55a68e89eda7e3fe59305b09e6c4de19481a8d7aa5df97579822c819ebf3ccaf`；既有字节补丁后 `1fa7b172bfc85d51e04ee2f5955f2f50cb6ba488e0972f2964e7eabc8a07557d`。实验源码补丁也已构建，但不等同原npm修改产物；没有用实验产物替换当前组件，重连/RPC/认证行为最终验收未执行。08后续产品裁剪仍须生成其真实增量补丁。

仓库patch使用零上下文格式，核对固定源hash后用 `git apply --unidiff-zero taki-reconnect-source.patch`；已实际应用到独立原源码副本，结果SHA256为 `cf21a5e49c75deeda40a064e9dba2f65933a97c9357beda743337dee4be60e49`，与实验构建输入一致。材料保留最初全上下文patch作过程证据，二者只差上下文表示，不改变源码。

原参考archive唯一内部链接 `packages/online/app/assets -> ../../client/app/assets`：只接受该固定路径/目标，验证归一化落在源树内，在材料源码树显式物化为普通文件并记录转换。原归档hash和链接语义仍保存；不静默丢弃。不放宽product_release对安装包链接的拒绝。工具链Node发行archive内部链接按独立工具解压处理，不能当产品包许可。

## 材料布局与剩余门槛

本地材料包路径、size/hash、manifest及构建日志见原任务09 HANDOFF和artifact-index.csv。包含source（完整固定树的构建上界、显式链接转换、新锁/缓存）、tools、inputs、report、evidence（原重建、Taki实验输出、patch）、logs、replay-logs及复现脚本；原输入、实验输出与当前npm分别命名。manifest逐文件列hash；它是材料清单，非获批产品身份manifest。没有真实账号、业务库、名片、日志或私有Haneoka状态。

最新增量见[第五轮有限追溯](CONSOLE_TRACE_ROUND5.md)：Vue复制识别由11关闭，补固定源码/许可；12项chunk参与者和65项范围分类已形成材料，六个原dist有限比较仍不同并停止版本探索。上表保留第四轮实验结果，不作当前全部缺口计数。

尚缺：原前端完整首选源码对应及许可证补齐（尤其lodash-unified原许可）、剩余6个dist对应性论证、最终08修改及实际运行组件绑定、接收方/网络用户源码取得渠道。A继续受此门禁限制。若仍不能补齐，00可决定：B自建下游组件（新身份/hash，需重新评审patch并回归RPC/认证/重连/个人行为）；C换有证据的固定版本（需核API/peer/Node及完整材料）；D排除/替换Console（需替代管理入口并检查嵌套/rollback残留）。本轮没有选择或实施B/C/D。

## 源码下载入口设计（未发布）

建议发行目录使用不可变相对路径 `sources/console/<component-sha256>/source-materials.zip`，旁置manifest及SHA256；最终发行索引明确组件原/修改后hash、Taki source commit、source archive hash和内容是否完整。候选自报URL不能成为信任源，控制器按经审核外部发行索引绑定；不以latest路径随更新漂移。历史与rollback版本保留对应源码。

Console“关于/版本”区域和常驻页脚提供显著“本版本源码与修改”链接，展示组件版本与摘要短码；受信管理启动时注入与实际组件测量相符的索引。不能由旧设置/恢复草稿改到别版源码；下载不需要额外付费或开发者私有凭据，不借源码链接授予管理/范围批准权限。网络用户范围以实际Console交互者为准，不自动推及所有QQ用户。

验收需10/11/12用接收方及实际Console用户身份验证：下载可得、hash/内容齐备、当前组件相符、升级/回退切换正确、旧链接稳定、错误/缺失保持发行阻塞。托管方、域名/稳定根路径、访问范围、保留策略与维护责任由用户决定。本轮只提供设计，不发布、不建立服务、不修改08 Console接线。
