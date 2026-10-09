# 第4批：Haneoka 分数表接入前置合同

> 阶段记录：本文保留接入／研究时的合同与证据。2026-10-09 已确认的默认来源、原生普通／激奏参考分数表、活动静态关联及时间策略见 [当前统一说明](HANEOKA_UNIFIED.md)；本文旧默认值和当时待办不直接代表现状。

> 2026-10-09：本地默认入口及用户确认的 Haneoka 普通／激奏原生 meta 改用见 [统一来源说明](HANEOKA_UNIFIED.md)。本文保留先前合同／研究及显式旧源说明；其中原默认值、静态关联未实现及必须复现 Moenotes 模型的结论不再表示当前默认行为。生产发布须另行验收。

**状态：等价统计输入尚未确认，接入与切换未完成。** 2026-10-08（香港时间）复核公开接口与固定源码后，仍不能将 Haneoka 输入交给现行有限求值器并保持同一语义。本批保留 `OURNOTES_META_SOURCE=moenotes`、TW 共通参考范围、自由／激奏双榜、八类排行及单局兼容；不修改运行代码、实际配置或缓存。本文是核验结论和可转交的输入需求，不是 Haneoka 已承诺提供的接口。

## 核验范围与证据

核验源码版本为 [Haneoka b419e6c0a21a0061649e8fc557abea0218512f5e](https://github.com/haneoka-gakuen/haneoka/tree/b419e6c0a21a0061649e8fc557abea0218512f5e)。公开 [OpenAPI](https://docs.haneoka.org/openapi.json) 本次有 130 个路径，正文 SHA256 为 `a31e73481126eea58847d93644bf84d363f4c973e10cb5b3f4f624723d702d8a`。路径数量不证明不存在其他未公开能力；本结论只覆盖已核验的公开合同、资源及源码。

| 服务器 | 固定 release | song-meta 覆盖 | Team Builder 歌曲数 |
|---|---|---|---|
| jp | `r-2d7fe14470e2e90b2aae` | 86 首／344 谱面 | 86 |
| intl | `r-8da1a1f914879ac54d8a` | 87 首／348 谱面 | 87 |

分别匿名读取 `/api/v1/servers/{server}/release?projection=identity`，再固定该 release 读取 `/api/v1/servers/{server}/song-meta?release=…` 和 `/api/v1/team-builder/{server}?release=…`。响应 release/source 头与各服 identity 一致，完整 JSON 通过解析。本次 Team Builder 下载只用于审计，不成为机器人启动依赖。

四份完整数据中均未出现 `seeds/offSeeds/weights/ranges/rangeWeights/rankBonusPercents/scorePerfect/skip/unplayable/sourceSha256` 这些现行统计字段。仅靠字段名缺失不能排除其他命名的等价合同，因此进一步核查了实际语义：

- `song-meta` 同时有普通和激奏参考输出，但普通参考使用汇总技能倍率与共享技能窗口；例如 intl 100001 EXPERT 的倍率 2.5、技能窗口 10 秒，时长取同曲各难度最后判定时间最大值。它不是现行 BGM 优先／谱面时长回退及种子均值输入。
- 同一谱面的激奏参考标明不应用普通技能、采用历史 JUST／LUCK 近似口径；`nativeScore` 为 null。不能因输出叫“激奏”就当作现行 `seeds` 模型等价结果。
- [参考请求实现](https://github.com/haneoka-gakuen/haneoka/blob/b419e6c0a21a0061649e8fc557abea0218512f5e/src/lib/team-builder/data/reference-request.ts) 限制固定队伍、normal 模式及单一谱面场景。`meta-reference/{server}/{releaseId}/{recipeSha256}/{requestSha256}` 是内容寻址参考求值合同，不是完整双榜统计目录。未为未知 recipe/request 哈希构造请求。
- Team Builder 有技能、判定、成长、阈值和原生规则证据等生成素材，但这些不能直接充当已生成的五位置权重或种子统计。原生规则的 method/ABI 指纹也不等于现行统计生成算法的指纹。完整原生模拟器移植属于另一项模型更新，不能混入换源。
- intl 的 87 首与既有 TW 参考数量相同，并不证明集合、区域生效时间和后续版本覆盖等价；还需正式 TW 比较池及实体映射合同。

构建语义依据：[scripts/build/api.py](https://github.com/haneoka-gakuen/haneoka/blob/b419e6c0a21a0061649e8fc557abea0218512f5e/scripts/build/api.py)。本次没有复制上游实现、补造统计或把 Haneoka 身份写成已验证的 Moenotes 指纹。

## 给数据提供方的最小输入需求

以下内容可转交 Haneoka 开发组，本轮**未对外发送消息或创建上游 issue**。字段名沿用 Taki 当前输入以明确语义，不要求服务端采用同名字段或本文指定 URL；若已有等价资源，请提供正式路径、schema、字段映射及版本保证。

> 我们希望将 Taki 的分数表数据入口迁移至 Haneoka，同时保留现行 TW 共通比较池与有限统计模型，不改为固定队伍模拟。请确认能否提供版本固定、可完整获取的等价统计快照，以及独立参考结果。若暂不能提供，请明确哪些输入缺失；现行来源会继续保留。

| 输入组 | 必须明确的内容 |
|---|---|
| 身份与完整性 | TW 参考范围、资源服务器映射；schema/release/source、内容摘要、生成或观测时间；分页完整性与完整 song/score ID 集合；新增／移除／重复 ID 的规则。不能把 JP 或 intl 数量相同视作 TW 等价证明 |
| 事实与筛选 | `musicId/scoreId/difficulty` 唯一关联；四难度、等级、乐队与颜色、激奏三段任务、审核别名可绑定的标题；Notes 的判定音符口径、首／末判定毫秒、主／最大 BPM。缺失与 0 区分，保证混合难度、条件筛选和完整比较池 |
| 时长与阈值 | BGM `durationMs`／`lengthMs` 与各谱面 `musicLengthMs` 的独立口径、单位和回退规则；D/C/B/A/S/SS 自由 `requiredScore` 与激奏 `battleRequiredScore`，阈值取整及人数换算依据 |
| 统计模型 | 等价 `deck.model.power`、`deck.kinds`（effectType、durationMs、目标及条件／次数限制）、五位置定义；统计格式、真实生成算法／版本／源码指纹、技能种类索引语义。原模型及再发布方血缘分别保留 |
| 自由与激奏样本 | 每谱面 `offSeeds` 与 `seeds` 的完整样本集合、选择／数量／顺序／均值语义；每样本 `score`、适用时的 `scorePerfect`；按技能种类 × 五位置的 `weights`。固定队伍最终分数不足以反推这些输入 |
| 三段任务 | 三段 `ranges` 的任务类型、五档 `rankBonusPercents`；每样本各段 `rangeScore/rangeScorePerfect/rankBonus`；技能种类 × 五位置 × 三段 `rangeWeights`；缺项、边界、取整和 JUST/PERFECT 差异 |
| 可用性 | `positions/skip/unplayable` 的确切语义、未知与不可游玩的区别、每谱面缺项状态；不得以 0 填补缺权重、阈值或时长 |
| 独立参照与许可 | 绑定同一输入摘要／模型身份的独立输出：基础系数、五位置均值权重、120 顺序计数、排行与并列次序、前沿。注明上游血缘、再分发和使用许可；未知算法不能借旧 SHA 白名单通过 |

当前输入投影见 [moenotes_music_data.py](../src/ournotes_bot/sources/moenotes_music_data.py)，数学消费见 [meta_model.py](../src/ournotes_bot/query/meta_model.py)，条件和双榜请求见 [meta_parameters.py](../src/ournotes_bot/query/meta_parameters.py)。当前行为的唯一运行说明仍为 [META_OPEN](META_OPEN.md)。

## 合同满足后的实施和验收

1. 先取得正式合同与一份完整固定版本统计快照，逐项映射上述输入，确认 TW 比较池及四难度身份。只有原生数据或参考分数时不越过此门槛。
2. 使用明确的新配置值，例如候选 `haneoka-stats`，接入现行 `MetaRequest` 路径；**该值目前未实现，不能配置使用**。既有 `haneoka` 继续表示旧预计算分支，不能将它改名或重新解释为本批已完成。仅修改配置模板，生产切换另行授权。
3. 增加独立完整快照缓存，固定版本并校验响应身份／内容摘要后发布；不跨版本拼字段、不自动访问旧联网源。事实快照与完整兼容模型快照分开处理：缺算法仍可显示经验证的事实排行，计算排行明确不可用；恢复时重解析完整同语义请求。
4. 复用现有有限求值器和 [独立对拍入口](../scripts/verify_meta_reference.py)，为新来源增加真实身份校验，不能伪装当前 Moenotes provenance 来复用白名单。覆盖自由／激奏、四难度、三段任务、0／缺失值、BGM 缺失回退、SS 阈值、取整、120 顺序与并列排序。参考结果必须在独立上游环境生成，不能调用 Taki 自己生成预期值。数字容差沿用现有对拍门槛；候选集合、顺序与计数精确一致。
5. 验证效率／活动／速度／等级／Notes／最长／最短／跳过得分及单局兼容；自由／激奏双榜、单场景、颜色／任务、混合难度、分页／续查全部保留。版本或来源变化使旧续查失效；缓存损坏、限额、过期、写盘失败和未知指纹明确回退或不可用。
6. 同语义输入生成图文，对照旧布局与像素预算，再核实真实图片。完整离线回归、发行包与隔离安装、网络出口检查通过后才进入另行授权的生产／QQ 验收。

目前只完成本批前置核验与输入需求整理。新适配、等价统计独立对拍、视觉冻结、生产切换、真实 QQ 验收均未执行；不因此删除现行分数表或共享数值工具。第5批退役同样不能移除它仍依赖的旧数据出口。
