# Taki cache-content-type CJS 适配

独立包身份 `@taki/cache-content-type-cjs@0.1.0`；Taki MIT许可见 LICENSE。
唯一职责是将固定 `cache-content-type@2.1.0` 的 `getType` 导出为 Koa 要求的 CJS 函数。
不复制旧实现、不修改 Koa/node_modules、不安装全局加载钩子，也不改变 MIME 算法。

根 package.json 的直接依赖 `cache-content-type=file:compat/cache-content-type` 和
`overrides.cache-content-type=$cache-content-type` 将 Koa 2.16.4 的同名依赖指向本包；
本包通过独立 alias `cache-content-type-upstream=npm:cache-content-type@2.1.0` 加载上游。
根 package-lock 锁上游完整性、ylru2.0.0、mime-types2.1.35及mime-db1.52.0。
使用 npm12.2.0 `npm ci --ignore-scripts`；必须携带 compat 目录，不能只复制链接。

上游转换结果（包括未知扩展返回false）保持；Koa setter遇false仍清除Content-Type。
缓存沿用上游100容量的双代LRU，测试1000个唯一输入最多观察199项；不是严格总共100项。
2.1.0对false结果命中缓存，旧包装会重算；淘汰后重新计算，HTTP结果无变化。

准确来源、tgz/source/LICENSE hash及gitHead见 upstream-materials.json。
两包的首选src/index.ts及LICENSE与对应固定Git树逐字节一致；不将这项源码对应验证称为独立构建发布字节的证明。

给09：新安装树应包含本包实体源码及两个上游包，并更新运行依赖来源/许可索引；
源安装包须携带compat/cache-content-type、package/lock。封装node_modules时解引用此本地链接或同时保留正确相对目标，禁止留下悬空链接。
本轮不更新Console组件、发行脚本或现有冻结包。具体验收见 docs/task08-integration/CACHE_CONTENT_TYPE.md（仓库根）。
