# L2 Platform / QQ — 消息接入与交付

## Purpose

把 QQ 事件接入已有查询能力，并按 QQ 的身份、时限、顺序、媒体和消息序号规则交付。SDK 与真实消息生命周期构成明确的系统边界。

## Responsibilities

事件归一化、群触发/去重、有限并发准备、批内顺序和可配置的批间排序；对同一结果选图，上传与被动发送；运行数据刷新和通知任务的接线；显式菜单管理入口。

## Non-Responsibilities

不实现资料 schema、查询业务或模型校验，不管理部署事务/通知账本。启动通知任务和转发群事件属于接线，通知机制归跨目录运维专题 [更新通知机制](../docs/更新通知机制.md)；公告模块自己使用 QQ SDK，不经过本模块的被动回复发送函数。

## Inputs / Outputs

输入为 QQ 事件、Settings、SongRepository；内部 `PreparedReply(text, image)` 输出文字及可选图片字节；外部输出为被动回复或显式菜单变更。群标识、消息 ID 和 `msg_seq` 不进入 Query 事实模型。
榜线准备结果还可带完整 `pages` 与文字回退标记；`_expand_replies` 在任何发送前核算趋势／排名分页和剩余被动回复数量，必要时改用同一份有界完整文字；放不下时明确拒绝完整发送，不先发送部分表。上传失败退当前页文字；媒体发送不确定时仍不重发。既有 QueryGate／ReplySequencer 与上限保持不变。

## Core Contracts

[qq.py](ournotes_bot/platforms/qq/qq.py) 中：
`QueryGate` 管容量，取消请求后仍等待后台线程释放容量；
`ReplySequencer` 管批间次序，等待超时可退化为无序；
`_prepare_reply` 捕获文字/图片；
`_deliver_reply` 区分上传失败与发送不确定性。
`DeliveryOutcome` 只有有效消息 ID 的平台回执才为 success；异常或缺失回执为 uncertain，明确错误码为 failure。`reply_commands` 提取普通身份键，串行处理同用户续查，并在发送确认后提交 `PreparedReply.context`。新完整查询立即清除旧上下文，批内多列表不建立默认上下文；取消和迟到代次不提交。
[menu.py](ournotes_bot/platforms/qq/menu.py) 的 `setup_menu` 是独立管理入口。

## Internal Subsystems

内部可按事件/调度、准备、交付、后台接线、菜单定向阅读，但暂不设 L2-2：它们共用一次 QQ 连接和一条回复生命周期，现有符号与测试可直接定位。文件长不是新增文档层的充分理由。

## Dependencies

`main` 动态进入 `run_bot/setup_menu`；QQ 调 Query、Rendering、Data 的谱面/刷新入口，并组装 [运行时通知](../docs/更新通知机制.md#入口与依赖)。核心业务不反向导入 QQ。受测试隔离的核心范围排除了 `qq/menu/update_notice`，不能把该测试扩写成“所有非 qq.py 文件无 SDK”。

## State / Side Effects

连接、后台任务、队列/信号量、排序票号和进程内群消息去重；网络上传/发送和菜单写入。主资料刷新及歌曲属性刷新由当前 QQ 运行时调度；没有已实现的独立通用后台调度服务。

## Failure / Degradation Boundaries

准备失败返回安全文本；绘图失败保留文字；上传失败或缺上传回执可发一次文字；媒体发送已经发起但结果不明时终止，不再退文字重发。群发现不授权主动通知。排序超时语义与严格顺序保证不同。

## Navigation to L2-2 / L3

直接进入 [qq.py 文件头 L3](ournotes_bot/platforms/qq/qq.py) 和 [menu.py 文件头 L3](ournotes_bot/platforms/qq/menu.py)，再查上述符号。本域当前源码仅这两个文件；`bot_info.py` 由 Query 管理，[update_notice.py 文件头 L3](ournotes_bot/update_notice.py) 属于跨目录运维通知主题，机制见 [更新通知机制](../docs/更新通知机制.md)。QQ 只负责群事件与后台任务接线，不把通知源码并入本域清单；运维任务入口见 [DEV_GUIDE 的 Windows 更新、回退、群通知导航](../docs/DEV_GUIDE.md#任务导航)。

## Relevant Tests

- [test_reply_pipeline.py](../tests/test_reply_pipeline.py)：同源选择、回退、不盲重发、通知重连接线。
- [test_multi_command.py](../tests/test_multi_command.py)、[test_reply_order.py](../tests/test_reply_order.py)、[test_stage5_limits.py](../tests/test_stage5_limits.py)：容量、顺序、超时与取消。
- [test_media.py](../tests/test_media.py)、[test_menu.py](../tests/test_menu.py)、[test_observability.py](../tests/test_observability.py)：事件/菜单/日志隐私。
- [test_platform_boundary.py](../tests/test_platform_boundary.py)：核心独立性。

人工操作只参考 [LOCAL_QQ_TEST](../LOCAL_QQ_TEST.md)。

## 当前实现与边界

实现为 platforms/qq/qq.py 与 platforms/qq/menu.py，文件名和生命周期不变；platforms 只提供命名空间。main 仍延迟导入实际入口。QQ logger 显式保持 ournotes_bot.qq，更新器据此捕获“已上线”；不按目录变化改变运行日志协议。

向上阅读：[仓库 L1](../L1.md)。源码文件头提供唯一 L3，具体行为与字段以实现为准。
