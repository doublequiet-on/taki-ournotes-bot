// L3: Read-only complete-ledger costs, budget routing explanations and execution facts; no model calls.
import { h } from './runtime.js';
import { stamp } from './views.js';
const number=v=>{if(v==null)return '未知';const [a,b=1]=String(v).split('/').map(Number);return Number.isFinite(a/b)?(a/b).toLocaleString('zh-CN',{maximumFractionDigits:6}):'未知';};
const money=v=>v==null?'未知':numberFraction(v,1e6);
function numberFraction(v,d){const [a,b=1]=String(v).split('/').map(Number);return number(a/b/d);}
const reasons={primary_budget_available:'主模型预算可覆盖首轮与最终回复的安全预留',primary_budget_exceeded:'主模型余额不足以覆盖安全预留',fallback_not_configured:'备用模型尚未配置',model_not_configured:'模型尚未配置',fallback_configuration:'备用模型配置不符合要求',reference_unit_mismatch:'主备计价单位不一致',primary_budget_currency:'主模型预算需要美元计价',usage_unavailable:'存在无法确定上限的用量，已暂停模型准入'};
const reason=v=>reasons[v]||v||'原因未采集';
const data=s=>s.overview.value?.backend?.sections?.service;
export function costAlerts(s){
 const section=data(s),c=section?.data?.model_costs,u=section?.data?.weighted_usage;
 const alerts=[];
 if(!c)alerts.push('费用状态尚未采集');
 if(section?.stale||s.stale.value)alerts.push('连接或采集状态异常，以下费用可能已陈旧');
 if(c?.next_route==='blocked')alerts.push('模型准入受阻：'+reason(c.route_reason));
 if(c&&c.fallback_ready!==true)alerts.push('备用模型未就绪：'+reason(c.fallback_reason)+'；主模型预算不足时无法继续服务');
 if(u?.billing_incomplete)alerts.push(`${u.billing_incomplete} 条用量未完整结算，相关预留继续保留`);
 if(c?.next_route==='fallback')alerts.push('下一轮预计使用 DeepSeek：'+reason(c.route_reason));
 return h('section',[h('h2','运行关注'),...alerts.map(t=>h('p',{role:'status'},t)),!alerts.length&&h('p','当前未发现模型费用或准入异常'),h('small','配置就绪不代表服务商连通已验证；实际调用仍受群权限、个人额度与并发限制。')]);
}
export function costsView(s){
 const section=data(s),c=section?.data?.model_costs;
 return h('div',{class:'costs-page'},[h('h1','模型与费用'),h('p','全机器人共享主模型预算；DeepSeek 费用另计。参考费用依据登记价格与用量，不等同于提供方最终账单。'),
 !c?h('p','费用尚未取得，请等待总览刷新。'):h('section',[h('h2',`${c.day} · 北京时间`),h('small',`每日 00:00 重置 · 采集于 ${stamp(s.overview.value?.backend?.generated_at)}`),
 h('div',{class:'stats'},[[c.limit_micro,'主模型每日上限'],[c.primary_spent_and_reserved_micro,'已用与预留合计'],[c.remaining_micro,'可用余额']].map(([v,t])=>h('div',[h('strong','USD '+money(v)),h('span',t)]))),
 h('p',`引擎：${c.engine} · 下一轮预计：${({primary:'主模型',fallback:'DeepSeek',blocked:'暂停准入'})[c.next_route]||'未知'}`),h('p',reason(c.route_reason)),
 h('p',`首轮与最终回复安全预留：USD ${money(c.admission_micro)}。余额大于零也可能不足以接收新一轮；在途请求保留原模型，不因 HTTP 错误自动重试或切换。`),
 h('p',`主模型：${c.primary?.model||'未配置'} · 备用：${c.fallback?.model||'未配置'}`),
 h('p','表格可左右滚动查看预留与不完整记录。'),h('div',{class:'cost-table-scroll'},[h('table',{class:'cost-table'},[h('caption','今日按实际模型汇总（完整账本）'),h('thead',[h('tr',['模型 / 币种','已发生参考费用','进行中预留','待核定预留','尝试 / 不完整'].map(t=>h('th',t)))]),h('tbody',(c.items||[]).map(i=>h('tr',[h('th',`${i.provider} / ${i.model} · ${i.currency}`),...[i.cost,i.pending,i.unknown].map(v=>h('td',money(v))),h('td',`${i.attempts} / ${i.incomplete}`)])))])]),
 !(c.items||[]).length&&h('p','今日暂无模型尝试。'),h('small','释放的最终回复预留不代表实际付费调用。未知用量的预留不会当作已发生费用，也不会自动清零。')]),costAlerts(s)]);
}
const modelStages={decision:'判断',generation:'正文生成',model:'模型调用'};
const transportStages={connect:'连接',dns:'域名解析',upload:'上传',wait_headers:'等待响应头',read_body:'读取响应正文',complete:'传输完成'};
function attemptDetails(events){
 const stages=new Map(),transport=new Map();
 for(const event of events||[]){
   const data=event.data||{};
   if(event.kind==='attempt_stage')stages.set(data.attempt_id,data.stage);
   if(event.kind==='transport'){
     transport.set(data.attempt_id,data);
     if(data.model_stage)stages.set(data.attempt_id,data.model_stage);
   }
 }
 return {stages,transport};
}
function transportLine(data){
 if(!data)return null;
 const parts=[transportStages[data.stage]||data.stage, data.elapsed_ms==null?null:data.elapsed_ms+' ms',
   data.input_images==null?null:'入模图片 '+data.input_images+' 张',data.http_status==null?null:'HTTP '+data.http_status,
   data.name==='disconnected'?'连接断开':data.name==='timeout'?'超时':null,data.provider_request_id];
 return h('p',parts.filter(Boolean).join(' · '));
}
export function executionView(detail){
 const attempts=detail.model_attempts,spans=detail.spans||[],routes=spans.filter(v=>v.kind==='model_route');
 const {stages,transport}=attemptDetails(detail.chat_events);
 const linked=new Set((attempts||[]).map(a=>a.attempt_id));
 const otherEvents=(detail.chat_events||[]).filter(e=>e.kind!=='transport'||!linked.has(e.data?.attempt_id));
 return h('section',[chatEventsView(otherEvents),h('h3','模型执行详情'),h('p',routes.length?routes.map(v=>reason(v.outcome)).join(' → '):'此记录未采集路由原因；以下按实际调用记录展示。'),
 attempts==null?h('p','模型账本不可用'):!attempts.length?h('p','没有模型尝试记录；不能据此推断未记录的历史调用。'):h('ol',attempts.map(a=>h('li',[h('strong',[modelStages[stages.get(a.attempt_id)],a.provider+' / '+a.model].filter(Boolean).join(' · ')),h('p',`状态 ${({reserved:'已预留',started:'调用中',settled:'已结算',released:'预留已释放'})[a.state]||a.state} · ${a.error_code||'无错误记录'} · ${a.duration_ms==null?'耗时未采集':a.duration_ms+' ms'}`),h('p',`输入 ${number(a.usage?.input_total)} · 缓存 ${number(a.usage?.input_cached)} · 输出 ${number(a.usage?.output_total)}`),h('p',`${a.price?.currency||''} ${money(a.reference_cost_micro)} · ${a.state==='released'?'未调用，预留已释放':a.state!=='settled'?'尚未结算':a.billing_incomplete?'用量不完整':'用量记录完整'} · ${a.price?.estimated?'登记估算价':'计价快照'}`),h('p',`工具类别：${a.tool_kind||'未采集'} · 预留 ${number(a.reserved_tokens)} 加权 Token`),transportLine(transport.get(a.attempt_id))]))),
 h('h3','处理与发送阶段'),h('ul',spans.filter(v=>v.kind!=='model_route').map(v=>h('li',`${v.kind} · ${v.duration_ms==null?'耗时未采集':v.duration_ms+' ms'} · ${v.outcome||'结果未采集'}`))),h('p',`平台确认 ${detail.confirmed_parts} / ${detail.part_count} 部分；平台回执不代表客户端已读。`)]);
}

