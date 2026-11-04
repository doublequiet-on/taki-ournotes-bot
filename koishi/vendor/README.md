# 固定下游 Console 开发候选

当前依赖为原始 `taki-console-0.1.0-candidate.3.tgz`，安装名为
`@koishijs/plugin-console`，包版本 `5.30.10-taki.3`。不是 npm-repack 包，也不是最终发行包。
**原Markdown链接属性注入已在实际网关浏览器复验通过；发行装配和12独立复核仍待完成。**
版本绑定及08自查见 [candidate.3记录](../../docs/task08-integration/CONSOLE_CANDIDATE3.md)。

来源提交：`ca7a64175aa4c359c018b8a7e1dc6c506fbd6552`（仅消费固定组件，未合入其分支）。
SHA256：`ea92ea91d816648023e61cf6ce7dfe2de98aece38e1ca8dd9292166f5537d1fb`，634671字节、168文件。
包内 `TAKI-CONSOLE.json` 记录上游、源码重连修复及构建输入。

`package.json` 使用仓库相对 file 依赖；`package-lock.json` 锁 SHA512 与完整外部依赖树。
在 koishi 目录使用 npm 12.2.0 `npm ci --ignore-scripts`，不覆盖 dist、不使用个人路径。
`console-candidate.json` 是给09/11的机器可读版本接口，登记外部依赖及源码材料 hash。
源码输入和source-support两包已按hash绑定，尚未发布，`hosting_url=null`。

服务仍使用现有 `console.cjs::install` 的默认插件类、服务注入、addEntry/addListener 和认证。
AGPL-3.0 组件的对应源码完整性及公开材料放行仍待09/11；`product_release=false`。
严禁再次应用旧 npm client 字节补丁。现有组合发行器尚未支持此候选，接入必须由09提供显式分支。
本目录不授权发布、最终 selection 或物理裁剪。实际验收和装配缺口见
[任务08 Console 接入记录](../../docs/task08-integration/CONSOLE_CANDIDATE.md)。原candidate.1/2 tgz及其历史证据保留，当前package/lock不引用它；原版本同样复现上述链接属性问题。
