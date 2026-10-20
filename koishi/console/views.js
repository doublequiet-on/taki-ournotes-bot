import { h } from './runtime.js';
export const labels={processing:'处理中',complete:'平台确认全部发送',partial:'部分发送',failed:'已确认失败',uncertain:'发送结果不确定',closed_unsent:'已关闭，未确认发送',unknown:'未知'};
export const stamp=v => v ? new Date(v*1000).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong'}) : '尚未采集';
export const button=(text,action,disabled=false) => h('button',{onClick:action,disabled},text);
const guide=(title,text)=>h('aside',{class:'guide'},[h('strong',title),h('p',text)]);
const technical=(title,children)=>h('details',{class:'technical'},[h('summary',title),...children]);
const deliveryLabel=item=>item.delivery_reason==='completed_without_output'?'已完成，无输出':(labels[item.delivery_state]||item.delivery_state)+(item.uncertain_parts?'（仍有片段结果未确认）':'');
const names={available:'可用',partial:'部分采集',unavailable:'暂不可用',unsupported:'尚不支持',
  fresh:'刚同步',cached:'已验证缓存',stale:'旧缓存',unsaved:'已取得但未保存',c2c:'私聊',group:'群聊',
  queries:'新查询',notifications:'主动通知',natural_query:'/问 模式',reasons:'查询限制原因',notification_reasons:'通知限制原因',
  model_ceiling:'模型上限',backend_configuration_and_quota:'后端配置及额度',policy:'已保存策略',label:'群备注',
  inherit:'继承',allow:'允许',pause:'暂停',off:'关闭',local:'仅本地解析',model:'允许模型（受后端限制）',
  global_queries_disabled:'全局已暂停查询',global_group_excluded:'全局群范围不允许此群',group_queries_paused:'此群已暂停查询',
  global_notifications_disabled:'全局已暂停通知',group_notifications_paused:'此群已暂停通知',
  queriesEnabled:'接收新查询',privateEnabled:'允许私聊',groupsEnabled:'允许群查询与通知',groupAllowlist:'全局群范围',notificationsEnabled:'主动通知',
  background:'后台来源与采样',refreshes:'实际刷新观测',sampling:'历史采样',refresh_catalog:'主目录刷新',refresh_song_traits:'歌曲属性刷新',
  last_attempt_at:'最近刷新尝试',last_finished_at:'最近刷新完成',last_error_code:'最近刷新错误',running:'正在刷新',enabled:'已启用',servers:'服务器',interval_seconds:'间隔（秒）',
  observation:'后端采集健康',catalog:'主目录',other_sources:'其他来源',
  cache_state:'缓存状态',song_count:'歌曲数量',last_successful_sync_at:'最近成功同步',
  reason_code:'缺失原因',dropped_events:'丢弃观测',retention_seconds:'保留秒数',request_capacity:'请求容量',spans_per_request:'每请求跨度上限',
  queued:'待提交观测',last_success_at:'最近提交成功',last_failure_at:'最近提交失败',availability:'可用性',
  metrics_not_collected:'尚未采集匿名指标',notifications_disabled:'后端通知已关闭',source_snapshot_not_instrumented:'该来源尚未采集快照',
  metrics_snapshot_busy:'指标正在写入，本次读取暂不可用',
  observed_groups_not_platform_roster:'仅包含已观察群，不是平台群名单',historical_metadata_not_collected:'历史元数据尚未采集',
  request_id:'请求标识',delivery_id:'交付标识',created_at:'受理时间',lifecycle:'生命周期',delivery_state:'交付状态',
  uncertain_parts:'未确认片段数',confirmed_parts:'平台确认片段数',part_count:'片段数',scope:'范围',group_id:'群标识',
  command_kinds:'命令类别',business_outcome:'业务结果',observation_reason:'观测限制',missing_reason:'缺失原因',
  commands:'命令结果',parts:'发送片段',part_observations:'准备片段',spans:'阶段耗时',kind:'类型',outcome:'结果',position:'位置',
  command_index:'命令序号',business_status:'业务状态',fallback_available:'文字回退可用',primary_kind:'原始类型',
  parse_origin:'解析来源',processing_route:'处理路径',cache:'缓存',direct:'直接指令',continuation:'续查',
  platform_confirmed:'平台已确认',status:'状态',mode:'模式',attempt:'尝试标识',duration_ms:'耗时（毫秒）',observed_at:'观测时间',
  today:'今日',last_seven_days:'近七日已采集计数',observed_days:'已采集天数',missing_days:'缺失天数',timezone:'统计时区',write_available:'计数写入可用',
  denominator:'统计口径',ask_total:'问请求总数',route_local:'本地处理',route_cache:'缓存处理',route_model:'模型处理',
  prepare:'准备',reply_order_wait:'等待回复顺序',upload:'上传',send:'平台发送',receipt_submit:'回执提交',
  transport_coverage_not_guaranteed:'运输观测可能缺失，不能推定覆盖完整',items:'通知记录',groups:'群许可',present:'仍在群中',allowed:'平台通知许可',revision:'版本'};
