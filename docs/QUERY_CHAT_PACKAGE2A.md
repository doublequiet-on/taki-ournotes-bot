# 查询聊天包 2A：确定性歌曲指标

本包依赖包 1 的固定快照和 ACK 接口，实现 v3 的 Meta 查询、单曲卡片与排行分页。仍处于开发验收阶段，正式上线依赖包 5；知识库、原生模型工具和加权额度不在本包。

## 查询入口

`/问` 在旧字段短答和模型路径之前识别有限语法，例如：

- `/问 家团最短的歌是哪首`
- `/问 家团 EX 27级以上最短的前3首`
- `/问 国际服 EX BPM至少180按NPS降序前10首`
- `/问 ID100080 EX 谱面时长`

默认日服 `jp`，国际服用 `intl`／国际服。榜线的 `hk/kr/en` 不映射成 Meta 区服。乐队复用审核别名；单曲用明确 ID。未知条件返回提示，空集合不放宽条件，不调用模型补答案。

“最短／最长”是歌曲各难度最大末判定时刻形成的**谱面时长（末判定）**，单位秒。指定 EX 不改变歌曲级时长。等级保留小数；Note 数来自 Master fullComboCount；NPS 使用歌曲时长分母；BPM 至少 X 表示最低 BPM ≥ X。等级、Note、NPS、首／最低／最高 BPM 必须明确难度。音源长度不参与谱面排行；未来发布时间不自动排除。参考得分、效率、推测定数和配队不可查询。

## 合同与来源

纯类型和白名单位于 `domain/meta_query.py`。`MetaQuerySpec` v1 只允许 song、jp/intl、规范难度、注册字段及匹配的运算符：最多 8 个 AND 条件、3 项排序（含最后的数字歌曲 ID 升序）、1–50 条结果。`from_dict` 拒绝未知键及错误类型；`execute_many` 最多 4 个规范查询，供后续工具接口复用。

`sources/haneoka/song_metrics.py` 复用公共 transport。每次新查询先在线读取 `/release?projection=identity`，再读取固定 release 的 catalog、song-meta、songs 索引、songs 批次和 bands。批次至多 100 ID，校验 missing、数量、ID 集合及每个响应的 release/source。单响应不超过 8 MB、总读取不超过 32 MB，共用 18 秒读取预算。同版资源可以复用每服一份内存快照；身份读取失败也不能使用旧结果冒充当前数据，不写持久缓存。

`MetaResult` 保留规范请求、区服、release/source、检查时间、不可变行值、覆盖和缺失字段。状态为 ok / empty / partial / source_unavailable / source_incomplete / unsupported_field / invalid_argument。共享 `QueryResult` 仍使用现有终态枚举，partial 有自己的文本与覆盖标记，不冒充完整排名。

覆盖计算先判断全部已知条件：已确定不匹配者不计缺失；未知乐队归属或过滤值不算确认匹配；未知排序／展示值保留为未知且后排，不能填零。如果所有排序值缺失，不给出第一名。跨难度共享时长不一致直接返回 source_incomplete。

## 展示与续查

`rendering/song_metrics_layout.py` 使用既有主题、字体、封面加载器与编码预算，缺图走占位。先计算完整行高度，再按页面高度和字节预算分组，每页一张汇总图；序号全局连续，不按每首歌拆图。文字与图片消费同一份结果。

PreparedReply 标记 `song-meta/1` 版式，包 1 快照保存已编码页面及原始行值／来源。后端 ACK 仅开放该页实际展示的序号；`/问 下一页`、`/问 选 6` 和详情读取原结果，不重新查询当前来源，也不把 intl 歌曲 ID 交给旧 JP 详情入口。改变条件或难度须发送新查询。绝对 600 秒期限和既有消息／图片预算保持不变。真实 QQ 引用字段的启用状态仍遵循包 1 契约。

## 验证入口

已存在的 `tests/test_query_refactor.py` 覆盖本地入口、每次重新检查来源、失败不复用旧结果、稳定排序和参数边界；`tests/test_continuation.py` 覆盖真实后端准备／ACK／分页／选择与区服保持。包 0 原始 jp/intl 捕获用于本地来源合同与图片测试，不打包进应用。来源验证与 QQ 客户端送达是不同证据层；公开源检查不代表生产上线。

构建后运行 `scripts/check_release_artifact.py`，隔离安装后运行 `scripts/check_installed_query_upgrade.py`；新增四个模块均列入安装检查。新增源码依赖须通过 `scripts/check_architecture.py`。
