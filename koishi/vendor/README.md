# 固定下游 Console 开发候选

当前依赖为原始 `taki-console-0.1.0-candidate.2.tgz`，安装名为
`@koishijs/plugin-console`，包版本 `5.30.10-taki.2`。不是 npm-repack 包，也不是最终发行包。
**浏览器验收发现默认Markdown安全模式的链接属性注入，当前状态blocked。**
具体复现和09归属见 [candidate.2记录](../../docs/task08-integration/CONSOLE_CANDIDATE2.md)。

来源提交：`e4c0cebade52272e4e2b2f249d1d66d5ce27f722`（仅消费固定组件，未合入其分支）。
SHA256：`5b73df05ece95041f0586177bd077e72cb4bdd0df7c996c5ad7017909bad62dc`，634437字节、168文件。
包内 `TAKI-CONSOLE.json` 记录上游、源码重连修复及构建输入。

`package.json` 使用仓库相对 file 依赖；`package-lock.json` 锁 SHA512 与完整外部依赖树。
在 koishi 目录使用 npm 12.2.0 `npm ci --ignore-scripts`，不覆盖 dist、不使用个人路径。
`console-candidate.json` 是给09/11的机器可读版本接口，登记外部依赖及源码材料 hash。
源码输入和source-support两包已按hash绑定，尚未发布，`hosting_url=null`。

服务仍使用现有 `console.cjs::install` 的默认插件类、服务注入、addEntry/addListener 和认证。
AGPL-3.0 组件的对应源码完整性及公开材料放行仍待09/11；`product_release=false`。
严禁再次应用旧 npm client 字节补丁。现有组合发行器尚未支持此候选，接入必须由09提供显式分支。
本目录不授权发布、最终 selection 或物理裁剪。实际验收和装配缺口见
[任务08 Console 接入记录](../../docs/task08-integration/CONSOLE_CANDIDATE.md)。原candidate.1 tgz及其历史证据保留，当前package/lock不引用它；原版本同样复现上述链接属性问题。
