// Exercise the shipped state module with real Vue refs and delayed RPC receipts.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function main(){
  const vue=fs.readFileSync(path.join(path.dirname(require.resolve('@koishijs/plugin-console/package.json')),'dist/vue.js'),'utf8')
  globalThis.consoleStateFixture={...await import('data:text/javascript;base64,'+Buffer.from(vue).toString('base64'))}
  const source=fs.readFileSync(require.resolve('../console/state.js'),'utf8').replace(/^import .*?;\r?\n/,
    'const {ref,toRaw}=globalThis.consoleStateFixture;const send=(...args)=>globalThis.consoleStateFixture.send(...args);\n')
  const {sessionState}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
  const settings={queriesEnabled:true,privateEnabled:true,groupsEnabled:true,groupAllowlist:[],notificationsEnabled:true}
  let server=structuredClone(settings),revision=0,receipt
  globalThis.consoleStateFixture.send=async(method,value)=>{
    if(method==='taki/settings/update')return new Promise(resolve=>{receipt=()=>{server=structuredClone(value);resolve({guard:{gateway_epoch:'fixture',revision:++revision}})}})
    if(method==='taki/overview')return {settings:structuredClone(server),guard:{gateway_epoch:'fixture',revision}}
    throw Error('unexpected RPC '+method)
  }
  const state=sessionState();state.logged.value=true;await state.load(state.overview,'overview')
  state.draft.value.privateEnabled=false;state.dirty.value=true
  const saving=state.save()
  state.draft.value.groupsEnabled=false;state.dirty.value=true
  receipt();await saving
  assert.equal(server.groupsEnabled,true,'only the submitted snapshot was saved')
  assert.equal(state.draft.value.groupsEnabled,false,'later edits survive the save response and refresh')
  assert.equal(state.dirty.value,true)
  assert.equal(state.guard.value.revision,1)
  const resaving=state.save();receipt();await resaving
  assert.equal(server.groupsEnabled,false);assert.equal(state.dirty.value,false)
  const stale=state.save();state.invalidate();state.draft.value=null;state.guard.value=null
  receipt();await stale
  assert.equal(state.draft.value,null);assert.equal(state.guard.value,null,'an invalidated response cannot restore protected state')
  console.log(JSON.stringify({passed:true,saveDraftRace:true,invalidatedSave:true}))
}
main().catch(error=>{console.error(error);process.exitCode=1}).finally(()=>{delete globalThis.consoleStateFixture})
