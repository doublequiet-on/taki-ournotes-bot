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
    if(method.startsWith('taki/chat/group/'))return {availability:'unavailable',reason_code:'chat_unavailable'}
    if(method==='taki/model/group')return{daily_limit:1000}
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
    if(method.startsWith('taki/chat/group/'))return {availability:'unavailable',reason_code:'chat_unavailable'}
    if(method==='taki/model/group')return{daily_limit:1000}
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
  const model=sessionState();model.logged.value=true
  const modelValue={profile:{provider:'deepseek',model:'fixture',config_version:'1'},guard:{backend_epoch:'e',fingerprint:'old'},groups:{},endpoints:{deepseek:'https://api.deepseek.com/chat/completions'}}
  globalThis.consoleStateFixture.send=async(method,value)=>{
    if(method==='taki/model/settings')return structuredClone(modelValue)
    if(method==='taki/model/save')return new Promise(resolve=>{receipt=()=>resolve({...structuredClone(modelValue),profile:value.profile,guard:{backend_epoch:'e',fingerprint:'new'}})})
    if(method==='taki/logout')return true
    throw Error('unexpected model RPC')
  }
  await model.load(model.model,'model/settings');model.modelKey.value='private-new-key';model.modelDirty.value=true
  const savingModel=model.saveModel();model.modelDraft.value.model='later edit';receipt();await savingModel
  assert.equal(model.modelKey.value,'');assert.equal(model.modelDraft.value.model,'later edit');assert.equal(model.modelDirty.value,true)
  assert.equal(model.modelConflict.value,false)
  await model.logout();assert.equal(model.model.value,null);assert.equal(model.modelDraft.value,null);assert.equal(model.modelKey.value,'')
  let epoch='gateway-before',attempted=[],trustedAttempts=[],finishJob,finishTrusted
  globalThis.consoleStateFixture.send=async(method,value)=>{
    if(method==='taki/overview')return {settings:structuredClone(settings),guard:{gateway_epoch:epoch,revision:0}}
    if(method==='taki/groups/list')return {policy:{groups:{},revision:0}}
    if(method==='taki/model/group'||method==='taki/ops/notice-target')return {sections:{}}
    if(method==='taki/ops/jobs/start'){attempted.push(value.operation_id);return new Promise(resolve=>{finishJob=resolve})}
    if(method==='taki/ops/trusted/start'){trustedAttempts.push(value.operation_id);return new Promise(resolve=>{finishTrusted=resolve})}
    if(method==='taki/ops/jobs/list')return {items:[]}
    if(method==='taki/logout')return true
    throw Error('unexpected recovery RPC '+method)
  }
  const before=sessionState();before.logged.value=true;await before.load(before.overview,'overview');await before.load(before.groups,'groups/list')
  before.draft.value.privateEnabled=false;before.dirty.value=true;before.selectGroup('alpha');before.groupDraft.value.label='unsaved';before.groupDirty.value=true
  before.modelDraft.value={provider:'fixture',model:'unsaved'};before.modelGuard.value={backend_epoch:'old'};before.modelDirty.value=true;before.modelKey.value='secret-must-not-be-retained'
  before.trusted.value={plan:{plan_id:'fixture-plan',action:'restart',instance_id:'fixture',target_sha:'fixture'}}
  const uncertain=before.startJob('fixture',{}),uncertainTrusted=before.trustedRequest('restart',before.trusted.value.plan)
  before.suspend();finishJob({id:'fixture-id'});finishTrusted({state:'fixture'});await Promise.all([uncertain,uncertainTrusted])
  const recovery=before.captureEdits();assert.equal(JSON.stringify(recovery).includes('secret-must-not-be-retained'),false)
  const after=sessionState();after.restoreEdits(recovery);assert.equal(after.modelKey.value,'');assert.equal(after.draft.value.privateEnabled,false)
  assert.equal(after.groupDraft.value.label,'unsaved');assert.equal(after.groupRecoveryBlocked.value,true)
  epoch='gateway-after';await after.load(after.overview,'overview');assert.equal(after.groupRecoveryBlocked.value,true);assert.equal(after.conflict.value,true)
  const retry=after.startJob('fixture',{});assert.equal(attempted[0],attempted[1]);finishJob({id:'fixture-id'});await retry
  const retryTrusted=after.trustedRequest('restart',{plan_id:'fixture-plan',action:'restart',instance_id:'fixture',target_sha:'fixture'})
  assert.equal(trustedAttempts[0],trustedAttempts[1]);finishTrusted({state:'fixture'});await retryTrusted
  await after.logout();const cleared=after.captureEdits();assert.equal(cleared.global,null);assert.equal(cleared.group,null);assert.equal(cleared.model,null)
  const missing=sessionState();await missing.load(missing.groups,'groups/list');missing.selectGroup('alpha');missing.groupDirty.value=true;missing.suspend()
  const restored=sessionState();restored.restoreEdits(missing.captureEdits());await restored.load(restored.overview,'overview');assert.equal(restored.groupRecoveryBlocked.value,true)
  for(const original of [null,'gateway-before']){
    let current=restored;const snapshot=current.captureEdits();snapshot.group.epoch=original;current=sessionState();current.restoreEdits(snapshot)
    for(let reconnect=0;reconnect<2;reconnect++){
      await current.load(current.overview,'overview');current.suspend();const next=sessionState();next.restoreEdits(current.captureEdits());await next.load(next.overview,'overview')
      assert.equal(next.groupRecoveryBlocked.value,true,'repeated reconnects must not replace the draft original gateway identity')
      assert.equal(next.captureEdits().group.epoch,original,'the original gateway must stay unchanged until the draft is discarded')
      await next.saveGroup();assert.match(next.message.value,/网关状态尚未确认或已变化/);current=next
    }
  }
  globalThis.consoleStateFixture.send=async(method,value)=>{
    if(method==='taki/groups/list')return {policy:{groups:{},revision:0}}
    if(method==='taki/groups/update')return {revision:1}
    if(method==='taki/model/group'||method==='taki/ops/notice-target')throw Error('fixture_backend_unavailable')
    throw Error('unexpected status RPC '+method)
  }
  const status=sessionState();await status.load(status.groups,'groups/list');status.selectGroup('alpha');status.groupDraft.value.label='fixture';status.groupDirty.value=true
  await status.saveGroup();assert.equal(status.message.value,'群策略已保存。新查询使用新规则，在途查询继续完成。')
  assert.match(status.readMessage.value,/读取失败/);assert.equal(status.stale.value,true)
  console.log(JSON.stringify({passed:true,saveDraftRace:true,groupSaveDraftRace:true,groupSwitchDuringSave:true,invalidatedSave:true,recoveredDrafts:true,secretCleared:true,groupEpochBlocked:true,operationIdReused:true,logoutCleared:true,actionAndReadStatusSeparated:true}))
}
main().catch(error=>{console.error(error);process.exitCode=1}).finally(()=>{delete globalThis.consoleStateFixture})