const eventKinds={control:'接话判断',decision:'参与判断',task:'资料任务',tool:'资料工具',research:'资料补查',context:'交流上下文',image:'图片理解',error:'处理失败',finished:'处理结束',transport:'模型传输'};
const eventValues={excluded_user:'命中不主动搭话名单',followup_disabled:'本群已关闭免 @ 续聊',platform_unapproved:'平台普通群收发尚未批准',followup_candidate:'窗口内的续聊候选',conversation_ended:'用户已结束交流',settings_saved:'交流设置已保存',window_expired:'续聊窗口已结束',observation:'群消息观察',continuation:'免 @ 续聊',settings:'交流设置',skip:'跳过',candidate:'等待语义判断',saved:'已保存',reply:'接话',respond:'接话',ignore:'跳过',set:'建立或更新',cancel:'取消',result:'结果',waiting:'等待补充',partial:'部分完成',completed:'资料已齐',cancelled:'已取消',ok:'成功',not_available:'数据不可用',no_results:'没有匹配资料',source_unavailable:'资料来源暂不可用',tool_timeout:'查询超时',query_source_changed:'资料版本已变化',knowledge_search:'知识库搜索',site_search:'Haneoka 搜索',site_read:'读取 Haneoka 资料',read_entity_fields:'读取对象字段',research_task:'连续资料任务',web_search:'联网搜索',observe:'观察',delivered:'已交给平台',failed:'失败',not_delivered:'未送达'};
Object.assign(eventValues,{defer:'暂不接话，等待更多上下文',conversation_busy:'正在处理另一位用户发起的交流',supplement_merged:'已合并同一用户的连续补充',budget_exhausted:'本次交流额度已用完',chat_service_cost_limit:'本群每小时费用额度不足（包含待核账占用）',chat_service_daily_cost_limit:'本群每日费用额度不足（包含待核账占用）',chat_service_reservation_limit:'剩余额度不足以预留判断和回复',chat_service_evaluation_limit:'本群每小时接话判断次数已达上限',chat_service_call_limit:'本群每小时模型调用次数已达上限',chat_service_reply_limit:'本群每小时主动回复次数已达上限',chat_service_cooldown:'尚在主动发起间隔内',service_disabled:'本群主动参与已关闭',success:'处理成功',proactive_skip:'本轮跳过',proactive_defer:'等待更多上下文',proactive_stale:'交流上下文已变化',model_protocol_error:'模型返回格式不符合要求',invalid_json:'判断结果格式无效',json_syntax:'结构化结果解析失败',non_text:'结果不是文字',loaded:'图片已读取',provider_unavailable:'模型服务暂不可用',quota_exceeded:'额度已用完',deadline:'处理超时',aborted:'处理已终止',expired:'请求已过期',haneoka:'Haneoka',knowledge:'已审核知识库'});
Object.assign(eventValues,{echo:'主动回复重复当前消息，已跳过',lightweight:'轻量纯文本上下文',lightweight_echo:'轻量回复原样重复，已跳过',
 chat_service_lightweight_skip:'消息不符合轻量纯文本范围'});
