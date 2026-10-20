import { ref, send, toRaw } from './runtime.js';
export function sessionState() {
  const logged=ref(false), busy=ref(false), message=ref(''), stale=ref(false), dirty=ref(false), conflict=ref(false);
  const overview=ref(null), requests=ref(null), detail=ref(null), sources=ref(null), metrics=ref(null), groups=ref(null), notices=ref(null), draft=ref(null), guard=ref(null);
  const groupId=ref(''),groupDraft=ref(null),groupRevision=ref(null),groupDirty=ref(false),groupConflict=ref(false);
  let generation=0;
  const sequences=new Map();
  const failed=new Set();
  function clearProtected() {
    generation++;logged.value=false;failed.clear();sequences.clear();stale.value=false;
    for(const value of [overview,requests,detail,sources,metrics,groups,notices,draft,guard])value.value=null;
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
    busy.value=true;
    try { const result=await rpc('settings/update',draft.value,guard.value); dirty.value=false; conflict.value=false;
      guard.value=result.guard; message.value='已保存生效；在途查询继续完成。'; await load(overview,'overview'); }
    catch(error) {if(unauthorized(error))return;dirty.value=true;conflict.value=true;message.value=error==='invalid_settings'?'设置格式不正确，请检查群 OpenID；草稿已保留。':'保存未确认。草稿已保留，请核对服务器当前值。';await load(overview,'overview');}
    finally { busy.value=false; }
  }
  function selectGroup(id) {
    if(groupDirty.value){if(id!==groupId.value)message.value='请先保存或放弃当前群草稿。';return;}
    groupId.value=id;groupDraft.value=structuredClone(toRaw(groups.value?.policy?.groups?.[id])||{label:'',queries:'inherit',notifications:'inherit',natural_query:'inherit'});
    groupRevision.value=groups.value?.policy?.revision;groupDirty.value=false;groupConflict.value=false;
  }
  async function saveGroup() {
    busy.value=true;
    try {
      const result=await rpc('groups/update',{group_id:groupId.value,policy:groupDraft.value,revision:groupRevision.value});
      groupRevision.value=result.revision;groupDirty.value=false;groupConflict.value=false;message.value='群策略已保存。新查询使用新规则，在途查询继续完成。';
      await load(groups,'groups/list');
    }catch(error){if(unauthorized(error))return;groupDirty.value=true;groupConflict.value=true;
      message.value=['invalid_group_id','invalid_group_policy'].includes(error)?'群 OpenID 或策略格式不正确，备注最多80字；草稿已保留。':'群策略保存未确认或版本冲突。草稿已保留，请先核对服务器值。';await load(groups,'groups/list');}
    finally{busy.value=false;}
  }
  return {logged,busy,message,stale,dirty,conflict,overview,requests,detail,sources,metrics,groups,notices,draft,guard,rpc,load,login,logout,save,
    groupId,groupDraft,groupRevision,groupDirty,groupConflict,selectGroup,saveGroup,invalidate(){generation++;}};
}
