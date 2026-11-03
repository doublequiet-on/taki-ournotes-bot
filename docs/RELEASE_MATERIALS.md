# 发行材料与验收门

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
