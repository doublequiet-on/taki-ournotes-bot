# Backend撤权与存储关闭生命周期

后续已接10固定a6e102a的实际跨进程业务入口，当前范围与残留见[FD会话接线](RECOVERY_SESSION_WIRING.md)。下文保留本阶段版本证据。

本轮基线 `37af64177389c4faa944d4dd7a9360f73980aaa6`。完成08进程内的writer排空接口，不定义10的跨进程会话、双角色屏障或宿主协议，不接PR202。

## 受信调用顺序

组合根继续使用已有 `build_backend/build_query_backend(..., assert_recovery_current=...)`。断言现在贯穿构造前、initializer前、HTTP入口/请求体解析后及100ms事件循环检查；返回None为通过，异常或其他返回值导致粘滞撤权。100ms不是实时上界，读取器须是受信的当前会话检查，不能把旧checked报告固定返回为成功。

从已启动的aiohttp应用取得同一Backend对象：

```python
from ournotes_bot.application.backend import RECOVERY_BACKEND

backend = app[RECOVERY_BACKEND]
backend.revoke_recovery()                  # 同步关入口并发停止信号
state = await backend.quiesce_recovery()   # 业务writer确实结束；ACK尾窗仍开
# 10在其受信执行边界等待网关retire、发送/ACK排空；本接口不代签该证明。
closed = await backend.close_recovery()    # 封闭尾窗，等待在途HTTP，再关闭存储
await runner.cleanup()                    # 停HTTP监听，重复清理复用同一关闭任务
```

必须从应用事件循环的控制协程调用，不能在一个受本middleware跟踪的HTTP handler内部等待自身排空。跨线程只能由受信宿主安排到该loop；本轮不提供远程管理端点。正常aiohttp cleanup也复用同一关闭序列。**不能对运行中的app单独调用backend.close()绕过lifetime**；该方法仍用于未启动的组合构造失败/既有内部所有者清理。

`quiesce_recovery()`返回 `business_writers_stopped`、`receipt_tail_open`、`pending_terminations`；`close_recovery()`返回 `stores_closed`、`pending_terminations`。这些是本进程观察，不是身份、范围或恢复批准。pending_terminations非零表示终止事实仍未确认，保持既有02诊断/恢复语义，交人工处理，不能因“线程已停”改写为干净恢复。

## 实际停止与等待范围

- revoke先设置closing/ready、拒绝Operations新任务，停止initializer、reaper和observation；同步取消RuntimeTasks/采样、价格刷新、模型管理probe和准备任务。共享查询执行检查也拒绝在撤权后开始下一步。
- initializer、观察刷新和查询的真实线程沿finish_thread/drain_task保留所有权。取消只发停止信号，不能让数据库先于线程关闭。既有ModelScheduler在途执行、管理任务worker以及已进入的HTTP handler全部等待结束。
- RuntimeTasks、ModelRuntime、ModelAdmin、Operations拆出最小停止/排空阶段；quiesce时保持ledger、usage、agent、管理审计和知识存储打开。RuntimeTasks关闭等待其实际采样/刷新任务，不重排采样语义。
- 撤权期间只开放health、503 ready、gateway retire和严格的receipt尾事件。新查询、模型许可、公告claim、管理动作及新intent均拒绝。delivery尾窗仅ack/aborted/batch_finished；公告继续由既有event合同验证ack/aborted。ACK强制allow_context=false，事实可持久化，旧话题/派生intent不能复活。
- 尾窗内不以ACK触发常规expire维护；必要的ledger事实、撤销、已开始操作结果/用量审计及notice观察尾写仍允许。既有发生中的不可撤销写入必须结束，不能声称撤权瞬间磁盘零写入。
- close_recovery先封闭尾窗，再等待已进入HTTP及全部业务排空，最后依既有顺序关闭模型/知识、operations、ledger、diagnostics和DataOwner锁。存储关闭后只保留无存储依赖的health响应，其余请求503。

关闭任务归lifetime所有，多个调用者复用同一任务。等待者连续取消不会分离真实线程或跳过关闭；取消在所有者结束后传播。若真实线程不退出或排空失败，不得解除宿主writer隔离；10仍负责外层进程/cgroup及失联处理，不能把wait_for超时当作成功停止。

## 验证

证据在原08工作区 `runtime/task08-recovery`。本轮新增本地驱动 `lifecycle.py`，不提交新增测试文件：

1. 真正阻塞的initializer线程；当前权威撤销后新HTTP拒绝；关闭等待者两次取消仍等线程，最后才释放DataOwner。
2. 实际RuntimeTasks、Operations worker、observation与查询线程同时阻塞，模型价格及probe合成任务执行审计尾写；撤权后新任务被拒绝，所有真实所有者退出后才关库。
3. 真实aiohttp→SQLite delivery intent已授予；撤权/排空后迟到成功ACK仍记录，send_allowed=false且无上下文；旧权威恢复不能重新接纳，新intent拒绝；最终封窗后ACK拒绝。
4. 已进入但尚未收齐请求体的HTTP请求阻止提前关库，收齐后以503拒绝，随后关闭完成。

以上4项通过；最终源码162项相关既有回归通过，覆盖HTTP共享合同、initializer/observation取消、管理操作、模型probe及原生模型运行。源码架构门禁通过。完整日志与最终SHA绑定保存在delivery记录。没有真实模型供应商、QQ、WSL、生产数据或新的组件浏览器验收。

保留首轮夹具失败：线程内误用主线程SQLite连接、选中尚未到点的对齐刷新；修正为独立合成writer和实际立即刷新的traits任务，没有降低关闭顺序断言。既有回归还检出了新增“终止记录未确认即不关闭”会改变原02收尾合同；已移除该额外判断，改为如实返回pending_terminations，不跳过原测试。

## 仍交10/00的接口

本进程内Backend生命周期缺口已实施。仍需10给最小可信跨进程当前会话与双角色无业务屏障，在同一会话中先撤销双方，再等待网关尾写并调用close_recovery；10首测量P2、真实宿主失联/强停、全writer清单不在本轮修改范围。05/07/09权威读取与固定来源保持[前轮交接](RECOVERY_BUSINESS_GATE.md)，本轮没有制造批准、代选selection或放行恢复。
