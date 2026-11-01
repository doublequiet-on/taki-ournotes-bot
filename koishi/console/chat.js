// L3: Group conversation settings, approval metadata and guarded memory deletion; no chat bodies.
import { h } from './runtime.js';
import { button, stamp } from './views.js';
import { chatEventsView } from './costs.js';
const names={primary:'主模型',fallback:'备用模型',web:'联网搜索',vision:'图片理解',proactive:'主动发言',summary:'摘要'};
const reasons={dedicated_proactive_model:'主动发言使用独立模型',approved:'已有批准记录',agent_disabled:'Agent 尚未启用',group_required:'请在群详情核对批准范围',approval_absent_or_profile_changed:'未批准或批准绑定的模型版本已变化',model_not_configured:'模型尚未配置',chat_services_absent:'群服务未配置',chat_service_unapproved:'本群服务未批准',chat_service_profile_mismatch:'群服务绑定的模型版本已变化',service_disabled:'群服务配置为关闭',proactive_limits_unsupported:'主动发言限额超出当前支持范围',ready:'已有本群服务批准',chat_capability_configuration:'能力配置格式需要核对',chat_capability_unverified:'接口或币种不符合批准要求',capability_read_failed:'能力文件读取失败'};
const reason=v=>reasons[v]||v||'尚未采集';
export function capabilityView(value,effective=null){
 return h('section',[h('h2','能力与批准状态'),!value?h('p','尚未取得能力状态'):h('div',[h('p',`运行引擎：${value.engine} · 读取不会发起模型探测`),
 h('p','下列批准来自管理员登记的证据；当前面板未进行联网、图片或平台收发测试。模型版本变化后需重新核对批准。'),
 effective&&h('p',`本群查询：${effective.queries===true?'允许':effective.queries===false?'暂停':'未知'} · 模型模式：${({model:'允许模型',local:'仅本地',off:'关闭',inherit:'继承'})[effective.natural_query]||'未知'}；本群策略与额度仍会限制实际执行。`),
 ...(value.profiles||[]).map(p=>h('details',{open:true},[h('summary',`${p.role==='proactive'?'主动参与模型':names[p.role]} · ${p.model||'未配置'} · ${p.config_version||'无版本'}`),!p.configured&&h('p',reason(p.reason)),
 h('ul',(p.capabilities||[]).map(c=>h('li',[h('strong',names[c.kind]),h('p',`实现：${c.implemented?'已有':'未知'} · 批准：${c.approved?'已有记录':'无有效记录'} · 批准前置条件：${c.eligible?'条件满足，仍需权限与额度':'条件未满足'} · ${reason(c.reason)}`),c.evidence_ref&&h('small','证据编号：'+c.evidence_ref)]))),
 p.services&&h('div',[h('h3','群服务预算'),h('p',reason(p.services.reason)),...['summary','proactive'].filter(k=>p.services[k]).map(k=>{const v=p.services[k];return h('p',`${names[k]}：${v.enabled?'已配置开启':'关闭'} · 每小时费用上限 ${p.services.currency} ${v.max_cost_micro_per_hour/1e6} · ${k==='summary'?'最多任务 '+v.max_jobs_per_hour:'最多调用 '+v.max_calls_per_hour+' / 回复 '+v.max_replies_per_hour+' · 冷却 '+v.cooldown_seconds+' 秒'}`);})])]))])]);
}
export function groupChatView(s,effective){
 const m=s.memory.value?.availability==='unavailable'?null:s.memory.value,disabled=s.busy.value||s.stale.value||!m||!s.guard.value?.gateway_epoch;
 const confirm=action=>{const label=s.groups.value?.policy?.groups?.[s.groupId.value]?.label||'…'+s.groupId.value.slice(-6);if(window.confirm(`${action==='clear'?'清空':'删除指定消息的'}群记忆（${label}）？关联摘要与在途回答会失效，历史费用继续保留。`))void s.updateMemory(action);};
 return h('div',{class:'chat-management'},[conversationSettings(s),capabilityView(s.chatGroup.value,effective),h('section',[h('h2','群记忆'),button('刷新记忆统计',()=>s.load(s.memory,'chat/group/memory',s.groupId.value),s.busy.value),!m?h('p','记忆统计尚未取得，Agent 未启用时不可用。'):h('div',[h('p',`保留 ${m.retention_days} 天 · 消息 ${m.messages} · 摘要 ${m.summaries} · 图片来源 ${m.media_sources}`),h('small',`删除标记 ${m.message_tombstones} · 记忆版本 ${m.revision}${m.clear_before_ms?' · 已清空至 '+stamp(m.clear_before_ms/1000):''}`),h('p','这里只展示统计。删除会同时失效关联摘要与在途回答，不删除计费账本。'),h('label',['待删除消息 ID（每行一个，最多 100 个）',h('textarea',{rows:3,value:s.memoryIds.value,onInput:e=>s.memoryIds.value=e.target.value})]),button('删除指定消息记忆',()=>confirm('delete'),disabled||!s.memoryIds.value.trim()),button('清空本群记忆',()=>confirm('clear'),disabled)])])]);
}

