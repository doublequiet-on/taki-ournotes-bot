# Console candidate.3源码材料收敛

本页绑定任务09固定组件与本地重建材料；不提供公开托管地址，也不改变冻结整包。当前运行组件仍是`taki-console@0.1.0-candidate.3`，tgz SHA-256为`ea92ea91d816648023e61cf6ce7dfe2de98aece38e1ca8dd9292166f5537d1fb`；固定组合仍为`4ce3e43013199145e19e3a13caa089dd7b826fd431c22182192ece182bf9ca32`。

## 构建cache-content-type边界

原candidate.3输入`c6261043446d4d717af86a3466733f3f284021dbfd996082464ec202b2a68e0b`的Yarn锁由Koa引入`cache-content-type@1.0.1`。加载即拒绝探针覆盖完整immutable安装、types、lib、两项源码patch和client构建：12个Node进程没有解析该包，构建tgz与冻结candidate.3逐字节相同。这证明它是本次构建的解析/安装边，不是执行输入；不推广到其他Koishi工作流。

`prepare_console_build_inputs.py public`从固定输入和固定组合派生新材料，不修改原ZIP：

- 将提交中的`koishi/compat/cache-content-type`五文件作为本地适配源码；
- 从已审组合提取准确`cache-content-type@2.1.0`与`ylru@2.0.0`安装文件，保留首选TS、生成JS、原LICENSE及固定来源摘要；
- 离线重锁到三个本地portal，并将`mime-types`固定到缓存已有的`2.1.35`；
- 要求新锁、缓存和安装源均无旧1.0.1，且本地适配实际被选中；
- 保留前两次失败：第一次缺精确mime-types resolution，第二次因Yarn已自动清理旧缓存而触发过严后置断言。

新输入`runtime/task09/material-closure-20261104/console-candidate3-public-source-v2.zip`为128378153 bytes，SHA-256 `5b8f58153d17b2df4ecb26a218f57b7b0662fced3c89b63d9360f55c03d3c67f`，3828成员。用冻结builder字节和临时输入规格完整复建后，168包成员中仅`TAKI-CONSOLE.json`的输入摘要变化；`lib/dist/src`共26项与冻结candidate.3逐字节相同。复建tgz为634667 bytes / `30e585e0f7749147c2609a424528a636c266949601b0000c43d8d654f2da6677`，只作重建证据，不替换当前组件、vendor或准入身份。

## Rollup Linux原生传递材料

### 24项随行材料装配与单项阻塞

历史`original-text-followup25-v1.zip`只新增声明/文件头，原文新增数0，保留原SHA256 `d60bede26813b5403c5c5844979151080cb33b3b1cce10da16c342e47f53e782`。按11第十三轮，本次不再将25项一概列为“必须找回历史根LICENSE，否则换Rollup”。23项准确Apache声明可映射权威全文，swc_sourcemap10.0.2有明确派生BSD原文；这24项已完成[随行装配](CONSOLE_ROLLUP_LICENSES.md)，等待11核实际增量。

新`rollup-license-supplement24-v1.zip`：94916 bytes，SHA256 `0922573405ad4e38eb9bc1cb10e2d927cabc4a71f581c2a1ea4d81835bb30f04`；内置索引SHA256 `34ced28faba576b74d35b553a187dfcd5245e8a0d9d662f30bcb1cb021c5cae9`。它保存三份全文、24项源hash/准确Cargo及registry适用映射和11个原始Rust文件头，明确采用Apache任选分支；准确源文件未改。sourcemap的9.3.4 Cargo只作许可派生证据，绝不替代10.0.2源码。

本组只剩base-encode0.3.1的有出处版权/完整MIT随行材料阻塞；不以authors或模板代填，不替换工具。是否授权它的两处数字进制编码独立实现仍交00/用户。本轮没有重试404、替换Rollup或重编native；已授权隔离验证可继续，公开获取、实际产品随行及最终分发仍有各自门禁。

本轮继续补齐固定目标的剩余源码差集。Cargo1.99.1/rustc1.99.1仅用于元数据解析，工具来自官方固定2026-10-15归档并验SHA256；不替代源码的nightly-2025-07-25构建工具链，也未编译native。隔离目录执行`cargo metadata --locked --filter-platform x86_64-unknown-linux-gnu --format-version 1`，再以`cargo tree --locked -p bindings_napi --target x86_64-unknown-linux-gnu -e normal,build --prefix none --format '{p}|{f}'`交叉核对。两者包集合一致；11复核确认4包feature并集减少（mimalloc-safe、libmimalloc-sys2、syn3、serde_core），phf_shared并集仍为std而保留空/std两种单元，memchr也有两组单元。原“5组额外features”说法过粗；材料以已交入口树的单元集合为准，保留原始metadata供复核，未改组件字节。

完整图为188节点：3个Rollup本地源码、37个已有材料、148个新增准确crate。新`rollup-linux-target-source-materials-v4.zip`为12054447 bytes / SHA256 `0fe03bf985205730b7217f2598e96c1546492b672a400666acf69b348ce345a7`，含所有新增`.crate`首选Rust源码、Cargo原始/规范化配置、build脚本、图/锁/feature证据和199条许可原文索引引用。全部148份crate按原Cargo.lock校验；没有重新取得已核37份作为新增材料。工具可按`--metadata/--tree/--lock/--cache/--reuse/--output`重放，已有结果禁止覆盖。

