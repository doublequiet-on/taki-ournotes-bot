# 任务07自查证据

## 隔离副本完整登记C6 v2（2026-11-08）

基于07政策提交 `ec6992bcb0b41f45a25bbd636e9937b25ca580fe`，只读对照10 `ea1ed3000b6300678c7ede53c43aeefea990e08d` 的INSTANCE_RECOVERY末节。未改10宿主代码。v2完整保留quarantined实体，selection不能选它；其旧代际不伪装已应用删除，只有可信当前宿主隔离证明才能使其他已应用删除的副本通过本项检查。

17项合成正负例通过：完整v2及无隔离v2、旧备份选中（含伪标verified）拒绝、漏备份manual、篡改disposition/state拒绝、缺/漏/失效/IO错误的宿主证明manual、绑定/证据版本/selection/hash/真布尔检查、核验期间撤销权威、其他restorable仍未传播时不allow、隔离实物变化、空/重复/未知selection、未来隔离代际。实际加载固定旧 `8bdde8392ab79180ebf32a11fc05ed0a5190fd1e` 的history_retention源码验证v2权威拒绝，伪降version也拒绝。原读取器10项及07专项18项重新通过，架构/diff通过。原v1判定仍受回归保护。

本地脚本 `runtime/task07/test_quarantine_contract.py`，日志quarantine-tests.log、quarantine-v1-reader.log、quarantine-v1-regression.log；依AGENTS不提交新增测试文件。宿主证明明确是合成替身；没有执行生产隔离、销毁、实际宿主观察器或08/10组合验收。实际current_isolation未配置/无法核实仍manual。本轮不重跑全套或包构建。

## 当前权威读取收尾（2026-11-08）

基于07 `3686da76a939345690d556d7ddeb40198e15a979`，只读核查固定集成 `602a95f49fd059deb7a59377be74becb4d55e008`；聊天删除journal和发行清理journal不能充当历史完整副本权威。未读取生产账本。来源范围、明确缺项和08/10接线要求见数据合同“首版人工当前记录读取”。

合成专项10项通过：树外完整批准可读，缺文件/批准撤销/旧字节重放/读取期间换代均manual；恢复树路径和缺排除配置拒绝；重复键、坏JSON、NaN、journal不一致拒绝；缺WAL副本/传播未完成不allow；错误binding/政策/删除代际/证据版本拒绝；批准读取IO失败manual。原07专项18项重新通过，包含分区/归档/跨旧版本/低盘及恢复判定；architecture/diff通过。本次只改读取合同，未重新进行全套/包构建、宿主停写或组合激活验收。

本地脚本 `runtime/task07/test_authority_reader.py`（按AGENTS不提交）；日志 `runtime/task07/authority-reader.log` 和 `authority-regression.log`。全部用合成临时文件，TEMP/TMP指定本工作区runtime/task07/tmp。生产没有真实外部批准记录时仍manual；双读不是宿主持续性或防回滚证明。

## 任务12 P1修复复验（2026-11-03）

本次增量基于被审head `a63de717df487f896ff2182ea5bf6008672db4ad`，未合入其他任务。任务12原始 `runtime/task12-review/evidence/REVIEW.md`、`repro_writer.py`及日志保留不动；仅用合成临时库运行原脚本。修复前首次与重试均为storage_error；修复后首次注入故障仍为storage_error，重试为valid。

- 初始化身份、schema和版本在同一写锁事务提交；首笔观测失败不再撤销身份。初始化事务本身故障整体回滚，可重试。
- 本地专项15项通过（5项本次故障/并发/入口验证，10项原任务07回归）：同一writer和重新打开writer恢复；两个独立SQLite连接同时读到版本0后竞争创建，连续10轮各保留两笔观测且身份唯一；错误provider/normalization/time_kind、不可信版本0/4文件仍拒绝且字节不变；按需和后台实际仓库入口失败提示与重试恢复通过。
- 本次重新执行相关168项回归通过，覆盖正常分区、旧schema1/2/3、107/logical4、压缩归档、查询和低盘拒绝；架构检查及diff检查通过。未进行跨进程/掉电/生产验收。
- 本次日志与本地专项脚本位于本工作区 `runtime/task07/p1/`：`before.log`、`after.log`、`initialization.log`、`regression.log`、`test_initialization.py`。TEMP/TMP指向同目录tmp；新增专项脚本按AGENTS不提交，已有 `tests/test_haneoka_cutoffs.py` 已补充真实按需入口回归。

