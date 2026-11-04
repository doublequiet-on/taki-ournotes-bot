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

固定目标为`x86_64-unknown-linux-gnu`、入口`rust/bindings_napi`、Rollup源码提交`18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6`。现有15个直接/关联解析不重做；新增材料只覆盖11点名的三条传递链：

- `mimalloc-safe 0.1.69` → `libmimalloc-sys2 0.1.65`，包括mimalloc C源码及`cc/cmake/find-msvc-tools/shlex/libc`构建链；
- `napi 3.15.0` → `napi-sys 3.4.1` → `libloading/cfg-if`；
- `swc_common 26.0.0` / `parking_lot 0.12.5` → `parking_lot_core/lock_api/scopeguard/smallvec/libc/cfg-if`。

`redox_syscall`和`windows-link`按目标条件登记为非Linux选择，不混入选中源码集合。16个选中crate的官方`.crate`字节逐Cargo.lock checksum核验，27份原LICENSE/COPYING/NOTICE及Cargo.toml许可表达式进入`rollup-4.67.4-linux-x64-glibc-transitive-materials.zip`：3367015 bytes，SHA-256 `0a70a0e6412abf0fb75d60fb31ee7812278e27338bb365ba43c4a0e15f3a4197`。该材料没有重建Rollup原生二进制，也不恢复esbuild历史工具链搜索。

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

版本化本地索引`deploy/licenses/koishi-console/candidate3-source-index.json`绑定当前builder/spec/两份patch、新v2输入、旧3762父材料及本轮Rollup增量；旧补充包中的builder不得覆盖索引指向的当前提交。`hosting_url`保持空、`published=false`。公开位置、访问范围、保留责任、个人/定制selection、物理裁剪、真实安装恢复和公开分发仍需后续决定，因此`complete_corresponding_source=false`、`product_release=false`保持。
