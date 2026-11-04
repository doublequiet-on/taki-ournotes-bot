# Console构建材料：ICU两项生成数据

本说明只补固定`icu_normalizer_data@2.4.0`与`icu_properties_data@2.4.0`的生成关系。准确crate和Unicode-3.0原文已随`rollup-linux-target-source-materials-v4.zip`交付，本轮复用，不重新取得148项源码。固定ICU4X提交为`031d9e89f6a3fd26a14491e090729d46094328fd`，包目录分别为`provider/data/normalizer`、`provider/data/properties`。

## 数据与生成入口

| 数据crate | 与固定树的对应 | 实际生成输入 |
| --- | --- | --- |
| normalizer2.4.0 | 7个`data/*.rs.data`及mod/lib/build/原Cargo/LICENSE全部匹配固定Git blob | ICU79.1rc的`norm/fast/{nfd,nfkd,uts46d,nfdex,nfkdex,compositions,decompositionex}.toml`；默认Fast trie，NFD/NFKD也强制Fast |
| properties2.4.0 | 141个`data/*.rs.data`及同类入口/声明文件全部匹配固定Git blob | Unicode18.0.0 UCD/emoji属性源；CaseSensitive通过casemap读UnicodeData/SpecialCasing/CaseFolding，SegmentStarter和normalization-inert属性通过normalizer复用上述ICU norm表 |

逐文件Git blob、SHA256与7份norm TOML摘要在材料包`binding-checks.json`。这证明发布crate数据与固定生成源树配对；没有执行datagen或声称从原输入逐字节重生数据。

上游维护入口是[tools/make/bakeddata/src/main.rs](https://github.com/unicode-org/icu4x/blob/031d9e89f6a3fd26a14491e090729d46094328fd/tools/make/bakeddata/src/main.rs)，通过两个组件的`provider::MARKERS`生成各自`provider/data/<component>`。仅在新的完整ICU4X源码副本中可执行以下有界命令；它会覆盖该副本的两个输出目录，本任务未运行：

```sh
cargo run --locked --manifest-path tools/make/bakeddata/Cargo.toml --release -- normalizer properties
```

等价维护任务是`cargo make bakeddata normalizer properties`，配方在同提交`tools/make/data.toml`。Rust工具链声明为1.98.0，随附上游Cargo.lock SHA256为`cc10660049d62b582a673297ceef343381c0c82fac7a8f4972c212b52ddbd00a`；它不是Taki原生工具的nightly工具链，也未安装或替换本机工具。生成器依赖选择`icu/unstable`、`icu_provider_source/networking,unstable`、`icu_provider_export/baked_exporter,rayon`。配方选Modern/Moderate/Basic覆盖层并包含descendants，deduplication=Maximal；BakedExporter使用pretty=true、overwrite=true、internal_fallback=true、use_separate_crates=false。包模板统一写CLDR、Unicode、segmenter标签；标签不等于每个组件都读该源。

## 首选修改输入与取得方式

维护数据应修改以下原输入或固定provider转换代码，再运行上述配方。`.rs.data`是已生成的编译输入，本任务没有把它自动定义为上游首选修改形式。

- ICU norm输入：官方`release-79.1rc/icu4x-icuexportdata-79.1rc.zip`，4865183 bytes，SHA256 `ba95a2b748772a4984bcead9bc556f834448ef46e375c4163f27929d8c2d98c9`，已随增量材料保留。与GitHub官方release asset589090459的digest一致。来源由固定`provider/source/src/lib.rs::TESTED_ICUEXPORT_TAG`和`with_icuexport_for_tag`确定，读取与转换在`provider/source/src/normalizer`。
- Unicode属性原输入：[UCD18.0.0 ZIP](https://www.unicode.org/Public/18.0.0/ucd/UCD.zip)，5657953 bytes，SHA256 `7b3e555514060b92290d154f53655c5eb0fa62b16eb04c03434ff72d1a66a0d8`；[emoji-sequences18](https://www.unicode.org/Public/18.0.0/emoji/emoji-sequences.txt)，197000 bytes，SHA256 `1823dce71f3dd9cb0ad1976797baee4ffbdc1909756f43af8ce4c57f732843a4`。均已保存；摘要为本次官方固定版本下载实算值，不冒称官方签名。属性provider保留实际文件名/派生路径规则，包括PropertyAliases/PropertyValueAliases、DerivedCoreProperties、DerivedNormalizationProps、PropList、emoji-data、Bidi、ScriptExtensions及各enumerated property文件；UCD ZIP覆盖这些源，emoji-sequences位于ucd之外。
- CLDR49.0.0是共用生成驱动的locale规划/LocaleFallbacker输入，不能因为这两组payload为singleton就从原配方删除。配方读取`cldr-core/coverageLevels.json`等locale元数据；固定来源为[cldr-49.0.0-json-full.zip](https://github.com/unicode-org/cldr-json/releases/download/49.0.0/cldr-49.0.0-json-full.zip)，76248823 bytes，官方asset664174369摘要`3b1f5d684d29c541d3acf91fe834b3fe36ae0f34775e3ee50e93558bf14ca174`。本轮只保存官方release记录、digest和获取地址，没有下载该76MB输入；完整离线datagen须再按摘要取得它。
- 两组件不读取segmenter LSTM或TZDB payload；`SourceDataProvider::new()`仅注册懒加载源。传入normalizer/properties也不会进入“所有组件”分支的全标记验证。因此不要求下载LSTM模型或TZDB来补这两项关系。共享Rust源码中的编译资源仍须来自完整固定ICU4X树；随附49个有关文件是有界说明材料，不冒充可独立编译的整个ICU4X仓库。

ICU4X相关生成代码和两个data包的同提交`LICENSE`为Unicode-3.0，原文覆盖data/software；Unicode输入的ReadMe/头部许可引用及固定根原文保留在材料中。此处继承11已核的两包原文结论，不重新称“缺许可证”。ICU export ZIP本身没有LICENSE成员，已补官方release-79.1rc对应提交`c4eb46bbb0da3b06ed1f6975babddd593ce4514e`的完整根LICENSE（含随行条款）及`icu4c/source/tools/icuexportdata/icuexportdata.cpp`，证明norm TOML的上游导出工具定位；原文SHA256为`e09827667e0bb3c808b2a463548ba814a5e6249b67353234d70c8dfc2bcc417d`。再分发材料适用范围仍交11核，不借Taki许可证覆盖。

## 默认编译与剩余范围

两包`src/lib.rs`默认include随包`data/mod.rs`；`build.rs`只有发现`ICU4X_DATA_DIR`才设置`icu4x_custom_data`并改读外部目录。Taki仓库的builder/workflow没有设置该覆盖，当前Console构建使用固定预编译Rollup native工具，也没有重编这两包。本轮不推定原native发布方的环境已知。

已完成固定crate→生成器/选项→输入版本与路径的静态配对、源树Git blob与原输入摘要核验。未执行完整ICU datagen/工具编译、输出逐字节复生或Console重建；原始ICU4C导出TOML的完整构建链也未展开。CLDR完整离线输入尚未携带，固定完整ICU4X源树及其datagen工具依赖可沿提交/锁取得。本次关系材料供11有界复核；`complete_corresponding_source=false`、`product_release=false`保持，ea92/002/4ce3冻结不变。
