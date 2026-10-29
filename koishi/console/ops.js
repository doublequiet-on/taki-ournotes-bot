import { libraryView } from './library.js';
import { h } from './runtime.js';
import { button, section, stamp } from './views.js';
export const actions={catalog_sync:'检查并同步',catalog_reload:'重新获取资料',assets_verify:'核验图片',assets_repair:'修复图片',assets_retry:'重试失败图片',tasks_recover:'检查并恢复后台任务',events_check:'检查当前活动',sample_once:'立即采样',upstream_check:'检查上游连接'};
const states={queued:'排队中',running:'执行中',succeeded:'已完成',partial:'部分完成',failed:'失败',interrupted:'已中断',intent:'结果待核对',applied:'已生效'};
export function jobsView(s) {
  return h('section',[h('h2','操作记录'),button('刷新记录',()=>s.load(s.jobs,'ops/jobs/list')),
    !s.jobs.value?h('p','尚未取得操作记录'):h('ul',{class:'record-list'},(s.jobs.value.items||[]).map(job=>h('li',[
      h('strong',actions[job.action]||job.action),h('span',`${states[job.state]||job.state} · ${stamp(job.created_at)}`),
      button('查看结果',()=>s.load(s.jobDetail,'ops/jobs/detail',job.id)),job.result&&section({availability:'available',observed_at:job.finished_at,data:job.result},'结果')]))),
    s.jobDetail.value&&section({availability:'available',observed_at:s.jobDetail.value.finished_at,data:s.jobDetail.value},'操作详情'),
    s.jobs.value?.items?.length===0&&h('p','暂无操作记录')]);
}
export function sourcesView(s) {
  const supported=s.jobs.value?.health?.actions||[];
  return h('div',[h('h1','数据来源'),libraryView(s),...Object.entries(s.sources.value?.sections||{}).map(([key,item])=>section(item,key)),
    h('section',[h('h2','同步与修复'),...Object.entries(actions).map(([action,label])=>button(label,()=>{
      if(['catalog_reload','assets_repair','assets_retry'].includes(action)&&!window.confirm(label+'？将获取上游资料或图片，不重启机器人。'))return;
      void s.startJob(action);
    },s.busy.value||!supported.includes(action))),!supported.length&&h('p','后端尚未提供可执行运维能力')]),jobsView(s)]);
}
export function attentionView(s,search) {
  const v=s.overview.value,stats=v?.backend?.sections?.requests?.data,problems=[];
  if(v&&!v.health?.ready)problems.push(['连接需要关注','刷新状态',()=>s.load(s.overview,'overview')]);
  if(stats&&(stats.retained_unresolved??stats.states.partial+stats.states.uncertain)>0)problems.push(['存在部分发送或未确认记录','查看请求',search]);
  const catalog=s.sources.value?.sections?.catalog?.data;
  if(catalog&&['stale','unsaved','unknown'].includes(catalog.cache_state))problems.push(['资料同步需要关注','查看来源',()=>s.navigate('sources')]);
  if(v?.policy_health?.availability==='unavailable'||['frozen','conflict'].includes(v?.recovery_policy?.state))problems.push(['策略限制或恢复冲突','查看设置',()=>s.navigate('settings')]);
  if(v?.gateway_observation?.dropped_events||v?.backend?.sections?.observation?.data?.dropped_events)problems.push(['存在缺失的诊断观测','查看采集',()=>s.navigate('sources')]);
  return h('section',[h('h2','需要处理'),!problems.length?h('p',v?'暂无已知异常':'尚未取得状态'):h('ul',problems.map(([label,text,fn])=>h('li',[label,button(text,fn)]))),button('导出诊断',()=>s.exportDiagnostic())]);
}
export function operationsSettings(s) {
  const host=s.host.value,data=host?.data,runtime=data?.runtime,controller=data?.controller;
  const timer=data?.services?.['taki-updater.timer'];
  const short=v=>v?v.slice(0,8):'尚未采集',proof=v=>v===true?'有记录':v===false?'未通过':'尚未采集';
  const objects={code_environment:'代码与环境',business_data:'业务数据',console_settings:'设置与群策略',trusted_configuration:'控制器与系统配置',isolated_restore:'隔离恢复',offsite_copy:'异地副本'};
  return h('div',[recoveryView(s),controlView(s),h('section',[h('h2','运行与恢复准备'),button('刷新主机状态',()=>s.load(s.host,'ops/host')),
    h('div',{class:'stats'},[[short(data?.processes?.backend?.cwd_release_sha),'后端进程目录版本'],[short(controller?.observed_target),'最近观察目标'],[short(runtime?.gateway_sha),'网关运行版本'],[short(controller?.current_sha),'控制器记录'],[short(controller?.previous_sha),'前一版本']].map(([value,label])=>h('div',[h('strong',value),h('span',label)]))),
    controller?.paused&&h('p','更新控制器已暂停'),host?.reason_code&&h('p',({host_collector_not_installed:'主机采集器尚未安装',host_snapshot_stale:'主机快照已过期',host_clock_changed:'主机观测时间需要核对'})[host.reason_code]||'主机观测暂不可用'),
    timer&&h('p',`自动更新定时器：${timer.UnitFileState==='disabled'||timer.UnitFileState==='masked'?'已停用':timer.UnitFileState||'未知'} · ${timer.ActiveState==='active'?'已激活':timer.ActiveState==='inactive'?'未激活':'状态未知'}`),
    data?.backup_coverage&&h('details',[h('summary','备份覆盖与核验记录'),h('ul',data.backup_coverage.map(row=>h('li',[
      h('strong',objects[row.object]||row.object),h('span',`归档存在：${row.archive_exists===true?'是':row.archive_exists===false?'否':'未知'} · 摘要核验：${proof(row.checksum_verified)} · 恢复核验：${proof(row.restore_verified)}`),
      row.evidence_at&&h('small',`外部受信记录 ${stamp(row.evidence_at)}`)])))]),
    host&&h('details',[h('summary','运行、空间与恢复详情'),section(host,'主机状态')])]),
    h('section',[h('h2','管理记录'),button('刷新管理记录',()=>s.load(s.audit,'ops/audit')),
      s.audit.value&&!s.audit.value.available&&h('p',{role:'alert'},'管理记录不可用'),
      h('ul',(s.audit.value?.items||[]).slice(0,25).map(e=>h('li',[h('details',[h('summary',`${new Date(e.at).toLocaleString()} · ${e.action} · ${states[e.state]||e.state}`),
        h('p',`会话 ${e.session?.slice(-8)||'尚未采集'} · 字段 ${e.summary?.fields?.join('、')||'无变更'}`),
        section({availability:'available',observed_at:e.at/1000,data:{accepted_guard:e.accepted_guard??null,result_guard:e.result_guard??null,code:e.code??null}},'保存版本')])])) )])]);
}
export function controlView(s){
  const controls=s.controls.value,disabled=s.busy.value||s.dirty.value||s.groupDirty.value||!controls?.available||!controls.settings_persistent_matches_live;
  const activity=s.overview.value?.activity;
  return h('section',[h('h2','维护与恢复'),button('进入维护',()=>{
    if(window.confirm('暂停新查询和主动通知？在途查询与确认会继续完成。'))void s.startControl('maintenance',[]);
  },disabled),
  h('p',activity?`已受理在途 ${activity.accepted_inflight} · 接纳中 ${activity.admission_inflight}`:'在途数量尚未采集'),
  controls&&!controls.persistent_matches_live&&h('p',{role:'alert'},'持久设置与当前运行值不一致，已阻止管理操作。'),
  h('ul',{class:'record-list'},(controls?.items||[]).map(entry=>h('li',[
    h('strong',entry.control==='maintenance'?'维护模式':`临时本地解析 · ${entry.group_count} 个群`),
    h('span',entry.control==='local'?(entry.group_ids||[]).map(id=>s.groups.value?.policy?.groups?.[id]?.label||'…'+id.slice(-6)).join('、'):`原设置：查询${entry.before?.queriesEnabled?'开启':'关闭'} · 通知${entry.before?.notificationsEnabled?'开启':'关闭'}`),
    (entry.current_matches===false||entry.persistence_matches===false)&&h('p','已检测到后续保存或恢复证据缺失。原设置保留，本次不会覆盖。'),
    entry.before_modes&&h('small','原模式：'+Object.entries(entry.before_modes).map(([mode,count])=>`${({inherit:'继承',model:'允许模型',local:'本地',off:'关闭'})[mode]||mode} ${count}`).join(' · ')),
    button('恢复这次变更',()=>{if(window.confirm('恢复这次操作前的设置？期间修改过的字段会报告冲突。'))void s.restoreControl(entry.id);},disabled||!entry.restorable)
  ]))),!controls?.items?.length&&h('p','没有待恢复的管理操作')]);
}

