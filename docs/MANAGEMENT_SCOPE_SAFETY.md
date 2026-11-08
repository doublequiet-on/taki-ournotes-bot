# 管理范围与撤权接口（任务05 / C5、C6）

本包最初堆叠于 PR #192 的 `d9240dffecee0f0a0066d5f70c8357fc9a398632`，现吸收其固定修复提交 `c6048b58ad054979be29542176776bd0cbe3551a`，沿用 [查询投递合同](QUERY_DELIVERY_SAFETY.md)，不修改02话题协议。定制产品装配尚未完成：05范围侧身份适配已提供；首版人工范围批准接口已提供，08/10受信控制器接线未完成，08负责构造与 Console/RPC 页面。不能据此宣称定制版可激活。

## 给08：管理入口

`new Management(directory, identity, scopeStore)` 的第三参数显式选择定制范围约束；未传时保持个人版缺文件默认值与空群名单代表全部的原语义。08必须依据09验证后的产品合同注入，不得根据恢复目录是否存在、候选自报身份或配置缺失选择个人模式。

`ScopeStore` 位于 `koishi/src/scope-authority.ts`。`approved-scope.v1.json` 包含 schema=1、generation（正安全整数）、approval（批准标识）、binding（09拥有的不透明身份载荷），以及互不替代的 groups/c2c/announcements 三个范围。范围为 `{kind:'none'}`、`{kind:'all'}`、`{kind:'selected',ids:[...]}`；空 selected 拒绝，须显式 none。C2C IDs 是会话标识，不是群 ID。

- `inspect()`：候选、字节摘要及 allow/deny/manual 判定。缺失/损坏拒绝；未提供独立权威时 manual，不能外发。不能将管理快照公开给匿名客户端。
- `stagingTag()` 与 `stage(candidate, expectedDigest)`：认证管理恢复使用的 CAS token 和暂存入口。初次缺失 token=null；坏 JSON 可获取摘要后替换。同一文件排他创建锁、唯一临时文件、fsync、原子 rename；并发写入拒绝，锁冲突不自行删除。保存不构成批准，失败应回读，不能盲重试。CAS仅覆盖遵守同一锁的管理写入者；10恢复须先隔离外发并停相关写入。
- `reloadRecoveredConfiguration(guard)`：08/10通过各自认证恢复流程修复 settings.json 与 group-policies.v1.json 后，重新读取并校验两者。失败保持定制接纳关闭；无需QQ。此入口不批准 scope。
- `setControls({admissionStopped,sendStopped}, guard)`：使用现有 gateway epoch/revision guard；前者只停新接纳，既有已接纳查询可继续；后者停新 intent、公告发送，并禁止成功ACK投影上下文。两项是当前网关进程控制，不是跨重启批准记录；重启激活门交给09/10及独立人工批准。
- `accepts`、`sendsAllowed`、`noticesAllowed` / `noticeScope`：分别用于新请求、既有请求许可、公告。定制模式每次检查文件一致性及独立批准权威；群策略文件缺失、错实例、损坏也关闭访问。旧群名单不替代定制三通道范围；已有全局开关及群 pause 仍可收紧。

receiver 在后端识别前过滤管理范围；每次普通查询 intent 前重新检查平台权限与管理范围。成功 ACK 前复查权限，后端 HTTP ACK 每次重试重新检查管理许可，使用既有 allow_context=false；账本事实照常提交。已授 grant 无法撤回，撤权之后可能仍收到其平台成功回执；不保证撤销已经发起的网络调用。迟到ACK不应当恢复被撤权上下文。

## 给09：唯一身份权威

唯一支持版本为09 PR #194 的 `3dda6d29955328777f7e526c21bfd2828dbbea8a`，唯一生产判定入口为其 `deploy/product_identity.py::check_scope_binding(binding, candidate, expected, observed)`。该函数每次先调用 `check_identity`，再检查binding；09负责身份解释以及 allow/reject/manual → allow/deny/manual 映射。05不再直接消费check_identity结果，不保留旧完整candidate绑定路径。

binding固定为严格对象 `{contract_version:1, manifest_sha256:64位小写hex}`，仅引用发行manifest，不携带expected、observed或判定。旧完整candidate对象不自动迁移/批准，按新合同拒绝，须在认证管理恢复流程重新暂存绑定；范围批准仍独立。

### 同步接口及可信来源（08/10）

`koishi/src/scope-identity.ts` 提供 `ProductIdentitySession`、`productIdentityAdapter(session.read)` 和 `productScopeAuthority(session.read, approval?)`。08/10受信组合根创建会话，按下列顺序接入；普通查询只读内存快照，不spawn Python，不新增RPC框架。

