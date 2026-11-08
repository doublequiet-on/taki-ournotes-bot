# 下游Console固定候选装配

当前个人隔离候选在 602a95f 上接入固定 08 `4fd680b9096ac7283facf8c9735aaa88b9147a6f`。组合额外测量编译的 `createRecoveryScope` 和 controller 内的身份、删除权威预检文件，Python sdist 随行两份源码。恢复仍需独立可信宿主和已指定管理员；普通 Console 登录无批准权。个人正常启动及原功能沿用，当前工件用于个人隔离审查。

## 当前增量：恢复业务入口

以固定 B2 组合源码 `cf1f09176e8e2d5dda310586a454a7d4ecac8d42` 为底，接收08 `a384335f69e0514190c51c5dfce5808b55a19f60` 相对 a7ca 的九文件增量。组合显式携带 `gateway/recovery.cjs`，与 `gateway/run.cjs` 的入口及配对 wheel 内的 `recovery_entry.py` 一起写入 manifest 摘要。验包同时核对配对 Git 源归档中的入口、实际 wheel 的 `CODE_FILES` 聚合摘要及单项摘要。

[恢复入口合同](task08-integration/RECOVERY_SESSION_WIRING.md)引用10固定协议 `a6e102a5b94be6af65652985fe0063491352cbf5`。本组合仅带08业务入口，未装入10的 `isolated_runtime.py` 和 Python/Node `recovery_session` 宿主。独立05/07/09/10权威、宿主停写与真实恢复继续按原门禁，装配测量不授予批准。

## B2 固定准入

本轮以09固定 `5ad4e3c234705c642f920aaa760e5707a72f1149` 接收08固定 `a7ca40d1c50d2a4d1a216f21cb3c8e87c2bd511f`。保留08恢复业务门、Backend生命周期、d1夹具及既有 cache 适配。包、锁、vendor 绑定采用08固定文件，README以08版本为基础补充准入信息，导航保留两边记录。10后续接线另计。

装配器仅接收原始 B2，npm 版本 `5.30.10-taki.4`，SHA-256 `1668e57b5b387814aab60f79f2d157f7017299088b5413e49690ef8aa5b5f0c9`。组件来源固定 `a526abab715989145ed1368ce1610413213a1a8c`。准入核 vendor 绑定、完整 lock、tgz 与168安装成员、重连和 Markdown 源码标记、构建输入与 native 绑定，以及135项许可。旧官方5.30.11仍使用原字节校验和补丁。B2 分支直接复制原始字节。

组合、sdist 及内嵌 Git 源归档的 vendor 仅收固定 B2、绑定和 README。旧 tgz 保留在仓库和各自冻结工件。组合随带固定来源索引及构建输入规格。新 lock 的 registry 条目及本地 compat 和冻结组合一致，沿用原网关声明与原文，只更新其 lock 绑定。Node LICENSE、控制器/回退材料及配对检查继续适用。

08 的[隔离网关记录](task08-integration/CONSOLE_CANDIDATE4.md)绑定同一 B2。12 的独立审查允许该固定工件接续验收，并确认 A2/B2 还差一次 ElConfigProvider 的 withInstall 调用。根因未定，整包字节复现尚未通过。11 对新候选的 base-encode 材料缺口已收口，公开分发及最终产品仍待验。

## 历史装配：冻结 candidate.3

11第九轮和12整包复核已补充确认固定`0026159…`准入、安装树、负例与无网验包；这关闭原“08接入待独立复核”口径，不等于完整对应源码、首装恢复、最终双发行或物理裁剪通过。冻结组合及原摘要不变；后续源码材料增量见[Console candidate.3源码材料收敛](CONSOLE_SOURCE_MATERIALS.md)。

当前装配分支先保留09安全修复 `ca7a64175aa4c359c018b8a7e1dc6c506fbd6552`，再按08已提交祖先链接收 `e5e532a…`（cache适配）、`399cc8a…`（candidate.2基线）和固定接入 `982f042889d9de84e13aa2aedaca51faeea5bd5b`。唯一冲突是 `docs/L2.md` 导航，双方入口均保留；vendor、package/lock、compat和业务字节均按08提交接收。没有合并GitHub PR/main或读取08未提交文件。

## 两条组件路径

`build_koishi_release.py`保留官方5.30.11的旧资产hash/唯一替换校验。仅固定下游5.30.10-taki.3进入新路径：核vendor绑定清单、tgz SHA256/SHA512、完整规范化lock、package依赖与lock一致、全部168安装文件、TAKI-CONSOLE源码重连和Markdown安全补丁标识与135项许可索引；未知版本、改包、混搭和重复旧补丁均拒绝。新路径不写旧补丁说明，不修改组件字节。

`koishi/vendor`整目录与lock随组合；sdist只收录当前candidate.3 tgz、绑定JSON、两份npm配置和固定 `compat/cache-content-type` 五文件。JSON例外均为准确路径，未放宽其他JSON、私密文件或运行数据。组合同时携带并核对compat源码目录和解引用安装树；现有Node完整LICENSE及控制器/rollback层仍随包。

当前组件是SHA256 `ea92ea91d816648023e61cf6ce7dfe2de98aece38e1ca8dd9292166f5537d1fb`，来源09 `ca7a64175aa4c359c018b8a7e1dc6c506fbd6552`，08固定接入 `982f042889d9de84e13aa2aedaca51faeea5bd5b`。不通过npm pack重新封装组件；复制后的168文件逐项核验，LICENSE、licenses/NOTICE、TAKI-*均须保留。12已独立关闭固定组件的Markdown属性注入，但明确未复核08网关接入或本整包。

