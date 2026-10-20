import { h } from './runtime.js';
export const labels={processing:'处理中',complete:'平台确认全部发送',partial:'部分发送',failed:'已确认失败',uncertain:'发送结果不确定',closed_unsent:'已关闭，未确认发送',unknown:'未知'};
export const stamp=v => v ? new Date(v*1000).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong'}) : '尚未采集';
export const button=(text,action,disabled=false) => h('button',{onClick:action,disabled},text);
const names={available:'可用',partial:'部分采集',unavailable:'暂不可用',unsupported:'尚不支持',
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
  platform_confirmed:'平台已确认',status:'状态',mode:'模式',attempt:'尝试标识',duration_ms:'耗时（毫秒）',observed_at:'观测时间',
  today:'今日',last_seven_days:'近七日已采集计数',observed_days:'已采集天数',missing_days:'缺失天数',timezone:'统计时区',write_available:'计数写入可用',
  denominator:'统计口径',ask_total:'问请求总数',route_local:'本地处理',route_cache:'缓存处理',route_model:'模型处理',
  prepare:'准备',reply_order_wait:'等待回复顺序',upload:'上传',send:'平台发送',receipt_submit:'回执提交',
  transport_coverage_not_guaranteed:'运输观测可能缺失，不能推定覆盖完整',items:'通知记录',groups:'群许可',present:'仍在群中',allowed:'平台通知许可',revision:'版本'};
function facts(value) {
  if(value===null||value===undefined)return h('span','尚未采集');
  if(Array.isArray(value))return h('ul',value.map(item=>h('li',[facts(item)])));
  if(typeof value==='object')return h('dl',Object.entries(value).map(([key,item])=>h('div',{class:'fact'},[h('dt',names[key]||key),h('dd',[facts(item)])])));
  if(typeof value==='boolean')return h('span',value?'是':'否');
  return h('span',names[value]||labels[value]||String(value));
}
export function section(value,title) {
  return h('section',[h('h2',names[title]||title),h('p',value ? `${names[value.availability]||value.availability} · ${stamp(value.observed_at)}${value.stale ? ' · 陈旧' : ''}` : '尚未读取'),
    value?.reason_code && h('p',names[value.reason_code]||value.reason_code),value?.data!=null && facts(value.data)]);
}
function row(item,open) {
  return h('li',[button(`${stamp(item.created_at)} · ${labels[item.delivery_state]}`,() => open(item.request_id)),
    h('span',`${item.command_kinds?.join('、') || '命令尚未采集'} · ${item.confirmed_parts}/${item.part_count} 片段平台确认`),
    h('small',item.group_id ? `群 …${item.group_id.slice(-6)}` : item.scope==='c2c' ? '私聊' : '作用域尚未采集')]);
}
export function overviewView(s,open) {
  const value=s.overview.value,stats=value?.backend?.sections?.requests?.data;
  return h('div',[h('h1','Taki 总览'),h('p','最近 24 小时的已受理请求。平台确认发送不代表客户端已读。'),
    value && h('section',[h('h2',value.health.ready ? '服务已就绪' : '服务需要关注'),
      h('p',`QQ ${value.health.qq_online ? '在线' : '未在线'} · 后端 ${value.health.backend_ready ? '就绪' : '未就绪'} · 握手 ${value.health.handshake ? '已建立' : '未建立'}`),
      h('p',`${stamp(value.health.observed_at)}${value.health.stale ? ' · 当前读取失败' : ''}`),h('small',value.health.release_sha)]),
    stats && h('section',[h('h2',`已受理 ${stats.total} 条`),h('div',{class:'stats'},Object.entries(stats.states).map(([key,count]) => h('div',[h('strong',String(count)),h('span',labels[key])]))),
      h('p',`元数据缺失 ${stats.metadata_missing} 条`),h('ul',stats.recent.map(item => row(item,open)))]),
    value?.gateway_observation && section({ availability:value.gateway_observation.availability, data:value.gateway_observation },'网关采集健康'),
    ...Object.entries(value?.backend?.sections||{}).filter(([key]) => !['requests','service'].includes(key)).map(([key,item]) => section(item,key)),
    ...Object.entries(s.sources.value?.sections||{}).map(([key,item]) => section(item,`来源：${key}`)),
    ...Object.values(s.metrics.value?.sections||{}).map(item => section(item,'/问 匿名指标'))]);
}
export function requestsView(s,open,params,search,more) {
  const list=s.requests.value?.sections?.requests?.data,detail=s.detail.value?.sections?.request?.data;
  return h('div',[h('h1','查询诊断'),h('p','只读观察已受理请求；历史缺失字段保持未知。'),
    h('label',['状态 ',h('select',{value:params.state||'',onChange:e => {params.state=e.target.value;}},[h('option',{value:''},'全部'),...Object.entries(labels).map(([value,text]) => h('option',{value},text))])]),
    button('最近 24 小时',() => search(false)),button('需要关注（含历史未决）',() => search(true)),
    list && h('p',`${stamp(list.from)} 至 ${stamp(list.to)}，当前 ${list.items.length} 条`),
    h('ul',(list?.items||[]).map(item => row(item,open))),list?.next_cursor && button('下一页',() => more(list.next_cursor)),
    detail && h('section',[h('h2','请求详情'),h('p',labels[detail.delivery_state]),h('p',`业务结果：${detail.business_outcome} · 观测：${detail.observation}`),
      h('p',names[detail.missing_reason]||'各阶段采用各进程单调时钟，不跨进程相减。'),facts(detail)])]);
}
export function groupsView(s) {
  const items=s.groups.value?.sections?.groups?.data||[];
  return h('div',[h('h1','分群管理'),h('p','当前为只读群入口；最近观察不代表完整 QQ 入群名单。'),
    h('ul',items.map(item => h('li',`群 …${item.group_id.slice(-6)} · 最近受理 ${stamp(item.last_seen)} · ${item.accepted_requests} 条`))),
    ...Object.values(s.notices.value?.sections||{}).map(item => section(item,'通知与平台许可'))]);
}
export function settingsView(s) {
  if(!s.draft.value)return null;
  const toggle=(key,text) => h('label',{class:'toggle'},[h('input',{type:'checkbox',checked:s.draft.value[key],onChange:e => {s.draft.value[key]=e.target.checked;s.dirty.value=true;}}),text]);
  return h('section',[h('h2','全局设置'),toggle('queriesEnabled','接收新查询'),toggle('privateEnabled','允许私聊'),toggle('groupsEnabled','允许群查询与通知'),toggle('notificationsEnabled','允许主动通知'),
    h('label',['允许的群 OpenID（每行一个，空白表示全部）',h('textarea',{rows:4,value:s.draft.value.groupAllowlist.join('\n'),onInput:e => {s.draft.value.groupAllowlist=e.target.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);s.dirty.value=true;}})]),
    s.conflict.value && h('p',{role:'alert'},'服务器有新值或保存未确认。草稿保留，请核对服务器设置。'),button('保存设置',s.save,s.busy.value),
    button('载入服务器设置并放弃草稿',() => {s.dirty.value=false;s.conflict.value=false;void s.load(s.overview,'overview');}),
    s.conflict.value && h('pre',JSON.stringify(s.overview.value?.settings,null,2))]);
}
