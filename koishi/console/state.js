import { ref, send, toRaw } from './runtime.js';
export function sessionState() {
  const logged=ref(false), busy=ref(false), message=ref(''), stale=ref(false), dirty=ref(false), conflict=ref(false);
  const overview=ref(null), requests=ref(null), detail=ref(null), sources=ref(null), metrics=ref(null), groups=ref(null), notices=ref(null), draft=ref(null), guard=ref(null);
  const groupId=ref(''),groupDraft=ref(null),groupRevision=ref(null),groupDirty=ref(false),groupConflict=ref(false);
  const jobs=ref(null),jobDetail=ref(null),audit=ref(null),host=ref(null),noticeTarget=ref(null),controls=ref(null),pendingJobs=new Map(),pendingControls=new Map();
  const model=ref(null),modelDraft=ref(null),modelGuard=ref(null),modelKey=ref(''),modelDirty=ref(false),modelConflict=ref(false),groupUsage=ref(null),trusted=ref(null);
  const pendingProbes=new Map(),pendingTrusted=new Map();
  let generation=0;
  const sequences=new Map();
  const failed=new Set();
  function clearProtected() {
    generation++;logged.value=false;failed.clear();sequences.clear();stale.value=false;
    for(const value of [overview,requests,detail,sources,metrics,groups,notices,draft,guard,jobs,jobDetail,audit,host,noticeTarget,controls,model,modelDraft,modelGuard,groupUsage,trusted])value.value=null;
    modelKey.value='';modelDirty.value=false;modelConflict.value=false;pendingProbes.clear();pendingTrusted.clear();
    pendingJobs.clear();pendingControls.clear();
    dirty.value=false;conflict.value=false;groupId.value='';groupDraft.value=null;groupRevision.value=null;groupDirty.value=false;groupConflict.value=false;
  }
  function unauthorized(error) {
    if(error!=='unauthorized'&&error?.message!=='unauthorized')return false;
    clearProtected();message.value='管理会话已过期，请重新登录。';return true;
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
      if(backendMissing){failed.add(target);message.value='后端暂不可用，保留最近请求数据和草稿；当前健康未就绪。';}else failed.delete(target);
      stale.value=failed.size>0;
      if(!stale.value&&/^(读取失败|后端暂不可用)/.test(message.value))message.value='连接已恢复，草稿未被覆盖。';
      if(method==='overview') {
        if(!dirty.value) { draft.value=structuredClone(value.settings); guard.value=value.guard; }
        else if(JSON.stringify(guard.value)!==JSON.stringify(value.guard))conflict.value=true;
      }
      if(method==='groups/list'&&groupId.value) {
        if(!groupDirty.value)selectGroup(groupId.value);
        else if(groupRevision.value!==value.policy?.revision)groupConflict.value=true;
      }
      if(method==='model/settings')acceptModel(value);
    } catch (error) {
      if(token!==generation||seq!==sequences.get(target))return;
      if(unauthorized(error))return;
      failed.add(target); stale.value=true; message.value='读取失败，保留上次数据和草稿。会话过期时请重新登录。';
      if(target.value?.sections)for(const item of Object.values(target.value.sections))item.stale=true;
    }
  }
  async function login(password) {
    busy.value=true;
    try { await rpc('login',password); logged.value=true; message.value=''; await load(overview,'overview'); }
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
    groupId.value=id;groupDraft.value=structuredClone(toRaw(groups.value?.policy?.groups?.[id])||{label:'',queries:'inherit',notifications:'inherit',natural_query:'inherit'});
    groupRevision.value=groups.value?.policy?.revision;groupDirty.value=false;groupConflict.value=false;
    noticeTarget.value=null;
    groupUsage.value=null;
    if(/^[A-Za-z0-9_-]{1,128}$/.test(id))void load(noticeTarget,'ops/notice-target',id);
    if(/^[A-Za-z0-9_-]{1,128}$/.test(id))void load(groupUsage,'model/group',id);
  }
  async function saveGroup() {
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
    if(['succeeded','failed'].includes(value.capability?.state))pendingProbes.delete(value.profile?.config_version);
    if(!modelDirty.value){modelDraft.value=structuredClone(toRaw(value.profile));modelGuard.value=value.guard;}
    else if(JSON.stringify(modelGuard.value)!==JSON.stringify(value.guard))modelConflict.value=true;
  }
  async function saveModel(group=null,limits=null){
    const token=generation,profile=JSON.stringify(group?model.value?.profile:modelDraft.value),key=modelKey.value;
    if(group&&modelDirty.value){message.value='请先保存或放弃模型设置草稿。';return;}
    busy.value=true;
    try{
      let groups=structuredClone(toRaw(model.value?.groups)||{});
      if(group){if(limits===null)delete groups[group];else groups={...groups,[group]:limits};}
      const result=await rpc('model/save',{operation_id:crypto.randomUUID().replaceAll('-',''),guard:group?model.value.guard:modelGuard.value,
        profile:JSON.parse(profile),groups,api_key:group?'':key,api_url:model.value.endpoints[JSON.parse(profile).provider]});
      if(token!==generation)return;
      if(!group)modelDirty.value=JSON.stringify(modelDraft.value)!==profile;
      modelConflict.value=false;model.value=result;modelGuard.value=result.guard;acceptModel(result);message.value='AI 设置已按新版本保存。';
      if(group)await load(groupUsage,'model/group',group);
    }catch(error){if(token!==generation||unauthorized(error))return;modelConflict.value=true;
      message.value='AI 设置未确认，请核对服务器值：'+String(error?.message||error);await load(model,'model/settings');
    }finally{modelKey.value='';busy.value=false;}
  }
  async function probeModel(){
    const token=generation,version=model.value?.profile?.config_version;
    if(modelDirty.value||!version)return;
    if(!pendingProbes.has(version))pendingProbes.set(version,{operation_id:crypto.randomUUID().replaceAll('-',''),guard:model.value.guard,confirm:'billable_capability_probe'});
    busy.value=true;
    try{const value=await rpc('model/probe',pendingProbes.get(version));if(token!==generation)return;
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
  return {model,modelDraft,modelGuard,modelKey,modelDirty,modelConflict,groupUsage,trusted,saveModel,probeModel,trustedRequest,
    jobs,jobDetail,audit,host,noticeTarget,controls,startControl:(action,ids)=>controlRequest(action,ids),restoreControl:id=>controlRequest('restore',[],id),startJob,exportDiagnostic,logged,busy,message,stale,dirty,conflict,overview,requests,detail,sources,metrics,groups,notices,draft,guard,rpc,load,login,logout,save,
    groupId,groupDraft,groupRevision,groupDirty,groupConflict,selectGroup,saveGroup,invalidate(){generation++;}};
}