function facts(value) {
  if(value===null||value===undefined)return h('span','尚未采集');
  if(Array.isArray(value))return value.length?h('ul',value.map(item=>h('li',[facts(item)]))):h('span','无');
  if(typeof value==='object')return h('dl',Object.entries(value).map(([key,item])=>h('div',{class:'fact'},[h('dt',names[key]||key),h('dd',[key==='group_id'&&typeof item==='string'?h('span','…'+item.slice(-6)):key.endsWith('_at')&&typeof item==='number'?h('span',stamp(item)):facts(item)])])));
  if(typeof value==='boolean')return h('span',value?'是':'否');
  return h('span',names[value]||labels[value]||String(value));
}
export function section(value,title) {
  return h('section',{class:'data-section'},[h('h2',names[title]||title),h('p',{class:'muted'},value ? `${names[value.availability]||value.availability} · ${stamp(value.observed_at)}${value.stale ? ' · 陈旧' : ''}` : '尚未读取'),
    value?.reason_code && h('p',names[value.reason_code]||value.reason_code),value?.data!=null && facts(value.data)]);
}
function row(item,open,groups) {
  const label=groups?.sections?.groups?.data?.find(g=>g.group_id===item.group_id)?.policy?.label;
  return h('li',{class:'request-row'},[h('button',{'aria-label':'查看请求 '+item.request_id,onClick:()=>open(item.request_id)},`${stamp(item.created_at)} · ${deliveryLabel(item)}`),
    h('span',`${item.command_kinds?.join('、') || '命令尚未采集'} · ${item.confirmed_parts}/${item.part_count} 片段平台确认`),
    h('small',item.group_id ? `${label||'群'} …${item.group_id.slice(-6)}` : item.scope==='c2c' ? '私聊' : '作用域尚未采集')]);
}
export function overviewView(s,open) {
  const value=s.overview.value,stats=value?.backend?.sections?.requests?.data;
  return h('div',[h('h1','Taki 总览'),h('p','最近 24 小时的已受理请求。平台确认发送不代表客户端已读。'),
    guide('第一次使用？先看这三处','总览确认机器人是否就绪；查询诊断查看某次回复的进度；分群管理设置群备注和各群权限。只查看页面不会发送 QQ 消息。'),
    ['conflict','frozen'].includes(value?.recovery_policy?.state)&&h('p',{role:'alert'},value.recovery_policy.state==='frozen'?'回退保护已暂停全局查询和通知。':'恢复开关发生冲突：保留人工修改，请核对全局与群策略。'),
    value?.policy_health?.availability==='unavailable'&&h('p',{role:'alert'},'群策略损坏或实例不匹配：新群查询和通知已限制，私聊仍按全局规则处理。'),
    !value&&guide('正在读取机器人状态','如果长时间没有结果，请检查页面上方的连接提示。尚未读取不代表机器人离线。'),
    value && h('section',{class:'health-card'},[h('span',{class:'eyebrow'},'运行状态'),h('h2',value.health.ready ? '服务已就绪' : '服务需要关注'),
      h('p',value.health.ready?'服务连接正常。能否回复某个群，还需看下方全局设置与该群规则。':'先检查 QQ 连接与后端状态。不要重复发送同一条指令；可到查询诊断查看是否仍在处理中。'),
      h('p',`QQ ${value.health.qq_online ? '在线' : '未在线'} · 后端 ${value.health.backend_ready ? '就绪' : '未就绪'} · 握手 ${value.health.handshake ? '已建立' : '未建立'}`),
      h('p',`新查询：${value.settings.queriesEnabled?'接收（按范围和群规则检查）':'全局暂停'} · 主动通知：${value.settings.notificationsEnabled?'按群规则和平台许可检查':'全局暂停'}`),
      h('p',value.backend?.sections?.service?.data?.natural_query?.model_configured===false?'模型未配置，/问 仍可使用本地解析。':'/问 模型路径受后端配置、额度与群策略共同限制。'),
      h('p',{class:'muted'},`${stamp(value.health.observed_at)}${value.health.stale ? ' · 当前读取失败' : ''}`),technical('运行版本（排障时使用）',[h('small',value.health.release_sha||'尚未采集')])]),
    stats && h('section',[h('h2',`已受理 ${stats.total} 条`),h('div',{class:'stats'},Object.entries(stats.states).map(([key,count]) => h('div',[h('strong',String(count)),h('span',labels[key])]))),
      h('p',{class:'muted'},`元数据缺失 ${stats.metadata_missing} 条`),h('h3','最近的查询'),stats.recent.length?h('ul',{class:'record-list'},stats.recent.map(item => row(item,open,s.groups.value))):h('p','此时间范围内没有已受理的查询记录。')]),
    !stats&&guide('查询统计暂未取得','数据恢复后会自动更新。缺失的数据不会显示为 0。'),
    technical('数据来源与采集状态 · 排查缺图、旧数据时查看',[
    value?.gateway_observation && section({ availability:value.gateway_observation.availability, data:value.gateway_observation },'网关采集健康'),
    ...Object.entries(value?.backend?.sections||{}).filter(([key]) => !['requests','service'].includes(key)).map(([key,item]) => section(item,key)),
    ...Object.entries(s.sources.value?.sections||{}).map(([key,item]) => section(item,`来源：${names[key]||key}`))]),
    technical('/问 使用统计 · 仅统计已采集的数据',Object.values(s.metrics.value?.sections||{}).map(item => section(item,'/问 匿名指标')))]);
}
export function requestsView(s,open,params,search,more,group) {
  const list=s.requests.value?.sections?.requests?.data,detail=s.detail.value?.sections?.request?.data;
  return h('div',[h('h1','查询诊断'),h('p','只读观察已受理请求；历史缺失字段保持未知。'),
    guide('机器人没有回复，先从这里找','点击“需要关注”查看仍在处理、部分发送或结果不确定的请求，再打开记录看详情。结果不确定时先核对 QQ 群，不要立即重发；平台确认发送也不等于用户已读。'),
    h('label',['状态 ',h('select',{value:params.state||'',onChange:e => {params.state=e.target.value;}},[h('option',{value:''},'全部'),...Object.entries(labels).map(([value,text]) => h('option',{value},text))])]),
    button('最近 24 小时',() => search(false)),button('需要关注（含历史未决）',() => search(true)),
    params.group_id&&h('p',[`当前群 …${params.group_id.slice(-6)} `,button('清除群筛选',()=>{delete params.group_id;search(false);})]),
    list && h('p',`${stamp(list.from)} 至 ${stamp(list.to)}，当前 ${list.items.length} 条`),
    list&&!list.items.length&&guide('没有符合条件的记录','可切回全部状态或最近 24 小时。这里只包含机器人已受理的请求；没有记录不代表 QQ 一定没有收到消息。'),
    h('ul',{class:'record-list'},(list?.items||[]).map(item => row(item,open,s.groups.value))),list?.next_cursor && button('下一页',() => more(list.next_cursor)),
    detail && h('section',[h('h2','请求详情'),detail.group_id&&button('查看关联群',()=>group(detail.group_id)),h('p',deliveryLabel(detail)),h('p',`业务结果：${detail.business_outcome} · 观测：${detail.observation}`),
      h('p',names[detail.missing_reason]||'准备是生成回复的时间；上传与发送是交给平台的时间。缺失阶段不代表耗时为零。'),technical('查看完整记录与阶段耗时',[h('p','各阶段采用各进程单调时钟，不跨进程相减。'),facts(detail)])])]);
}
export function groupsView(s,requests) {
  const items=s.groups.value?.sections?.groups?.data||[];
  const value=s.groups.value?.policy,edit=s.groupDraft.value;
  const choice=(key,title,options)=>h('label',{class:'toggle'},[title,h('select',{'aria-label':title,value:edit[key],onChange:e=>{edit[key]=e.target.value;s.groupDirty.value=true;}},options.map(([value,label])=>h('option',{value},label)))]);
  const current=items.find(item=>item.group_id===s.groupId.value);
  return h('div',[h('h1','分群管理'),h('p','来源为最近观察、通知记录及已有配置，不代表完整 QQ 入群名单。全局限制和平台许可始终优先。'),
    guide('先选群，再改设置','点击下方群名称，先填写一个容易识别的备注。通常保留“继承”即可；只想暂停某个群时，在该群中改为“暂停”并保存。这里的允许不会越过全局限制。'),
    s.groups.value?.sections?.groups?.stale&&h('p',{role:'alert'},'群观察读取失败：保留上次观察，配置规则仍取当前值。'),
    value?.reason_code&&h('p',{role:'alert'},'群策略不可用：新群查询及通知已限制，请检查配置文件。'),
    !items.length&&h('p','还没有可显示的群记录。机器人受理群查询后会留下观察记录，也可以使用下方管理员关联入口。'),
    h('ul',{class:'record-list group-list'},items.map(item => h('li',[button(item.policy.label||`群 …${item.group_id.slice(-6)}`,()=>s.selectGroup(item.group_id)),
      h('small',`最近受理 ${stamp(item.last_seen)} · ${item.accepted_requests??'未采集'} 条`),button('查看此群请求',()=>requests(item.group_id)),
      h('span',`查询 ${item.effective.queries?'允许':'暂停'} · 通知 ${!item.effective.notifications?'暂停':item.allowed===0?'平台禁止':item.allowed===1?'允许':'等待平台许可确认'}`)]))),
    h('p',`当前 ${items.length} 条，可关联记录共 ${s.groups.value?.sections?.groups?.total??'尚未采集'} 条`),
    button('刷新群记录',()=>s.load(s.groups,'groups/list')),
    s.groups.value?.sections?.groups?.next_cursor&&button('下一页群记录',()=>s.load(s.groups,'groups/list',{cursor:s.groups.value.sections.groups.next_cursor})),
    technical('手动关联群（管理员高级入口）',[h('p','OpenID 是 QQ 平台给机器人的群标识，不是你在 QQ 里看到的群号。已有群记录时，直接点击上方群名称即可。'),h('label',['管理员关联群 OpenID',h('input',{value:s.groupId.value,onChange:e=>s.selectGroup(e.target.value),'aria-label':'群 OpenID'})]),
    button('编辑此群',()=>s.selectGroup(s.groupId.value),!value||!!value.reason_code)]),
    edit&&h('section',[h('h2',edit.label||`群 …${s.groupId.value.slice(-6)}`),
      h('label',['群备注（最多80字）',h('input',{value:edit.label,onInput:e=>{edit.label=e.target.value;s.groupDirty.value=true;},'aria-label':'群备注'})]),
      choice('queries','新查询',[['inherit','继承全局'],['allow','允许（受全局限制）'],['pause','暂停']]),
      choice('notifications','主动通知',[['inherit','继承全局'],['allow','允许（受全局和平台限制）'],['pause','暂停']]),
      choice('natural_query','/问 模式',[['inherit','继承后端能力'],['off','关闭'],['local','仅本地解析'],['model','允许模型（受配置与额度限制）']]),
      h('p',{class:'guide'},'新查询决定是否接收新指令；主动通知决定是否允许机器人主动推送。/问 的“仅本地解析”不调用模型；/查卡、/查曲等直接指令始终不调用模型。'),
      current&&section({availability:'available',observed_at:s.groups.value.generated_at,data:current.effective},'当前已生效规则'),
      s.groupConflict.value&&h('p',{role:'alert'},'群策略版本冲突或保存未确认；草稿仍保留。'),
      s.groupConflict.value&&section({availability:'available',observed_at:s.groups.value.generated_at,data:value?.groups?.[s.groupId.value]},'服务器当前值'),
      h('p',{role:'status',class:'draft-status'},s.groupDirty.value?'有未保存的群设置，点击保存后才会生效。':'当前显示已载入的群设置。'),button('保存群策略',s.saveGroup,s.busy.value||!!value?.reason_code),
      button('放弃群草稿并载入服务器值',()=>{s.groupDirty.value=false;s.selectGroup(s.groupId.value);}),
      h('p','群策略与全局设置分别保存；已有查询保留接纳时规则，通知发送前再次检查当前规则。')]),
    technical('通知记录与平台许可', [...Object.values(s.notices.value?.sections||{}).map(item => section(item,'通知与平台许可')),
    s.notices.value?.sections?.notices?.data?.next_cursor&&button('下一页通知记录',()=>s.load(s.notices,'notices/list',{cursor:s.notices.value.sections.notices.data.next_cursor}))])]);
}
export function settingsView(s) {
  if(!s.draft.value)return null;
  const toggle=(key,text) => h('label',{class:'toggle'},[h('input',{type:'checkbox',checked:s.draft.value[key],onChange:e => {s.draft.value[key]=e.target.checked;s.dirty.value=true;}}),text]);
  return h('section',{class:'settings-card'},[h('h2','全局设置'),h('p','这里影响整个机器人。只调整一个群，请使用“分群管理”。关闭新查询不会中断已经受理的回复。'),toggle('queriesEnabled','接收新查询'),toggle('privateEnabled','允许私聊'),toggle('groupsEnabled','允许群查询与通知'),toggle('notificationsEnabled','允许主动通知'),
    h('p',{class:'muted'},'主动通知是机器人主动推送，与回复查询分开控制。群查询还需同时开启“接收新查询”。'),
    h('label',['允许的群 OpenID（每行一个，空白表示全部）',h('textarea',{rows:4,value:s.draft.value.groupAllowlist.join('\n'),onInput:e => {s.draft.value.groupAllowlist=e.target.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);s.dirty.value=true;}})]),
    h('small','OpenID 不是 QQ 群号。清空此列表会放开全局群范围，仍受分群规则和平台权限约束。'),h('p',{role:'status',class:'draft-status'},s.dirty.value?'有未保存的全局设置，点击保存后才会生效。':'当前显示已载入的全局设置。'),
    s.conflict.value && h('p',{role:'alert'},'服务器有新值或保存未确认。草稿保留，请核对服务器设置。'),button('保存设置',s.save,s.busy.value),
    button('载入服务器设置并放弃草稿',() => {s.dirty.value=false;s.conflict.value=false;void s.load(s.overview,'overview');}),
    s.conflict.value && facts(s.overview.value?.settings),
    h('details',[h('summary','最近管理记录（仅本进程）'),h('ul',(s.overview.value?.events||[]).slice(-20).reverse().map(e=>h('li',`${e.time} ${e.event}`)))])]);
}