1. 启动时会话为空（manual）。调用 `ticket=session.beginCheck()`：立即清空旧快照，获得本次校验的进程内ticket。
2. 受信控制器获取发行/备份/恢复树之外的当前expected；从同一次读取的manifest原始字节实算摘要、经09 `read_json`严格解析candidate；独立测量components、controller和实际schema。不得把candidate字段复制成observed，也不得恢复expected或执行候选内校验器。
3. 在已有受信启动/管理边界调用固定09 `check_scope_binding`，将 `TrustedScopeBindingCheck={binding,decision,checkerCommit}` 交 `session.publish(ticket,snapshot)`。decision是09原样 `{outcome,reason,evidence}`；checkerCommit必须为上述固定SHA。ticket跨会话、已消费、被取代或失效均返回false。publish只接收会话内检查结果，不是范围批准。
4. 同步 `ScopeAuthority.identity(binding)` 每次读取该session快照。请求binding必须与快照实际校验binding深相等；未匹配不能复用allow。只作结果形状/版本/证据和绑定关联检查，不重写09产品/能力/schema规则。缺快照manual，异常/不合法结果deny。
5. identity allow后仍调用独立C5 approval。未注入approval时返回manual/scope_approval_policy_pending。预检不是激活，10仍组合07与安装/握手检查。

### 生命周期及失效

快照只属于当前网关会话，不写磁盘、不进备份、不跨重启恢复。`invalidate()`立即清空快照并撤销未完成ticket；beginCheck也会先失效。发布与读取均复制对象，外部修改旧对象不能改变活动快照；同一ticket不能二次发布。

08/10必须在**变化之前**隔离外发并调用invalidate：候选manifest/组件、绑定、实例/AppID/通道配置、外部expected及其权限/下限、控制器版本、存储schema、安装/回退/恢复、受信控制通道失联或新鲜性无法确认。然后独立重测、重新beginCheck/publish；晚到的旧检查不能重开。新会话从空开始。无法保证这些变更通知/隔离时不得发布可用快照。该类只执行显式失效，不自动侦测文件、跨进程状态或网络断开；实际可信通道、事件接线、认证Console由08/10负责且仍未组合验收。

### 判定与证据

| 09内部check_identity | 09 check_scope_binding / 05保留判定 |
| --- | --- |
| allow / identity_verified且binding匹配 | allow，仅身份检查；不代替C5批准 |
| reject / 拒绝reason | deny，保留reason，不调用approval |
| manual / schema_incompatible | manual，保留reason，不调用approval |
| 身份allow但binding非法/不匹配 | deny / scope_binding_malformed或scope_binding_mismatch |

09 evidence是对 `{contract_version:1,candidate,expected,observed}` 以09规定的ASCII JSON编码得到的SHA256；05原样保留，并由范围最终判定的identity_evidence带出。它不是签名或可复用授权。格式非法的C1输入由09返回deny/malformed_contract及空evidence，05保留拒绝；其他缺失/非法evidence一律deny。05拒绝未知版本、原因/状态组合及旧三字段check_identity回执。可信来源靠08/10组合根，不靠候选自带hash。

09仍未认证任意个人旧baseline；本包不增加旧包豁免，也不将身份失败降为个人模式。

`ScopeAuthority.approval(candidate,digest)` 必须查询候选恢复内容之外的当前批准权威，返回 `{outcome:'allow'|'deny'|'manual',reason,evidence}`。generation/approval/hash 都只是候选信息；合法旧备份中的高代际、合法身份、完整文件均不等于最新批准。测试中的合成权威只证明接口拒绝旧宽范围，不是生产批准实现。

## 给10：恢复判定

`ScopeStore.recovery()` 返回同一判定三态与原因、证据版本；10先隔离外发，调用09身份兼容检查，再消费本接口及07删除权威，最终组合激活门。allow 仅代表当前范围检查允许，不代表整套恢复完成；manual 必须留待人工，deny 不可激活。settings/group策略文件修复及加载也须完成；不要只看 scope 判定。持久投递 facts/unknown 不删除，本包无业务DB迁移。

本轮00转达用户首版决定：人工核对当前权限和删除记录并批准，未知不恢复。05据此提供下面的当前进程批准，不提供持久锚点或无人值守自动激活。09/08/10接线完成后必须重新用实际候选验收，不能以本包离线夹具替代。

## 首版独立人工范围批准（05 → 08/10）

`ManualScopeApproval` 与 `ScopeStore` 同在 `koishi/src/scope-authority.ts`。它是受信组合根持有的进程内对象，不是权限服务、Console登录态或恢复布尔值。重启为空；既有未注入approval的产品仍manual。08将 `manual.approval` 注入 `productScopeAuthority(identitySession.read, manual.approval)`，ScopeStore把本次实际09身份判定作为approval第三参数传入；证据必须与人工核对时一致。

同步接口：

