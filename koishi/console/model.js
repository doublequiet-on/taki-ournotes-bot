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
  const fallback=s.modelRole.value==='fallback',proactive=s.modelRole.value==='proactive';
  const rolePicker=h('label',['管理配置',h('select',{'aria-label':'管理配置',value:s.modelRole.value,disabled:s.busy.value||s.modelDirty.value||s.webEdit.value?.dirty,onChange:e=>s.selectModelRole(e.target.value)},[h('option',{value:'primary'},'主模型'),h('option',{value:'fallback'},'DeepSeek 备用模型'),h('option',{value:'proactive'},'主动参与模型')])]);
  if(!value||!p)return h('section',{class:'model-settings'},[h('h2','模型与额度'),rolePicker,h('p','模型设置尚未取得'),button('读取模型设置',()=>s.refreshModel())]);
  const text=(obj,key,title,type='text',min=null,max=null)=>h('label',[title,h('input',{'aria-label':title,type,value:obj[key],min,max,
    onInput:e=>{obj[key]=type==='number'?Number(e.target.value):e.target.value;s.modelDirty.value=true;}})]);
  const flag=(key,title)=>h('label',{class:'toggle'},[h('input',{type:'checkbox',checked:p[key],onChange:e=>{p[key]=e.target.checked;s.modelDirty.value=true;}}),title]);
  const probe=value.capability;
  return h('section',{class:'model-settings'},[h('h2','模型与额度'),rolePicker,h('p',`${code(value.code)} · 密钥${value.key_configured?'已配置':'未配置'}`),
    !value.editable&&h('p',{role:'alert'},'模型配置目录不可安全写入，请修复后端配置目录的权限与服务可写路径。各模型配置均受此限制。'),
    !fallback&&!proactive&&s.budgetEdit?.value&&h('section',{class:'budget-editor'},[h('h3','全机器人每日主模型预算'),h('p',`当前生效：USD ${value.primary_budget.daily_usd}`),h('label',['每日上限（美元）',h('input',{type:'number',min:0,max:999999.999999,step:'0.01','aria-label':'每日主模型预算（美元）',value:s.budgetEdit.value.daily,onInput:e=>{s.budgetEdit.value.daily=e.target.value;s.budgetEdit.value.dirty=true;}})]),button('保存每日预算',()=>s.saveBudget(),s.busy.value||!s.budgetEdit.value.dirty),s.budgetEdit.value.dirty&&JSON.stringify(s.budgetEdit.value.guard)!==JSON.stringify(value.primary_budget.guard)&&button('核对最新预算后保留草稿',()=>{if(window.confirm(`服务器当前预算为 USD ${value.primary_budget.daily_usd}。保留你的金额并以此版本重试？`))s.budgetEdit.value.guard=value.primary_budget.guard;},s.busy.value),button('放弃预算草稿',()=>{s.budgetEdit.value=null;void s.refreshModel();},s.busy.value),h('small','即时生效并持久保存；0 表示普通账号直接使用备用模型。本人额度豁免继续按服务器名单生效。')]),
    h('div',{class:'form-grid'},[h('label',['服务商',h('select',{'aria-label':'服务商',value:p.provider,onChange:e=>{p.provider=e.target.value;p.wire_profile=p.provider==='openai'?'openai-responses-v1':p.provider+'-chat-v1';p.parallel_control=false;p.reasoning_effort=null;p.max_output_tokens=Math.min(p.max_output_tokens,p.wire_profile==='openai-responses-v1'?8192:1536);s.modelDirty.value=true;}},
      (fallback?['deepseek']:['deepseek','openai','openrouter']).map(v=>h('option',{value:v},v)))]),text(p,'model','模型 ID'),
      h('label',['API 地址',h('input',{value:p.wire_profile==='openai-responses-v1'?'https://api.openai.com/v1/responses':value.endpoints[p.provider],readonly:true,'aria-label':'API 地址'})]),
      h('label',['新密钥（留空保留）',h('input',{type:'password',autocomplete:'new-password','aria-label':'新密钥',value:s.modelKey.value,
        onInput:e=>{s.modelKey.value=e.target.value;s.modelDirty.value=true;}})]),
      text(p,'user_daily_limit',proactive?'主动参与每日额度（加权 Token）':'用户每日额度（加权 Token）','number',0,1e12),text(p,'group_daily_limit','群默认每日额度（加权 Token）','number',0,1e12),
      text(p,'user_rate_requests',proactive?'主动参与窗口内请求数':'每用户窗口内请求数','number',1,1000),text(p,'group_rate_requests','每群窗口内请求数','number',1,1000),
      text(p,'rate_window_seconds','频率窗口（秒）','number',1,3600)]),
    (p.provider==='openai'||p.provider==='deepseek')&&h('label',[p.provider==='deepseek'?'DeepSeek 推理强度':'OpenAI 推理强度',h('select',{'aria-label':p.provider==='deepseek'?'DeepSeek 推理强度':'OpenAI 推理强度',value:p.reasoning_effort||(p.provider==='deepseek'?'none':''),disabled:p.provider==='openai'&&p.wire_profile!=='openai-responses-v1',onChange:e=>{p.reasoning_effort=e.target.value||null;s.modelDirty.value=true;}},[(p.provider==='deepseek'?['none','low','high','max']:['','none','minimal','low','medium','high','xhigh','max']).map(v=>h('option',{value:v},p.provider==='deepseek'?({none:'关闭思考',low:'低',high:'高',max:'最高'}[v]):(v||'模型默认')))])]),
    p.provider==='openai'&&h('label',['OpenAI 接口',h('select',{'aria-label':'OpenAI 接口',value:p.wire_profile,onChange:e=>{p.wire_profile=e.target.value;p.reasoning_effort=null;s.modelDirty.value=true;}},['openai-responses-v1','openai-chat-v1'].map(v=>h('option',{value:v},v))) ]),
    h('p','各角色的模型分别保存。推理档位以所选模型支持为准；输出上限包含推理 Token。调整推理强度、Token 预算或额度时保留当前有效的能力和群服务批准；更换模型、接口、密钥或计价合同后需重新验收。更换服务商必须填写该服务商的新密钥。'),
    h('small','地址仅支持已登记的 HTTPS 接口。额度按北京时间每日结算，参考费用不是提供方账单。'),
    h('details',[h('summary','模型能力与默认估算价'),h('p','能力依据是管理员登记的信息；下方测试记录才表示该配置版本的实际探测结果。价格以每百万 Token 的微货币单位填写。'),
      text(p,'capability_evidence','能力依据编号'),text(p,'max_input_tokens','输入预算','number',8192,65536),text(p,'max_output_tokens','最大输出 Token','number',64,p.wire_profile==='openai-responses-v1'?8192:1536),
      flag('parallel_control','发送禁止并行工具参数'),flag('cache_usage','按登记合同读取缓存用量'),flag('reasoning_usage','按登记合同读取推理用量'),
      h('label',['币种',h('select',{'aria-label':'币种',value:p.price.currency,onChange:e=>{p.price.currency=e.target.value;s.modelDirty.value=true;}},['CNY','USD'].map(v=>h('option',{value:v},v)))]),
      ...[['input_rate','输入单价'],['cached_rate','缓存输入单价'],['output_rate','输出单价'],['reference_rate','固定参考单价']].map(([k,t])=>text(p.price,k,t,'number',1,1e12)),
      h('small','已有账本的参考币种和参考单价固定；改变它们会被拒绝。'),
      h('p',`当前价格：${value.price_status?.price_source||'未采集'} · ${stamp(value.price_status?.price_observed_at)} · ${value.price_status?.price_estimated===false?'已获取报价':'默认估算'}`)]),
    s.modelConflict.value&&h('div',[h('p',{role:'alert'},'模型设置版本冲突或保存未确认，草稿仍保留。'),button('以最新版本重试此草稿',()=>{if(window.confirm('将保留你的草稿，并以当前服务器版本作为保存基础，确认继续？')){s.modelGuard.value=value.guard;s.modelConflict.value=false;}})]),
    h('p',{role:'status'},s.modelDirty.value?'有未保存的 AI 设置':'当前显示已载入的 AI 设置'),
    button('保存 AI 设置',()=>s.saveModel(),s.busy.value||!value.editable||!s.modelDirty.value),button('放弃 AI 草稿并刷新',()=>{s.modelDirty.value=false;s.modelConflict.value=false;s.modelKey.value='';void s.refreshModel();},s.busy.value),
    !fallback&&webSearchSettings(s),
    proactive&&proactiveQuota(s),
    proactive&&h('p','用于主动观察后的判断与回复；不会自动切换主模型。保存不会启用群发言，仍需单独批准群服务与能力。'),
    fallback&&h('p','备用模型可单独配置；此处付费探测入口支持主模型和主动参与模型。'),h('h3','连接与能力测试'),h('p',probe?`${code(probe.state)} · ${code(probe.result?.code)}`:'此配置版本尚未进行实际测试'),
    button('发起一次能力测试',()=>{if(window.confirm('将向当前服务商发送一次合成工具测试，可能产生费用。确认执行？'))void s.probeModel();},
      fallback||s.busy.value||s.modelDirty.value||!value.key_configured||['intent','unknown','interrupted'].includes(probe?.state)),
    h('small','只在明确确认后调用；打开或刷新页面不会发起模型测试。'),
    h('details',[h('summary','AI 配置与测试记录'),...(value.audit||[]).map(a=>h('p',`${stamp(a.created_at)} · ${a.action} · ${code(a.state)} · ${a.config_version}`))])]);
}
function webSearchSettings(s){
  const value=s.model.value?.web_search,edit=s.webEdit.value;
  if(!value||!edit)return h('section',{class:'web-search-settings'},[h('h3','联网搜索'),h('p','请先配置模型，随后登记该版本的联网搜索验收记录。')]);
  const mark=()=>{edit.dirty=true;},blocked=s.busy.value||!value.editable;
  return h('section',{class:'web-search-settings'},[h('h3','联网搜索'),
    h('p',{role:'status'},`当前模型：${s.model.value.profile.model} · ${value.code==='enabled'?'已启用':value.code==='disabled'?'已关闭，验收记录保留':'尚未验收'}`),
    h('label',{class:'toggle'},[h('input',{type:'checkbox','aria-label':'启用联网搜索',checked:edit.enabled,disabled:blocked,onChange:e=>{edit.enabled=e.target.checked;mark();}}),'启用联网搜索']),
    h('label',['每次搜索请求的调用上限',h('input',{type:'number',min:1,max:2,step:1,'aria-label':'联网搜索调用上限',value:edit.max_calls,disabled:blocked,onInput:e=>{edit.max_calls=Number(e.target.value);mark();}})]),
    value.evidence_ref?h('div',[h('p',`搜索单价：${value.currency} ${value.unit_cost_micro/1e6} / 次；模型 Token 另计。`),h('p',{style:{overflowWrap:'anywhere'}},`验收依据：${value.evidence_ref}`)]):h('p','当前版本没有可恢复的批准，请先完成实际联网搜索验收。'),
    h('p','仅发送当前搜索问题，不上传整段群聊。费用沿用现有额度；启用后由模型按需要搜索。'),
    edit.conflict&&h('p',{role:'alert'},'服务器配置已变化或保存未确认；草稿保留，请先核对最新设置。'),
    button('保存联网搜索设置',()=>s.saveWeb(),blocked||!edit.dirty||edit.conflict||s.modelDirty.value||!Number.isInteger(edit.max_calls)||edit.max_calls<1||edit.max_calls>2),
    edit.conflict&&value.editable&&button('核对最新联网设置后保留草稿',()=>{if(window.confirm(`服务器当前联网搜索${value.enabled?'已启用':'已关闭'}，调用上限 ${value.max_calls}。保留草稿并以此版本重试？`)){edit.guard=value.guard;edit.conflict=false;}},s.busy.value),
    button('放弃联网草稿并刷新',()=>{s.webEdit.value=null;void s.refreshModel();},s.busy.value),
    h('small',edit.dirty?'有未保存的联网搜索设置':'当前显示已保存的联网搜索设置')]);
}
function proactiveQuota(s){
  const value=s.proactiveLimits.value,edit=s.proactiveEdit.value,items=s.groups.value?.sections?.groups?.data||[];
  const ids=[...new Set([...items.map(g=>g.group_id),...Object.keys(s.groups.value?.policy?.groups||{}),s.groupId.value,edit?.group_id].filter(Boolean))];
  const fields=[['max_calls_per_hour','每小时最多模型请求',120],['max_replies_per_hour','每小时最多主动回复',120],['cooldown_seconds','回复冷却（秒）',86400]];
  const mark=()=>{edit.dirty=true;};
  return h('section',{class:'proactive-quota'},[h('h3','主动参与限额'),
    h('label',['目标群',h('select',{'aria-label':'主动参与目标群',value:edit?.group_id||'',disabled:s.busy.value||edit?.dirty,onChange:e=>s.refreshProactiveLimits(e.target.value)},[
      h('option',{value:''},'请选择群'),...ids.map(id=>h('option',{value:id},items.find(g=>g.group_id===id)?.policy?.label||s.groups.value?.policy?.groups?.[id]?.label||`群 …${id.slice(-6)}`))])]),
    button('刷新可选群',()=>s.load(s.groups,'groups/list'),s.busy.value),
    !ids.length&&h('p','暂无群记录，请先在群管理中关联目标群。'),
    edit?.dirty&&h('p','请先保存或放弃当前群限额，再切换目标群。'),
    edit&&h('div',[
      h('p',value?.approved?(value.capability_approved?'本群已有服务和能力批准':'群服务已批准，能力尚待核对'):'本群尚未批准自动发言；可先保存限额'),
      h('label',{class:'toggle'},[h('input',{type:'checkbox',checked:edit.limits.enabled,onChange:e=>{edit.limits.enabled=e.target.checked;mark();}}),'启用主动参与（仍须本群批准）']),
      h('div',{class:'form-grid'},fields.map(([key,title,max])=>h('label',[title,h('input',{type:'number',min:0,max,step:1,'aria-label':title,value:edit.limits[key],onInput:e=>{edit.limits[key]=Number(e.target.value);mark();}})]))),
      h('label',[`每小时费用上限（${value?.currency||'当前币种'}）`,h('input',{type:'number',min:0,max:1000000,step:'0.01','aria-label':'主动参与每小时费用上限',value:edit.limits.max_cost_micro_per_hour/1e6,onInput:e=>{edit.limits.max_cost_micro_per_hour=Math.round(Number(e.target.value)*1e6);mark();}})]),
      h('p','模型请求数包含判断、略过、暂缓和工具续接；成功回复另计次数。费用上限也要容纳整轮预留，达到限额后停止主动参与。'),
      edit.conflict&&h('p',{role:'alert'},'服务器配置已变化或保存未确认；草稿保留，请先读取最新值核对。'),
      button('保存主动参与限额',()=>s.saveProactiveLimits(),s.busy.value||!edit.dirty||edit.conflict||s.modelDirty.value),
      edit.conflict&&value?.group_id===edit.group_id&&button('核对最新限额后保留草稿',()=>{if(window.confirm(`服务器当前限额：每小时 ${value.limits.max_calls_per_hour} 次请求、${value.limits.max_replies_per_hour} 次回复、${value.currency} ${value.limits.max_cost_micro_per_hour/1e6}。保留你的草稿并以此版本重试？`)){edit.guard=value.guard;edit.conflict=false;}},s.busy.value),
      button('放弃限额草稿并刷新',()=>s.refreshProactiveLimits(edit.group_id,true),s.busy.value),
      h('small',edit.dirty?'有未保存的主动参与限额':'当前显示已保存的主动参与限额')])]);
}
export function groupQuota(s){
  if(s.modelRole.value!=='primary')return h('section',[h('h3','群 AI 额度'),h('p','群额度管理使用主模型配置；各模型的默认额度在上方单独保存。'),button('切换到主模型配置',()=>s.selectModelRole('primary'),s.modelDirty.value||s.busy.value)]);
  const v=s.groupUsage.value,id=s.groupId.value,model=s.model.value;
  const override=model?.groups?.[id];
  return h('section',[h('h3','群 AI 额度'),v?h('p',`今日已用与预留 ${quantity(v.spent_and_reserved)} / ${v.daily_limit} · ${v.rate_window_seconds} 秒内 ${v.rate_requests} 次`):h('p','群额度尚未取得'),
    h('p',override?'此群使用独立额度':'此群继承默认额度'),
    h('form',{key:id,class:'quota-form',onSubmit:e=>{e.preventDefault();const d=new FormData(e.target);void s.saveModel(id,{group_daily_limit:Number(d.get('daily')),group_rate_requests:Number(d.get('rate'))});}},[
      h('label',['每日额度',h('input',{name:'daily',type:'number',min:0,max:1e12,required:true,placeholder:String(v?.daily_limit??''),'aria-label':'此群每日额度'})]),
      h('label',['窗口内请求数',h('input',{name:'rate',type:'number',min:1,max:1000,required:true,placeholder:String(v?.rate_requests??''),'aria-label':'此群频率'})]),
      h('button',{type:'submit',disabled:s.busy.value||s.modelDirty.value||!model?.editable},'保存此群额度')]),
    button('恢复默认额度',()=>s.saveModel(id,null),s.busy.value||s.modelDirty.value||!model?.editable||!override)]);
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
