# 恢复范围批准与当前删除权威组合

08在a384/B2之上接收05/07固定增量，提供受信审批入口和07→10预检接线。真实管理员、认证来源或宿主当前权威未配置，不能用本候选恢复生产。本文是作者自查，12独立审查另计。

## 固定版本与所有权

| 输入 | 完整版本 | 本轮处理 |
| --- | --- | --- |
| 08基线 | a384335f69e0514190c51c5dfce5808b55a19f60 | 保留业务FD接线及B2 |
| 05 | 054a101276f5ca451ad78d9397531534a2ce0249 | 仅a9c27d7eb94833df713995b8e6f9a85fc45ea0d5后的范围源码、既有probe及合同增量 |
| 07 | 8bdde8392ab79180ebf32a11fc05ed0a5190fd1e | 仅3686da76a939345690d556d7ddeb40198e15a979后的四文件增量；保留历史分区及03投影 |
| 10宿主依赖 | 23839bc05850009131f9c73311b1e6ba42295e38 | 外置宿主固定接口依赖；测试直接导出Git文件，不整包合入08 |
| 09身份实现 | 3dda6d29955328777f7e526c21bfd2828dbbea8a | 当前deploy/product_identity.py与该对象相同 |

05/07业务文件均与上述固定对象相同。05旧导航的“政策未决定”按本组合更新，不覆盖08其他导航。10相对a6e102a5b94be6af65652985fe0063491352cbf5的新时序是先构造/测量RecoverySession，再approve_start签发原30秒启动票据；startup_timeout明确1–300秒、默认120。双角色commit仍再次核对，不续签旧票据。

## 受信入口

`koishi/src/recovery-scope.ts::createRecoveryScope(directory, identitySession, sources)`在业务网关启动前构造。返回scope供既有startRecovery注入，begin/confirm供受信操作者流程，checkScope供10预检，read().revision供10的scope代际。不是Console RPC、HTTP服务或磁盘allow文件。

受信宿主提供readAdministrators、readOperator、readRevocationGeneration、readDeletionReview。个人subjects只能来自用户指定，定制subjects只能来自接收方指定；未配置/空名单拒绝。管理员记录的identity_evidence绑定当前09身份，product/instance必须匹配认证操作者。会话标识由认证来源产生；Console普通登录、请求JSON、10确认布尔不能产生这些回调或批准。

begin撤销前次批准、返回完整范围审阅和内存ticket；人工看过后confirm同一个对象，重读操作者、管理员、身份、删除证据、候选及撤销代际。跨会话/产品/实例、伪造/重复ticket拒绝。每次scope使用重新核对；来源消失再恢复不自动恢复批准。宿主必须在权限/身份/删除记录/认证变化之前隔离外发、递增独立撤销代际并调用invalidate；轮询不是ABA防护。

## 07到10读取合同

`deploy/recovery_authority.py`提供三个窄函数：

- current_authority_reader把07读取器包装成10要求的无参回调；路径为空或无独立批准回调时未知，每次重读。
- deletion_scope_review执行07 check_recovery，allow后再核当前记录。仅allow产生review_sha256；SHA256输入为UTF-8 JSON，sort_keys=True、separators=(',',':')、ensure_ascii=True、allow_nan=False，字段为format='taki-scope-deletion-review/1'、candidate、authority、decision。不能拿journal摘要或任意evidence_version当05删除证据。reject/manual保留07语义。
- check_current_recovery接受10固定recovery_preflight函数及实时05 checkScope/09身份/隔离读取，把07真实checker和无参权威回调传入10。不自动启动；check_scope必须调用活的05对象，禁止复用JSON allow。

顺序：10隔离/独立测量 → 09身份/07删除审阅 → 指定管理员05批准 → 08 check_current_recovery → 10 ManualRecoveryReview → 构造测量RecoverySession → approve_start → 两角色等待 → commit再次审阅 → 既有08业务入口。

## 验证与边界

runtime/task08-authority保留本地脚本、固定10 Git源和日志；新增测试不进PR。Python通过本地stdin/stdout测试通道实时读取实际Node05对象，真实09/07/10检查执行；认证/管理员、隔离事实和独立批准来源为显式合成数据。该测试通道不是交付的宿主认证/传输实现。

11项组合测试覆盖未配置/非指定管理员、个人/定制、错误产品/实例、会话变化、ticket伪造/重放、撤销/身份失效、宽范围恢复、树内记录、删除版本不符、副本未验证、权威漂移及完整指纹。首轮夹具代际倒退正确被拒绝，改为单调递增后通过，失败日志保留。固定10新时序4项测试通过（合成宿主/时钟），网关152项、相关历史50项、TypeScript及架构通过。

a384真实HTTP/Receiver/FD生命周期证据只绑定a384，不冒充本轮新宿主全链复验。未运行QQ、付费模型、产品整包、真实宿主恢复或新浏览器验收。

## 09/10/12剩余接口

10仍需提供真实管理员认证入口/会话源、树外控制路径/owner/mode、单一撤销代际与独立批准hash来源、全部排除树/副本、隔离/停写与九项当前代际，以及跨Python/Node的活05调用生命周期。技术路径由10提出；本轮不造服务、猜路径或把Console密码变成批准权限。未知来源保持manual/deny。

09装配需携带recovery-scope编译模块、deploy/recovery_authority.py及固定10宿主依赖，测量完整来源；保留a384的koishi/recovery.cjs、B2和cache适配。08不含10全部部署文件，不能把测试导出目录当正式宿主。不改旧发行器门禁。12可独立复核固定增量和这两个组合文件。

用户已选择：定制首版本地已验证查询，排除自由/主动聊天及付费模型；材料与同Release交付但不发布；不可改写旧备份隔离禁止恢复、按既定期限处理，本次不删除。这些是09/11发行与10恢复输入，本轮不生成最终selection或发布/清理。真实管理员标识未给，批准人仍未配置。
