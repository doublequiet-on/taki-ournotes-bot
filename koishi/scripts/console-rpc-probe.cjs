// Real local WebSocket authorization and save contracts. No QQ adapter or external network.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {Context}=require('koishi'),WebSocket=require('ws'),{install}=require('../console.cjs')
const {TakiTransport}=require('../lib')
function controlProbe(directory){
  const {Management}=require('../lib/management'),{OperationJournal}=require('../operation-journal.cjs'),{Controls}=require('../ops-controls.cjs')
  const identity={appId:'fixture-app',instanceId:'fixture-instance'},dir=path.join(directory,'control-unit')
  let m=new Management(dir,identity),journal=new OperationJournal(dir,'fixture'),controls=new Controls(m,journal)
  const inherited={label:'',queries:'inherit',notifications:'inherit',natural_query:'inherit'}
  m.groups.update('a',inherited,0);m.groups.update('b',{...inherited,natural_query:'model'},1);m.groups.update('off',{...inherited,natural_query:'off'},2)
  const payload=(action,id,groups=[])=>({operation_id:id.repeat(32),action,group_ids:groups,guard:m.snapshot().guard,group_revision:m.groups.snapshot().revision})
  const restore=(id,target)=>({operation_id:id.repeat(32),target_id:target,guard:m.snapshot().guard,group_revision:m.groups.snapshot().revision})
  const first=payload('local','1',['a','b','off']),accepted=controls.start(first,'fixture-session')
  assert.equal(m.groups.snapshot().revision,4);assert.equal(m.groups.get('a').natural_query,'local');assert.equal(m.groups.get('off').natural_query,'off')
  assert.equal(controls.start(first).id,accepted.id);assert.equal(m.groups.snapshot().revision,4)
  assert.throws(()=>controls.start({...first,group_ids:['a']}),/control_id_conflict/)
  controls.restore(restore('2',accepted.id));assert.equal(m.groups.get('a').natural_query,'inherit');assert.equal(m.groups.get('b').natural_query,'model')
  const local=controls.start(payload('local','3',['a']))
  m=new Management(dir,identity);journal=new OperationJournal(dir,'fixture');journal.reconcile(m);controls=new Controls(m,journal)
  assert.equal(controls.snapshot().items.find(e=>e.id===local.id).restorable,true)
  controls.restore(restore('4',local.id));assert.equal(m.groups.get('a').natural_query,'inherit')
  const maintenance=controls.start(payload('maintenance','5'))
  assert.equal(m.snapshot().settings.queriesEnabled,false);assert.equal(m.snapshot().settings.notificationsEnabled,false)
  m.updateGuarded({...m.snapshot().settings,queriesEnabled:true},m.snapshot().guard)
  m.updateGuarded({...m.snapshot().settings,queriesEnabled:false},m.snapshot().guard)
  assert.throws(()=>controls.restore(restore('6',maintenance.id)),/control_restore_conflict/,'ABA saves must not restore stale permissions')
  assert.equal(m.snapshot().settings.queriesEnabled,false)
  const historical=journal.begin('settings/update',{},{});journal.finish(historical,'applied')
  historical.at=0;journal.find(maintenance.id).at=0;journal.prune()
  assert.equal(journal.find(historical.id),undefined)
  assert.ok(journal.find(maintenance.id),'active recovery material survives audit retention')
  const file=path.join(dir,'group-policies.v1.json'),disk=JSON.parse(fs.readFileSync(file,'utf8'));disk.groups.a.natural_query='off';fs.writeFileSync(file,JSON.stringify(disk))
  assert.throws(()=>controls.start(payload('local','7',['a'])),/group_policy_conflict/)
  assert.equal(JSON.parse(fs.readFileSync(file,'utf8')).groups.a.natural_query,'off')
  const brokenDir=path.join(directory,'control-broken'),normal=new Management(brokenDir,identity)
  fs.writeFileSync(path.join(brokenDir,'operations.v1.json'),'broken')
  const broken=new OperationJournal(brokenDir,'fixture'),blocked=new Controls(normal,broken)
  assert.equal(blocked.snapshot().available,false)
  assert.equal(normal.snapshot().settings.queriesEnabled,true)
  const faultDir=path.join(directory,'control-write-failure'),faultManagement=new Management(faultDir,identity)
  const faultJournal=new OperationJournal(faultDir,'fixture'),faultControls=new Controls(faultManagement,faultJournal)
  const save=faultJournal.save.bind(faultJournal);let writes=0
  faultJournal.save=()=>{if(++writes===2)throw Error('fixture result write failure');return save()}
  const fault={operation_id:'f'.repeat(32),action:'maintenance',group_ids:[],guard:faultManagement.snapshot().guard,group_revision:0}
  assert.throws(()=>faultControls.start(fault),/fixture result write failure/)
  assert.equal(faultManagement.snapshot().settings.queriesEnabled,false)
  assert.equal(faultJournal.find(fault.operation_id).state,'intent');assert.equal(faultControls.snapshot().available,false)
  assert.throws(()=>faultControls.start(fault),/ops_journal_write_unconfirmed/)
  assert.equal(faultManagement.snapshot().revision,1,'retry must not reapply the control')
  const restarted=new OperationJournal(faultDir,'fixture');restarted.reconcile(faultManagement)
  const recovered=new Controls(faultManagement,restarted)
  assert.equal(restarted.find(fault.operation_id).state,'applied')
  assert.equal(recovered.snapshot().items[0].restorable,false,'missing durable post-tag cannot authorize restoration')
  console.log(JSON.stringify({controls:true,batchRevision:true,offPreserved:true,idempotency:true,restartRestore:true,abaConflict:true,externalEditPreserved:true,corruptionIsolated:true,resultWriteFailureIsolated:true}))
}
function journalProbe(directory){
  const {OperationJournal}=require('../operation-journal.cjs'),folder=path.join(directory,'journal-faults')
  fs.mkdirSync(folder)
  const j=new OperationJournal(folder,'fixture'),save=j.save.bind(j);let writes=0,executions=0
  j.save=()=>{if(++writes===2)throw Error('fixture result write failure');return save()}
  assert.throws(()=>j.apply('settings/update',{enabled:true},{enabled:false},()=>{executions++}),/fixture result write failure/)
  assert.equal(executions,1);assert.equal(j.snapshot().available,false);assert.equal(j.snapshot().items[0].state,'intent')
  assert.equal(JSON.parse(fs.readFileSync(j.file,'utf8')).entries[0].state,'intent')
  assert.throws(()=>j.apply('settings/update',{}, {},()=>{executions++}),/ops_journal_write_unconfirmed/);assert.equal(executions,1)
  fs.writeFileSync(j.file,JSON.stringify({schema:1,identity:'fixture',entries:[null]}))
  assert.equal(new OperationJournal(folder,'fixture').snapshot().available,false)
}
async function connect(origin='http://127.0.0.1:15143'){
  const ws=new WebSocket('ws://127.0.0.1:15143/status',{origin}),pending=new Map();let seq=0
  ws.on('message',bytes=>{const m=JSON.parse(bytes);if(m.type==='response')pending.get(m.body.id)?.(m.body)})
  await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject)})
  return{ws,call(type,...args){return new Promise((resolve,reject)=>{const id=String(++seq),timer=setTimeout(()=>reject(Error('timeout')),3000);pending.set(id,v=>{clearTimeout(timer);pending.delete(id);resolve(v)});ws.send(JSON.stringify({id,type,args}))})}}
}
async function main(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'taki-console-rpc-')),ctx=new Context(),transport=new TakiTransport(ctx)
  journalProbe(dir)
  await trustedProbe(dir)
  let reads=0,offset=0;const clock=Date.now;Date.now=()=>clock()+offset
  const targets=['pending','sending','uncertain','sent','claimed','permission_unknown'].map((stage,i)=>({group_id:'g'+String(i).padStart(2,'0'),stage,ledger_status:stage,claim:{status:stage}}))
  const noticeFixture={sections:{notices:{data:{targets}},notice_target:{data:{targets}}}}
  const noticeBefore=JSON.stringify(noticeFixture)
  transport.receiver={client:{epoch:'fixture-epoch',request:async url=>{reads++;return url.includes('/notices')||url.includes('/ops/notice-targets/')?noticeFixture:{sections:{}}}},health:()=>({}),activity:()=>({observation_diagnostics:[{at:1,correlation:'fixture-correlation',event_type:'GROUP_MESSAGE_CREATE',trigger:'observation',stage:'backend_response',observed:false}]})};ctx.set('takiTransport',transport)
  const manager=install(ctx,{port:15143,password:'fixture-console-password-000000',directory:dir},{commit:'b'.repeat(40)})
  const clients=[]
  const methods=['overview','requests/list','requests/detail','sources','notices/list','query-metrics','groups/list','groups/update','settings/update',
    'ops/jobs/list','ops/jobs/detail','ops/jobs/start','ops/export','ops/audit','ops/host','ops/notice-target',
    'ops/controls','ops/controls/start','ops/controls/restore',
    'library/status','library/preview','library/compare','library/publish','library/withdraw','chat/status','chat/group/status','chat/group/memory','chat/memory/update','model/fallback/settings','model/fallback/save','model/settings','model/save','model/probe','model/group','ops/trusted','ops/trusted/prepare','ops/trusted/start']
  try{
    await ctx.start();await manager.ready();const a=await connect();clients.push(a.ws)
    for(const m of methods)assert.equal((await a.call('taki/'+m)).error,'unauthorized')
    assert.equal(reads,0)
    const attacker=await connect();clients.push(attacker.ws)
    const loginTime=Date.now();Date.now=()=>loginTime
    try {
      assert.equal((await attacker.call('taki/login','wrong')).error,'登录失败')
      assert.equal((await attacker.call('taki/login','wrong')).error,'请稍后再登录')
      assert.equal((await a.call('taki/login','fixture-console-password-000000')).value,true,'another connection must not consume the administrator login budget')
      assert.equal((await a.call('taki/login','fixture-console-password-000000')).error,'请稍后再登录')
    } finally {Date.now=()=>clock()+offset}
    const diagnosticView=(await a.call('taki/overview')).value.activity.observation_diagnostics
    assert.equal(diagnosticView[0].stage,'backend_response')
    assert.equal(diagnosticView[0].observed,false)
    const state=(await a.call('taki/status')).value
    for(const method of ['chat/status','chat/group/status','chat/group/memory'])assert.ok((await a.call('taki/'+method,...(method==='chat/status'?[]:['g1']))).value)
    assert.equal((await a.call('taki/chat/group/memory','../g1')).error,'admin_group')
    const beforeMemory=reads,mutation={action:'clear',expected_revision:0}
    assert.equal((await a.call('taki/chat/memory/update',{group_id:'g1',mutation,epoch:'old'})).error,'ops_gateway_epoch')
    assert.equal((await a.call('taki/chat/memory/update',{group_id:'g1',mutation:{...mutation,expected_revision:'0'},epoch:'fixture-epoch'})).error,'chat_memory_shape')
    assert.equal(reads,beforeMemory,'rejected memory changes must never reach backend')
    assert.ok((await a.call('taki/chat/memory/update',{group_id:'g1',mutation,epoch:'fixture-epoch'})).value)
    const libraryReads=reads
    assert.equal((await a.call('taki/library/status',{kind:'../story',offset:0})).error,'library_shape')
    assert.equal((await a.call('taki/library/publish',{epoch:'old',payload:{preview_id:'fixture',confirm_reviewed:true}})).error,'ops_gateway_epoch')
    assert.equal(reads,libraryReads)
    assert.ok((await a.call('taki/library/status',{kind:'knowledge',offset:0})).value)
    const {readHostSnapshot}=require('../ops-host.cjs'),hostFile=path.join(dir,'host-fixture.json')
    fs.writeFileSync(hostFile,JSON.stringify({schema:'taki-host-ops-v1',observed_at:Date.now()/1000-120,controller:{current_sha:'a'.repeat(40)}}),{mode:0o600})
    assert.equal(readHostSnapshot(hostFile).stale,true)
    fs.writeFileSync(hostFile,'broken')
    assert.equal(readHostSnapshot(hostFile).availability,'unavailable')
    assert.equal((await a.call('taki/ops/host')).value.reason_code,'host_collector_not_installed')
    assert.equal((await a.call('taki/ops/trusted')).value.reason_code,'controller_not_installed')
    assert.equal((await a.call('taki/ops/trusted/prepare','command')).error,'controller_action')
    assert.equal((await a.call('taki/model/group','../secret')).error,'admin_group')
    assert.equal((await a.call('taki/ops/jobs/detail','../secret')).error,'ops_identity')
    assert.equal((await a.call('taki/ops/export','../secret')).error,'admin_request_id')
    assert.equal((await a.call('taki/ops/jobs/start',{command:'reboot'})).error,'ops_shape')
    const existed=fs.existsSync(path.join(dir,'settings.json'))
    assert.equal((await a.call('taki/settings',state.settings,state.revision)).error,'upgrade_required')
    assert.equal(fs.existsSync(path.join(dir,'settings.json')),existed)
    const changed=await a.call('taki/settings/update',{...state.settings,privateEnabled:false},state.guard)
    assert.equal(changed.value.settings.privateEnabled,false)
    const audit=(await a.call('taki/ops/audit')).value
    assert.equal(audit.items[0].state,'applied')
    assert.ok(!JSON.stringify(audit).includes('fixture-console-password'))
    const maintenance={operation_id:'e'.repeat(32),action:'maintenance',group_ids:[],guard:changed.value.guard,group_revision:0}
    assert.equal((await a.call('taki/ops/controls/start',maintenance)).value.state,'applied')
    const controlState=(await a.call('taki/ops/controls')).value
    assert.equal((await a.call('taki/ops/controls/restore',{operation_id:'f'.repeat(32),target_id:maintenance.operation_id,guard:controlState.guard,group_revision:0})).value.state,'applied')
    assert.equal(manager.management.snapshot().settings.privateEnabled,false)
    controlProbe(dir)
    assert.equal((await a.call('taki/settings/update',state.settings,state.guard)).error,'settings_conflict')
    assert.ok((await a.call('taki/requests/list',{limit:200})).error)
    assert.equal((await a.call('taki/requests/list',{cursor:'x'.repeat(2049)})).error,'admin_parameters')
    assert.equal((await a.call('taki/requests/detail','../secret')).error,'admin_request_id')
    const policy={label:'',queries:'inherit',notifications:'inherit',natural_query:'inherit'}
    for(let i=0;i<30;i++)manager.management.groups.update('g'+String(i).padStart(2,'0'),policy,i)
    const noticeRows=async method=>(await a.call(method,...(method==='taki/ops/notice-target'?['g00']:[]))).value.sections[method==='taki/ops/notice-target'?'notice_target':'notices'].data.targets
    const saveSettings=async changes=>{const current=(await a.call('taki/status')).value;const result=await a.call('taki/settings/update',{...current.settings,...changes},current.guard);assert.ok(result.value,result.error)}
    await saveSettings({notificationsEnabled:false})
    for(const method of ['taki/notices/list','taki/ops/notice-target']){
      const rows=await noticeRows(method)
      assert.equal(rows[0].stage,'policy_blocked');assert.equal(rows[0].backend_stage,'pending')
      assert.deepEqual(rows[0].notification_reasons,['global_notifications_disabled'])
      assert.deepEqual(rows.slice(1,4).map(r=>r.stage),['sending','uncertain','sent'])
      assert.equal(rows[4].stage,'policy_blocked');assert.equal(rows[4].claim.status,'claimed')
      assert.equal(rows[5].stage,'permission_unknown')
    }
    await saveSettings({notificationsEnabled:true})
    assert.equal((await noticeRows('taki/notices/list'))[0].stage,'pending')
    manager.management.groups.update('g00',{...policy,notifications:'pause'},30)
    assert.deepEqual((await noticeRows('taki/ops/notice-target'))[0].notification_reasons,['group_notifications_paused'])
    manager.management.groups.update('g00',policy,31)
    await saveSettings({groupAllowlist:['g01']})
    assert.deepEqual((await noticeRows('taki/notices/list'))[0].notification_reasons,['global_group_excluded'])
    await saveSettings({groupAllowlist:[]})
    assert.equal((await noticeRows('taki/notices/list'))[0].effective_notifications,true)
    assert.equal(JSON.stringify(noticeFixture),noticeBefore,'policy projection must not mutate cached ledger facts')
    const first=(await a.call('taki/groups/list',{limit:'10'})).value.sections.groups
    const second=(await a.call('taki/groups/list',{limit:'10',cursor:first.next_cursor})).value.sections.groups
    assert.equal(first.data.length,10);assert.equal(second.data.length,10)
    assert.equal(new Set([...first.data,...second.data].map(g=>g.group_id)).size,20)
    assert.equal((await a.call('taki/groups/list',{cursor:'invalid'})).error,'group_cursor_invalid')
    offset=1100
    const b=await connect();clients.push(b.ws)
    assert.equal((await b.call('taki/login','fixture-console-password-000000')).value,true)
    assert.equal((await b.call('taki/groups/list',{cursor:first.next_cursor})).error,'group_cursor_invalid')
    offset=1800001
    for(const m of methods)assert.equal((await a.call('taki/'+m)).error,'unauthorized')
    const foreign=await connect('https://foreign.invalid');clients.push(foreign.ws)
    await new Promise(resolve=>foreign.ws.once('close',code=>{assert.equal(code,1008);resolve()}))
    for(const [frame,expected] of [['{invalid',1008],[JSON.stringify({id:'bad',type:'unknown-method',args:[]}),1008],
      [JSON.stringify({id:'large',type:'taki/login',args:['x'.repeat(33000)]}),1009]]) {
      const connection=await connect();clients.push(connection.ws)
      const closed=new Promise(resolve=>connection.ws.once('close',code=>{assert.equal(code,expected);resolve()}))
      connection.ws.send(frame);await closed
    }
    console.log(JSON.stringify({passed:true,authenticated_methods:methods.length,old_write:'upgrade_required',expired:'rejected',origin:'rejected'}))
  } finally {for(const ws of clients)ws.terminate();await manager.close();await ctx.stop();Date.now=clock;fs.rmSync(dir,{recursive:true,force:true})}
}
async function trustedProbe(directory){
  const {TrustedActions}=require('../trusted-actions.cjs'),{Management}=require('../lib/management'),{OperationJournal}=require('../operation-journal.cjs')
  const m=new Management(path.join(directory,'trusted'),{instanceId:'fixture',appId:'a'}),j=new OperationJournal(path.join(directory,'trusted'),'fixture')
  m.update({...m.snapshot().settings,queriesEnabled:false,notificationsEnabled:false},0)
  const plan={instance_id:'fixture',plan_id:'a'.repeat(32),action:'update',from_sha:'a'.repeat(40),target_sha:'b'.repeat(40),
    config_digest:'c'.repeat(64),backend_digest:'d'.repeat(64),before_pids:{backend:1,gateway:2},inflight_policy:'drain',expires_at:Date.now()/1000+120,gates:{ci:true,compatibility:true,combination:true}}
  let submissions=0,items=[]
  const bridge=new TrustedActions(m,j,{instance:'fixture',transport:async(method,url,value)=>{
    if(url.endsWith('/execute')){submissions++;assert.equal(j.value.entries.at(-1).state,'intent');throw Error('lost receipt')}
    return{schema:'taki-trusted-actions/1',instance_id:'fixture',plans:[plan],items}
  }})
  const value={operation_id:'e'.repeat(32),plan,confirmation:'update:fixture:'+plan.target_sha}
  assert.equal((await bridge.start(value,'session')).reason_code,'controller_outcome_unknown')
  await bridge.start(value,'session');assert.equal(submissions,1,'unknown outcome must only read back')
  items=[{operation_id:value.operation_id,state:'succeeded',health:{ready:true,release_sha:plan.from_sha,backend_digest:plan.backend_digest,backend_pid:12,gateway_pid:34}}]
  assert.equal((await bridge.status()).items[0].state,'unknown','wrong runtime cannot prove success')
  items[0].health.release_sha=plan.target_sha
  assert.equal((await bridge.status()).items[0].verified,true)
  assert.equal(j.value.entries.at(-1).state,'applied')
  const restart=new OperationJournal(path.join(directory,'trusted'),'fixture');restart.reconcile(m)
  assert.equal(restart.value.entries.at(-1).state,'applied')
  assert.ok(!JSON.stringify(j.publicSnapshot()).includes('confirmation'))
}
main().catch(e=>{console.error(e);process.exitCode=1})
