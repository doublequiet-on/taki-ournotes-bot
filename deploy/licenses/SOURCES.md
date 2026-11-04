# 固定版本随行许可证

用户已选择方案B：下游独立身份与实际源码生成见 [CONSOLE_DOWNSTREAM.md](../../docs/CONSOLE_DOWNSTREAM.md)。`downstream-inputs.json`保留冻结运行构建规格；`downstream-inputs-public-v2.json`绑定缩小后的可公开重建输入，不能互换摘要。`downstream-build.cjs`编译首选源码及新Taki Lodash适配层，源码reconnect patch仅应用一次。现有个人builder仍使用下述旧npm路径；新候选不得叠加旧字节补丁。以下方案A材料保留为历史，不继续版本组合探索。

方案A续轮已形成独立研究材料包：全部lib/dist逐文件来源/差异、实际前端模块图、本次解析锁/缓存及许可索引、源码补丁与复现脚本。16项重建9项同npm、7项不同，历史前端来源仍待闭合；不是最终发行材料全部齐备。详见仓库docs/CONSOLE_SOURCE_MATERIALS.md及原task09回执。此处reproduce.sh按材料包根目录布局使用，不是产品安装脚本；源码patch也不自动应用到当前npm。

2026-11-03补充复核：npm的10个src文件与下列固定Git树逐字节一致；package版本和三处依赖值由5.30.10变为5.30.11。三个lib source maps均无sourcesContent；参考树没有依赖锁。源码子集匹配不证明lib/dist来源，源码完整性仍未验收。具体构建配置、参考archive摘要及剩余阻塞见随行 `RELEASE_MATERIALS.md` 的Console固定源码复核。

- `node/LICENSE`：Node v24.19.0 [完整上游文本](https://raw.githubusercontent.com/nodejs/node/v24.19.0/LICENSE)，157606 bytes，SHA-256 `148eacf7863ef4329224a29398623077200a27194aa075569faf4a0a85566ca5`。含其捆绑第三方材料，不能换成MIT摘要。
- `koishi-console/LICENSE`：Console npm 5.30.11的gitHead `896882b50cc91d16f522a52ded9644045d461c26` 指向的 [webui许可证参考](https://raw.githubusercontent.com/koishijs/webui/896882b50cc91d16f522a52ded9644045d461c26/LICENSE)，34523 bytes，SHA-256 `8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef`。其[精确package.json](https://github.com/koishijs/webui/blob/896882b50cc91d16f522a52ded9644045d461c26/plugins/console/package.json)版本实际为5.30.10，声明AGPL-3.0；与npm 5.30.11不一致，完整对应源码及编译产物来源尚未确认。

2026-11-03核对 npm tarball 的SHA-512与发布integrity一致；其27个成员没有LICENSE文件。这里补注册信息指向的固定源码文本，不把npm元数据当作实际随包材料。构建时保留node_modules原有LICENSE/NOTICE，不根据根许可证覆盖第三方条件。

对应源码参考：[完整固定源码树](https://github.com/koishijs/webui/tree/896882b50cc91d16f522a52ded9644045d461c26)。官方npm5.30.11兼容路径仍由 `scripts/build_koishi_release.py::patch_koishi_client` 执行并记录逐字节替换；下游candidate.3在首选源码中一次性应用重连和Markdown安全补丁，包内 `TAKI-CONSOLE.json`、`TAKI-MODIFICATIONS.txt` 及发行绑定记录精确输入和摘要，发行器禁止再次运行旧字节补丁。

提供这些参考不表示对应源码/构建依赖/源码获取渠道与网络交互条款已经完整验收。最终选择保留Console时须完成 RELEASE_MATERIALS.md 的逐项核对，并确定向接收方提供源码、补丁、构建材料及网络用户获取方式。本次不对整个Taki作许可结论。
