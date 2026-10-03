# 查询升级 v1：实现与验收

实施基线为 PR #30 `131c54ae4c846ae06ba134d110230fb3bdad36ba`。分批分支用于审查；全部集成后统一发布，分批实现不代表已部署或真实 QQ 送达。

## 批次

| 批次 | 内容 | 进度 |
|---|---|---|
| P1 | 唯一实体、查询终态、Note 可信状态与旧缓存兼容 | 首批实现，309 项离线测试通过 |
| P2 | 查曲与分数表共享筛选、逐谱面过滤、稳定分页 | 316 项离线测试通过 |
| P3 | SQLite 历史、四服 300 秒采样、长期保留、容量状态 | 328 项完整回归通过；随后补强取消中重启检查，13 项目标测试通过 |
| P4 | 最多五个排名、每曲趋势卡、完整数值退路 | 336 项离线回归通过；已查看 430px 合成样图 |
| P5 | 本地字段短答、完整集合计数、列表编号 | 342 项离线回归通过；不触发模型或无关绘图 |
| P6 | 有限连续查询、隔离和发送确认后提交 | 18 项目标测试及 360 项完整离线回归通过；已查看列表及谱面 430px 样图 |

## 数据约定

Note 缺失用 `None`，源确认的零为 `0`。旧主缓存行保持原结构；新顶层 `chart_notes_known` 区分缺失与真实零，旧版本忽略扩展仍可读取。旧缓存未标注的零在新版本中不作已知 Note。

查询状态使用 `success`、`empty`、`unknown_entity`、`ambiguous`、`invalid_arguments`、`data_unavailable`、`unsupported`；覆盖缺失提示独立保存。唯一对象重名返回候选与可复制指令，列表允许多个对象。

## 验收记录

| 编号 | 实现／证据 | 状态 |
|---|---|---|
| ACC-01、ACC-02 | `test_query_correctness` 重名不同 ID、名称中的 EX／数字 | 已通过目标测试；其余解析边界随 P2 验收 |
| ACC-04 | `test_query_correctness` 来源缺失／真实零／旧缓存零／兼容整数行 | 已通过目标测试 |
| ACC-05、ACC-06 | 公共及专项结果状态；现有图文捕获链 | 309 项离线回归通过，外网尝试 0 |
| META-01～META-12 | `test_meta_filters`、`test_song_meta`、`test_song_traits`；共用解析／谓词／序列化；属性版本随记录捕获 | 新增 7 项目标测试及 316 项完整回归通过，外网尝试 0 |
| HIST-01～HIST-10 | `test_cutoff_history` 加既有 `test_event_cutoffs`：原始位置、整数、去重、冲突、迟到、断线、故障、四服与前台让位 | 328 项完整回归及 13 项补强目标测试通过，外网尝试 0 |
| RANK-01～RANK-04、PLOT-01～PLOT-08 | `test_cutoff_trends` 与既有榜线测试；`preview_cutoff_trends.py` 生成五线、零点、单点、重叠、超长整数样图 | 7 项新增测试通过；430px 五线／单点／长整数图已实际查看，图片均小于 0.7 MB |
| UX-01、UX-02 | `test_field_queries`：Note／等级／颜色／激奏／难度、完整集合计数、明确类型与 ID 的技能；图文列表使用页内编号 | 6 项新增测试及 342 项完整离线回归通过，外网尝试 0 |
| UX-03～UX-06 | `test_continuation`：页内 ID／难度快照、版本、四维隔离、TTL／容量、回执、取消、旧请求和排队续查失效 | 18 项目标测试与 360 项完整回归通过，外网尝试 0；平台面板定义更新，未写入远端 |

集成阶段另补了数字歌名带难度时优先于短 ID、模糊候选不忽略尾部条件、写盘失败不破坏当前榜、300 秒采样不累计网络耗时。364 项完整离线回归通过，外部网络尝试与仓库写入尝试均为 0。六批审查 PR 为 #31～#36，各批 Python 3.10／3.12 CI 已通过；最终发布 PR 仍须对其准确 HEAD 单独运行 CI 与安装检查。

