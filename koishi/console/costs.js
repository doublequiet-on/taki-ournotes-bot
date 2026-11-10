// L3: Read-only complete-ledger costs, budget routing explanations and execution facts; no model calls.
import { h } from './runtime.js';
import { button, stamp } from './views.js';
const number=v=>{if(v==null)return '未知';const [a,b=1]=String(v).split('/').map(Number);return Number.isFinite(a/b)?(a/b).toLocaleString('zh-CN',{maximumFractionDigits:6}):'未知';};
export const money=v=>v==null?'未知':numberFraction(v,1e6);
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
 return h('section',[h('h2','模型费用提醒'),...alerts.map(t=>h('p',{role:'status'},t)),!alerts.length&&h('p','当前未发现模型费用或准入异常'),h('small','配置就绪不代表服务商连通已验证；实际调用仍受群权限、个人额度与并发限制。')]);
}
const usd=value=>value==null?'未知':'USD '+money(value);
const route=value=>({primary:'主模型',fallback:'DeepSeek 备用',blocked:'暂停准入'})[value]||'尚未采集';
const stats=items=>h('dl',{class:'budget-stats'},items.map(([label,value])=>h('div',[h('dt',label),h('dd',value)])));

export function dailyCostSummary(s){
 const section=data(s),c=section?.data?.model_costs,u=section?.data?.weighted_usage;
 const totals=c&&u?.day===c.day?Object.entries(u.reference_cost_micro||{}):[];
 return h('section',{class:'dashboard-card'},[
   h('div',{class:'card-heading'},[h('h2','今日费用与预算'),button('查看费用',()=>s.navigate('costs'))]),
   c?h('div',[
     h('p',{class:'field-note'},[c.day,' · 北京时间',section.stale?' · 数据陈旧':'']),
     h('div',{class:'metric-focus'},[h('small','已发生参考费用'),h('strong',totals.length?totals.map(([currency,value])=>currency+' '+money(value)).join(' · '):c.items?.length?'尚未采集':'暂无记录')]),
     stats([['主模型每日上限',usd(c.limit_micro)],['主模型预算余量',usd(c.remaining_micro)]]),
     h('small','参考费用按登记价格估算。预算余量已扣除适用的费用和预留。'),
   ]):h('p','费用尚未采集。'),
 ]);
}
export function proactiveBudgetObservation(s,{compact=false,collapsed=false}={}){
 const settings=s.chatSettings.value;
 const event=settings?.group_id===s.groupId.value&&settings.recent_events?.find(e=>e.data?.day_paced_available_micro!=null&&e.data.risk_limit_micro==null);
 if(!event)return h('div',{class:'proactive-budget-observation'},[
   h('strong','日内可用额度'),h('p',{class:'field-note'},s.chatReadError.value?'预算记录读取失败。':'近期没有日预算检查记录。'),
 ]);
 const value=event.data;
 const details=()=>[
   stats([['日上限',usd(value.day_limit_micro)],['已确认费用',usd(value.day_known_micro)],['当日待核',usd(value.day_unknown_micro)],['在途预留',usd(value.day_reserved_micro)]]),
   h('p',{class:'field-note'},'显示该次预算检查时已开放的可用额度。已结束的待核费用归原计费日，跨日仍在途的请求继续占用额度。'),
 ];
 return h('div',{class:'proactive-budget-observation'},[
   h('div',{class:'proactive-budget-heading'},[h('strong','日内可用额度'),h('span',{class:'state-pill'},'检查时记录')]),
   h('strong',{class:'proactive-budget-value'},usd(value.day_paced_available_micro)),
   h('small',stamp(event.observed_at)+(s.chatReadError.value?' · 读取失败，保留上次数据':'')),
   !compact&&(collapsed?h('details',[h('summary','日预算占用明细'),...details()]):h('div',details())),
 ]);
}
export function costsView(s){
 const section=data(s),c=section?.data?.model_costs;
 const columns=['模型 / 币种','已发生参考费用','进行中预留','待核定预留','尝试 / 不完整'];
 return h('div',{class:'costs-page'},[
   h('div',{class:'page-heading'},[h('div',[h('h1','费用与预算'),h('p',{class:'page-intro'},'查看今日模型账本、准入预算和各类预留。')]),button('刷新费用',()=>s.load(s.overview,'overview'))]),
   !c?h('section',[h('p','费用尚未取得，请等待总览刷新。')]):h('div',[
     h('section',[h('h2','主模型准入预算'),h('small',c.day+' · 北京时间 00:00 重置 · 采集于 '+stamp(s.overview.value?.backend?.generated_at)),
       stats([['每日配置上限',usd(c.limit_micro)],['已用与预留合计',usd(c.primary_spent_and_reserved_micro)],['当前预算余量',usd(c.remaining_micro)]]),
       h('p',['下一轮预计：',h('strong',route(c.next_route)),' · ',reason(c.route_reason)]),
       h('small','首轮与最终回复安全预留 '+usd(c.admission_micro)+'。在途请求保留原模型。备用模型及豁免调用的费用仍计入下方账本。'),
     ]),
     h('section',[h('h2','今日费用账本'),
       h('dl',{class:'cost-definitions'},[
         ['已发生参考费用','按已取得的用量与登记价格计算。服务商账单需另行核对。'],
         ['进行中预留','给待调用或在途请求占用的额度，结算或释放后更新。'],
         ['待核定预留','用量缺失或不完整时继续保留的额度，未计入已发生费用。'],
       ].map(([term,description])=>h('div',[h('dt',term),h('dd',description)]))),
       c.items?.length?h('div',{class:'cost-table-scroll'},[
         h('table',{class:'cost-table'},[
           h('caption','按实际模型与币种分组'),
           h('thead',[h('tr',columns.map(label=>h('th',{scope:'col'},label)))]),
           h('tbody',c.items.map(i=>h('tr',[
             h('th',{scope:'row'},[i.provider+' / '+i.model,h('small',i.currency)]),
             ...[money(i.cost),money(i.pending),money(i.unknown),i.attempts+' / '+i.incomplete].map((value,index)=>
               h('td',[h('span',{class:'mobile-cell-label','aria-hidden':'true'},columns[index+1]),value])),
           ]))),
         ]),
       ]):h('p','今日暂无模型尝试。'),
     ]),
   ]),
   h('section',[h('div',{class:'card-heading'},[h('h2','主动接话日预算'),button('查看群预算',s.navigateGroup)]),
     s.groupId.value?proactiveBudgetObservation(s):h('p','从群管理选择一个群，查看该群最近一次预算检查的日内额度。'),
   ]),
 ]);
}

