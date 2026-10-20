import { ref, send } from './runtime.js';
export function sessionState() {
  const logged=ref(false), busy=ref(false), message=ref(''), stale=ref(false), dirty=ref(false), conflict=ref(false);
  const overview=ref(null), requests=ref(null), detail=ref(null), sources=ref(null), metrics=ref(null), groups=ref(null), notices=ref(null), draft=ref(null), guard=ref(null);
  let generation=0;
  const sequences=new Map();
  const failed=new Set();
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
      target.value=value; failed.delete(target); stale.value=failed.size>0;
      if(method==='overview') {
        if(!dirty.value) { draft.value=structuredClone(value.settings); guard.value=value.guard; }
        else if(JSON.stringify(guard.value)!==JSON.stringify(value.guard))conflict.value=true;
      }
    } catch (error) {
      if(token!==generation)return;
      if(error==='unauthorized'||error?.message==='unauthorized') {
        generation++; logged.value=false;
        for(const value of [overview,requests,detail,sources,metrics,groups,notices,draft])value.value=null;
        dirty.value=false; message.value='管理会话已过期，请重新登录。'; return;
      }
      failed.add(target); stale.value=true; message.value='读取失败，保留上次数据和草稿。会话过期时请重新登录。';
    }
  }
  async function login(password) {
    busy.value=true;
    try { await rpc('login',password); logged.value=true; message.value=''; await load(overview,'overview'); }
    catch { message.value='登录失败，请检查密码并稍后重试。'; }
    finally { busy.value=false; }
  }
  async function logout() {
    await rpc('logout'); generation++; logged.value=false;
    for(const target of [overview,requests,detail,sources,metrics,groups,notices,draft])target.value=null;
    dirty.value=false;
  }
  async function save() {
    const token=generation, submitted=JSON.stringify(draft.value);
    busy.value=true;
    try { const result=await rpc('settings/update',JSON.parse(submitted),guard.value);
      if(token!==generation)return;
      dirty.value=JSON.stringify(draft.value)!==submitted; conflict.value=false;
      guard.value=result.guard; message.value=dirty.value?'已保存提交时的设置，后续编辑仍未保存。':'已保存生效；在途查询继续完成。'; await load(overview,'overview'); }
    catch { if(token!==generation)return; dirty.value=true; conflict.value=true; message.value='保存未确认。草稿已保留，请核对服务器当前值。'; }
    finally { busy.value=false; }
  }
  return {logged,busy,message,stale,dirty,conflict,overview,requests,detail,sources,metrics,groups,notices,draft,guard,rpc,load,login,logout,save,invalidate(){generation++;}};
}