export function conversationSettings(s){
 const value=s.chatSettings.value,edit=s.chatEdit.value?.group_id===s.groupId.value?s.chatEdit.value:null;
 const disabled=s.busy.value||!!s.chatReadError.value,settings=edit?.settings;
 const mark=()=>{edit.dirty=true;};
 const users=value?.recent_users||[],tasks=value?.tasks;
 const userLabel=user=>user.label||users.find(item=>item.user_id===user.user_id)?.display_name||'未设置备注';
 return h('section',{class:'conversation-settings'},[
   h('div',{class:'card-heading'},[h('h2','交流设置'),button('刷新交流设置',()=>s.refreshChatSettings(),s.busy.value)]),
   s.chatReadError.value&&h('p',{role:'alert'},s.chatReadError.value),
   !settings?h('p','正在读取本群交流设置。'):h('div',[
     h('label',{class:'toggle'},[h('input',{type:'checkbox','aria-label':'启用免 @ 续聊',checked:settings.followup_enabled,disabled,onChange:e=>{settings.followup_enabled=e.target.checked;mark();}}),'免 @ 续聊']),
     h('p','用户主动找 Taki 聊过后，可直接继续说。Taki 会结合上下文判断是否仍在和她交流。'),
     h('div',{class:'conversation-timing'},[
       h('label',['续聊窗口（秒）',h('input',{type:'number',min:60,max:600,step:1,'aria-label':'续聊窗口（秒）',value:settings.followup_window_seconds,disabled,onInput:e=>{settings.followup_window_seconds=Number(e.target.value);mark();}})]),
       h('label',['合并消息等待（秒）',h('input',{type:'number',min:1,max:2,step:0.1,'aria-label':'合并消息等待（秒）',value:settings.coalesce_seconds,disabled,onInput:e=>{settings.coalesce_seconds=Number(e.target.value);mark();}})])]),
     h('p',{class:'field-note'},'每次成功续聊后重新计时。连续发送的文字和图片会短暂等待，一起理解。'),
     h('h3','不主动搭话的用户'),
     h('p','名单只阻止 Taki 自行搭话。用户主动 @、点名或发命令时正常回应，之后仍可免 @ 续聊。'),
     h('label',['从近期用户选择',h('select',{'aria-label':'从近期用户选择',value:users.some(user=>user.user_id===s.chatUserId.value)?s.chatUserId.value:'',disabled,onChange:e=>{s.chatUserId.value=e.target.value;s.chatUserLabel.value=(users.find(user=>user.user_id===e.target.value)?.display_name||'').slice(0,80);}},[
       h('option',{value:''},users.length?'请选择用户':'暂无近期用户，可填写用户 ID'),...users.map(user=>h('option',{value:user.user_id},`${user.display_name||'未提供昵称'} · …${user.user_id.slice(-8)}`))])]),
     h('div',{class:'conversation-user-inputs'},[
       h('label',['平台用户 ID',h('input',{'aria-label':'平台用户 ID',value:s.chatUserId.value,maxlength:256,disabled,onInput:e=>s.chatUserId.value=e.target.value})]),
       h('label',['备注（可选）',h('input',{'aria-label':'用户备注',value:s.chatUserLabel.value,maxlength:80,disabled,onInput:e=>s.chatUserLabel.value=e.target.value})])]),
     button('添加到名单',()=>s.addExcludedUser(),disabled||!s.chatUserId.value.trim()),
     h('p',{class:'field-note'},`本群名单 ${settings.excluded_users.length} / 200 人；保存后生效，重启和切换模型后保留。`),
     settings.excluded_users.length?h('ul',{class:'conversation-exclusions'},settings.excluded_users.map(user=>h('li',{key:user.user_id},[
       h('div',[h('strong',userLabel(user)),h('code',user.user_id)]),h('button',{disabled,'aria-label':'移除 '+userLabel(user),onClick:()=>s.removeExcludedUser(user.user_id)},'移除')]))):h('p',{class:'empty-copy'},'名单为空，所有用户均可触发正常的主动参与判断。'),
     edit.conflict&&h('div',{class:'conversation-conflict',role:'alert'},[
       h('p','服务器设置已变化或保存未确认，草稿保留。请核对当前值后继续。'),
       value&&h('details',{open:true},[h('summary','服务器当前值'),h('p',`免 @ 续聊${value.settings.followup_enabled?'开启':'关闭'} · ${value.settings.followup_window_seconds} 秒 · 合并等待 ${value.settings.coalesce_seconds} 秒`),h('ul',value.settings.excluded_users.map(user=>h('li',`${user.label||'无备注'} · ${user.user_id}`)))]),
       button('核对当前值后保留草稿',()=>{if(window.confirm('已核对服务器当前值，保留草稿并使用当前版本继续编辑？')){edit.guard=value.guard;edit.conflict=false;}},disabled||!value)]),
     h('div',{class:'conversation-actions'},[
       button('放弃交流草稿并刷新',()=>s.refreshChatSettings(s.groupId.value,true),s.busy.value),
       button('保存交流设置',s.saveChatSettings,disabled||!edit.dirty||edit.conflict||!Number.isInteger(settings.followup_window_seconds)||settings.followup_window_seconds<60||settings.followup_window_seconds>600||!Number.isFinite(settings.coalesce_seconds)||settings.coalesce_seconds<1||settings.coalesce_seconds>2)]),
     h('small',{role:'status'},edit.dirty?'有未保存的交流设置':'当前显示已保存的交流设置')]),
   tasks&&h('div',{class:'conversation-tasks'},[h('h3','连续资料任务'),h('p',`进行中 ${tasks.active_tasks} · 待补充 ${tasks.waiting_tasks} · 部分完成 ${tasks.partial_tasks} · 已完成 ${tasks.completed_tasks}`),h('small',`任务对象和条件保留 ${Math.round(tasks.window_seconds/60)} 分钟，重启后结束。普通资料默认日服；需要时自动到 Haneoka 补查。`)]),
   value&&chatEventsView(value.recent_events,{group:true})]);
}