export function budgetEditor(s){
 const model=s.budgetModel.value,budget=model?.primary_budget,edit=s.budgetEdit.value;
 const conflict=edit?.dirty&&budget&&JSON.stringify(edit.guard)!==JSON.stringify(budget.guard);
 return h('section',{class:'budget-editor'},[
   h('h2','编辑每日主模型上限'),
   !budget||!edit?h('div',[h('p','每日预算设置尚未取得。'),button('读取预算设置',s.refreshBudget,s.busy.value)]):h('div',[
     h('p','当前生效：'+'USD '+budget.daily_usd),
     h('label',['每日上限（美元）',h('input',{type:'text',inputmode:'decimal','aria-label':'每日主模型预算（美元）',value:edit.daily,disabled:s.busy.value,
       onInput:e=>{edit.daily=e.target.value;edit.dirty=true;}})]),
     h('small','最多六位小数。保存即时生效并持久保存。0 表示普通账号直接使用备用模型，服务器配置的额度豁免继续生效。'),
     conflict&&h('p',{role:'alert'},'服务器预算版本已变化，请核对当前生效金额后继续。'),
     h('div',{class:'form-actions'},[
       h('small',{role:'status'},edit.dirty?'有未保存的每日预算':'当前显示已保存的预算'),
       button('放弃预算草稿',()=>{s.budgetEdit.value=null;void s.refreshBudget();},s.busy.value||!edit.dirty),
       conflict&&button('核对最新预算后保留草稿',()=>{if(window.confirm('服务器当前预算为 USD '+budget.daily_usd+'。保留你的金额并以此版本重试？'))edit.guard=budget.guard;},s.busy.value),
       h('button',{class:'primary-button',disabled:s.busy.value||!edit.dirty||conflict,onClick:s.saveBudget},'保存每日预算'),
     ]),
   ]),
 ]);
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
eventValues.chat_service_risk_limit='旧规则下累计风险额度不足';
eventValues.chat_service_risk_warning='旧规则下累计风险余额偏低';
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
     if(data.day_paced_available_micro!=null&&data.risk_limit_micro==null)parts.push('日内可用 USD '+money(data.day_paced_available_micro));
     if(data.risk_available_micro!=null)parts.push('风险余量 USD '+money(data.risk_available_micro));
     if(data.risk_round_micro!=null)parts.push('本模式每轮预留 USD '+money(data.risk_round_micro));
     if(data.risk_unknown_micro!=null)parts.push('待核 USD '+money(data.risk_unknown_micro));
     if(data.risk_reserved_micro!=null)parts.push('在途 USD '+money(data.risk_reserved_micro));
     return h('li',[h('div',{class:'event-heading'},[h('strong',eventKinds[event.kind]||'交流事件'),h('time',stamp(event.observed_at))]),h('p',parts.join(' · ')||'状态已记录'),h('details',[h('summary','技术记录'),h('code',JSON.stringify({request_id:event.request_id,kind:event.kind,...data}))])]);
   }))]);
}
