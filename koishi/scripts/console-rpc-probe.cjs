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
  const methods=['overview','requests/list','requests/detail','sources','notices/list','query-metrics','groups/list','settings/update']
  try{
    await ctx.start();await manager.ready();const a=await connect();clients.push(a.ws)
    for(const m of methods)assert.equal((await a.call('taki/'+m)).error,'unauthorized')
    assert.equal(reads,0)
    assert.equal((await a.call('taki/login','fixture-console-password-000000')).value,true)
    const state=(await a.call('taki/status')).value
    assert.equal((await a.call('taki/settings',state.settings,state.revision)).error,'upgrade_required')
    const changed=await a.call('taki/settings/update',{...state.settings,privateEnabled:false},state.guard)
    assert.equal(changed.value.settings.privateEnabled,false)
    assert.equal((await a.call('taki/settings/update',state.settings,state.guard)).error,'settings_conflict')
    assert.ok((await a.call('taki/requests/list',{limit:200})).error)
    assert.equal((await a.call('taki/requests/detail','../secret')).error,'admin_request_id')
    offset=1800001
    for(const m of methods)assert.equal((await a.call('taki/'+m)).error,'unauthorized')
    const foreign=await connect('https://foreign.invalid');clients.push(foreign.ws)
    await new Promise(resolve=>foreign.ws.once('close',code=>{assert.equal(code,1008);resolve()}))
    console.log(JSON.stringify({passed:true,authenticated_methods:methods.length,old_write:'upgrade_required',expired:'rejected',origin:'rejected'}))
  } finally {for(const ws of clients)ws.terminate();await manager.close();await ctx.stop();Date.now=clock;fs.rmSync(dir,{recursive:true,force:true})}
}
main().catch(e=>{console.error(e);process.exitCode=1})
