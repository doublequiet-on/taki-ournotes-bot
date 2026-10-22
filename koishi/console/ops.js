import { h } from './runtime.js';
import { button, section, stamp } from './views.js';
export const actions={catalog_sync:'检查并同步',catalog_reload:'重新获取资料',assets_verify:'核验图片',assets_repair:'修复图片',assets_retry:'重试失败图片',tasks_recover:'检查并恢复后台任务',events_check:'检查当前活动',sample_once:'立即采样',upstream_check:'检查上游连接'};
const states={queued:'排队中',running:'执行中',succeeded:'已完成',partial:'部分完成',failed:'失败',interrupted:'已中断',intent:'结果待核对',applied:'已生效'};
export function jobsView(s) {
  return h('section',[h('h2','操作记录'),button('刷新记录',()=>s.load(s.jobs,'ops/jobs/list')),
    !s.jobs.value?h('p','尚未取得操作记录'):h('ul',{class:'record-list'},(s.jobs.value.items||[]).map(job=>h('li',[
      h('strong',actions[job.action]||job.action),h('span',`${states[job.state]||job.state} · ${stamp(job.created_at)}`),
      job.result&&section({availability:'available',observed_at:job.finished_at,data:job.result},'结果')]))),
    s.jobs.value?.items?.length===0&&h('p','暂无操作记录')]);
}
export function sourcesView(s) {
  const supported=s.jobs.value?.health?.actions||[];
  return h('div',[h('h1','数据来源'),...Object.entries(s.sources.value?.sections||{}).map(([key,item])=>section(item,key)),
    h('section',[h('h2','同步与修复'),...Object.entries(actions).map(([action,label])=>button(label,()=>{
      if(['catalog_reload','assets_repair','assets_retry'].includes(action)&&!window.confirm(label+'？将获取上游资料或图片，不重启机器人。'))return;
      void s.startJob(action);
    },s.busy.value||!supported.includes(action))),!supported.length&&h('p','后端尚未提供可执行运维能力')]),jobsView(s)]);
}
export function attentionView(s,search) {
  const v=s.overview.value,stats=v?.backend?.sections?.requests?.data,problems=[];
  if(v&&!v.health?.ready)problems.push(['连接需要关注','刷新状态',()=>s.load(s.overview,'overview')]);
  if(stats&&stats.states.partial+stats.states.uncertain+stats.states.failed>0)problems.push(['存在部分发送、失败或未确认记录','查看请求',search]);
  const catalog=s.sources.value?.sections?.catalog?.data;
  if(catalog&&['stale','unsaved','unknown'].includes(catalog.cache_state))problems.push(['资料同步需要关注','查看来源',()=>s.navigate('sources')]);
  if(v?.policy_health?.availability==='unavailable'||['frozen','conflict'].includes(v?.recovery_policy?.state))problems.push(['策略限制或恢复冲突','查看设置',()=>s.navigate('settings')]);
  if(v?.gateway_observation?.dropped_events||v?.backend?.sections?.observation?.data?.dropped_events)problems.push(['存在缺失的诊断观测','查看采集',()=>s.navigate('sources')]);
  return h('section',[h('h2','需要处理'),!problems.length?h('p',v?'暂无已知异常':'尚未取得状态'):h('ul',problems.map(([label,text,fn])=>h('li',[label,button(text,fn)]))),button('导出诊断',()=>s.exportDiagnostic())]);
}
export function operationsSettings(s) {
  return h('div',[h('section',[h('h2','运行与恢复准备'),s.host.value?section(s.host.value,'主机状态'):h('p','采集器尚未安装或状态未取得'),button('刷新主机状态',()=>s.load(s.host,'ops/host'))]),
    h('section',[h('h2','管理记录'),button('刷新管理记录',()=>s.load(s.audit,'ops/audit')),
      s.audit.value&&!s.audit.value.available&&h('p',{role:'alert'},'管理记录不可用'),
      h('ul',(s.audit.value?.items||[]).slice(0,25).map(e=>h('li',`${new Date(e.at).toLocaleString()} · ${e.action} · ${states[e.state]||e.state}`)))])]);
}
