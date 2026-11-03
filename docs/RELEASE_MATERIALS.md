# 发行材料与验收门

## Console固定源码复核（2026-11-03）

对象是 npm `@koishijs/plugin-console@5.30.11`，不是对整个项目的许可结论。[npm固定包](https://registry.npmjs.org/@koishijs/plugin-console/-/plugin-console-5.30.11.tgz)已核发布integrity；其gitHead指向[webui固定源码](https://github.com/koishijs/webui/tree/896882b50cc91d16f522a52ded9644045d461c26)，该树plugin package版本为5.30.10。逐项比对结果：

- npm的10个src文件（三个TS入口、七个locale）与该Git树完全同字节。package.json只有version及三处依赖版本值不同：peer/dev的`@koishijs/client`和dependency的`@koishijs/console`从`^5.30.10`到`^5.30.11`。这证明已比对的源码子集对应，不证明lib/dist由这棵树构建。
- npm的三个lib source maps指回src，但均无sourcesContent；不能用它们补齐前端dist对应源码或完整构建输入。
- 固定树声明yarn@4.10.3；`yakumo.yml`的build依次为tsc、esbuild、client，根scripts含`yakumo version`和`yakumo publish`。树中没有依赖锁文件。版本变化可能发生于发布步骤，但目前没有实际发布日志、完整依赖解析或重建字节证据，不能把这一推测当版本对应证明。
- 下载的固定参考源码archive为289746 bytes，SHA-256 `85528dd1b9407b0f7158ca7c049156088c53de850dad8e3ef695f1d96198e1b9`。它只作为本地审计参考，不自动复制到发行包，不标记为完整对应源码。详细逐文件hash与构建配置保留在任务09回执的`console-source-comparison.json`。

交付阻塞仍包括：确定发布对应源码和完整构建依赖/步骤，验证实际lib/dist来源，形成接收方可取得的源码及Taki修改/补丁材料，确定网络用户源码获取方式并验收。现有reconnect recipe记录的是已发布client字节替换，不是从源码重建证明；最终08裁剪还会产生额外修改，须按最终固定候选重新出补丁/说明。未执行上游构建、未发布源码服务。不得把参考archive或已有LICENSE视为这些要求已满足。

## 全部交付层

本表承接任务11源码审计，区分已核对文本与尚未形成的实际交付。任务09基础阶段未形成定制产品工件；不得把个人版CI产物用作定制裁剪证明。

| 材料 | 当前证据与构建行为 | 最终候选仍须提供 |
| --- | --- | --- |
| Node v24.19.0 | 固定上游完整LICENSE已校验并由组合构建复制至 `licenses/node/LICENSE`，进入组合files摘要 | 实际二进制来源发行包、来源摘要与二进制hash；目标OS/arch验证；不是仅凭`node --version`认定来源 |
| Console 5.30.11 | 真实npm tarball校验integrity；27成员无LICENSE；固定gitHead的package为5.30.10，与npm 5.30.11不一致；AGPL-3.0声明已读；补LICENSE、版本来源、修改sidecar和字节替换recipe | 先解决5.30.11发布与gitHead源码5.30.10的对应关系，再补完整对应源码/构建材料、实际源码或补丁提供渠道、网络交互源码获取方式；修改后client hash由组合files记录。现有dist及src子集不能自动视为完整对应源码 |
| Python与原生库 | 11的44条锁记录、wheel来源；实际wheel包未下载/核验 | 最终所选wheel METADATA与LICENSE/NOTICE、Pillow等内嵌原生库；与锁和平台闭包对应；可选知识依赖另列 |
| npm与原生库 | 11的562个lock路径不是实际安装数量；builder保留原包树材料 | 最终node_modules逐包version/integrity、LICENSE/NOTICE；平台可选包/预览sharp-libvips只按实物判定 |
| 字体 | 现有两份OFL及字体保留原用途 | 精确上游字体版本与二进制来源；wheel安装后的实际路径和干净字体验收 |
| 947/品牌/游戏资源/知识正文 | 保留/替换/排除尚待决定；没有自动采纳11建议 | 决定者/范围、来源与逐文件摘要、权利材料；知识审核不等于再分发依据 |
| P165资源 | 08-P165有九项图标版本/hash修正 | 以最终闭包SHA的索引为准，不用main旧索引覆盖；不得预装真实名片或个人素材缓存 |

Taki宿主依赖与Haneoka外部serverless API分开登记。Haneoka R2/D1/采集状态和私有账户不进入源码、发行或恢复树。不得将远程消费授权推导为镜像/预装授权。

后续每个工件交绝对路径、size、SHA-256、构建日志、product manifest、精确source commit、依赖锁和递归inventory。所有交付层（含wheel/sdist/git archive、压缩嵌套、source maps、controller rollback、安装树/previous）独立审查。缺失材料和未决选项保持待验，不能用根MIT或路径扫描的成功补齐。
