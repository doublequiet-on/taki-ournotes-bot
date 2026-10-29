import { ref, send, toRaw } from './runtime.js';
export function sessionState() {
  const modelFailure=error=>({model_config_permissions:'配置目录不可安全写入，请修复后端目录权限和可写路径。',model_config_conflict:'服务器配置已变化，请核对最新值后再保存。',primary_budget_configuration:'请输入非负美元金额，最多六位小数。',model_admin_busy:'另一个模型管理操作正在执行，请稍后重试。',model_not_configured:'请先保存模型配置。',chat_service_profile_mismatch:'主动参与模型版本已变化，请先重新核对本群批准。',chat_service_currency_mismatch:'服务预算币种与当前模型不一致，请核对批准。',chat_service_configuration:'限额配置需要核对；启用时请求、回复和费用上限都须为正数。',proactive_limits_unsupported:'每小时请求和回复上限均不能超过120。'})[String(error?.message||error)]||String(error?.message||error);
  const logged=ref(false), busy=ref(false), message=ref(''), readMessage=ref(''), stale=ref(false), dirty=ref(false), conflict=ref(false);
  const overview=ref(null), requests=ref(null), detail=ref(null), sources=ref(null), metrics=ref(null), groups=ref(null), notices=ref(null), draft=ref(null), guard=ref(null);
  const groupId=ref(''),groupDraft=ref(null),groupRevision=ref(null),groupDirty=ref(false),groupConflict=ref(false);
  const jobs=ref(null),jobDetail=ref(null),audit=ref(null),host=ref(null),noticeTarget=ref(null),controls=ref(null),pendingJobs=new Map(),pendingControls=new Map();
  const model=ref(null),modelDraft=ref(null),modelGuard=ref(null),modelKey=ref(''),modelDirty=ref(false),modelConflict=ref(false),groupUsage=ref(null),trusted=ref(null);
  const library=ref(null),libraryKind=ref('knowledge'),libraryText=ref(''),libraryPreview=ref(null),libraryConfirmed=ref(false);
  const modelRole=ref('primary'),budgetEdit=ref(null),proactiveLimits=ref(null),proactiveEdit=ref(null),pendingProactive=new Map();
  const chat=ref(null),chatGroup=ref(null),memory=ref(null),memoryIds=ref('');
  const groupRecoveryBlocked=ref(false);
  const pendingProbes=new Map(),pendingTrusted=new Map();
  let generation=0,reauthNotice='',groupEpochBeforeDisconnect;
  const sequences=new Map();
  const failed=new Set();
  function clearProtected(keepEdits=false) {
    const hadDraft=dirty.value||groupDirty.value||modelDirty.value||budgetEdit.value?.dirty||proactiveEdit.value?.dirty,hadModelKey=!!modelKey.value;
    if(keepEdits&&groupDirty.value&&groupEpochBeforeDisconnect===undefined)groupEpochBeforeDisconnect=guard.value?.gateway_epoch||null;
    generation++;logged.value=false;failed.clear();sequences.clear();stale.value=false;readMessage.value='';
    for(const value of [overview,requests,detail,sources,metrics,groups,notices,jobs,jobDetail,audit,host,noticeTarget,controls,model,groupUsage,trusted,chat,chatGroup,memory,library,libraryPreview,proactiveLimits])value.value=null;
    if(!keepEdits||!dirty.value){draft.value=null;guard.value=null;dirty.value=false;conflict.value=false;}
    if(!keepEdits||!groupDirty.value){groupId.value='';groupDraft.value=null;groupRevision.value=null;groupDirty.value=false;groupConflict.value=false;groupRecoveryBlocked.value=false;groupEpochBeforeDisconnect=undefined;}
    if(!keepEdits)budgetEdit.value=null;
    if(!keepEdits||!proactiveEdit.value?.dirty)proactiveEdit.value=null;
    if(!keepEdits||!modelDirty.value){modelDraft.value=null;modelGuard.value=null;modelDirty.value=false;modelConflict.value=false;}
    modelKey.value='';memoryIds.value='';libraryText.value='';libraryConfirmed.value=false;
    if(!keepEdits){pendingProactive.clear();pendingProbes.clear();pendingTrusted.clear();pendingJobs.clear();pendingControls.clear();reauthNotice='';}
    return {hadDraft,hadModelKey};
  }
  function unauthorized(error) {
    if(error!=='unauthorized'&&error?.message!=='unauthorized')return false;
    suspend();return true;
  }
  function suspend() {
    const {hadDraft,hadModelKey}=clearProtected(true);
    if(hadModelKey)reauthNotice='设置草稿已保留；未提交的密钥已清除，请重新输入。';
    else if(hadDraft&&!reauthNotice)reauthNotice='未保存的设置草稿已保留，请核对后继续。';
    message.value='管理会话已过期，请重新登录。'+reauthNotice;
  }
  function captureEdits() {
    // RPC payloads are JSON values; serialization also unwraps nested Vue proxies.
    const copy=value=>value===undefined?null:JSON.parse(JSON.stringify(value));
    return {schema:1,modelRole:modelRole.value,notice:modelKey.value?'设置草稿已保留；未提交的密钥已清除，请重新输入。':reauthNotice,
      budget:budgetEdit.value?.dirty?copy(budgetEdit.value):null,
      proactive:proactiveEdit.value?.dirty?copy(proactiveEdit.value):null,proactiveOperations:copy([...pendingProactive]),
      global:dirty.value?{draft:copy(draft.value),guard:copy(guard.value),conflict:conflict.value}:null,
      group:groupDirty.value?{id:groupId.value,draft:copy(groupDraft.value),revision:groupRevision.value,epoch:groupEpochBeforeDisconnect===undefined?(guard.value?.gateway_epoch||null):groupEpochBeforeDisconnect}:null,
      model:modelDirty.value?{role:modelRole.value,draft:copy(modelDraft.value),guard:copy(modelGuard.value),conflict:modelConflict.value}:null,
      jobs:copy([...pendingJobs]),controls:copy([...pendingControls]),probes:copy([...pendingProbes]),trusted:copy([...pendingTrusted])};
  }
  function restoreEdits(snapshot) {
    if(!snapshot||snapshot.schema!==1)return;
    reauthNotice=snapshot.notice||'';
    if(['primary','fallback','proactive'].includes(snapshot.modelRole))modelRole.value=snapshot.modelRole;
    if(snapshot.budget)budgetEdit.value=snapshot.budget;
    if(snapshot.proactive)proactiveEdit.value=snapshot.proactive;
    if(snapshot.global){draft.value=snapshot.global.draft;guard.value=snapshot.global.guard;dirty.value=true;conflict.value=!!snapshot.global.conflict;}
    if(snapshot.group){groupId.value=snapshot.group.id;groupDraft.value=snapshot.group.draft;groupRevision.value=snapshot.group.revision;groupDirty.value=true;
      groupEpochBeforeDisconnect=snapshot.group.epoch;groupRecoveryBlocked.value=true;}
    if(snapshot.model){modelRole.value=['fallback','proactive'].includes(snapshot.model.role)?snapshot.model.role:'primary';modelDraft.value=snapshot.model.draft;modelGuard.value=snapshot.model.guard;modelDirty.value=true;modelConflict.value=!!snapshot.model.conflict;}
    for(const [map,entries] of [[pendingProactive,snapshot.proactiveOperations],[pendingJobs,snapshot.jobs],[pendingControls,snapshot.controls],[pendingProbes,snapshot.probes],[pendingTrusted,snapshot.trusted]])
      if(Array.isArray(entries))for(const [key,value] of entries)map.set(key,value);
    if(reauthNotice)message.value='管理会话已过期，请重新登录。'+reauthNotice;
  }
  async function rpc(method,...args) {
    let timer;
    try { return await Promise.race([send('taki/'+method,...args),new Promise((_,reject) => { timer=setTimeout(() => reject(Error('timeout')),2500); })]); }
    finally { clearTimeout(timer); }
  }
  async function load(target,method,...args) {
    const token=generation, seq=(sequences.get(target)||0)+1; sequences.set(target,seq);
    try {
      const value=await rpc(method,...args);
      if(token!==generation || seq!==sequences.get(target))return;
      const backendMissing=method==='overview'&&value.backend?.sections?.backend?.availability==='unavailable';
      if(backendMissing&&target.value?.backend?.sections?.requests) {
        value.backend={...target.value.backend,sections:Object.fromEntries(Object.entries(target.value.backend.sections).map(([key,item])=>[key,{...item,stale:true,reason_code:'backend_unavailable'}]))};
      }
      target.value=value;
      if(backendMissing){failed.add(target);readMessage.value='后端暂不可用，保留最近请求数据和草稿；当前健康未就绪。';}else failed.delete(target);
      stale.value=failed.size>0;
      if(!stale.value&&/^(读取失败|后端暂不可用)/.test(readMessage.value))readMessage.value='连接已恢复，草稿未被覆盖。';
      if(method==='overview') {
        if(groupDirty.value&&groupEpochBeforeDisconnect!==undefined){groupRecoveryBlocked.value=!groupEpochBeforeDisconnect||groupEpochBeforeDisconnect!==value.guard?.gateway_epoch;if(groupRecoveryBlocked.value)groupConflict.value=true;}
        if(!groupRecoveryBlocked.value)groupEpochBeforeDisconnect=undefined;
        if(!dirty.value) { draft.value=structuredClone(value.settings); guard.value=value.guard; }
        else if(JSON.stringify(guard.value)!==JSON.stringify(value.guard))conflict.value=true;
      }
      if(method==='groups/list'&&groupId.value) {
        if(!groupDirty.value)selectGroup(groupId.value);
        else if(groupRevision.value!==value.policy?.revision)groupConflict.value=true;
      }
      if(['model/settings','model/fallback/settings','model/proactive/settings'].includes(method))acceptModel(value);
    } catch (error) {
      if(token!==generation||seq!==sequences.get(target))return;
      if(unauthorized(error))return;
      failed.add(target); stale.value=true; readMessage.value='读取失败，保留上次数据和草稿。会话过期时请重新登录。';
      if(target.value?.sections)for(const item of Object.values(target.value.sections))item.stale=true;
    }
  }
  async function login(password) {
    busy.value=true;
    try { await rpc('login',password); logged.value=true; message.value=reauthNotice;reauthNotice=''; await load(overview,'overview'); }
    catch { message.value='登录失败，请检查密码并稍后重试。'; }
    finally { busy.value=false; }
  }
  async function logout() {
    try{await rpc('logout');message.value='';}catch{message.value='退出未确认；当前页面已清除。';}finally{clearProtected();}
  }
  async function save() {
    const token=generation, submitted=JSON.stringify(draft.value);
    busy.value=true;
    try { const result=await rpc('settings/update',JSON.parse(submitted),guard.value);
      if(token!==generation)return;
      dirty.value=JSON.stringify(draft.value)!==submitted; conflict.value=false;
      guard.value=result.guard; message.value=dirty.value?'已保存提交时的设置，后续编辑仍未保存。':'已保存生效；在途查询继续完成。'; await load(overview,'overview'); }
    catch(error) {if(token!==generation||unauthorized(error))return;dirty.value=true;conflict.value=true;message.value=error==='invalid_settings'?'设置格式不正确，请检查群 OpenID；草稿已保留。':'保存未确认。草稿已保留，请核对服务器当前值。';await load(overview,'overview');}
    finally { busy.value=false; }
  }
  function selectGroup(id) {
    if(groupDirty.value){if(id!==groupId.value)message.value='请先保存或放弃当前群草稿。';return;}
    const changed=id!==groupId.value;
    groupId.value=id;groupDraft.value=structuredClone(toRaw(groups.value?.policy?.groups?.[id])||{label:'',queries:'inherit',notifications:'inherit',natural_query:'inherit'});
    groupRevision.value=groups.value?.policy?.revision;groupDirty.value=false;groupConflict.value=false;groupRecoveryBlocked.value=false;
    noticeTarget.value=null;if(changed){chatGroup.value=null;memory.value=null;memoryIds.value='';}
    groupUsage.value=null;
    if(/^[A-Za-z0-9_-]{1,128}$/.test(id))void load(noticeTarget,'ops/notice-target',id);
    if(/^[A-Za-z0-9_-]{1,128}$/.test(id)){void load(groupUsage,'model/group',id);void load(chatGroup,'chat/group/status',id);void load(memory,'chat/group/memory',id);}
  }
  async function saveGroup() {
    if(groupRecoveryBlocked.value){message.value='网关状态尚未确认或已变化，请核对群草稿后继续。';return;}
    const token=generation, id=groupId.value, submitted=JSON.stringify(groupDraft.value);
    busy.value=true;
    try {
      const result=await rpc('groups/update',{group_id:id,policy:JSON.parse(submitted),revision:groupRevision.value});
      if(token!==generation)return;
      if(groupId.value===id){
        groupRevision.value=result.revision;groupDirty.value=JSON.stringify(groupDraft.value)!==submitted;groupConflict.value=false;
      }
      message.value=groupDirty.value?'已保存提交时的群策略，后续编辑仍未保存。':'群策略已保存。新查询使用新规则，在途查询继续完成。';
      await load(groups,'groups/list');
    }catch(error){if(token!==generation||unauthorized(error))return;if(groupId.value===id){groupDirty.value=true;groupConflict.value=true;}
      message.value=['invalid_group_id','invalid_group_policy'].includes(error)?'群 OpenID 或策略格式不正确，备注最多80字；草稿已保留。':'群策略保存未确认或版本冲突。草稿已保留，请先核对服务器值。';await load(groups,'groups/list');}
    finally{busy.value=false;}
  }
  async function startJob(action,params={}) {
    const token=generation,key=JSON.stringify([action,params]);
    if(!pendingJobs.has(key))pendingJobs.set(key,crypto.randomUUID().replaceAll('-',''));
    busy.value=true;
    try{const job=await rpc('ops/jobs/start',{operation_id:pendingJobs.get(key),action,params});
      if(token!==generation)return;
      pendingJobs.delete(key);message.value='操作已受理：'+job.id.slice(0,8);await load(jobs,'ops/jobs/list');
    }catch(error){if(token!==generation||unauthorized(error))return;message.value='操作受理未确认，请查看任务记录；重试将复用同一操作标识。';}
    finally{busy.value=false;}
  }
  async function exportDiagnostic(id) {
    const token=generation;
    try{const value=await rpc('ops/export',...(id?[id]:[]));if(token!==generation)return;
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='taki-diagnostic.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){if(token!==generation||unauthorized(error))return;message.value='诊断导出失败，请稍后重试。';}
  }
  async function controlRequest(action,groupIds=[],targetId=null) {
    if(dirty.value||groupDirty.value){message.value='请先保存或还原当前草稿。';return;}
    const token=generation,key=JSON.stringify([action,groupIds,targetId]);busy.value=true;
    try{
      if(!pendingControls.has(key)){
        const snapshot=await rpc('ops/controls');if(token!==generation)return;controls.value=snapshot;
        const local=action==='local'||targetId&&snapshot.items.find(e=>e.id===targetId)?.control==='local';
        if(!snapshot.available||!snapshot.settings_persistent_matches_live||local&&!snapshot.groups_persistent_matches_live)throw Error('persistent_conflict');
        pendingControls.set(key,{operation_id:crypto.randomUUID().replaceAll('-',''),guard:snapshot.guard,group_revision:snapshot.group_revision,
          ...(targetId?{target_id:targetId}:{action,group_ids:groupIds})});
      }
      const result=await rpc(targetId?'ops/controls/restore':'ops/controls/start',pendingControls.get(key));if(token!==generation)return;
      if(result.state!=='intent')pendingControls.delete(key);
      message.value=result.state==='applied'?(result.no_change?'当前设置无需变更。':'管理操作已生效，在途查询继续完成。'):'操作结果需要核对：'+result.state;
      await Promise.all([load(controls,'ops/controls'),load(overview,'overview'),load(groups,'groups/list'),load(audit,'ops/audit')]);
    }catch(error){if(token!==generation||unauthorized(error))return;
      const reason=String(error?.message||error);
      if(/conflict|shape|parameters|not_restorable|capacity/.test(reason))pendingControls.delete(key);
      message.value=/conflict/.test(reason)?'设置已变化，操作未覆盖新值。请核对当前设置后重试。':'管理操作结果未确认；重试将复用操作标识。';
      await load(controls,'ops/controls');
    }finally{busy.value=false;}
  }
  function acceptModel(value){
    if(value.primary_budget&&!budgetEdit.value?.dirty)budgetEdit.value={daily:value.primary_budget.daily_usd,guard:value.primary_budget.guard,dirty:false};
    if(['succeeded','failed'].includes(value.capability?.state))pendingProbes.delete((value.role||modelRole.value)+'/'+value.profile?.config_version);
    if(!modelDirty.value){modelDraft.value=structuredClone(toRaw(value.profile));modelGuard.value=value.guard;}
    else if(JSON.stringify(modelGuard.value)!==JSON.stringify(value.guard))modelConflict.value=true;
  }
  function refreshModel(){return load(model,modelRole.value==='primary'?'model/settings':`model/${modelRole.value}/settings`);}
  function selectModelRole(role){
    if(modelDirty.value){message.value='请先保存或放弃当前模型草稿，再切换模型配置。';return;}
    if(role==='proactive'&&!groups.value)void load(groups,'groups/list');
    modelRole.value=role;model.value=null;modelDraft.value=null;modelGuard.value=null;modelKey.value='';void refreshModel();
  }
  async function refreshProactiveLimits(id=proactiveEdit.value?.group_id||groupId.value,discard=false){
    if(!id)return;
    if(proactiveEdit.value?.dirty&&proactiveEdit.value.group_id!==id){message.value='请先保存或放弃当前群的主动参与限额。';return;}
    const token=generation,seq=(sequences.get(proactiveLimits)||0)+1;sequences.set(proactiveLimits,seq);
    try{
      const value=await rpc('model/proactive/limits/settings',id);if(token!==generation||seq!==sequences.get(proactiveLimits))return;
      proactiveLimits.value=value;
      if(discard||!proactiveEdit.value?.dirty)proactiveEdit.value={group_id:id,limits:structuredClone(value.limits),guard:value.guard,dirty:false,conflict:false};
      else if(JSON.stringify(proactiveEdit.value.guard)!==JSON.stringify(value.guard))proactiveEdit.value.conflict=true;
    }catch(error){if(token!==generation||seq!==sequences.get(proactiveLimits)||unauthorized(error))return;message.value='主动参与限额未读取：'+modelFailure(error);}
  }
  async function saveProactiveLimits(){
    if(!proactiveEdit.value?.dirty)return;
    const token=generation,submitted=JSON.stringify(proactiveEdit.value),edit=JSON.parse(submitted);
    const key=JSON.stringify([edit.group_id,edit.guard,edit.limits]);
    if(!pendingProactive.has(key))pendingProactive.set(key,crypto.randomUUID().replaceAll('-',''));
    busy.value=true;
    try{
      const result=await rpc('model/proactive/limits/save',{operation_id:pendingProactive.get(key),group_id:edit.group_id,guard:edit.guard,limits:edit.limits});
      if(token!==generation)return;pendingProactive.delete(key);
      if(proactiveEdit.value?.group_id===edit.group_id){
        proactiveLimits.value=result;
        proactiveEdit.value.dirty=JSON.stringify(proactiveEdit.value.limits)!==JSON.stringify(edit.limits);
        proactiveEdit.value.guard=result.guard;proactiveEdit.value.conflict=false;
      }
      message.value=result.approved&&result.capability_approved?'主动参与限额已保存。后续任务使用新限额。':'主动参与限额已保存，群服务或能力尚待批准，暂不自动发言。';
    }catch(error){if(token!==generation||unauthorized(error))return;
      if(proactiveEdit.value)proactiveEdit.value.conflict=true;message.value='主动参与限额未确认，草稿已保留：'+modelFailure(error);
      await refreshProactiveLimits(edit.group_id);
    }finally{busy.value=false;}
  }
  async function saveBudget(){
    if(!/^\d{1,6}(?:\.\d{1,6})?$/.test(budgetEdit.value?.daily||'')){message.value='请输入非负美元金额，最多六位小数。';return;}
    const token=generation,snapshot=JSON.stringify(budgetEdit.value);busy.value=true;
    try{const result=await rpc('model/budget/save',{operation_id:crypto.randomUUID().replaceAll('-',''),guard:JSON.parse(snapshot).guard,daily_usd:JSON.parse(snapshot).daily});if(token!==generation)return;if(JSON.stringify(budgetEdit.value)===snapshot)budgetEdit.value.dirty=false;budgetEdit.value.guard=result.primary_budget.guard;message.value='每日主模型预算已保存，重启后仍有效。';await Promise.all([refreshModel(),load(overview,'overview')]);}
    catch(error){if(token!==generation||unauthorized(error))return;message.value='预算未保存：'+modelFailure(error);}
    finally{busy.value=false;}
  }
  async function saveModel(group=null,limits=null){
    if(group&&modelRole.value!=='primary'){message.value='群额度需要在主模型配置中保存。';return;}
    const token=generation,profile=JSON.stringify(group?model.value?.profile:modelDraft.value),key=modelKey.value;
    if(group&&modelDirty.value){message.value='请先保存或放弃模型设置草稿。';return;}
    busy.value=true;
    try{
      let groups=structuredClone(toRaw(model.value?.groups)||{});
      if(group){if(limits===null)delete groups[group];else groups={...groups,[group]:limits};}
      const result=await rpc(modelRole.value==='primary'?'model/save':`model/${modelRole.value}/save`,{operation_id:crypto.randomUUID().replaceAll('-',''),guard:group?model.value.guard:modelGuard.value,
        profile:JSON.parse(profile),groups,api_key:group?'':key,api_url:JSON.parse(profile).wire_profile==='openai-responses-v1'?'https://api.openai.com/v1/responses':model.value.endpoints[JSON.parse(profile).provider]});
      if(token!==generation)return;
      if(!group)modelDirty.value=JSON.stringify(modelDraft.value)!==profile;
      modelConflict.value=false;model.value=result;modelGuard.value=result.guard;acceptModel(result);message.value='AI 设置已按新版本保存。';
      if(modelRole.value==='proactive'&&proactiveEdit.value&&proactiveEdit.value.guard.model_fingerprint!==result.guard.fingerprint)proactiveEdit.value.conflict=true;
      if(group)await load(groupUsage,'model/group',group);
    }catch(error){if(token!==generation||unauthorized(error))return;modelConflict.value=true;
      message.value='AI 设置未确认，请核对服务器值：'+modelFailure(error);await refreshModel();
    }finally{modelKey.value='';busy.value=false;}
  }
  async function probeModel(){
    const token=generation,version=model.value?.profile?.config_version,probeKey=modelRole.value+'/'+version;
    if(modelDirty.value||!version||modelRole.value==='fallback')return;
    if(!pendingProbes.has(probeKey))pendingProbes.set(probeKey,{operation_id:crypto.randomUUID().replaceAll('-',''),guard:model.value.guard,confirm:'billable_capability_probe'});
    busy.value=true;
    try{const value=await rpc(modelRole.value==='proactive'?'model/proactive/probe':'model/probe',pendingProbes.get(probeKey));if(token!==generation)return;
      model.value=value;acceptModel(value);message.value='能力测试已受理；用量与结果将单独记录。';
    }catch(error){if(token!==generation||unauthorized(error))return;message.value='能力测试结果未确认，请刷新记录；不会自动重复调用。';}
    finally{busy.value=false;}
  }
  async function trustedRequest(action,plan=null){
    const token=generation;busy.value=true;
    try{
      let result;
      if(!plan)result=await rpc('ops/trusted/prepare',action);
      else{
        if(!pendingTrusted.has(plan.plan_id))pendingTrusted.set(plan.plan_id,{operation_id:crypto.randomUUID().replaceAll('-',''),plan,
          confirmation:`${plan.action}:${plan.instance_id}:${plan.target_sha}`});
        result=await rpc('ops/trusted/start',pendingTrusted.get(plan.plan_id));
      }
      if(token!==generation)return;trusted.value=result;message.value=plan?'已提交；以控制器回读的运行版本和健康结果为准。':'待执行版本已核定，请核对后确认。';
    }catch(error){if(token!==generation||unauthorized(error))return;message.value='控制器状态需要核对：'+String(error?.message||error);await load(trusted,'ops/trusted');}
    finally{busy.value=false;}
  }
  async function updateMemory(action){
    if(!memory.value||busy.value||!guard.value?.gateway_epoch)return;
    const id=groupId.value,token=generation;
    const mutation={action,expected_revision:memory.value.revision};
    if(action==='delete')mutation.message_ids=memoryIds.value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
    busy.value=true;
    try{
      await rpc('chat/memory/update',{group_id:id,epoch:guard.value.gateway_epoch,mutation});
      if(token!==generation)return;
      message.value='群记忆删除已确认，关联摘要及在途回答已失效。';
      if(groupId.value===id)memoryIds.value='';
    }catch(error){if(token!==generation||unauthorized(error))return;message.value='删除未确认或记忆版本已变化，请刷新核对后再操作；不会自动重试。';}
    finally{busy.value=false;if(token===generation&&groupId.value===id)await load(memory,'chat/group/memory',id);}
  }
  async function refreshLibrary(offset=0){
    const previous=JSON.stringify(library.value?.guard),kind=libraryKind.value;
    await load(library,'library/status',{kind,offset});
    if(kind===libraryKind.value&&offset>0&&previous&&previous!==JSON.stringify(library.value?.guard)){message.value='资料版本已变化，已回到第一页。';await load(library,'library/status',{kind,offset:0});}
  }
  function selectLibrary(kind){if(busy.value)return;libraryKind.value=kind;library.value=null;libraryPreview.value=null;libraryText.value='';libraryConfirmed.value=false;void refreshLibrary();}
  async function libraryAction(action,extra={}){
    if(busy.value||!guard.value?.gateway_epoch)return;
    const token=generation,kind=libraryKind.value,text=libraryText.value;busy.value=true;
    try{
      let payload=extra;
      if(action==='preview')payload={kind,content:libraryText.value};
      if(action==='publish')payload={preview_id:libraryPreview.value?.preview_id,confirm_reviewed:libraryConfirmed.value};
      if(action==='withdraw')payload={kind,id:extra.id,reason:extra.reason,guard:library.value?.guard,confirm_withdraw:true};
      const result=await rpc('library/'+action,{epoch:guard.value.gateway_epoch,payload});
      if(token!==generation||kind!==libraryKind.value)return;
      if(['preview','compare'].includes(action)&&text!==libraryText.value){message.value='内容已变化，请重新生成预览。';return;}
      if(['preview','compare'].includes(action)){libraryPreview.value=result;libraryConfirmed.value=false;message.value='差异预览已生成，请核对内容、来源及人工确认记录。';}
      else{libraryPreview.value=null;libraryConfirmed.value=false;message.value='资料操作已确认，当前状态已刷新。';await refreshLibrary();}
    }catch(error){if(token!==generation||unauthorized(error))return;libraryPreview.value=null;libraryConfirmed.value=false;message.value='资料操作未确认：'+String(error?.message||error)+'。请刷新核对后重新预览，不会自动重试。';}
    finally{busy.value=false;}
  }
  return {proactiveLimits,proactiveEdit,refreshProactiveLimits,saveProactiveLimits,library,libraryKind,libraryText,libraryPreview,libraryConfirmed,refreshLibrary,selectLibrary,libraryAction,budgetEdit,saveBudget,modelRole,refreshModel,selectModelRole,chat,chatGroup,memory,memoryIds,updateMemory,model,modelDraft,modelGuard,modelKey,modelDirty,modelConflict,groupUsage,trusted,saveModel,probeModel,trustedRequest,
    jobs,jobDetail,audit,host,noticeTarget,controls,startControl:(action,ids)=>controlRequest(action,ids),restoreControl:id=>controlRequest('restore',[],id),startJob,exportDiagnostic,logged,busy,message,readMessage,stale,dirty,conflict,overview,requests,detail,sources,metrics,groups,notices,draft,guard,rpc,load,login,logout,save,
    groupId,groupDraft,groupRevision,groupDirty,groupConflict,groupRecoveryBlocked,selectGroup,saveGroup,suspend,captureEdits,restoreEdits,invalidate(){generation++;}};
}