export function recoveryView(s){
 const host=s.host.value,r=host?.data?.business_recovery,b=r?.backup,v=r?.restore;
 const runtime=host?.data?.runtime;
 const checks=[['主机快照新鲜',host?host.stale===false:null],['后端代码摘要匹配',runtime?.backend_digest_matches??null],['控制器与网关版本匹配',runtime?.controller_matches_gateway??null],['业务备份未超过 36 小时',b?!b.stale:null],['同一备份已通过隔离恢复',v?v.verified&&!v.stale:null]];
 return h('section',[h('h2','上线与恢复证据'),h('p','各项证据分别核对；未采集或过期的状态需要刷新。'),
 h('ul',checks.map(([label,value])=>h('li',`${label}：${value===true?'有通过记录':value===false?'需要处理':'尚未采集'}`))),
 b?h('div',[h('p',`最近业务备份 ${b.id} · ${b.databases} 个数据库 · ${stamp(b.created_epoch)}`),h('p',`备份运行版本 ${b.runtime_sha}`),b.inventory&&h('ul',b.inventory.map(row=>h('li',`${row.name}：${row.included?'已列入备份清单':'未列入本次清单'}`)))]):h('p','尚未取得完整业务备份 v2 回执。'),
 v&&h('p',`隔离恢复验证 ${v.backup_id} · ${stamp(v.checked_epoch)}`),
 r?.reason_code&&h('small','恢复证据：'+({trusted_procedure_receipts:'来自受信备份与恢复程序',restore_receipt_missing_or_invalid:'缺少与本次备份匹配的有效恢复回执',full_backup_receipt_missing:'完整业务备份回执未采集'})[r.reason_code]),
 h('small','数据库数量来自备份回执，清单仅表示归档包含项；没有清单时不推断具体数据库覆盖；旧采集器需独立升级后才能提供这些字段。')]);
}