## 实际外部依赖和材料

外部树来自08实际lock：console5.30.11、koishi4.18.11、open8.4.2、uuid8.3.2、ws8.22.0，以及本地CJS适配→cache-content-type2.1.0→ylru2.0.0；完整规范化lock SHA256 `587178bda427fcaf3aa115798fbedf0830ab7ee83e06cf6ae4e7edf26867aeed`。不沿用早期workspace console5.30.10/ws8.22.1或cache-content-type1.0.1材料。

构建前`console_release.installed_npm_materials`从npm完整性缓存读取锁定registry tar，重算SHA512并与实际安装文件比较。180个registry包（包含实际随包开发工具）逐包版本、resolved、integrity、tar摘要、repository、文件与原文路径/hash写入组合`licenses/npm-installation.json`；本地taki-console-assets跟随已提交console目录。npm缓存缺失或安装字节变化即拒绝。固定agent-base tar含`package/./dist/index.js`与`package/dist/index.js`相同字节：仅完整性核验后的registry材料读取允许并记录同字节别名；冲突重复、组件tar重复和产品链接仍拒绝。

原树30包没有独立命名的许可文件，另从精确npm元数据指向的固定源取得25包原文（其中2包原文在README），随`gateway-notices.json`交付。旧cache-content-type1.0.1缺口不再属于当前运行lock；新2.1.0与ylru2.0.0的精确源码、npm包和许可摘要由compat索引固定，实际npm原文由安装材料逐包记录。其余既有缺口继续保留：@koishijs/plugin-http、@koishijs/plugin-proxy-agent、koa-compose和only的固定来源/原文证据尚未全部闭合。MIT声明不替代原文；不据此作整个Taki许可结论。

## 缩小重建材料和研究留档

原165834683-byte源码研究ZIP及786缓存输入保留原位置/hash。新的`console-reduced-rebuild-inputs.zip`只用于拟公开集合审阅：保留client/components/console/plugin-console四个workspace及独立Taki适配源码；去掉无关测试/开发workspace和工具；500项解析缓存中无旧lodash-unified、Mocha、ESLint、c8。根resolutions明确绑定新适配源码，避免旧wrapper仍被传递安装。完整源树及工具原文仍待逐项材料审查，不能因缓存数量减少就宣布公开放行。

稳定复建入口：

```sh
python3 scripts/rebuild_console_sources.py --inputs /absolute/console-reduced-rebuild-inputs.zip \
  --inputs-sha256 7440ab10e2df8556ec065c17d1aac9f5b46fde199b18bf57a37c1673e1d1a5ef \
  --work /absolute/new-linux-directory --output /absolute/new-output
```

工具先核ZIP与每个成员；新HOME与安装树、Yarn禁网/immutable缓存及禁生命周期脚本，再执行types、lib、源码patch、client，比较7个lib和9个dist。500缓存冻结后的第二目录复建16/16逐字节一致，未重封装组件、未生成新版本身份。宿主网络并未由该脚本关闭，勿称OS禁网证明。失败日志包含缺yml-register类型工具、误用全局缓存及旧wrapper残留拒绝，均保留；未跳过类型检查。

22包逐实际文件→源码/生成配置→证据→缺口见本轮`source-matrix/matrix-22.json`及追加记录：async-validator、normalize-wheel-es的实际map源码全文与固定源逐字节相同；marked的11份map内容实际是TS擦除后的JS，固定TS5.9.3重放10份同字节、全部11份规范化语法相同，不能把`.ts`文件名误当原TS全文。Floating UI core/dom1.8.0的原gitHead树无对应包，精确官方tag均解引用到`12d94738472e922e1b3fa31b02b2b61b9ed77e6a`，对应包版本/入口/rollup配置已补，原错误树作为历史保留。其余包的实际npm文件、首选源码树、版本、build脚本/配置/锁摘要逐项列出；未建立完整输出生成关系的项目仍标待证明，不机械重建全部上游或重启方案A版本组合搜索。

最小源码包中的500工具/传递包最初有46项未检出独立命名许可文件；已复用22项原文，其余24项的逐包结果与新的材料hash见[2026-11-04材料增量](CONSOLE_MATERIALS_FOLLOWUP.md)。未据文件命名断言无许可。冻结包不被增量覆盖，Node、Yarn、编译器/native工具来源和外部依赖仍需11按实际交付范围复核。`complete_corresponding_source=false`、`product_release=false`保持。

## 验证与下一包

实物路径、size、hash、构建日志和组合manifest记录在原09 HANDOFF及artifact-index。组件9项负例覆盖改tar/绑定/lock、未知版本、改client、丢LICENSE/NOTICE/TAKI-CONSOLE和重复旧字节补丁。真实组合构建与原验包、安装程序结果须绑定本轮最终代码，不以旧tgz或08浏览器记录替代。

08固定接入的业务自查不因装包自动升级为独立验收。00/12仍需复核实际安装后的RPC、认证、重连、动态插件与个人管理入口；10再验安装恢复/激活，11递归审新整包与补充材料。若后续改变组件代码/版本，另交候选并由00安排08增量回归。最终定制selection、范围批准、公开托管/访问与维护责任均未代选；当前组合含个人/共享/聊天混合代码，不是物理裁剪或最终双发行通过。
