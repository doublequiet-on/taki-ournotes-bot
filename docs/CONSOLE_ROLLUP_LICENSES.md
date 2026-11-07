# Rollup：24项随行许可增量

本增量按11第十三轮的具体装配路径完成。准确源码复用`rollup-linux-target-source-materials-v4.zip`（SHA256 `0fe03bf985205730b7217f2598e96c1546492b672a400666acf69b348ce345a7`）；不替换crate、不改变锁、Rollup工具或Console字节。新材料包中的`index.json`逐包记录准确版本、源包SHA256、原始/规范化Cargo声明、官方registry记录及随行全文；不需要重新寻找历史404。

## 23项Apache

适用依据是各准确发布包自身的Apache-2.0声明，共用[ASF权威全文](https://www.apache.org/licenses/LICENSE-2.0.txt)，SHA256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`。未替换标准附录中的示例占位符，也没有据模板制造版权。

准确集合：ast_node7.0.0、better_scoped_tls1.0.1、from_variant3.0.0、string_enum1.0.2、swc_allocator5.0.0、swc_atoms10.0.0、swc_common26.0.0、swc_compiler_base66.0.0、swc_config6.0.1、swc_config_macro1.0.1、swc_ecma_ast29.0.2、swc_ecma_codegen32.0.5、swc_ecma_codegen_macros2.0.2、swc_ecma_hooks1.0.0、swc_ecma_minifier63.0.4、swc_ecma_parser46.1.2、swc_ecma_transforms_base50.0.3、swc_ecma_transforms_optimization53.0.0、swc_ecma_utils35.1.0、swc_ecma_visit29.0.0、swc_eq_ignore_macros1.0.1、swc_macros_common1.0.1、swc_visit2.0.2。

另保留[SWC官方153b286/LICENSE](https://raw.githubusercontent.com/swc-project/swc/153b28663bb5bc2cb8346db363cc87f10b5c66e7/LICENSE)作家族归属补充，原始SHA256 `d96eba1f1881334d50ffc144276dfa98e147a415e7c591ecdfcb81680361dc91`。它是已知固定快照，不冒充23项准确发布提交的根文件，也不取代本版本声明作为适用依据。材料ZIP保留原始字节；仓库随行副本仅追加末尾LF，SHA256 `21da174fa23042e1b27dce5a4f691c0340f8df53dbc3e435f2646a9d0ea8c3ec`，两摘要不互换。

`swc_common26.0.0`的11个Rust文件头保留原版权年份、Rust Project Developers、COPYRIGHT引用和Apache-2.0 **or** MIT文字。本增量明确采用Apache分支，并在`attributions/rust-file-headers.json`逐文件绑定完整源hash、原头及共享全文；准确父源码包保持未改。没有把Rust版权改为SWC版权，亦不为同一任选条款机械追两份全文。其余两项copyright字串命中不是新增许可声明。

## swc_sourcemap10.0.2

准确10.0.2原始Cargo明确BSD-3-Clause和派生自getsentry/rust-sourcemap。保留准确源hash及声明，随附[SWC sourcemap64fcb47/LICENSE](https://raw.githubusercontent.com/swc-project/swc-sourcemap/64fcb47c16547d40fb37bbe4d8790b520716c741/LICENSE)；其字节与[Sentry来源713106c/LICENSE](https://raw.githubusercontent.com/getsentry/rust-sourcemap/713106c63ee21a178e5ea9f508e5897453c0e290/LICENSE)相同，SHA256 `7516e1cf340213f60d96bca77bb012882dbf80e7cca5922c914174f605d9ef71`。全文含Armin Ronacher 2016版权、三条条件及免责。

映射以10.0.2自身声明和明确派生链为依据。源仓库该快照Cargo版本为9.3.4，只作为许可来源证据；不将9.3.4代码装入或标成10.0.2源码。两端原文出处与该Cargo证据均在材料中。

## 随行与剩余范围

三份全文位于`deploy/licenses/koishi-console/rollup-license-supplement/*/LICENSE`并由既有源码包规则收录；实际24项逐包映射、11个原头、声明与源hash在新最小ZIP中。原23项/10.0.2准确源包没有独立NOTICE/COPYRIGHT文件，是这些发行物的已核事实，不推广成整个上游从无NOTICE。没有生成一份声称来自上游的虚构NOTICE；未来若发现具体适用文本则增量保留。

24项现已完成本次随行文本和归属映射的作者装配，待11验新包；不再统一列为“25项许可未知”或“缺SWC源码”。本组仅`base-encode0.3.1`继续缺有出处的版权/完整随行MIT材料，不以authors代填、不制造MIT版权模板，也不自行独立实现或替换Rollup。

冻结ea92组件、002/4ce3整包未装入本增量、未重造；新索引只绑定材料，产品实际随行仍须将原准确源材料与本增量配对验收。ICU生成说明、公开获取入口、最终selection、物理裁剪及安装恢复等按各自门禁继续。`complete_corresponding_source=false`、`product_release=false`、`published=false`保持；本次不是法律批准、公开分发或最终产品验收。

### base-encode 的最小替代范围（待 00 决定）

固定 Rollup `18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6` 的 Rust 工作区由 `rust/xxhash/Cargo.toml` 引入 `base-encode@0.3.1`，`rust/xxhash/src/lib.rs` 只用其 `to_string` 实现 `xxhash_base64_url` 和 `xxhash_base36`。最小方案是在该文件独立实现固定 16 字节的基数转换，移除 crate 声明并重锁 `rust/Cargo.lock`。保留 xxh3-128、两套现有字母表和 base16 路径。兼容规则是把 hash 的小端字节序列作为大端整数做除余转换，同时逐字节保留开头的零为字母表首字符。普通 Base64 编码不能复用这一契约。

先用隔离旧实现采样固定测试向量，覆盖全零、前导零、边界进位、两种基数、base16 及真实 chunk 命名。若获批，先重建并标识新的 Linux x64 glibc Rollup native 工具，复查 `bindings_napi` 导出、共享 `bindings_wasm` 调用契约和 chunk/hash 名称，再更新 Console 配方中的 native 包/完整性锁和实际源许可索引。重建比较 Console 运行输出。如果运行字节变化，分配新组件及组合包身份并交 08/12 回归。当前交付仅登记方案，既有 native 工具与来源缺口继续按其原身份记录。
