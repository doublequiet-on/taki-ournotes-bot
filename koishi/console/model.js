import { h } from './runtime.js';
import { button, section, stamp } from './views.js';
const quantity=v=>{if(v===null||v===undefined)return '未知';const [a,b=1]=String(v).split('/').map(Number);return Number.isFinite(a/b)?(a/b).toLocaleString('zh-CN',{maximumFractionDigits:2}):'未知';};
const code=v=>({ready:'已配置',model_not_configured:'尚未配置',model_configuration:'配置需要核对',identity_unverified:'身份尚未绑定',
  permission_timeout:'网关许可未及时返回',policy_denied:'当前策略不允许',permission_expired:'执行许可已过期',
  controller_not_installed:'受信动作控制器尚未安装',controller_unavailable:'控制器当前不可用',controller_outcome_unknown:'操作结果未知，请核对记录',
  probe_passed:'工具与用量已确认',capability_incomplete:'工具或用量不完整',
  intent:'已受理',succeeded:'已通过',failed:'未通过',unknown:'结果未知',interrupted:'已中断',running:'执行中'})[v]||v||'尚未采集';
export function modelOverview(s){
  const service=s.overview.value?.backend?.sections?.service,q=service?.data?.model_scheduler,m=service?.data?.native_model,u=service?.data?.weighted_usage,p=service?.data?.model_permissions;
  return h('section',[h('h2','AI 问答'),h('p',`${code(m?.code)}${service?.stale?' · 数据已陈旧':''}`),
    m?.configured&&m.identity_bindings===0&&h('p',{role:'alert'},'AI 身份绑定尚未配置'),
    q&&h('div',{class:'stats'},[[q.running,'执行中'],[q.waiting,'等待中'],[u?quantity(u.weighted_tokens):'未知','今日加权 Token']].map(([v,t])=>h('div',[h('strong',String(v)),h('span',t)]))),
    q&&h('small',`执行上限 ${q.running_limit} · 等待上限 ${q.waiting_limit} · 执行 ${q.execution_seconds} 秒超时`),
    u&&h('p',`${u.day}（北京时间）· ${u.attempts} 次尝试 · 未知用量 ${u.usage_unknown} · 不完整 ${u.billing_incomplete}`),
    u&&h('p','参考费用：'+(Object.entries(u.reference_cost_micro||{}).map(([currency,v])=>`${currency} ${quantityFractionCurrency(v)}`).join(' · ')||'暂无记录')+` · 估算价 ${u.price_estimated} 次`),
    u?.observation_error&&h('p',{role:'alert'},'部分工具类别或耗时记录未保存，已结算用量仍保留。'),
    p?.last_failure&&h('p',{role:'alert'},`最近许可问题：${code(p.last_failure)}`),
    h('details',[h('summary','当前许可与策略版本'),section({availability:'available',observed_at:s.overview.value?.backend?.generated_at,
      data:{...s.overview.value?.current_policy_version,...p,error_counts:u?.error_counts}},'执行许可')])]);
}
function quantityFractionCurrency(v){const [a,b=1]=String(v).split('/').map(Number);return Number.isFinite(a/b)?(a/b/1e6).toLocaleString('zh-CN',{maximumFractionDigits:6}):'未知';}
export function modelSettings(s){
  const value=s.model.value,p=s.modelDraft.value;
  if(!value||!p)return h('section',[h('h2','模型与额度'),h('p','模型设置尚未取得'),button('读取模型设置',()=>s.load(s.model,'model/settings'))]);
  const text=(obj,key,title,type='text',min=null,max=null)=>h('label',[title,h('input',{'aria-label':title,type,value:obj[key],min,max,
    onInput:e=>{obj[key]=type==='number'?Number(e.target.value):e.target.value;s.modelDirty.value=true;}})]);
  const flag=(key,title)=>h('label',{class:'toggle'},[h('input',{type:'checkbox',checked:p[key],onChange:e=>{p[key]=e.target.checked;s.modelDirty.value=true;}}),title]);
  const probe=value.capability;
  return h('section',[h('h2','模型与额度'),h('p',`${code(value.code)} · 密钥${value.key_configured?'已配置':'未配置'}`),
    !value.editable&&h('p',{role:'alert'},'后端尚未指定可写的受保护配置，或配置无法读取。'),
    h('div',{class:'form-grid'},[h('label',['服务商',h('select',{'aria-label':'服务商',value:p.provider,onChange:e=>{p.provider=e.target.value;p.wire_profile=p.provider+'-chat-v1';p.parallel_control=false;s.modelDirty.value=true;}},
      ['deepseek','openai','openrouter'].map(v=>h('option',{value:v},v)))]),text(p,'model','模型 ID'),
      h('label',['API 地址',h('input',{value:value.endpoints[p.provider],readonly:true,'aria-label':'API 地址'})]),
      h('label',['新密钥（留空保留）',h('input',{type:'password',autocomplete:'new-password','aria-label':'新密钥',value:s.modelKey.value,
        onInput:e=>{s.modelKey.value=e.target.value;s.modelDirty.value=true;}})]),
      text(p,'user_daily_limit','用户每日额度','number',0,1e12),text(p,'group_daily_limit','群默认每日额度','number',0,1e12),
      text(p,'user_rate_requests','每用户窗口内请求数','number',1,1000),text(p,'group_rate_requests','每群窗口内请求数','number',1,1000),
      text(p,'rate_window_seconds','频率窗口（秒）','number',1,3600)]),
    h('small','地址仅支持已登记的 HTTPS 接口。额度按北京时间每日结算，参考费用不是提供方账单。'),
    h('details',[h('summary','模型能力与默认估算价'),h('p','能力依据是管理员登记的信息；下方测试记录才表示该配置版本的实际探测结果。价格以每百万 Token 的微货币单位填写。'),
      text(p,'capability_evidence','能力依据编号'),text(p,'max_input_tokens','输入预算','number',8192,65536),text(p,'max_output_tokens','最大输出 Token','number',64,1536),
      flag('parallel_control','发送禁止并行工具参数'),flag('cache_usage','按登记合同读取缓存用量'),flag('reasoning_usage','按登记合同读取推理用量'),
      h('label',['币种',h('select',{'aria-label':'币种',value:p.price.currency,onChange:e=>{p.price.currency=e.target.value;s.modelDirty.value=true;}},['CNY','USD'].map(v=>h('option',{value:v},v)))]),
      ...[['input_rate','输入单价'],['cached_rate','缓存输入单价'],['output_rate','输出单价'],['reference_rate','固定参考单价']].map(([k,t])=>text(p.price,k,t,'number',1,1e12)),
      h('small','已有账本的参考币种和参考单价固定；改变它们会被拒绝。'),
      h('p',`当前价格：${value.price_status?.price_source||'未采集'} · ${stamp(value.price_status?.price_observed_at)} · ${value.price_status?.price_estimated===false?'已获取报价':'默认估算'}`)]),
    s.modelConflict.value&&h('p',{role:'alert'},'模型设置版本冲突或保存未确认，草稿仍保留。'),
    h('p',{role:'status'},s.modelDirty.value?'有未保存的 AI 设置':'当前显示已载入的 AI 设置'),
    button('保存 AI 设置',()=>s.saveModel(),s.busy.value||!value.editable),button('放弃 AI 草稿并刷新',()=>{s.modelDirty.value=false;s.modelConflict.value=false;s.modelKey.value='';void s.load(s.model,'model/settings');}),
    h('h3','连接与能力测试'),h('p',probe?`${code(probe.state)} · ${code(probe.result?.code)}`:'此配置版本尚未进行实际测试'),
    button('发起一次能力测试',()=>{if(window.confirm('将向当前服务商发送一次合成工具测试，可能产生费用。确认执行？'))void s.probeModel();},
      s.busy.value||s.modelDirty.value||!value.key_configured||['intent','unknown','interrupted'].includes(probe?.state)),
    h('small','只在明确确认后调用；打开或刷新页面不会发起模型测试。'),
    h('details',[h('summary','AI 配置与测试记录'),...(value.audit||[]).map(a=>h('p',`${stamp(a.created_at)} · ${a.action} · ${code(a.state)} · ${a.config_version}`))])]);
}
export function groupQuota(s){
  const v=s.groupUsage.value,id=s.groupId.value,model=s.model.value;
  const override=model?.groups?.[id];
  return h('section',[h('h3','群 AI 额度'),v?h('p',`今日已用与预留 ${quantity(v.spent_and_reserved)} / ${v.daily_limit} · ${v.rate_window_seconds} 秒内 ${v.rate_requests} 次`):h('p','群额度尚未取得'),
    h('p',override?'此群使用独立额度':'此群继承默认额度'),
    h('form',{key:id,class:'quota-form',onSubmit:e=>{e.preventDefault();const d=new FormData(e.target);void s.saveModel(id,{group_daily_limit:Number(d.get('daily')),group_rate_requests:Number(d.get('rate'))});}},[
      h('label',['每日额度',h('input',{name:'daily',type:'number',min:0,max:1e12,required:true,placeholder:String(v?.daily_limit??''),'aria-label':'此群每日额度'})]),
      h('label',['窗口内请求数',h('input',{name:'rate',type:'number',min:1,max:1000,required:true,placeholder:String(v?.rate_requests??''),'aria-label':'此群频率'})]),
      h('button',{type:'submit',disabled:s.busy.value||s.modelDirty.value||!model?.editable},'保存此群额度')]),
    button('恢复默认额度',()=>s.saveModel(id,null),s.busy.value||s.modelDirty.value||!override)]);
}
export function trustedView(s){
  const v=s.trusted.value;
  return h('section',[h('h2','进程与代码'),h('p',v?.available?'受信控制器可用':code(v?.reason_code)),
    button('刷新控制器状态',()=>s.load(s.trusted,'ops/trusted')),
    v?.available&&h('div',[button('核定重启方案',()=>s.trustedRequest('restart'),s.busy.value),button('核定代码更新方案',()=>s.trustedRequest('update'),s.busy.value)]),
    ...(v?.plans||[]).map(p=>h('div',[h('h3',p.action==='restart'?'待确认重启':'待确认更新'),h('p',`实例 ${p.instance_id} · ${p.from_sha.slice(0,8)} → ${p.target_sha.slice(0,8)}`),
      h('small',`兼容与版本门禁已核定 · 方案到期 ${stamp(p.expires_at)}`),
      button('确认执行'+(p.action==='restart'?'重启':'更新'),()=>{if(window.confirm(`确认对 ${p.instance_id} 执行${p.action==='restart'?'重启':'代码更新'}，目标 ${p.target_sha}？`))void s.trustedRequest(p.action,p);},s.busy.value||s.overview.value?.settings.queriesEnabled||s.overview.value?.settings.notificationsEnabled)])),
    ...(v?.items||[]).map(r=>h('p',`${r.operation_id.slice(0,8)} · ${code(r.state)}${r.verified?' · 运行身份与健康已核对':''}`))]);
}