## 完整 55 项对应

下列测试均使用替身、合成输入或已归档公开资料；“图”指已实际查看的 430px 离线图。源码入口和测试名是可复核定位，不代表已经部署。最终 CI、安装报告和准确 HEAD 以发布 PR 的 Checks 及交付证据为准。

| 编号 | 实现和离线证据 |
|---|---|
| BASE-01 | `test_event_cutoffs`：裸名次、T 名次、四服、hk→tw、别名及不回退日服 |
| BASE-02 | 全套回归；`test_menu` 面板限制及帮助；原图文与自然语言入口 |
| ACC-01 | `test_query_correctness` 同名不同 ID；`test_field_queries` 歧义不绘图 |
| ACC-02 | `song_identity`；完整名称、数字／EX 名称和短 ID；既有别名冲突测试 |
| ACC-03 | `test_meta_filters` 未知及冲突条件；长名称尾部条件不能被模糊匹配忽略；模型验证回归 |
| ACC-04 | `test_missing_and_zero_survive_cache_and_old_rows_remain_readable`；不适用技能短答 |
| ACC-05 | 公共／专项 status 与覆盖字段；未知、歧义、非法和不可用目标断言 |
| ACC-06 | `test_reply_pipeline` 同次捕获及回退；`test_event_cutoffs` 渲染失败不重查 |
| META-01 | `song_query.parse_filter` 与 `traits_match`；五种激奏及乐队／颜色组合 |
| META-02 | `test_shared_modes_or_and_order` 颜色 OR、跨维度 AND、顺序和重复次数 |
| META-03 | `test_song_meta` 默认全难度／单曲 EX、ALL 及每页 10／20／30 |
| META-04 | `test_each_chart_level_not_catalog_max` 按每条谱面过滤 |
| META-05 | 全量筛选后稳定排序和分页；跨页去重；分数表手机图 |
| META-06 | `test_single_song_cannot_bypass_filters` |
| META-07 | `test_coverage_songs_and_rows` 缺 traits 仅影响依赖字段的查询 |
| META-08 | 全候选缺失返回 data_unavailable，覆盖独立计首／条 |
| META-09 | `test_song_meta` 参考条件、单位、映射、Fever 及版本回归 |
| META-10 | 既有指标和排序语法保留；非法语法目标测试 |
| META-11 | `test_supported_natural_filter_is_local` 直接／本地入口等价、零模型 |
| META-12 | `test_roundtrip_and_full_filter_before_page`；续查保留全部规范字段 |
| RANK-01 | `test_matrix_and_canonical_public_server` 覆盖歌曲与排名省略矩阵 |
| RANK-02 | `test_invalid_ranks_are_rejected_before_dedup` 范围、类型、去重和五项上限 |
| RANK-03 | T 大小写、两种逗号、数字歌曲和 ID；五线及重叠合成图 |
| RANK-04 | 公开 hk 命令往返，内部 tw 和排名元组保持一致 |
| HIST-01 | 仅 `EventCutoffRepository.board` 的挑战榜快照写入；没有积分或永久榜输入 |
| HIST-02 | `test_identity_scopes_and_same_board_music_conflict`；既有切期和单曲有效期测试 |
| HIST-03 | `test_lossless_positions_missing_zero_and_no_player_columns` |
| HIST-04 | `test_dedup_flat_new_time_and_restart` 同时同内容去重 |
| HIST-05 | 新时间持平保留；失采缺口和超过 15 分钟断线，不补造停机点 |
| HIST-06 | 迟到／冲突／未知时间／时钟／有效期目标测试；没有可信修订标识时双方均不画 |
| HIST-07 | 独立临时 SQLite；2**130 整数及表字段检查，不存玩家资料 |
| HIST-08 | 独占锁、损坏文件、写盘异常保留原字节；当前查询仍可用 |
| HIST-09 | 四服、前台让位、归档发现、重启及 start/stop 目标测试；仅运行入口启动 |
| HIST-10 | 既有同 key 合并／总并发／429／Retry-After 测试；多排名共用整榜 |
| PLOT-01 | `test_default_per_song_cards_and_single_point_are_complete`；每曲五线／指定单曲 |
| PLOT-02 | 合成趋势卡保留活动图、封面、完整分数、身份、时区和来源 |
| PLOT-03 | `test_geometry_keeps_gaps_flat_down_and_big_integers`；零点和单点样图 |
| PLOT-04 | 当前源时间和历史末次时间分开；陈旧／冲突文案保留 |
| PLOT-05 | 整数坐标无浮点；五线、长整数、重叠手机图已查看 |
| PLOT-06 | `test_reply_budget_keeps_every_song_and_rank`；既有多命令预算和顺序回归 |
| PLOT-07 | 绘图／上传失败用捕获文字；`test_reply_pipeline` 发送不明不重试 |
| PLOT-08 | `test_numeric_only_skips_history_read_and_all_drawing` |
| UX-01 | `test_field_queries` 字段本地短答，原完整详情测试保留 |
| UX-02 | 明确成员／SNAP 类型与 ID；完整效果、Lv.5 及不适用说明 |
| UX-03 | 完整集合计数和部分覆盖；图文页内编号、ID 及翻页一致 |
| UX-04 | `test_store_scope_ttl_capacity_receipts_and_generation` 四维隔离、过期和重启 |
| UX-05 | 可见页选择、分数表歌曲＋难度、版本变化；不会重搜后猜第二项 |
| UX-06 | 18 项续查测试：迟到、失败／不明、取消、多列表、新请求在途和旧排队操作 |
| OPS-01 | 各批目标测试先执行；共享契约及发送边界运行完整离线回归 |
| OPS-02 | 外部网络尝试 0、仓库写入尝试 0；未连接真实 QQ 或模型 |
| OPS-03 | 原模型超时、修复、额度测试改用仍需模型的问句，性质断言保留 |
| OPS-04 | `check_release_artifact.py` 与 `check_installed_query_upgrade.py`；CI 分别隔离安装 wheel／sdist |
| OPS-05 | README、帮助、面板定义、L3／地图、来源及采样说明同步；发布仍与实现区分 |
| OPS-06 | 同机合成 4 服×3 曲×288 点的存储／读取／绘图测量；无送达或加速承诺 |
| OPS-07 | 最终发布 PR 的准确 HEAD 单独验收；生产历史与真实 QQ 手机验收待部署后执行 |

