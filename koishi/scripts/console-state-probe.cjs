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
    if(method==='taki/ops/notice-target')return{sections:{notice_target:{availability:'unsupported'}}}
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
  let policies={},groupRevision=0
  globalThis.consoleStateFixture.send=async(method,value)=>{
    if(method==='taki/ops/notice-target')return{sections:{notice_target:{availability:'unsupported'}}}
    if(method==='taki/groups/update')return new Promise(resolve=>{receipt=()=>{policies[value.group_id]=structuredClone(value.policy);resolve({revision:++groupRevision})}})
    if(method==='taki/groups/list')return {policy:{groups:structuredClone(policies),revision:groupRevision}}
    throw Error('unexpected RPC '+method)
  }
  const groups=sessionState();groups.logged.value=true;await groups.load(groups.groups,'groups/list');groups.selectGroup('alpha')
  groups.groupDraft.value.label='submitted';groups.groupDirty.value=true
  const groupSaving=groups.saveGroup();groups.groupDraft.value.label='later edit';receipt();await groupSaving
  assert.equal(policies.alpha.label,'submitted')
  assert.equal(groups.groupDraft.value.label,'later edit');assert.equal(groups.groupDirty.value,true)
  assert.equal(groups.groupRevision.value,1)
  const groupResaving=groups.saveGroup();receipt();await groupResaving
  assert.equal(policies.alpha.label,'later edit');assert.equal(groups.groupDirty.value,false)
  const switchSaving=groups.saveGroup();groups.selectGroup('beta');groups.groupDraft.value.label='beta draft';groups.groupDirty.value=true
  receipt();await switchSaving
  assert.equal(groups.groupId.value,'beta');assert.equal(groups.groupDraft.value.label,'beta draft');assert.equal(groups.groupDirty.value,true)
  const expired=groups.saveGroup();groups.invalidate();groups.groupDraft.value=null;groups.groupRevision.value=null
  receipt();await expired
  assert.equal(groups.groupDraft.value,null);assert.equal(groups.groupRevision.value,null)
  console.log(JSON.stringify({passed:true,saveDraftRace:true,groupSaveDraftRace:true,groupSwitchDuringSave:true,invalidatedSave:true}))
}
main().catch(error=>{console.error(error);process.exitCode=1}).finally(()=>{delete globalThis.consoleStateFixture})