const eventText=value=>eventValues[value];
eventValues.chat_service_pacing_limit='当前已释放额度不足，剩余额度将分时释放';
eventValues.chat_service_risk_limit='累计待核费用与在途预留已接近上限，本次额度不足';
eventValues.chat_service_risk_warning='累计风险余额不足两次完整接话预留';
export function chatEventsView(events,{group=false}={}){
 const visible=Array.isArray(events)?events.filter(e=>e.kind!=='attempt_stage'):[];
 if(!visible.length)return group?h('p',{class:'empty-copy'},'近期还没有交流判断或资料任务记录。'):null;
 return h('div',{class:'conversation-events'},[h('h3',group?'近期交流记录':'交流处理记录'),
   group&&h('p',{class:'field-note'},'查看接话、跳过和资料补查的原因；只保留操作状态，不展示聊天正文。'),
   h('ol',visible.map(event=>{const data=event.data||{},parts=[];
     for(const key of ['name','action','decision','status','reason','source'])if(eventText(data[key]))parts.push(eventText(data[key]));
     if(event.kind==='transport'){
       parts.push(transportStages[data.stage]||data.stage);
       if(data.name==='disconnected')parts.push('连接断开');
       if(data.name==='timeout')parts.push('超时');
     }
     if(data.continuation)parts.push('免 @ 续聊');
     if(data.window_seconds!=null)parts.push(`窗口 ${data.window_seconds} 秒`);
     if(data.research_fallback)parts.push('知识库后继续补查 Haneoka');
     if(data.total!=null)parts.push(`字段进度 ${data.completed||0} / ${data.total}`);
     if(data.entities!=null)parts.push(`${data.entities} 个对象`);
     if(data.task_missing!=null)parts.push(`待补字段 ${data.task_missing}`);
     if(data.elapsed_ms!=null)parts.push(`耗时 ${number(data.elapsed_ms)} ms`);
     if(data.messages!=null)parts.push(`上下文 ${data.messages} 条`);
     if(data.input_images!=null)parts.push(`输入图片 ${data.input_images} 张`);
     if(data.loaded_images!=null)parts.push(`读取图片 ${data.loaded_images} 张`);
     if(data.delivered!=null)parts.push(data.delivered?'已交给平台':'未交给平台');
     if(data.risk_available_micro!=null)parts.push('风险余量 USD '+money(data.risk_available_micro));
     if(data.risk_round_micro!=null)parts.push('本模式每轮预留 USD '+money(data.risk_round_micro));
     if(data.risk_unknown_micro!=null)parts.push('待核 USD '+money(data.risk_unknown_micro));
     if(data.risk_reserved_micro!=null)parts.push('在途 USD '+money(data.risk_reserved_micro));
     return h('li',[h('div',{class:'event-heading'},[h('strong',eventKinds[event.kind]||'交流事件'),h('time',stamp(event.observed_at))]),h('p',parts.join(' · ')||'状态已记录'),h('details',[h('summary','技术记录'),h('code',JSON.stringify({request_id:event.request_id,kind:event.kind,...data}))])]);
   }))]);
}
