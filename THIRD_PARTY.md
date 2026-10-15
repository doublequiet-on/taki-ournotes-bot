# 第三方服务与素材

当前默认来源为 Haneoka，配置和已知缺失见 [统一来源说明](docs/HANEOKA_UNIFIED.md)。Haneoka Game Records 挑战榜由 Haneoka 转接 MoeNotes tracker（api.bdon.moe）。本项目独立实现 DTO 校验，不复制上游 MPL 代码；展示同时署名 Haneoka / MoeNotes，历史明确记录转接观测口径。更换入口不改变真实数据血缘或游戏数据权利。

Haneoka 默认主资料：`OURNOTES_DATA_SOURCE=haneoka` 使用[公开 Catalog 合同](https://docs.haneoka.org/openapi.json)的固定 JP 版本，以及核对实体／日文／技能机制一致的 intl 文本；完整技能有限模板转换为本项目原创，不复制上游 MPL 实现。14 个成员／SNAP 测试样本的必要字段与中日文、属性参考来自 2026-10-07 的公开 Haneoka 和 Project Yume 接口，仅用于对拍，不随 wheel／sdist 再分发。游戏素材、Master 与真实来源权利不因更换镜像而改变。图片和属性图标默认按需读取 Haneoka，不打包；旧源仅在显式配置时使用。版本及转换合同见 [主资料说明](docs/HANEOKA_CATALOG.md)，当前剩余标签映射和技能缺失见统一来源说明。

本仓库只授权本项目自行编写的代码。MIT 许可证不涵盖游戏名称、商标、美术、音视频、歌词、谱面、上游 MasterData、QQ 平台或第三方软件。

发布者 Bilibili @Adeliae 将本机器人作为非商业社区工具运营，不以游戏素材牟利。该声明不代表本仓库向他人授予游戏素材或数据的使用许可，也不改变代码的 MIT 许可证；使用者应分别遵守相关权利方及上游服务的要求。

- **Project Yume（显式旧主资料来源）**：歌曲、卡牌资料与图片从 [bdon.yatta.moe](https://bdon.yatta.moe/) 的公开文件读取。仓库不附带下载结果；网站可访问不等于授予素材再分发许可，部署者应确认上游的使用规则与素材权利。
- **MoeNotes（显式旧谱面来源）**：查询完整音符谱面图时，从 [MoeNotes](https://github.com/StarMoe-org/moenotes) 当前公开资产服务 `assets.bdon.moe` 按需读取谱面 JSON，并在本机短期缓存；无法读取时只显示等级和 Note 数。谱面文件及游戏内容的权利仍归原权利方。
- **Haneoka 完整谱面（默认）**：`OURNOTES_CHART_SOURCE=haneoka` 按 [公开接口合同](https://docs.haneoka.org/openapi.json) 读取 JP release identity、固定版本的歌曲实体和该实体返回的 `.bytes` 文件；原始 JSON 直接交给现有解析器与绘图层，不复制上游解析／绘图实现。文件、实体和 release/source 身份在独立目录缓存，失败不自动访问 MoeNotes。图片页脚保留 Haneoka JP 署名；游戏谱面权利及真实上游血缘不因改用镜像而改变，原始谱面和验收图片不随发行包再分发。主资料、封面与其他模块仍按各自来源获取。
- **Moenotes 可选开放平台**：
  - 可选正式来源仅[OpenAPI](https://bdon.moe/api/open/openapi.json)的challenge-ranking、四服和最低`moenotes:rankings`；适配器原创，[排名后端固定MIT版本](https://github.com/StarMoe-org/moenotes-api/tree/d094de568c570889b318e9490574da678fddf26d)只作接口／时间证据。当前默认 Haneoka 转接观测；open 的 Top100／四服权限／时间头透传未认证实测。公开活动发现、地区metadata／素材和本地历史保留明确例外；正式历史独立v2，旧库保留，来源段断线。
  - Secret仅传固定HTTPS认证operation，禁止重定向，不进CDN、URL、repr、缓存、日志、图片或QQ。维护者确认展示的日额度不是实际总量限制；不实现日拒绝，不降低原四服采样频率，分钟／并发／429仍按真实约束处理。
- **MoeNotes 活动挑战歌曲榜（显式旧 tracker 来源）**：`OURNOTES_CUTOFF_SOURCE=tracker` 使用 [MoeNotes](https://bdon.moe/events/tracker?server=jp) 的公开 `/{server}/events/current` 和 `/{server}/events/{eventId}/challenges/{challengeMusicId}/ranking`，基址为 `https://api.bdon.moe/api/v1`。2026-10-02 匿名取样确认四服当前活动及各服挑战榜；JP 三曲和其他服一曲均返回 100 个位置，`X-Position-Source: responseOrder`。该初版仅保存分数／缺值；2026-10-04的当前身份扩展见下项，历史仍不保存身份。接口可用性与覆盖范围不是 SLA，也不代表实时无延迟。
  - 查询升级 v1 的历史来自启用后本机记录的同一挑战榜快照；没有接入积分或永久排行榜历史。完整数据长期保留，未知时间、冲突和失采不造点；曲线显示真实观测，不能作为预测。四服采样与容量开关见 [历史运维](docs/CUTOFF_HISTORY.md)。
  - 2026-10-04 起，按维护者要求，当前榜单图文同时显示公开 `playerData.id` 数字ID及 `playerData.name` 用户名；当前缓存仅保存经过校验的这两个字段与分数，不保存队伍、卡组或完整profile；上述历史库仍不记录玩家身份。旧分数缓存或数字ID缓存保留可读，缺失字段明确标注。
  - 按 [上游固定版本](https://github.com/StarMoe-org/moenotes/tree/720112a252f92c97dce694355dd30f992b2f9d80) 核对路由、状态和时间语义；MoeNotes 源码采用 AGPL，Taki 独立实现接口适配，未复制其前端或算法。API 正式公开使用合同／速率保证仍未知，不将公开可读误述为无限授权。
  - 同服元数据来自 `metadata.bdon.moe/index.json` 及其版本化 MasterEvent／MasterChallengeMusic／MasterLiveMusic／MasterText／MasterStoryChapter，核对表哈希后使用。封面和活动 Banner 从同服 `assets.bdon.moe/{server}/{locale}/Image/Jacket/…` 与 `Story/Banner/Chapter/…` 按元数据名称构造；素材发布版本独立于 Master，版本不一致不冒充素材完整。
  - 图文注明 MoeNotes、非官方、逐曲源采集时间与状态；来源时钟可能偏差，`lastSeen` 仅是最后观测。取样中非日服 API 时间与按来源时区解析的 Master 时间相差一小时，因此冲突时间留待核实。实现与缓存策略见 [功能说明](docs/EVENT_CUTOFFS.md)。
  - 游戏图片和数据的权利仍归原权利方，代码许可不授予素材再分发权。本仓库不附素材或榜单包，不绕过认证、限流或反爬，不请求账号数据；如上游调整开放范围应停止对应读取并重新核实。
- **可选 AI 服务**：配置后，仅用于将自然语言识别为受限查询条件；事实由可靠来源检索产生。本地支持的歌曲、卡牌与榜线问法不调用模型；需要模型的其他 `/问` 会把问题内容发送给所配置的 API 服务，模型不得生成榜分或名次。
- **QQ 官方机器人**：使用 QQ 开放平台接口；需要部署者自己的 AppID、AppSecret 和相应权限。不得提交这些凭据。
- **Python 依赖**：`qq-botpy`、`Pillow`、`aiohttp` 由各自的许可证授权，安装时由包管理器获取。本仓库不打包虚拟环境或依赖源码。

本项目为非官方社区工具，与游戏版权方、QQ 平台及上述数据服务均无隶属关系。如需就素材展示提出调整，可通过 Bilibili @Adeliae 联系维护者。

## 歌曲颜色与激奏顺序

2026-09-28 核实 Haneoka 日服版本 `r-039641cd908372f8eaba`，来源版本 `v10050-4f54449965f4-22d176330341-mb9f5318479c8-n15b0b051`。公开接口基址 `https://haneoka.org/api/v1/servers/jp/`：先取 `release?projection=identity`，再用同一个 `release` 参数读取 `songs`、`songs/{ID}` 和 `gekisou`。列表为完整ID对象，无分页字段；详情与列表必须逐ID吻合，缺一份详情不替换有效快照。现场取得84首完整详情，查询侧还须和主歌曲库的ID、已有标题及封面标识同时匹配；未映射歌曲不自动并入主库。

依据上游源码提交 [6dbd3368](https://github.com/haneoka-gakuen/haneoka/blob/6dbd3368c2e00eb7f9b8a3049ed705bbcd922857/scripts/build/api.py)：`MasterLiveMusic._musicType` 对应 `musicType`；`_gekisouMission1/2/3` 按顺序对应详情 `gekisou.missionTypes`，属于歌曲而非难度。`gekisou.enums.missionType` 确认 1=Combo、2=Luck、3=JustCount；0=None 与4=All不是可直接替换成三类的标签。颜色图标映射见同提交 `src/lit/shared/song-tile.ts`：1红、2蓝、3绿、4黄、5紫；中文颜色是识别别名，非猜测的官方属性名。

默认颜色／激奏信息随 Haneoka 主快照捕获；旧 Yume 模式的 `song_traits.py` 独立适配与缓存，未复制上游程序。`tests/fixtures/haneoka-song-traits.json` 仅保存3条可追溯的必要事实样例（ID、标题、属性、序列、封面标识），其余测试为合成数据；不打包全量上游数据、游戏图片或算法。署名与公开接口使用要求沿用上文。本机 `fetched_at` 不是上游更新时间。

激奏类型小图标采用游戏资源中的 `UI/Texture/Tmp1/BattleLive/Icon_gekisou_{just,combo,luck}.png`。已逐一核对 [Haneoka 日服公开资源树](https://haneoka.org/api/v1/servers/jp/sources/tree) 与各文件的资源描述；三张 `Texture2D` 均为白色透明轮廓。回复图片仅给原轮廓添加本项目配色底框，并保留类型文字。素材在运行时按需读取并本机缓存，不提交到仓库；上游缺图时只显示文字。公开可访问不代表素材可再分发，游戏美术权利仍归原权利方。

## 角色卡与 SNAP 的资源核实

2026-09-28核对 Project Yume 的公开 MasterParsed 列表和122份成员/SNAP详情，以及网站 `useFilter-BIvyqgQJ.js`、`NonoCard-aiXKXz-b.js`、`cardSkill-B40radPu.js`。用来确认稀有度、类型图标URL、技能名称分类和指定状态的属性展示；没有复制上游JS代码或引入算分算法。各映射与实际样例见[卡牌说明](docs/更新说明-成员卡与支援卡查询.md)。上游没有在本次核实材料中给出可据此再分发游戏素材的授权；资源仍按需读取、缓存，不随源码提交。原生稀有度框和正式属性名称未确认，不能从截图猜资源或枚举。

默认成员列表技能摘要随 Haneoka 主快照；旧主资料模式另使用 [Haneoka 日服成员卡](https://haneoka.org/jp/zh-CN/member-cards/) 的公开 `api/v1/servers/jp/{cards,leader-skills,skills,gekisou-skills,skill-reference,bands}`，按release固定版本；来源与条件结构参考其公开 `scripts/build_api.py` 和 `src/lit/shared/skill-text.ts`。未复制上游程序代码；只保存少量注明来源的数值/描述测试样例及机制指纹，不打包游戏图像或全量数据。代码MPL许可不等于游戏素材再分发许可，继续遵守上述公开接口使用限制与署名要求。

新卡65/66的队长Lv.5效果与关联目标以 JP release `r-aab17ad7c67686ea5637`（2026-10-09）独立核对；最小事实夹具为 `tests/fixtures/haneoka_leader_parameters.json`，不含上游实现或游戏图像，不随发行包再分发。参数化摘要为本项目原创，读取已知机制的数据并验证完整日文描述，不下载或执行远程代码。

### 新成员卡摘要与 BD（2026-10-04 核验）

Haneoka JP release `r-d82e1e0581c690d38a27` 的 `cards`、`leader-skills`、`gekisou-skills`、`skill-reference`，与 Project Yume `membercards/61.json`、`62.json`、`64.json` 交叉核对身份。生日卡64的后台rarity=20，标题 HAPPY BIRTHDAY 26-27；映射仅用于成员，SNAP仍独立。

Lv.5：61队长为蓝色成员表演+102%、夢限大みゅーたいぷ成员表演+48%；62为该乐队成员表现+132%、JUST激奏成员表现+18%；64为一家Dumb Rock!成员表演+85%、紫色成员表演+40%。每项按自己的目标生效，不把两项之和当成无条件加成。64的LUCK激奏技能为期间条增量+200%、BAD以下扣血减少20%。受限摘要签名包含目标和机制，未知或变更仍拒绝。

短事实夹具 `tests/fixtures/haneoka_member_new_cards.json` 保留本次卡牌、Lv.5效果及所引用的公开条件，不含第三方实现代码。BD框配色参考用户提供的生日卡截图，为自行绘制渐变，不复制官方卡框资产。

### 维护者提供的彩蛋图片

`src/ournotes_bot/platforms/qq/assets/card-947.jpg` 为维护者在本次需求中提供的原始截图，仅用于 QQ 完整指令 `/查卡 947` 的固定图片回复，不进入 Our Notes 卡牌目录或查询事实。截图中的游戏角色与素材权利归原权利方，不代表本项目的代码许可覆盖这些素材。


## 统一美工素材

Noto Sans SC 与 Roboto 字体以 SIL Open Font License 1.1 分发，许可证在 `rendering/assets/*-OFL.txt`。仅字体随包分发；字体来源分别为 Google Fonts 的 ofl/notosanssc 与 ofl/roboto。

游戏稀有度、觉醒图标固定于 Haneoka JP release `r-d9e6e6e773a4bd9e090b`；原 note atlas/config 来自公开 `haneoka-gakuen/cassiopeia-plugin-our-notes` 提交 `cb2b6e470b6e99f30bfa81a88520cbd1c26055dd` 的 `src/assets/note-skins/skin001.*`。该上游代码为 MPL-2.0，不能据此推定游戏素材授权。项目仅保存 URL／校验和，运行时下载，不将游戏图片、atlas 或上游配置加入发行包。本地 note 绘制器为独立实现。

`visual_skill_contracts.json` 为本项目确认的中文短摘要及机制指纹，不包含上游 Master 或上游程序实现。机制变化不沿用旧摘要。字体缓存、原创渐变边框及布局由本项目实现。
