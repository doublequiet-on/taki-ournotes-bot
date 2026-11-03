# Taki下游Console候选（方案B）

用户已选择保留网页管理后台并维护下游构建。方案A历史版本组合探索停止。此处定义独立组件候选、构建输入与08接入边界，不是个人/定制最终发行或范围批准。

当前构建规格已推进到 candidate.2，原 candidate.1 与已装配包保持冻结。新增输入、逐输出比较、工具材料边界和08验收要求见[第七轮构建修复](CONSOLE_BUILD_REPAIR.md)。以下初版记录中的安装树和已完成验证不自动转移给新候选。

## 固定选择与独立身份

- 当前构建组件身份 `taki-console@0.1.0-candidate.2`；兼容安装路径保留 `@koishijs/plugin-console`，包版本明确为 `5.30.10-taki.2`，不标成官方npm5.30.11。
- webui源码 `896882b50cc91d16f522a52ded9644045d461c26`。选此基线是因为已有全部源码、许可证、lib重建与前端构建证据，能延续当前管理入口；不要求重现历史npm前端。
- 复用已验证的下游解析：Vue3.5.44、Router4.6.4、I18n9.14.5、VueUse11.3.0、Element Plus2.7.7源码；固定Node24.19.0、Yarn4.10.3、Vite5.4.21、TypeScript5.9.3。为了从Element Plus TSX源码编译，新增精确 `@vitejs/plugin-vue-jsx@4.2.0`，显式提供根Vue/Vite peer；新解析锁和缓存都冻结在输入包。没有升级Taki网关依赖或更换08接线。
- 唯一输入规格是 [downstream-inputs.json](../deploy/licenses/koishi-console/downstream-inputs.json)，完整输入文件集合/hash是输入ZIP内 `input-manifest.json`；候选 `TAKI-CONSOLE.json` 另绑定builder、recipe、重连patch摘要。源码Git提交和Taki最终提交分别记录，输入内容摘要优先于路径名称。

## 四类来源/生成问题的处理

| 对象 | 下游选择与真实生成链 | 兼容边界 |
| --- | --- | --- |
| lodash-unified | 不复制其缺许可wrapper。新Taki适配层从锁定lodash-es4.18.1公共命名导出集合生成显式re-export，排除default和Node互操作标记；Vite只将旧模块标识解析到此适配层。适配层自身按Taki MIT，Lodash原文单独保留 | 初次直接别名自查发现多出default，已修正；完整导出集合对照通过。原wrapper不得进入输出模块图；缓存中保留的原研究输入不因此获得外发放行 |
| Cordis/core/logger/schema/timer及Satori、schemastery | 明确选择准确npm随包TS，esbuild0.23.1将本地源码生成ESM、保持包依赖外部化，再由Vite完成组件装配。生成metafile、maps、输入TS均保存 | 不把gitHead旧TS覆盖npm实际TS。core/logger/timer已知差异保留；Schemastery的export=显式转换为ESM默认导出，未改业务逻辑 |
| devtools | 固定 `df6ab6bb7791a7a525a97990de73b3ea5e9a1941` 的packages/api TS（树版本6.6.3），独立登记6.6.3-taki.1；完整TypeScript程序生成ES2015，再由Vite装配 | 保留上游const enum和类型重导出的擦除语义；将已失效expect-error改成显式可选全局属性类型，运行表达式不变，保留before/after hash。完整诊断无错误才emit；三项导出、即时hook和排队注册合成对照通过。不再冒充npm6.6.4来源；真实浏览器握手仍待08 |
| Element Plus | 固定 `8c0180c37ae4741f40969a26a9704c1068be4d93` 完整Vue/TS/TSX/SCSS；显式生成version.ts=`2.7.7-taki.1`；Vue/JSX插件编译全部管理所用组件，Sass从theme-chalk/src/index.scss生成主题 | 不依赖原npm编译组件或两个未解释map输入。实际Vue虚拟export-helper内容/hash、module图和生成配置保存；保留全部组件入口，视觉/交互回归仍需08 |

实际前端共36个npm包（含上述从npm TS取源的包）取得准确npm、官方固定Git树及原许可/NOTICE，见输入 `materials/component-materials.json` 与 `materials/actual-sources/`。这些源码用于随行追溯；未声称每个第三方npm编译文件都已独立重建。webui、Element Plus及devtools源码另在source内。候选 `licenses/index.json` 记录随行原文的原始路径/hash；根许可不覆盖包级例外。工具、研究缓存、组件外部服务依赖和最终安装树分别审查。

devtools编译保留其tsconfig的严格函数/this检查及原skipLibCheck设置，覆盖module=ES2015、声明/maps、noEmitOnError=true。该API没有webpack全局引用，ambient types明确只取锁定Node声明，不要求未使用的webpack-env；没有忽略编译错误或改成transpile-only。

## 稳定构建入口

本次环境为Ubuntu24.04.5/WSL2、Python3.12.3、glibc2.39、Git2.43.0；Node/Yarn从固定工具归档取得。工作目录和输出目录必须全新。两次复建使用同一宿主的独立目录/HOME，未声称跨系统重现。材料路径取原HANDOFF，不能仅凭文件名接受下载。

```sh
python3 scripts/build_downstream_console.py \
  --inputs /absolute/console-candidate2-inputs.zip \
  --work /absolute/new-isolated-work \
  --output /absolute/new-evidence-and-candidate
```

