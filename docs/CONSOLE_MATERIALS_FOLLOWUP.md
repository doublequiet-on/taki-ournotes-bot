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
