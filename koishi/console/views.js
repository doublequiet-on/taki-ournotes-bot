import { groupChatView, lightweightStatus, proactiveSettings, proactiveStatus } from './chat.js';
import { dailyCostSummary, executionView, proactiveBudgetObservation } from './costs.js';
import { h } from './runtime.js';
import { groupQuota } from './model.js';
export const labels={processing:'处理中',complete:'平台确认全部发送',partial:'部分发送',failed:'已确认失败',uncertain:'发送结果不确定',closed_unsent:'已关闭，未确认发送',unknown:'未知'};
export const stamp=v => v ? new Date(v*1000).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong'}) : '尚未采集';
export const button=(text,action,disabled=false) => h('button',{type:'button',onClick:action,disabled},text);
const guide=(title,text)=>h('details',{class:'guide'},[h('summary',title),h('p',text)]);
const technical=(title,children)=>h('details',{class:'technical'},[h('summary',title),...children]);
const deliveryLabel=item=>item.delivery_reason==='proactive_error'?'主动评估失败':item.delivery_reason==='completed_without_output'?'已完成，无输出':(labels[item.delivery_state]||item.delivery_state)+(item.uncertain_parts?'（仍有片段结果未确认）':'');
const names={meta:'歌曲 Meta',knowledge:'百科批准与索引',error_counts:'今日调用错误',issue_count:'格式问题数',field_coverage:'已知字段数量',duration_seconds:'末判定时长',notes:'Note 数',nps:'NPS',level:'等级',bpm_first:'首 BPM',bpm_min:'最低 BPM',bpm_max:'最高 BPM',
  origin:'来源地址',release_id:'release',source_id:'来源版本',songs:'歌曲总数',charts:'谱面总数',not_observed:'尚无查询观测',
  approved_set_revision:'批准集合版本',index_revision:'索引版本',approved_documents:'可供答文档数',indexed_chunks:'索引片段数',index_error:'索引错误',last_build:'最近索引构建',encoder:'编码器',ready:'就绪',empty:'空库',index_pending:'索引待更新',
  model_attempts:'模型尝试',attempt_id:'尝试标识',tool_kind:'工具类别',usage:'用量',input_total:'输入 Token',input_cached:'其中缓存输入',output_total:'输出 Token',output_reasoning:'其中推理 Token',total:'总 Token',
  weighted_tokens:'加权 Token',reserved_tokens:'预留 Token',reference_cost_micro:'参考费用（微货币）',billing_incomplete:'用量不完整',config_version:'配置版本',price:'计价快照',pricing_version:'计价版本',estimated:'估算价',currency:'币种',provider:'服务商',
  started_at:'调用时间',settled_at:'结算时间',error_code:'原因',state:'状态',known:'已知',input_rate:'输入单价',cached_rate:'缓存单价',output_rate:'输出单价',reference_rate:'固定参考单价',source:'来源',fetched_at:'取价时间',
  global_revision:'全局策略版本',group_revision:'群策略版本',gateway_epoch:'网关代次',pending:'等待许可',last_failure:'最近许可失败',failures:'本进程失败计数',
  available:'可用',partial:'部分采集',unavailable:'暂不可用',unsupported:'尚不支持',
  policy_blocked:'策略已暂停',backend_stage:'后端观测阶段',effective_notifications:'当前策略允许通知',policy_observed_at:'策略检查时间',
  code:'结果',stage:'阶段',before_version:'更新前版本',after_version:'当前版本',added_counts:'本次新增',persisted:'已保存',published:'本次已发布到内存',data_available:'资料可用',snapshot:'目标快照',checked_at:'检查时间',checked:'已核验',reused:'复用',repaired:'已修复',failed:'失败',manifest_saved:'清单已保存',scope:'范围',failure_codes:'失败原因',fixed_asset_failures:'固定素材失败项',
  upstream_unchanged:'已是当前上游版本',synchronized:'同步完成',updated_unsaved:'已更新，缓存保存失败',refresh_failed_cached:'同步失败，继续使用旧数据',no_available_data:'暂无可用数据',assets_partial:'素材部分完成',assets_verified:'素材核验完成',catalog_snapshot_and_builtin_assets:'资料快照与固定游戏素材',
  runtime:'运行身份',controller:'更新控制器',services:'系统单元',processes:'进程观察',disk:'空间门槛',sizes:'目录占用',backup_coverage:'备份覆盖',gateway_sha:'网关版本',current_sha:'控制器记录版本',previous_sha:'前一版本',observed_target:'最近观察目标',paused:'已暂停',paused_reason:'暂停原因',preparation_step:'准备阶段',retry_after:'最早重试时间',blocked_reason:'阻止原因',download:'下载进度',idle:'无活动事务',
  deployment_revision:'通知目标版本',report_status:'通知内容状态',targets:'已观察目标',claims_collected:'已采集领取状态',candidate_count:'候选群总量',time_semantics:'时间口径',ledger_status:'发送账本状态',claim:'领取状态',observation_times:'新增旁路观测',first_observed_at:'首次观测',last_observed_at:'最近观测',claimed:'已领取',sent:'已确认发送',sending:'结果待确认',pending:'待处理',lease_expired:'领取已过期',deployment_target_unknown:'当前通知目标未知',permission_denied:'平台不允许',permission_unknown:'平台许可未知',group_removed:'已离群',aborted:'已撤销',
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
  return h('li',{class:'request-row'},[h('button',{class:'request-entry','aria-label':'查看请求 '+item.request_id,onClick:()=>open(item.request_id)},[
    h('span',{class:'request-avatar'},'≡'),h('span',{class:'request-copy'},[h('strong',item.command_kinds?.join('、')||'命令尚未采集'),
      h('small',`${item.group_id?`${label||'群'} …${item.group_id.slice(-6)}`:item.scope==='c2c'?'私聊':'作用域尚未采集'} · ${stamp(item.created_at)}`)]),
    h('span',{class:['state-pill',item.delivery_state]},deliveryLabel(item)),h('span',{'aria-hidden':'true'},'›')])]);
}
export function overviewView(s,open,attentionSearch) {
  const value=s.overview.value,stats=value?.backend?.sections?.requests?.data,health=value?.health,settings=value?.settings;
  const source=s.sources.value?.sections?.catalog,ask=s.metrics.value?.sections?.metrics?.data?.today;
  const number=v=>typeof v==='number'&&Number.isFinite(v)?String(v):'—';
  const state=(v,yes,no)=>v===true?yes:v===false?no:'尚未采集';
  const sourceText=source?.stale?'陈旧':({fresh:'可用',cached:'缓存',stale:'陈旧',unsaved:'未保存'})[source?.data?.cache_state]||'尚未采集';
  const attention=stats&&['processing','partial','uncertain'].every(key=>typeof stats.states?.[key]==='number')?stats.states.processing+stats.states.partial+stats.states.uncertain:null;
  const card=(title,value,caption,action)=>h('section',{class:'overview-stat'},[h('span',title),h('strong',value),h('small',caption),action&&button('查看记录',action)]);
  return h('div',{class:'overview-page'},[h('div',{class:'page-heading'},[
    h('div',[h('h1','总览'),h('p',{class:'page-intro'},'核对服务状态、模型配置和接话预算。')]),
    button('刷新',()=>s.load(s.overview,'overview')),
    ]),
    ['conflict','frozen'].includes(value?.recovery_policy?.state)&&h('p',{role:'alert'},value.recovery_policy.state==='frozen'?'回退保护已暂停全局查询和通知。':'恢复开关发生冲突：保留人工修改，请核对全局与群策略。'),
    value?.policy_health?.availability==='unavailable'&&h('p',{role:'alert'},'群策略损坏或实例不匹配：新群查询和通知已限制，私聊仍按全局规则处理。'),
    h('div',{class:'overview-top'},[h('section',{class:'health-card'},[h('div',{class:'instance-heading'},[h('img',{class:'instance-portrait',src:new URL('./brand/taki.png',import.meta.url).href,alt:''}),h('div',[h('h2','服务状态'),h('small','Taki · 机器人与网关')]),h('span',{class:['state-pill',health?.ready&&!health?.stale?'complete':'unknown']},health?.stale?'状态陈旧':state(health?.ready,'运行正常','需要关注'))]),
      h('div',{class:'service-grid'},[['QQ 连接',state(health?.qq_online,'在线','未在线')],['业务后端',state(health?.backend_ready,'就绪','未就绪')],['网关握手',state(health?.handshake,'已建立','未建立')]].map(([label,text])=>h('div',[h('small',label),h('strong',text)]))),
      h('div',{class:'card-foot'},[h('small',`观测 ${stamp(health?.observed_at)}${health?.stale?' · 陈旧':''}`),button('运行维护',()=>s.navigate('operations'))]),technical('运行版本（排障时使用）',[h('small',health?.release_sha||'尚未采集')])]),
      h('section',{class:'quick-card'},[h('h2','接收与推送'),...[['新查询',state(settings?.queriesEnabled,'接收中','已暂停')],['群查询',state(settings?.groupsEnabled,'按群规则','已暂停')],['主动通知',state(settings?.notificationsEnabled,'按群规则','已暂停')]].map(([label,text])=>h('div',{class:'quick-row'},[h('span',label),h('strong',text)])),button('全局设置',()=>s.navigate('settings'))])]),
    h('div',{class:'dashboard-grid'},[modelSnapshot(s),dailyCostSummary(s),groupSnapshot(s)]),
    h('div',{class:'overview-stats'},[card('已受理请求',number(stats?.total),'最近 24 小时'),card('平台已确认',number(stats?.states?.complete),'不代表客户端已读'),card('需要关注',number(attention),'最近 24 小时',attentionSearch),card('准备耗时中位数','—','尚未采集')]),
    h('div',{class:'overview-bottom'},[h('section',{class:'recent-card'},[h('div',{class:'card-heading'},[h('h2','最近查询'),button('全部记录',()=>s.navigate('requests'))]),
      !stats?h('p','查询统计尚未采集'):stats.recent?.length?h('ul',{class:'record-list'},stats.recent.map(item=>row(item,open,s.groups.value))):h('p','此时间范围内没有已受理的查询记录。'),
      stats&&h('small',`元数据缺失 ${number(stats.metadata_missing)} 条`)]),
      h('div',{class:'overview-side'},[h('section',{class:'source-card'},[h('div',{class:'card-heading'},[h('h2','数据来源'),button('查看',()=>s.navigate('sources'))]),
        h('div',{class:'source-mini'},[h('img',{src:new URL('./brand/haneoka.svg',import.meta.url).href,alt:''}),h('span',[h('strong','Haneoka'),h('small','主目录')]),h('span',{class:'state-pill'},sourceText)]),
        h('div',{class:'source-mini'},[h('img',{src:new URL('./brand/moenotes.png',import.meta.url).href,alt:''}),h('span',[h('strong','Moenotes'),h('small','活动榜线')]),h('span',{class:'state-pill'},'尚未采集')])]),
        h('section',{class:'ask-card'},[h('div',{class:'card-heading'},[h('h2','/问'),h('span',{class:'state-pill'},'今日')]),h('strong',{class:'ask-total'},number(ask?.ask_total)),h('small','次已采集请求'),
          h('p',`本地 ${number(ask?.route_local)} · 缓存 ${number(ask?.route_cache)} · 模型 ${number(ask?.route_model)}`)])])]),
    technical('数据来源与采集状态 · 排查缺图、旧数据时查看',[
    value?.gateway_observation && section({ availability:value.gateway_observation.availability, data:value.gateway_observation },'网关采集健康'),
    ...Object.entries(value?.backend?.sections||{}).filter(([key]) => !['requests','service'].includes(key)).map(([key,item]) => section(item,key)),
    ...Object.entries(s.sources.value?.sections||{}).map(([key,item]) => section(item,`来源：${names[key]||key}`))]),
    technical('/问 使用统计 · 仅统计已采集的数据',Object.values(s.metrics.value?.sections||{}).map(item => section(item,'/问 匿名指标')))]);
}
function modelSnapshot(s){
  const costs=s.overview.value?.backend?.sections?.service;
  const profiles=s.chat.value?.profiles||[],c=costs?.data?.model_costs;
  return h('section',{class:'dashboard-card'},[
    h('div',{class:'card-heading'},[h('h2','当前模型'),button('模型配置',()=>s.navigate('model'))]),
    h('dl',{class:'summary-rows'},[
      ['主模型',c?.primary?.model||profiles.find(p=>p.role==='primary')?.model],
      ['备用模型',c?.fallback?.model||profiles.find(p=>p.role==='fallback')?.model],
      ['主动接话',profiles.find(p=>p.role==='proactive')?.model],
    ].map(([label,model])=>h('div',[h('dt',label),h('dd',model||'未取得配置')]))),
    h('small',s.chat.value?.engine?'聊天引擎 '+s.chat.value.engine+(costs?.stale?' · 模型观测已陈旧':''):'聊天引擎尚未采集'),
  ]);
}
function groupSnapshot(s){
  const id=s.groupId.value,value=s.proactiveLimits.value?.group_id===id?s.proactiveLimits.value:null;
  const label=s.groups.value?.policy?.groups?.[id]?.label||'群 …'+id.slice(-6);
  return h('section',{class:'dashboard-card'},[
    h('div',{class:'card-heading'},[h('h2','群主动接话'),button(id?'群详情':'选择群',s.navigateGroup)]),
    id?h('div',[
      h('p',{class:'selected-group-name'},label),
      h('p',{class:'field-note'},proactiveStatus(value)+' · 轻量纯文本 '+lightweightStatus(value)),
      s.proactiveReadError.value&&h('p',{class:'field-note'},'限额读取失败，以上状态可能已陈旧。'),
      proactiveBudgetObservation(s,{compact:true}),
    ]):h('p','选择一个群后，可在这里核对该群的接话审批、轻量模式与最近风险观测。'),
  ]);
}
export function requestsView(s,open,params,search,more,group,close,selected,narrow) {
  const list=s.requests.value?.sections?.requests?.data,detail=selected&&s.detail.value?.sections?.request?.data;
  return h('div',{class:'requests-page'},[h('div',{class:'page-heading'},[h('h1','请求记录'),h('small','最近 24 小时')]),
    guide('机器人没有回复，先从这里找','点击“需要关注”查看仍在处理、部分发送或结果不确定的请求，再打开记录看详情。结果不确定时先核对 QQ 群，不要立即重发；平台确认发送也不等于用户已读。'),
    h('div',{class:'request-toolbar'},[h('div',{class:'filter-tabs'},[h('button',{'aria-pressed':params.attention!=='true',onClick:()=>search(false)},'最近 24 小时'),h('button',{'aria-pressed':params.attention==='true',onClick:()=>search(true)},'需要关注（含历史未决）')]),
      h('label',['状态 ',h('select',{'aria-label':'发送状态',value:params.state||'',onChange:e => {params.state=e.target.value;search(params.attention==='true');}},[h('option',{value:''},'全部状态'),...Object.entries(labels).map(([value,text]) => h('option',{value},text))])])]),
    params.group_id&&h('p',[`当前群 …${params.group_id.slice(-6)} `,button('清除群筛选',()=>{delete params.group_id;search(false);})]),
    h('div',{class:'record-workspace'},[h('section',{class:'record-directory'},[h('div',{class:'card-heading'},[h('h2','已受理请求'),h('small',list?`当前 ${list.items.length} 条`:'尚未取得')]),
      list&&!list.items.length&&h('p','没有符合条件的记录。'),h('ul',{class:'record-list'},(list?.items||[]).map(item => row(item,open,s.groups.value))),
      list&&h('small',`${stamp(list.from)} 至 ${stamp(list.to)} · 平台确认不代表客户端已读`),list?.next_cursor && button('下一页',() => more(list.next_cursor))]),
      narrow&&detail&&h('div',{class:'inspector-scrim','aria-hidden':'true',onClick:close}),
      detail?h('section',{class:'request-inspector',role:narrow?'dialog':undefined,'aria-modal':narrow?'true':undefined,'aria-label':narrow?'查询详情':undefined,'data-inspector':narrow?'request':undefined},[h('div',{class:'card-heading'},[h('h2','查询详情'),h('button',{'aria-label':'关闭查询详情',onClick:close},'关闭')]),detail.group_id&&button('查看关联群',()=>group(detail.group_id)),h('p',{class:['state-pill',detail.delivery_state]},deliveryLabel(detail)),h('p',`业务结果：${names[detail.business_outcome]||detail.business_outcome} · 观测：${names[detail.observation]||detail.observation}`),
        h('p',names[detail.missing_reason]||'准备是生成回复的时间；上传与发送是交给平台的时间。缺失阶段不代表耗时为零。'),button('导出此请求诊断',()=>s.exportDiagnostic(selected,'requests')),executionView(detail),technical('查看完整记录与阶段耗时',[h('p','各阶段采用各进程单调时钟，不跨进程相减。'),facts(detail)])]):h('section',{class:'inspector-empty'},selected?'正在读取查询详情':'选择一条记录查看详情')])]);
}
const groupPanes=[['proactive','主动接话'],['conversation','交流与记忆'],['policy','查询策略']];
export function groupsView(s,requests,openGroup,closeGroup,groupOpen,narrow,pane,filter,message) {
  const observed=s.groups.value?.sections?.groups,items=observed?.data||[];
  const value=s.groups.value?.policy,edit=s.groupDraft.value,id=s.groupId.value;
  const current=items.find(item=>item.group_id===id);
  const label=value?.groups?.[id]?.label||current?.policy?.label||'群 …'+id.slice(-6);
  const query=filter.value.trim().toLocaleLowerCase();
  const visible=items.filter(item=>!query||[item.group_id,item.policy?.label].some(text=>text?.toLocaleLowerCase().includes(query)));
  const open=!!(groupOpen&&edit);
  const modal=narrow&&open;
  const hasDraft=key=>({
    proactive:s.proactiveEdit.value?.group_id===id&&s.proactiveEdit.value.dirty,
    conversation:s.chatEdit.value?.group_id===id&&s.chatEdit.value.dirty,
    policy:s.groupDirty.value,
  })[key];
  const body={
    proactive:()=>proactiveSettings(s,openGroup),
    conversation:()=>groupChatView(s,current?.effective),
    policy:()=>groupPolicy(s,current,value,edit),
  };
  return h('div',{class:'groups-page'},[
    h('div',{class:'page-heading'},[h('div',[h('h1','群管理'),h('p',{class:'page-intro'},'选群后查看主动接话、交流设置和查询策略。')])]),
    observed?.stale&&h('p',{role:'alert'},'群观察读取失败：保留上次观察，配置规则仍取当前值。'),
    value?.reason_code&&h('p',{role:'alert'},'群策略不可用：新群查询及通知已限制，请检查配置文件。'),
    h('div',{class:'group-workspace'},[
      h('section',{class:'group-directory',inert:modal||undefined},[
        h('div',{class:'card-heading'},[h('h2','已观察群'),button('刷新群记录',()=>s.load(s.groups,'groups/list'))]),
        !open&&id&&groupPanes.some(([key])=>hasDraft(key))&&h('div',{class:'inline-callout'},[
          h('p',label+'有未保存修改。'),button('继续编辑群草稿',()=>openGroup(id),s.busy.value),
        ]),
        h('label',{class:'group-search'},['筛选当前页',h('input',{type:'search',placeholder:'群备注或 OpenID',value:filter.value,onInput:e=>filter.value=e.target.value})]),
        h('small',observed?'当前 '+visible.length+' / '+items.length+' 条 · 共 '+(observed.total??'未知')+' 条':'尚未取得群记录'),
        !visible.length&&h('p',{class:'field-note'},query?'当前页没有匹配的群。':'还没有可显示的群记录。'),
        h('ul',{class:'record-list group-list'},visible.map(item=>h('li',{key:item.group_id},[
          h('button',{class:'group-entry','aria-pressed':open&&id===item.group_id,disabled:s.busy.value,onClick:()=>openGroup(item.group_id)},[
            h('span',{class:'group-avatar'},(item.policy?.label||'群').slice(0,1)),
            h('span',{class:'group-copy'},[h('strong',item.policy?.label||'群 …'+item.group_id.slice(-6)),h('small',stamp(item.last_seen))]),
            h('span',{class:'state-pill'},item.effective?.queries===true?'接收中':item.effective?.queries===false?'已暂停':'未知'),
          ]),
          h('small',{class:'group-count'},'已受理 '+(item.accepted_requests??'未采集')+' 条'),
          button('查看此群请求',()=>requests(item.group_id)),
        ]))),
        observed?.next_cursor&&button('下一页群记录',()=>s.load(s.groups,'groups/list',{cursor:observed.next_cursor})),
        technical('手动关联群',[
          h('p','填写 QQ 平台提供的群 OpenID。'),
          h('form',{class:'inline-form',onSubmit:e=>{e.preventDefault();openGroup(String(new FormData(e.target).get('group')).trim());}},[
            h('label',['群 OpenID',h('input',{name:'group',required:true,pattern:'[A-Za-z0-9_\\-]{1,128}'})]),
            h('button',{type:'submit',disabled:s.busy.value||!value||!!value.reason_code},'编辑此群'),
          ]),
        ]),
      ]),
      modal&&h('div',{class:'inspector-scrim','aria-hidden':'true',onClick:closeGroup}),
      open?h('section',{class:'group-inspector',role:modal?'dialog':undefined,'aria-modal':modal?'true':undefined,'aria-label':modal?'群详情':undefined,'data-inspector':modal?'group':undefined},[
        h('header',{class:'group-inspector-header'},[
          h('div',{class:'card-heading'},[
            h('div',[h('small','群详情 · …'+id.slice(-6)),h('h2',label)]),
            h('button',{'aria-label':'关闭群详情',onClick:closeGroup},modal?'返回群列表':'关闭'),
          ]),
          h('div',{class:'filter-tabs','aria-label':'群详情页面',role:'group'},groupPanes.map(([key,text])=>
            h('button',{'aria-pressed':pane.value===key,onClick:e=>{
              if(pane.value!==key&&modal)e.currentTarget.closest('[data-inspector]').scrollTop=0;
              pane.value=key;
            }},[text,hasDraft(key)&&h('i',{class:'draft-dot','aria-label':'有未保存修改'})]))),
        ]),
        modal&&message(),
        h('div',{class:'group-pane',key:pane.value},[body[pane.value]()]),
      ]):h('section',{class:'inspector-empty'},[h('div',[h('h2','选择一个群'),h('p','从群列表打开详情，查看审批与轻量模式，再按需修改限额。')])]),
    ]),
    technical('通知记录与平台许可', [...Object.values(s.notices.value?.sections||{}).map(item=>section(item,'通知与平台许可')),
      s.notices.value?.sections?.notices?.data?.next_cursor&&button('下一页通知记录',()=>s.load(s.notices,'notices/list',{cursor:s.notices.value.sections.notices.data.next_cursor}))]),
  ]);
}
function groupPolicy(s,current,value,edit){
  const choice=(key,title,options)=>h('label',[title,h('select',{'aria-label':title,value:edit[key],onChange:e=>{edit[key]=e.target.value;s.groupDirty.value=true;}},options.map(([value,label])=>h('option',{value},label)))]);
  return h('div',[
    h('section',[
      h('h3','群查询与通知'),
      h('label',['群备注（最多80字）',h('input',{value:edit.label,maxlength:80,onInput:e=>{edit.label=e.target.value;s.groupDirty.value=true;},'aria-label':'群备注'})]),
      h('div',{class:'form-grid'},[
        choice('queries','新查询',[['inherit','继承全局'],['allow','允许（受全局限制）'],['pause','暂停']]),
        choice('notifications','主动通知',[['inherit','继承全局'],['allow','允许（受全局和平台限制）'],['pause','暂停']]),
        choice('natural_query','/问 模式',[['inherit','继承后端能力'],['off','关闭'],['local','仅本地解析'],['model','允许模型（受配置与额度限制）']]),
      ]),
      h('p',{class:'field-note'},'新查询决定是否接收新指令。主动通知在发送前再次检查当前规则。/问 的“仅本地解析”及 /查卡、/查曲等直接指令均在本地执行。'),
      (s.groupConflict.value||s.groupRecoveryBlocked.value)&&h('p',{role:'alert'},s.groupRecoveryBlocked.value?'网关状态尚未确认或已变化，群草稿仍保留；请核对后再继续。':'群策略版本冲突或保存未确认；草稿仍保留。'),
      s.groupConflict.value&&section({availability:'available',observed_at:s.groups.value?.generated_at,data:value?.groups?.[s.groupId.value]},'服务器当前值'),
      h('div',{class:'form-actions'},[
        h('small',{role:'status'},s.groupDirty.value?'有未保存的群设置，点击保存后才会生效。':'当前显示已载入的群设置。'),
        button('放弃群草稿并载入服务器值',()=>{s.groupDirty.value=false;s.selectGroup(s.groupId.value);},s.busy.value||!s.groupDirty.value),
        h('button',{class:'primary-button',onClick:s.saveGroup,disabled:s.busy.value||!!value?.reason_code||s.groupRecoveryBlocked.value||!s.groupDirty.value},'保存群策略'),
      ]),
      h('small','群策略与全局设置分别保存，已有查询保留接纳时规则。'),
    ]),
    current&&technical('当前已生效规则',[section({availability:'available',observed_at:s.groups.value.generated_at,data:current.effective},'当前已生效规则')]),
    s.noticeTarget.value?.sections?.notice_target&&section(s.noticeTarget.value.sections.notice_target,'通知状态'),
    groupQuota(s),
    h('section',[h('h3','临时收紧查询方式'),button('临时仅本地解析',()=>{
      if(window.confirm('将此群的模型解析收紧为本地解析？关闭模式保持关闭；之后需手动恢复。'))void s.startControl('local',[s.groupId.value]);
    },s.busy.value||s.dirty.value||s.groupDirty.value||!s.controls.value?.available||!s.controls.value?.persistent_matches_live||!/^[A-Za-z0-9_-]{1,128}$/.test(s.groupId.value))]),
  ]);
}
export function settingsView(s) {
  if(!s.draft.value)return null;
  const toggle=(key,text) => h('label',{class:'toggle'},[h('input',{type:'checkbox',checked:s.draft.value[key],onChange:e => {s.draft.value[key]=e.target.checked;s.dirty.value=true;}}),text]);
  return h('div',{class:'settings-workspace'},[h('section',{class:'settings-card'},[h('h2','查询与通知'),
    toggle('queriesEnabled','接收新查询'),toggle('privateEnabled','允许私聊'),toggle('groupsEnabled','允许群查询与通知'),toggle('notificationsEnabled','允许主动通知'),
    h('small','关闭新查询不会中断已受理的回复；主动通知与回复查询分开控制。')]),
    h('section',{class:'settings-card'},[h('h2','群范围'),h('label',['允许的群 OpenID（每行一个，空白表示全部）',h('textarea',{rows:4,value:s.draft.value.groupAllowlist.join('\n'),onInput:e => {s.draft.value.groupAllowlist=e.target.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);s.dirty.value=true;}})]),
      h('small','OpenID 不是 QQ 群号。清空此列表会放开全局群范围，仍受分群规则和平台权限约束。')]),
    s.conflict.value && h('p',{role:'alert'},'服务器有新值或保存未确认。草稿保留，请核对服务器设置。'),
    h('div',{class:'settings-actions'},[h('span',{role:'status',class:'draft-status'},s.dirty.value?'有未保存的全局设置':'当前显示已载入的全局设置。'),h('div',[
      h('button',{title:'放弃本地草稿并载入服务器设置',onClick:() => {s.dirty.value=false;s.conflict.value=false;void s.load(s.overview,'overview');}},'放弃修改'),button('保存设置',s.save,s.busy.value)])]),
    s.conflict.value && facts(s.overview.value?.settings),
    h('details',{class:'technical'},[h('summary','最近管理记录（仅本进程）'),h('ul',(s.overview.value?.events||[]).slice(-20).reverse().map(e=>h('li',`${e.time} ${e.event}`)))])]);
}