入口先核整个ZIP固定size/hash和每个成员，再准备任务独立HOME、Node/Yarn。安装使用 `--immutable --immutable-cache --mode=skip-build`，Yarn网络和生命周期脚本关闭；依次执行固定yakumo tsc、esbuild、源码重连patch及 [downstream-build.cjs](../deploy/licenses/koishi-console/downstream-build.cjs) 的client构建。绕开的是上游失效的client调度器，构建和校验阶段没有跳过。Yarn禁网不等于OS禁网安装验证。

输出包含确定性tgz、逐文件component-manifest、receipt、构建日志/exit、实际模块图和生成ESM/maps。归档时间/权限固定，不加入工作目录或当前时钟。输入归档内源码链接按已有显式物化保留；新增上游原archive以普通文件存放，不解包执行。Node工具归档内部链接通过Python data filter限制在隔离目录；产品包链接拒绝政策不变。

重连修改仍使用 [首选源码patch](../deploy/licenses/koishi-console/taki-reconnect-source.patch)：校验原data.ts摘要、只应用一次、核结果摘要后编译。**此候选禁止再次运行旧npm字节补丁。** 当前 `build_koishi_release.py::patch_koishi_client` 仍只接受旧npm固定hash，不能直接拿新候选调用；本轮没有降低旧门禁或擅自切换个人构建。

## 给08/00的最小接口与验收

固定参考是200的 `eed7e70fa8b5194befc379a9929ce72e7617707a`，没有读取其未提交业务文件或合入分支。该版本koishi/package.json仍固定官方5.30.11；09的新组件需要00安排一次显式接入。

| 文件/接口 | 最小接入与必须验收的内容 |
| --- | --- |
| koishi/package.json、package-lock.json | 在隔离候选中把 `@koishijs/plugin-console` 绑定本候选tgz摘要与下游版本；不得改成移动版本或只覆盖dist。核peer、完整安装树与外部@koishijs/console/koishi/ws等版本；Node范围保持现有24.x |
| koishi/console.cjs：Console加载、ctx.plugin、console.addEntry/addListener | 保持默认插件类、服务注入、/taki页面与WebSocket路径、动态插件入口。新前端需与Taki插件共同装配，不仅看静态页面 |
| console.cjs：taki/login与authority；scripts/console-rpc-probe.cjs | 真实隔离HTTP/WS测试登录成功/错误、未登录拒绝、权限撤回、断线会话失效、错误响应；不得连接真实QQ或生产后端。现有RPC保存/冲突/响应上限合同全部保留 |
| packages/client/client/data.ts对应已编译资产；scripts/console-browser.cjs | 浏览器中断线、连续失败、恢复后回到首次断线URL；登录页面跳转不覆盖原路径。重连只由源码patch提供，禁止叠加旧字节替换 |
| koishi/console/index.js、runtime.js、chat.js、model.js、library.js等及scripts/console-state-probe.cjs | 菜单/插件加载、设置表单、scope绑定与撤回、恢复草稿、个人chat/model/library/notice等现有能力；Element Plus组件、CSS、日期/选择/弹窗及devtools不报运行错误。产品裁剪须待选择，不能借下游构建减少功能 |
| scripts/build_koishi_release.py、check_koishi_release.py（09负责后续装配） | 在00确定统一候选后增加显式下游组件分支：校验独立manifest/hash、跳过已完成的旧字节补丁、装配许可/源码索引。旧npm路径保持独立且仍拒绝未知hash；本轮不把08候选SHA写入05 checkerCommit |

本轮09自查是源码构建、确定性重建、合成socket的RPC/data/patch/重连、Node插件模块加载和导出集合；真实网关RPC、认证、浏览器插件UI和个人行为未接线，不记通过。05 checker来源仍固定3dda6d2，allow只预检；07逐库physical/logical及代码回退不恢复数据边界不变。

隔离加载参考为koishi4.18.11、workspace @koishijs/console5.30.10、open8.4.2、uuid8.3.2、ws8.22.1；这些版本由本构建锁/源树决定，不是替08选择最终服务安装树。候选tar保留外部依赖声明，08必须在其固定package-lock中明确实际解析并回归；单独tar不是自足网关安装包。

## 源码获取、材料与剩余门槛

随行源码包由固定输入ZIP、当前builder/recipe/config/patch、生成材料及NOTICE组成。发行索引应绑定**组件tgz SHA256→源码包SHA256→Taki源码提交**，稳定相对位置 `sources/<component-sha256>/source-materials.zip`；关于/页脚显著链接由08接入可信索引。组件manifest仅准备相对位置，不能从候选自报URL推导信任。公开托管、访问范围、保留和发布仍待用户决定，本轮不发布。

`complete_corresponding_source=false`、`product_release=false`继续保留：36包取得源码/许可不等于全部上游生成证明或完整工具缓存外发许可审查；整个monorepo缓存含未入组件的旧wrapper及其他开发/测试包，源码输入包暂不是公开分发已放行材料。外部Node服务依赖树、最终08修改、获取入口、个人/定制物理裁剪、10安装恢复/激活及11递归实物验收仍未完成。历史A的来源缺口作为历史保留，不再阻止给此新身份做独立验证，也不自动被新构建“关闭”。
