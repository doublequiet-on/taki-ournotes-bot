// Real local WebSocket authorization and save contracts. No QQ adapter or external network.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os')
const {Context}=require('koishi'),WebSocket=require('ws'),{install}=require('../console.cjs')
const {TakiTransport}=require('../lib')
async function connect(origin='http://127.0.0.1:15143'){
  const ws=new WebSocket('ws://127.0.0.1:15143/status',{origin}),pending=new Map();let seq=0
  ws.on('message',bytes=>{const m=JSON.parse(bytes);if(m.type==='response')pending.get(m.body.id)?.(m.body)})
  await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject)})
  return{ws,call(type,...args){return new Promise((resolve,reject)=>{const id=String(++seq),timer=setTimeout(()=>reject(Error('timeout')),3000);pending.set(id,v=>{clearTimeout(timer);pending.delete(id);resolve(v)});ws.send(JSON.stringify({id,type,args}))})}}
}
async function main(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'taki-console-rpc-')),ctx=new Context(),transport=new TakiTransport(ctx)
  let reads=0,offset=0;const clock=Date.now;Date.now=()=>clock()+offset
  transport.receiver={client:{request:async()=>{reads++;return{sections:{}}}},health:()=>({})};ctx.set('takiTransport',transport)
  const manager=install(ctx,{port:15143,password:'fixture-console-password-000000',directory:dir},{commit:'b'.repeat(40)})
  const clients=[]
  const methods=['overview','requests/list','requests/detail','sources','notices/list','query-metrics','groups/list','groups/update','settings/update',
    'ops/jobs/list','ops/jobs/detail','ops/jobs/start','ops/export','ops/audit','ops/host','ops/notice-target']
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
    const state=(await a.call('taki/status')).value
    const {readHostSnapshot}=require('../ops-host.cjs'),hostFile=path.join(dir,'host-fixture.json')
    fs.writeFileSync(hostFile,JSON.stringify({schema:'taki-host-ops-v1',observed_at:Date.now()/1000-120,controller:{current_sha:'a'.repeat(40)}}),{mode:0o600})
    assert.equal(readHostSnapshot(hostFile).stale,true)
    fs.writeFileSync(hostFile,'broken')
    assert.equal(readHostSnapshot(hostFile).availability,'unavailable')
    assert.equal((await a.call('taki/ops/host')).value.reason_code,'host_collector_not_installed')
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
    assert.equal((await a.call('taki/settings/update',state.settings,state.guard)).error,'settings_conflict')
    assert.ok((await a.call('taki/requests/list',{limit:200})).error)
    assert.equal((await a.call('taki/requests/list',{cursor:'x'.repeat(2049)})).error,'admin_parameters')
    assert.equal((await a.call('taki/requests/detail','../secret')).error,'admin_request_id')
    const policy={label:'',queries:'inherit',notifications:'inherit',natural_query:'inherit'}
    for(let i=0;i<30;i++)manager.management.groups.update('g'+String(i).padStart(2,'0'),policy,i)
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
main().catch(e=>{console.error(e);process.exitCode=1})
