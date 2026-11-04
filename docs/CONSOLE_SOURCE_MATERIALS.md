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

### 25项原文停止条件与最小后续方案

仅对40a968a已登记的25项查询了官方crates.io准确版本记录，25/25 checksum与固定锁一致；保存发布记录、原始/规范化Cargo声明、README及源码内许可标记，未再次请求已证404的SWC Git路径。新证据`runtime/task09/spec-binding-20261104/original-text-followup25-v1.zip`：80512 bytes / SHA256 `d60bede26813b5403c5c5844979151080cb33b3b1cce10da16c342e47f53e782`，原文关闭项仍为0。

`swc_common26.0.0`中11个Rust来源文件保留Rust Project Developers版权和Apache-2.0/MIT双许可头（路径/hash/原行见followup25.json），不能仅以crate的Apache声明覆盖这些文件。其余两项copyright搜索命中分别为注释处理代码和测试样例字串，不据此创造新许可义务。24项SWC原文仍缺固定发布源对应的根许可及这些复制代码所指原文；base-encode0.3.1只有MIT声明，固定树及随包README未给全文。该证据不等于项目无许可证，也不等于缺项已放行。

可交用户的最小方案如下，当前均未执行：

- 保持冻结候选用于已授权隔离验证，暂停公开源码材料放行，等待能够证明适用于这些准确版本的原文。当前没有足够证据用另一个版本的LICENSE代替；没有联系第三方。
- base-encode若选择下游独立实现，改动仅限构建工具的`rust/xxhash/src/lib.rs`两处base36/base64-url调用及相应Cargo边。须按16字节输入、字节序、字符表和前导零语义独立实现；校验全零/前导零/边界/固定随机样本及两目录Console输出。它是数字进制编码，不可直接换成标准RFC base64。先交新工具/输入/输出hash，保持当前组件冻结，由00决定后续候选回归。
- SWC不适合小范围重写。若原文始终不能证齐，最小技术替代是另选可公开核对完整源码和原文的固定Rollup工具链，再验证准确normal/build/proc-macro闭包、Linux目标、插件API、hash命名及16输出。需要新工具输入、锁和双目录复建；如组件字节变化须新候选及08/12回归。当前没有选版本、改锁或替换运行依赖。

本轮已完成声明/精确版本适用关系补证，到此停止25项来源分支；原文及包级适用范围交11独立核查，是否选择替代路线交用户/00。

本轮继续补齐固定目标的剩余源码差集。Cargo1.99.1/rustc1.99.1仅用于元数据解析，工具来自官方固定2026-10-15归档并验SHA256；不替代源码的nightly-2025-07-25构建工具链，也未编译native。隔离目录执行`cargo metadata --locked --filter-platform x86_64-unknown-linux-gnu --format-version 1`，再以`cargo tree --locked -p bindings_napi --target x86_64-unknown-linux-gnu -e normal,build --prefix none --format '{p}|{f}'`交叉核对。两者包集合一致，workspace元数据有5组额外features，因此材料以入口树的feature集合为准，保留host/target不同集合及原始元数据供复核。

完整图为188节点：3个Rollup本地源码、37个已有材料、148个新增准确crate。新`rollup-linux-target-source-materials-v4.zip`为12054447 bytes / SHA256 `0fe03bf985205730b7217f2598e96c1546492b672a400666acf69b348ce345a7`，含所有新增`.crate`首选Rust源码、Cargo原始/规范化配置、build脚本、图/锁/feature证据和199条许可原文索引引用。全部148份crate按原Cargo.lock校验；没有重新取得已核37份作为新增材料。工具可按`--metadata/--tree/--lock/--cache/--reuse/--output`重放，已有结果禁止覆盖。

31个未随crate带独立原文的对象沿其`.cargo_vcs_info`固定树有界补证，6项取得根原文及对应Cargo声明：NAPI derive/backend、base64-simd/vsimd、bytes-str/par-core，仍须11核包级适用范围。其余25项在ZIP的`index.json`逐准确版本/仓库/提交/路径/HTTP结果登记：24项SWC的固定根/包级路径404，代表提交API422；base-encode0.3.1固定树41bdeb57488beec4ed63180e848ce1e99fba227d可读，但树和README缺原文。源码已取得，原文缺口保持阻塞，不继续猜历史版本或借其他项目许可覆盖。ICU等随crate生成的数据和上游生成关系仍需材料审计；本轮只做源码材料闭包和原文补证，不把源码存在等同上游字节重建。

固定目标为`x86_64-unknown-linux-gnu`、入口`rust/bindings_napi`、Rollup源码提交`18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6`。现有15个直接/关联解析不重做；新增材料只覆盖11点名的三条传递链：

- `mimalloc-safe 0.1.69` → `libmimalloc-sys2 0.1.65`，包括mimalloc C源码及`cc/cmake/find-msvc-tools/shlex/libc`构建链；
- `napi 3.15.0` → `napi-sys 3.4.1` → `libloading/cfg-if`；
- `swc_common 26.0.0` / `parking_lot 0.12.5` → `parking_lot_core/lock_api/scopeguard/smallvec/libc/cfg-if`。

`redox_syscall`和`windows-link`按目标条件登记为非Linux选择，不混入选中源码集合。已核的16个crate/27份原文保持关闭，不重复核验；它们只闭合上述三条链，不代表整个native闭包。针对11指出的真实差集，新增`rollup-4.67.4-linux-x64-glibc-napi-delta.zip`：1175096 bytes，SHA-256 `679f946e568ffea8bc0e2bd21cdd3f3bebbb451a757ab272e0194e08f21baf7a`。其中21个固定crate、40份原许可材料逐Cargo.lock checksum核验，补bitflags、`ctor(default-features=false)`、futures默认闭包、nohash-hasher、rustc-hash以及host build依赖napi-build 2.6.0；wasi-heap-sync不作用于本目标。

`deploy/licenses/koishi-console/rollup-linux-x64-glibc-coverage.json`逐入口记录目标、feature、复用、排除和缺口。当前mimalloc与napi选中链闭合；napi-derive仍缺napi-derive/backend/convert_case外部源码材料，parse_ast仍缺SWC/anyhow外部闭包，xxhash仍缺base-encode/xxhash-rust材料。该有界差集没有重建native二进制、全部Cargo依赖或esbuild历史工具链，因此不得据构建成功写成完整对应源码。

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