## 资源与视觉记录

Windows / Python 3.12 合成观测：四服各 3 首、每首 288 点、间隔 300 秒、每点 100 个 11 位整数，共 3,456 条，占库 5,214,208 字节（约 4.97 MiB）。连续写入期间进程工作集增加约 2.24 MiB，读取一首的五档 288 点 Python 堆峰值约 0.14 MiB；绘制一张五线图约 0.49 秒，进程工作集历史峰值相对绘图前增加约 84 MiB。数据源、硬件、字体和运行环境不同会改变数值，这些不是服务器实测或固定容量保证。

历史单曲读取最多 20,000 个点／缺口，绘图全局一次一张；全部活动保存在磁盘，不载入整个库。库长期保留、无自动删除，磁盘不足 512 MiB 暂停。实际查看了五线／单点／长整数趋势，以及歌曲列表、谱面、成员卡、SNAP、分数表的手机宽度图；合成趋势均明确标记。

## 发布前后边界

最终发布 PR 一次性包含 PR #30 和六批功能，分批 PR 不分别更新 main。发布配置位于 `deploy/query-upgrade-v1.env.example`，合并代码不会改写服务器已有环境。须合入四服 300 秒配置并确认运行进程后，分别验收持续积累、重启续写、低磁盘状态和真实 QQ 群／单聊、手机图及续查；面板定义也须在新运行版本支持后另行安装。

真实生产采样、服务器内存、QQ 接口回执和手机实际可读性尚未执行；离线测试、CI、安装包及预览不能替代它们。
