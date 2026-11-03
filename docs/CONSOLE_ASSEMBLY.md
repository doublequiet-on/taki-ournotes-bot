# 下游Console固定候选装配

本轮本地组合起点为08 `8d57eebb7efa279279166be53a6d9c102e6d90d1`，接收09固定 `610f14f8fd6ad55368a73e7ac4afea5f29e347a9`，共同祖先 `9a3eca0b21dd7a7724143f988def59a5fc558dca`。本地merge无冲突，保留两条历史；没有合并GitHub PR/main或修改08业务工作树。09新增仅发行脚本、材料与导航。

## 两条组件路径

`build_koishi_release.py`保留官方5.30.11的旧资产hash/唯一替换校验。仅固定下游5.30.10-taki.1进入新路径：核vendor绑定清单、tgz SHA256/SHA512、完整规范化lock、package依赖与lock一致、全部167安装文件、TAKI-CONSOLE源码重连标识与许可索引；未知版本、改包、混搭和重复旧补丁均拒绝。新路径不写旧补丁说明，不修改组件字节。

`koishi/vendor`整目录与lock随组合；sdist只收录准确tgz、绑定JSON和两份npm配置，新增JSON例外均为准确路径。未把目录或所有JSON加入允许列表。现有Node完整LICENSE及控制器/rollback层仍随包。

组件仍是SHA256 `72c286da0c53feb6820c54e4b283d2c752b3b4d7198db1b6f7d38e405b495612`，来源仍为09 `91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f`。本轮不会重新标记原组件或修改vendor来源索引，也不通过npm pack重新封装组件；复制后的167文件重新逐项核验，LICENSE、licenses/NOTICE、TAKI-*均须保留。

## 实际外部依赖和材料

外部树来自08实际lock：console5.30.11、koishi4.18.11、open8.4.2、uuid8.3.2、ws8.22.0，完整规范化lock SHA256 `91200cca290e997ef56fe577516460982b9cb318fc47e8a519b2ccbaf401974f`。不沿用早期workspace console5.30.10/ws8.22.1 smoke。

构建前`console_release.installed_npm_materials`从npm完整性缓存读取锁定registry tar，重算SHA512并与实际安装文件比较。180个registry包（包含实际随包开发工具）逐包版本、resolved、integrity、tar摘要、repository、文件与原文路径/hash写入组合`licenses/npm-installation.json`；本地taki-console-assets跟随已提交console目录。npm缓存缺失或安装字节变化即拒绝。固定agent-base tar含`package/./dist/index.js`与`package/dist/index.js`相同字节：仅完整性核验后的registry材料读取允许并记录同字节别名；冲突重复、组件tar重复和产品链接仍拒绝。

30包没有独立命名的许可文件，另从精确npm元数据指向的固定源取得25包原文（其中2包原文在README），随`gateway-notices.json`交付。5包仍阻塞材料放行：@koishijs/plugin-http、@koishijs/plugin-proxy-agent的gitHead archive返回404；cache-content-type、koa-compose固定树未找到原许可全文；only缺固定gitHead。MIT声明不替代原文；不擅自换版本。所取得根许可/包级路径保留来源，不据此作整个Taki许可结论。

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

08接线版本及组件字节未变；不要求为未变化代码重做业务实现。00/08收到组合包后复核真实安装后的RPC、认证、重连、动态插件与个人管理入口；10再验安装恢复/激活，11递归审完整包与补充材料。若后续改变组件代码/版本，另交候选并由00安排08增量回归。最终定制selection、范围批准、公开托管/访问与维护责任均未代选；当前组合含个人/共享/聊天混合代码，不是物理裁剪或最终双发行通过。
