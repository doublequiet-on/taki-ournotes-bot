// L3: Existing group approval metadata and guarded memory deletion; never displays chat bodies.
import { h } from './runtime.js';
import { button, stamp } from './views.js';
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
 return h('div',{class:'chat-management'},[capabilityView(s.chatGroup.value,effective),h('section',[h('h2','群记忆'),button('刷新记忆统计',()=>s.load(s.memory,'chat/group/memory',s.groupId.value),s.busy.value),!m?h('p','记忆统计尚未取得，Agent 未启用时不可用。'):h('div',[h('p',`保留 ${m.retention_days} 天 · 消息 ${m.messages} · 摘要 ${m.summaries} · 图片来源 ${m.media_sources}`),h('small',`删除标记 ${m.message_tombstones} · 记忆版本 ${m.revision}${m.clear_before_ms?' · 已清空至 '+stamp(m.clear_before_ms/1000):''}`),h('p','这里只展示统计。删除会同时失效关联摘要与在途回答，不删除计费账本。'),h('label',['待删除消息 ID（每行一个，最多 100 个）',h('textarea',{rows:3,value:s.memoryIds.value,onInput:e=>s.memoryIds.value=e.target.value})]),button('删除指定消息记忆',()=>confirm('delete'),disabled||!s.memoryIds.value.trim()),button('清空本群记忆',()=>confirm('clear'),disabled)])])]);
}
