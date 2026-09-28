# 第三方服务与素材

本仓库只授权本项目自行编写的代码。MIT 许可证不涵盖游戏名称、商标、美术、音视频、歌词、谱面、上游 MasterData、QQ 平台或第三方软件。

发布者 Bilibili @Adeliae 将本机器人作为非商业社区工具运营，不以游戏素材牟利。该声明不代表本仓库向他人授予游戏素材或数据的使用许可，也不改变代码的 MIT 许可证；使用者应分别遵守相关权利方及上游服务的要求。

- **Project Yume**：歌曲、卡牌资料与图片从 [bdon.yatta.moe](https://bdon.yatta.moe/) 的公开文件读取。仓库不附带下载结果；网站可访问不等于授予素材再分发许可，部署者应确认上游的使用规则与素材权利。
- **MoeNotes**：查询完整音符谱面图时，从 [MoeNotes](https://github.com/StarMoe-org/moenotes) 的公开资源服务器按需读取谱面 JSON，并在本机短期缓存；无法读取时只显示等级和 Note 数。谱面文件及游戏内容的权利仍归原权利方。
- **Haneoka**：`/查分数表` 使用其日服公开预计算分析，回复注明“Haneoka分析数据”。核实源码版本 `c6087abe91fc2b19066d9829febf793e4da47665` 的 `src/lit/catalog-screen.ts`、`scripts/build/api.py` 与服务器配置；源码仓库为 [haneoka-gakuen/haneoka](https://github.com/haneoka-gakuen/haneoka)。指定页面的 `intl` 是国际服，日服使用 `/jp/zh-CN/song-meta/`。未复制或移植其算分算法；上游自有代码采用 MPL-2.0，若将来复制需另行履行文件级许可义务。
  - 公开接口基址 `https://haneoka.org/api/v1/servers/jp/`：先读取 `release?projection=identity`，再读取 `songs?projection=4&release=<releaseId>` 与 `song-meta?release=<releaseId>`。两个集合都是完整 ID 字典，无分页；拒绝分页包装、缺失集合、重复 JSON 键、非有限值和错误页面。`releaseId` 固定同一版资料，`sourceId` 保留来源版本；未发现可当作上游更新时间的字段。
  - `musicId` 与 meta 顶层 ID 相连，难度索引 0/1/2/3 对应 EASY/NORMAL/HARD/EXPERT；与 Project Yume 的 ID 及已收录标题再次核验。已现场核对 100001《迷星叫》、100008《詩超絆》、100070《everscape》，不依靠模糊匹配合并。等级优先读取 `displayLevel`，缺失时回退 `playLevel`、`sortLevel`，不把小数排序等级冒充展示等级。
  - 页面默认 `eff` 降序；`chart.eff` 和 `chart.score` 为比例系数，页面乘 100 显示为百分数，bot 保留两位小数。上游将 `eff` 定义为 `score / (time + 30) * 60`，bot 直接读取结果，不自行计算。`scoreKind=chart-relative-factor`、`absoluteScoreAvailable=false`；只允许已确认的 `reference` 条件（Fever、全 PERFECT、技能倍率 2.5、10 秒、曲间 30 秒、区间端点包含）参与排名；默认将符合此参考条件的各难度分别作为一条谱面排名，按歌曲 ID、难度顺序稳定处理并列。`time` 单位秒，为同曲最晚判定点的最大值；`sr` 是技能区间覆盖的未加技能倍率贡献比。参数默认值与谱面差异警告保留并提示，非官方结论。
  - 上游 2026-09-27 使用条款允许在规定限制内合理使用公开接口，禁止绕过认证、限流、robots 或加重负担的自动化；本次核实 robots 允许公开访问。游戏数据与素材没有因代码开源而自动取得再分发许可。本功能低频查询并署名，不发布上游原始数据包，不将分析数据发送给模型；运营者仍需遵守权利方与上游的使用要求。条款见上游 `public/i18n/zh-CN.json` 的 `termsPage`，开源代码许可不覆盖游戏数据。
- **可选 AI 服务**：配置后，仅用于将自然语言识别为受限查询条件；事实由 Project Yume 或 Haneoka 本地缓存检索产生。常见效率问法不调用模型；需要模型的其他 `/问` 会把问题内容发送给所配置的 API 服务，模型不得生成效率值或排名。
- **QQ 官方机器人**：使用 QQ 开放平台接口；需要部署者自己的 AppID、AppSecret 和相应权限。不得提交这些凭据。
- **Python 依赖**：`qq-botpy`、`Pillow`、`aiohttp` 由各自的许可证授权，安装时由包管理器获取。本仓库不打包虚拟环境或依赖源码。

本项目为非官方社区工具，与游戏版权方、QQ 平台及上述数据服务均无隶属关系。如需就素材展示提出调整，可通过 Bilibili @Adeliae 联系维护者。

## 角色卡与 SNAP 的资源核实

2026-09-28核对 Project Yume 的公开 MasterParsed 列表和122份成员/SNAP详情，以及网站 `useFilter-BIvyqgQJ.js`、`NonoCard-aiXKXz-b.js`、`cardSkill-B40radPu.js`。用来确认稀有度、类型图标URL、技能名称分类和指定状态的属性展示；没有复制上游JS代码或引入算分算法。各映射与实际样例见[卡牌说明](更新说明-成员卡与支援卡查询.md)。上游没有在本次核实材料中给出可据此再分发游戏素材的授权；资源仍按需读取、缓存，不随源码提交。原生稀有度框和正式属性名称未确认，不能从截图猜资源或枚举。

成员列表技能摘要另使用 [Haneoka 日服成员卡](https://haneoka.org/jp/zh-CN/member-cards/) 的公开 `api/v1/servers/jp/{cards,leader-skills,skills,gekisou-skills,skill-reference}`，按release固定版本；来源与条件结构参考其公开 `scripts/build_api.py` 和 `src/lit/shared/skill-text.ts`。未复制上游程序代码；只保存少量注明来源的数值/描述测试样例及机制指纹，不打包游戏图像或全量数据。代码MPL许可不等于游戏素材再分发许可，继续遵守上述公开接口使用限制与署名要求。
