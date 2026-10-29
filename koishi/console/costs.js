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
export function executionView(detail){
 const attempts=detail.model_attempts,spans=detail.spans||[],routes=spans.filter(v=>v.kind==='model_route');
 return h('section',[h('h3','模型执行详情'),h('p',routes.length?routes.map(v=>reason(v.outcome)).join(' → '):'此记录未采集路由原因；以下按实际调用记录展示。'),
 attempts==null?h('p','模型账本不可用'):!attempts.length?h('p','没有模型尝试记录；不能据此推断未记录的历史调用。'):h('ol',attempts.map(a=>h('li',[h('strong',`${a.provider} / ${a.model}`),h('p',`状态 ${({reserved:'已预留',started:'调用中',settled:'已结算',released:'预留已释放'})[a.state]||a.state} · ${a.error_code||'无错误记录'} · ${a.duration_ms==null?'耗时未采集':a.duration_ms+' ms'}`),h('p',`输入 ${number(a.usage?.input_total)} · 缓存 ${number(a.usage?.input_cached)} · 输出 ${number(a.usage?.output_total)}`),h('p',`${a.price?.currency||''} ${money(a.reference_cost_micro)} · ${a.state==='released'?'未调用，预留已释放':a.state!=='settled'?'尚未结算':a.billing_incomplete?'用量不完整':'用量记录完整'} · ${a.price?.estimated?'登记估算价':'计价快照'}`),h('p',`工具类别：${a.tool_kind||'未采集'} · 预留 ${number(a.reserved_tokens)} 加权 Token`)]))),
 h('h3','处理与发送阶段'),h('ul',spans.filter(v=>v.kind!=='model_route').map(v=>h('li',`${v.kind} · ${v.duration_ms==null?'耗时未采集':v.duration_ms+' ms'} · ${v.outcome||'结果未采集'}`))),h('p',`平台确认 ${detail.confirmed_parts} / ${detail.part_count} 部分；平台回执不代表客户端已读。`)]);
}