- `new ManualScopeApproval(readCurrent)`：readCurrent每次重新读取当前受信来源，返回下表严格对象或undefined；读失败、未知、非法字段、代际倒退均撤销已有批准。这个读取器不能调用ScopeStore.inspect/recovery（会递归）；范围候选使用新增 `scope.readCandidate()`，只读校验后的document与原始字节digest，异常关闭。
- `begin()`：先撤销旧批准/旧ticket，重新取当前对象，返回 `{ticket,review}` 的审阅副本；未知返回undefined。受信管理入口展示三个范围、身份和删除证据，人工核对后才调用confirm。
- `confirm(ticket)`：仅接受本对象当前进程内ticket，一次消费；重新读取必须与审阅时完全相同。仅记录范围批准，返回true不等于整套恢复或激活。跨实例、JSON/布尔值、重复、迟到ticket拒绝。
- `approval(candidate,digest,identity)`：每次重新读当前来源，核对整个候选、原始摘要、本次09 allow及其evidence；无人工批准manual，关联不匹配deny并撤销，匹配才allow。ScopeStore保留identity_evidence；声明的generation/approval永远不能自证当前许可。
- `revoke()`：立即撤销批准和未完成ticket，改变本地代际。`read()`返回 `{revision,approved,revocation_generation}`；revision是本会话随机epoch、本地代际与当前输入的SHA256，供10 `revisions.scope` / read_running.scope使用。仅为变化证据，approved仅供诊断，均不能单独作为许可。稳定输入下revision稳定；重启/撤销/批准/源变化均不同。

| readCurrent字段 | 受信生产者与约束 |
| --- | --- |
| document / digest | 05 `scope.readCandidate()`；digest为同一次读取的scope原始字节SHA256。候选可来自恢复树，但它本身不是批准来源 |
| revocation_generation | 08/10受信管理组合根在恢复树外维护的正安全整数；当前会话内单调递增，任何权限撤销/权威替换必须先递增并撤销，不可从备份恢复。会话重启无批准，不能把旧值作为重开证明 |
| identity_evidence | 09固定check_scope_binding针对当前外部expected/独立observed所得allow的64位小写hex证据；与ScopeStore本次实际身份结果再次比对 |
| deletion_evidence | 07当前独立删除权威及候选核验通过的64位小写hex证据指纹，由受信组合根提供；不能只对恢复journal算hash，缺字段、unknown或拒绝须返回undefined |

来源位置必须在候选/发行/备份/恢复树外，且只允许受信管理入口修改。该接口不验证操作者身份或路径所有权，08负责认证动作到进程内ticket的绑定，10负责恢复树外控制状态、访问权限和当前来源观察。撤销或文件/身份/删除权威变化前，先隔离外发并调用revoke，同时失效ProductIdentitySession；通道失联/新鲜性未知也同样处理。不能仅依靠轮询曾读到新值，必须保证事件期间的撤销，避免A→B→A未观察变化。该类不提供跨进程监听、自动恢复批准或候选内批准文件。

### Python来源与恢复顺序（不增加第二套解释器）

Python身份合同唯一来源仍为09 `3dda6d29955328777f7e526c21bfd2828dbbea8a:deploy/product_identity.py`；Python调用check_identity作C1检查，TS身份快照来自该文件check_scope_binding，其identity_evidence必须相同。05不另写Python身份/范围解释器。10固定 `a6e102a5b94be6af65652985fe0063491352cbf5:deploy/recovery_preflight.py` 已定义check_scope无参同步回调，消费05 `scope.recovery()` 的outcome/reason/evidence/identity_evidence，并比对C1证据；跨进程如何获得当前结果由08/10接线，不允许从保存的JSON allow回执读取。

顺序：隔离→独立测量09与07当前权威→05人工范围审阅/confirm→10 recovery_preflight重新检查身份/范围/删除→10已有ManualRecoveryReview及启动屏障。05的readCurrent不得反调全套preflight，避免scope批准的循环依赖。10恢复人工确认不替代05独立范围批准；两者均不替代最后当前性/宿主检查。

尚待00/08/10固定的部署字段：受信操作者/认证入口、恢复树外控制状态的绝对位置与owner/mode、revocation_generation的实际生产者、07证据组合到deletion_evidence的固定算法及源版本。没有这些字段时readCurrent必须返回undefined，不能用合成常量投产。建议首版沿用既有10受信控制器与08认证管理入口，保留进程内人工批准；不新建权限服务。具体宿主路径/账户及接收方实际范围须由维护者/接收方选择；05不能从本机工作目录或生产复制推导。

## 固定图标

visuals.py 的固定 CardType 与 mission 图标使用禁止重定向的 urllib opener，响应仅接收200；Content-Length 超限提前拒绝，无长度时最多读取6,000,001字节，超过6,000,000拒绝。拒绝后不进入旧 aiohttp 回退；解码及像素限额保留。其他既有素材下载路径未扩展本次改动。

本包未实施登录跨连接限速/脱敏；如后续选择实施，独立小提交。没有生产数据、真实接收方范围、QQ消息或付费模型调用。
