# 管理范围与撤权接口（任务05 / C5、C6）

本包堆叠于 PR #192 固定提交 `d9240dffecee0f0a0066d5f70c8357fc9a398632`，沿用 [查询投递合同](QUERY_DELIVERY_SAFETY.md)，不修改02话题协议。定制产品装配尚未完成：05范围侧身份适配已提供；08/10受信控制器接线及用户批准政策未完成，08负责构造与 Console/RPC 页面。不能据此宣称定制版可激活。

## 给08：管理入口

`new Management(directory, identity, scopeStore)` 的第三参数显式选择定制范围约束；未传时保持个人版缺文件默认值与空群名单代表全部的原语义。08必须依据09验证后的产品合同注入，不得根据恢复目录是否存在、候选自报身份或配置缺失选择个人模式。

`ScopeStore` 位于 `koishi/src/scope-authority.ts`。`approved-scope.v1.json` 包含 schema=1、generation（正安全整数）、approval（批准标识）、binding（09拥有的不透明身份载荷），以及互不替代的 groups/c2c/announcements 三个范围。范围为 `{kind:'none'}`、`{kind:'all'}`、`{kind:'selected',ids:[...]}`；空 selected 拒绝，须显式 none。C2C IDs 是会话标识，不是群 ID。

- `inspect()`：候选、字节摘要及 allow/deny/manual 判定。缺失/损坏拒绝；未提供独立权威时 manual，不能外发。不能将管理快照公开给匿名客户端。
- `stagingTag()` 与 `stage(candidate, expectedDigest)`：认证管理恢复使用的 CAS token 和暂存入口。初次缺失 token=null；坏 JSON 可获取摘要后替换。同一文件排他创建锁、唯一临时文件、fsync、原子 rename；并发写入拒绝，锁冲突不自行删除。保存不构成批准，失败应回读，不能盲重试。CAS仅覆盖遵守同一锁的管理写入者；10恢复须先隔离外发并停相关写入。
- `reloadRecoveredConfiguration(guard)`：08/10通过各自认证恢复流程修复 settings.json 与 group-policies.v1.json 后，重新读取并校验两者。失败保持定制接纳关闭；无需QQ。此入口不批准 scope。
- `setControls({admissionStopped,sendStopped}, guard)`：使用现有 gateway epoch/revision guard；前者只停新接纳，既有已接纳查询可继续；后者停新 intent、公告发送，并禁止成功ACK投影上下文。两项是当前网关进程控制，不是跨重启批准记录；重启激活门交给09/10及待决定政策。
- `accepts`、`sendsAllowed`、`noticesAllowed` / `noticeScope`：分别用于新请求、既有请求许可、公告。定制模式每次检查文件一致性及独立批准权威；群策略文件缺失、错实例、损坏也关闭访问。旧群名单不替代定制三通道范围；已有全局开关及群 pause 仍可收紧。

receiver 在后端识别前过滤管理范围；每次普通查询 intent 前重新检查平台权限与管理范围。成功 ACK 前复查权限，后端 HTTP ACK 每次重试重新检查管理许可，使用既有 allow_context=false；账本事实照常提交。已授 grant 无法撤回，撤权之后可能仍收到其平台成功回执；不保证撤销已经发起的网络调用。迟到ACK不应当恢复被撤权上下文。

## 给09：唯一身份权威

09合同版本绑定 PR #194 的 `b29d69fd3d76b5be81610228ec3cfbb65fd044bf`，实际入口为该提交的 `deploy/product_identity.py::check_identity(candidate, expected, observed)`。本包不复制其身份解释或按旧备份自证身份。09后续版本须按实际SHA重新验收。

05提供 `koishi/src/scope-identity.ts::productIdentityAdapter(readTrustedCheck)` 及 `productScopeAuthority(readTrustedCheck, approval?)`。binding具体为09 `read_json` 严格解析得到的完整candidate对象；05只对它与受信回执中的candidate作深相等校验，不解释产品字段。它不是expected、observed或预检结果。不能拿A候选的allow去批准B的binding。

08/10提供的无参数回调必须从当前受信控制器读取 `TrustedIdentityCheck`，每次许可重新读取；不能从scope文件、恢复树、候选载荷或客户端RPC取得回执，也不能长期复用已失效的预检缓存。控制器用固定受信09实现校验，不执行候选内脚本。控制器必须从同一次读取的manifest原始字节实算摘要并严格解析candidate；expected在发行/备份/恢复树之外，observed来自独立测量。纯函数适配不提供这些信任来源，不是密码学签名验证。

回调字段：`candidate`、`verdict={status,reason,contract_version}`、`manifestSha256`（64位小写hex原始manifest摘要）、`checkerCommit`（40位实际受信09实现SHA）、`expectedRevision`/`observationRevision`（各64位受信输入快照摘要，绑定本次校验的expected/observed）。这些证据由控制器产生，05不从candidate补造。回调缺失返回manual/identity_check_pending，异常返回deny/identity_authority_unavailable；缺摘要、未知版本/原因/状态组合拒绝。

| 09判定 | 05身份判定 | 含义 |
| --- | --- | --- |
| allow / identity_verified | allow | 仅身份预检通过，仍执行独立C5批准 |
| reject / 09已定义拒绝原因 | deny，保留reason | 不调用范围批准 |
| manual / schema_incompatible | manual，保留reason | 保持隔离，不调用范围批准 |

身份evidence为 `c1/v1:checkerCommit:manifestSha256:expectedRevision:observationRevision`，范围最终判定额外保留 `identity_evidence`，原evidence继续表示范围批准证据。`productScopeAuthority`未传独立approval时返回manual/scope_approval_policy_pending，即使09为allow也不能激活。09不认证任意个人旧baseline：旧实例维持原流程，进入新合同须单独审阅；本包不增加旧包豁免。

`ScopeAuthority.approval(candidate,digest)` 必须查询候选恢复内容之外的当前批准权威，返回 `{outcome:'allow'|'deny'|'manual',reason,evidence}`。generation/approval/hash 都只是候选信息；合法旧备份中的高代际、合法身份、完整文件均不等于最新批准。测试中的合成权威只证明接口拒绝旧宽范围，不是生产批准实现。

## 给10：恢复判定

`ScopeStore.recovery()` 返回同一判定三态与原因、证据版本；10先隔离外发，调用09身份兼容检查，再消费本接口及07删除权威，最终组合激活门。allow 仅代表当前范围检查允许，不代表整套恢复完成；manual 必须留待人工，deny 不可激活。settings/group策略文件修复及加载也须完成；不要只看 scope 判定。持久投递 facts/unknown 不删除，本包无业务DB迁移。

用户仍须决定：每次启动人工批准，或独立最新批准锚点。两者的批准写入、撤销、持久化位置、恢复顺序及自动激活均暂停；本包没有替用户选择。09/08/10接线完成后必须重新用实际候选验收，不能以本包离线夹具替代。

## 固定图标

visuals.py 的固定 CardType 与 mission 图标使用禁止重定向的 urllib opener，响应仅接收200；Content-Length 超限提前拒绝，无长度时最多读取6,000,001字节，超过6,000,000拒绝。拒绝后不进入旧 aiohttp 回退；解码及像素限额保留。其他既有素材下载路径未扩展本次改动。

本包未实施登录跨连接限速/脱敏；如后续选择实施，独立小提交。没有生产数据、真实接收方范围、QQ消息或付费模型调用。
