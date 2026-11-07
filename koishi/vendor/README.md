# 固定下游 Console 开发候选

当前依赖为原始 B2 `taki-console-0.1.0-candidate.4.tgz`，安装名
`@koishijs/plugin-console`，包版本 `5.30.10-taki.4`。只用于隔离候选接入。
来源固定提交 `a526abab715989145ed1368ce1610413213a1a8c`，未合入09移动分支。
SHA256 `1668e57b5b387814aab60f79f2d157f7017299088b5413e49690ef8aa5b5f0c9`，635218字节、168文件。

package/lock 使用仓库相对 file 依赖及完整性摘要，npm 12.2.0 `npm ci --ignore-scripts` 可安装。
外部依赖及 cache-content-type 兼容层保持；不能覆盖dist或重复应用旧重连字节补丁。
机器接口 [console-candidate.json](console-candidate.json) 绑定固定源码索引、输入材料及准入状态。

本轮实际安装/浏览器/RPC证据见 [candidate.4自查](../../docs/task08-integration/CONSOLE_CANDIDATE4.md)。
A2/B2的dist/index.js差异仍待12审查，整包字节可复现未通过；接入通过不代表最终材料或产品发行通过。
09负责组合发行器准入和装包；旧client字节门禁保持。公开地址为null，完整对应源码及product_release为false。
历史candidate.1/2/3原包与各自验证记录保留，不混用旧证据作为本轮验收。