31个未随crate带独立原文的对象中，NAPI derive/backend、base64-simd/vsimd、bytes-str/par-core六项根原文适用关系已由11的40a轮关闭；历史ZIP仍保存其余25项的HTTP结果，不修改旧记录。本次24项补充映射覆盖其中23项Apache及1项BSD，只留base-encode0.3.1单项阻塞。ICU两项生成关系另由固定96cecd9增量交11复核，不把源码存在等同逐字节重生。

固定目标为`x86_64-unknown-linux-gnu`、入口`rust/bindings_napi`、Rollup源码提交`18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6`。现有15个直接/关联解析不重做；新增材料只覆盖11点名的三条传递链：

- `mimalloc-safe 0.1.69` → `libmimalloc-sys2 0.1.65`，包括mimalloc C源码及`cc/cmake/find-msvc-tools/shlex/libc`构建链；
- `napi 3.15.0` → `napi-sys 3.4.1` → `libloading/cfg-if`；
- `swc_common 26.0.0` / `parking_lot 0.12.5` → `parking_lot_core/lock_api/scopeguard/smallvec/libc/cfg-if`。

`redox_syscall`和`windows-link`按目标条件登记为非Linux选择，不混入选中源码集合。已核的16个crate/27份原文保持关闭，不重复核验；它们只闭合上述三条链，不代表整个native闭包。针对11指出的真实差集，新增`rollup-4.67.4-linux-x64-glibc-napi-delta.zip`：1175096 bytes，SHA-256 `679f946e568ffea8bc0e2bd21cdd3f3bebbb451a757ab272e0194e08f21baf7a`。其中21个固定crate、40份原许可材料逐Cargo.lock checksum核验，补bitflags、`ctor(default-features=false)`、futures默认闭包、nohash-hasher、rustc-hash以及host build依赖napi-build 2.6.0；wasi-heap-sync不作用于本目标。

`deploy/licenses/koishi-console/rollup-linux-x64-glibc-coverage.json`逐入口记录目标、feature、复用、排除和当前缺口。148项源码/锁绑定已闭，本次24项随行映射已装配待验，base-encode单项未齐。两项ICU数据本身和Unicode原文已交，[固定生成关系与首选输入](CONSOLE_ICU_DATA.md)由96cecd9另交；没有由材料取得推导完整对应源码或公开分发放行。

## 原始摘要与换行

- 组合ZIP内`combination.json`：418369 bytes，SHA-256 `2b55d44febfcf7054de8a667b044f1aee90f253ac612ef46bbd069afb1399564`，3353个LF；外置文件：421722 bytes，SHA-256 `b97ee009ebaf8ff850f3e3984f46bb5cf639f7750c774f8cfbedb9e05a20b6ba`，3353个CRLF。两者解析JSON相同，原始摘要不得互代。
- sdist内`gateway-notices.json`：107423 bytes，SHA-256 `1f57f3a9abb84008d82960cbfa0d2d123a85d9b87295e46d2a077018a588a857`，811个CRLF；固定Git blob：106612 bytes，SHA-256 `8a4d1a0cee475fea8e4cea26ebf3458d69513f803dd6499a4b8f6a5ac06274fa`，811个LF。规范化换行后相同。

## 最终产品vendor清单

冻结4ce3整包不重造。后续个人/定制产品构建从`koishi/vendor`只复制：

1. `README.md`；
2. `console-candidate.json`；
3. `taki-console-0.1.0-candidate.3.tgz`。

`candidate.1.tgz`和`candidate.2.tgz`不得进入最终产品、rollback或安装选择；二者仍保留在仓库内部历史与现有冻结审计包中，不删除、不重标为安全候选。实施产品复制白名单时须生成新路径整包，并只重验vendor成员、manifest、准入负例和安装树；不能覆盖4ce3。

## 仍未放行

版本化本地索引`deploy/licenses/koishi-console/candidate3-source-index.json`按机器ID绑定当前builder、两份规格、两份patch、新v2输入、旧3762父材料及两份Rollup材料。`downstream-inputs-public-v2.json`以版本化字节保存，SHA-256为`6867d09aca933575eec2680872af21a72db51f4913156afd93727bdf2665a788`；原运行规格`downstream-inputs.json`保持不变。可移植复建方式是在全新Linux目录复制固定提交，将public-v2规格临时覆盖到该副本的`deploy/licenses/koishi-console/downstream-inputs.json`，然后执行：

```sh
python3 scripts/build_downstream_console.py \
  --inputs /absolute/path/console-candidate3-public-source-v2.zip \
  --work /absolute/new/work \
  --output /absolute/new/output
```

校验器要求实际输入ZIP摘要、重建包`TAKI-CONSOLE.json.inputs_sha256`及索引`public_source_v2`角色三方一致，内部manifest自洽但错配的输入会被拒绝。规格文件还须在自身hash/size核验后解析：`inputs_file/inputs_size/inputs_sha256`必须描述实际输入（size严格为整数），builder写入绑定的`component/version/package_version/upstream_commit`须与重建组件一致，Node/Yarn标签须对应已验ZIP的固定工具成员。旧c626规格即使文件hash自洽也拒绝；builder未使用的element_plus/devtools/source_complete等说明字段不被冒称为生成过程验证。旧补充包中的builder不得覆盖索引指向的当前提交。`hosting_url`保持空、`published=false`。公开位置、访问范围、保留责任、个人/定制selection、物理裁剪、真实安装恢复和公开分发仍需后续决定，因此`complete_corresponding_source=false`、`product_release=false`保持。
