# 固定下游 Console 开发候选

`taki-console-0.1.0-candidate.1.tgz` 是任务09 final4 的完整 npm 组件，安装名为
`@koishijs/plugin-console`，包版本 `5.30.10-taki.1`。不是官方 npm 5.30.11，也不是最终发行包。

来源提交：`91bf8b91a8bcb1cf2b822d3eb38e2e11d301ad3f`（仅消费该版本的固定组件，未合入其分支）。
SHA256：`72c286da0c53feb6820c54e4b283d2c752b3b4d7198db1b6f7d38e405b495612`。
包内 `TAKI-CONSOLE.json` 记录上游、源码重连修复及构建输入。

`package.json` 使用仓库相对 file 依赖；`package-lock.json` 锁 SHA512 与完整外部依赖树。
在 koishi 目录使用 npm 12.2.0 `npm ci --ignore-scripts`，不覆盖 dist、不使用个人路径。
`console-candidate.json` 是给09/11的机器可读版本接口，登记外部依赖及源码材料 hash。
源码材料尚未发布，`hosting_url=null`；相对路径只是待装配绑定，不能作为已上线下载入口。

服务仍使用现有 `console.cjs::install` 的默认插件类、服务注入、addEntry/addListener 和认证。
AGPL-3.0 组件的对应源码完整性及公开材料放行仍待09/11；`product_release=false`。
严禁再次应用旧 npm client 字节补丁。现有组合发行器尚未支持此候选，接入必须由09提供显式分支。
本目录不授权发布、最终 selection 或物理裁剪。实际验收和装配缺口见
[任务08 Console 接入记录](../../docs/task08-integration/CONSOLE_CANDIDATE.md)。