已有schema4缺失身份的旧故障文件仍需隔离人工审阅；本修复不补猜身份、不删除或重建已有文件。08仅消费上述被审head之后的修复增量，12按新head复审；原始基础和此前验证记录如下。

基础：03 `17212e5df76cf948aa990af571ed6daf5c66ec25`，其直接依赖191 `e1a86a6db1f327a6dd0cc53fd860c4ababf50923`。这是实现者自查，不是独立审查、生产验收或05/09/10组合验收。

环境：Windows、Python 3.12，复用入口仓库 `.venv` 的依赖，`PYTHONPATH=src`；QQ_APP_ID/QQ_APP_SECRET/AI_API_KEY/MOENOTES_OPEN_SECRET/BDON_OPENPLATFORM 在测试解释器内清空。临时目录显式指向本工作区 `runtime/task07/tmp`，全部输入为合成数据。

## 已执行

- 最终相关168项通过：`test_cutoff_history.py`、`test_cutoff_player_history.py`、`test_cutoff_archive.py`、`test_haneoka_cutoffs.py`、`test_moenotes_open.py`、`test_cutoff_query_v2.py`、`test_event_cutoffs.py`。已有 `test_provider_history_gate_and_total_capability` 扩展为先证旧store门禁，再证ProviderHistory接线和C3/C4读回，随PR提交。
- 本地专项18项通过：provider/unknown隔离，零/null/排名读回，关闭历史不删数据，低盘拒绝，按需与后台写入，实际当前/历史查询，时钟隔离证据保留，归档恢复续写，失败.partial保留并可续跑，dry-run不改源，合成跨副本投影及恢复拒绝矩阵。
- 专项跨版本用 `git archive` 提取固定03的src，独立Python子进程验证其schema1/2/3读写导出；v4旧读取器拒绝。原输入字节均保持不变。107(logical4)可继续写入，导出仍是4。
- 删除传播在合成DB、缓存、冻结JSON、压缩归档和备份的独立副本上验证：缺任一已应用摘要/代际即reject；重新构建的投影归档无已删除的字典项，原副本保留。保护的非valid观测和分数逻辑摘要保持不变。该实验不提供生产apply工具。
- `python -B scripts/check_architecture.py` 与 `git diff --check` 通过。
- wheel/sdist构建和 `scripts/check_release_artifact.py` 通过；wheel安装到隔离target，`-I`解释器校验模块路径并禁止socket.create_connection，运行相同专项18项通过。没有声称完成Linux断网安装、05/09身份组合或QQ验收。

新增专项测试文件按当前AGENTS留在本地，不纳入提交/PR。实际路径：`E:/CodexData/Codex/taki/work/taki-dev/.worktrees/plan-20261103-07/runtime/task07/test_task07.py`；日志同目录 `task07-tests.log`、`final-regression.log`、`installed-tests.log`。复现跨版本用例前，以固定03 SHA导出src到 `runtime/task07/base/src`。测试设TEMP/TMP为工作区临时目录后执行 `python -B runtime/task07/test_task07.py -v`。CI中的已提交回归不能替代这些未提交专项验证。

## 空间测量

| 合成观测数 | 原库字节 | 107压缩字节 | 完整导出字节 | 节省字节 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 65536 | 98304 | 65536 | -32768 |
| 500 | 1425408 | 471040 | 1425408 | 954368 |

固定四槽分数/玩家、重复结构、变化时刻，每组逐表逻辑摘要一致；不是生产抽样或容量估计。压缩保留身份字典，anonymous=false。脚本 `runtime/task07/measure_space.py`，完整JSON `runtime/task07/space-evidence.json`；小样本变大说明不得用“压缩”承诺空间释放。

## 失败定位与待验

最初使用沙箱系统TEMP时，缓存原子rename被拒绝，引出4项缓存失败。固定03在同环境复现同样1 failure＋3 errors；将TEMP/TMP限定到本工作区后168项通过。未为该环境问题改业务缓存语义。

扩展全套离线测试另行启动，启动后补过分区边界校验，不能将其当最终head全套证据；最终改动由上述相关回归及专项验证覆盖。完整全套/最终head CI状态由交付回执记录。Linux满盘/掉电/跨进程恢复激活、真实宿主副本覆盖和异地备份责任待10，生产删除权威及期限/字段政策待用户决定。
