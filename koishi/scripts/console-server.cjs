// Test-only official Console + real isolated Python admin API. Never installs a QQ adapter.
const path=require('node:path'),fs=require('node:fs')
const {Context}=require('koishi'),{TakiTransport,BackendClient}=require('../lib')
const {install}=require('../console.cjs')
const ctx=new Context(),transport=new TakiTransport(ctx)
const client=new BackendClient('http://127.0.0.1:18765','fixture-backend-token-000000000000','fixture','fixture-gateway')
transport.receiver={client,health:()=>({active:true,handshake:false})}
ctx.set('takiTransport',transport)
const directory=path.resolve(process.argv[2]),assets=path.resolve(process.argv[3])
const request=client.request.bind(client)
client.request=(...args)=>{
  if(!fs.existsSync(path.join(directory,'fixture-backend-offline')))return request(...args)
  // A stalled Python response must exhaust the real management read budget.
  const signal=args[4]||AbortSignal.timeout(2500)
  return new Promise((_,reject)=>{
    if(signal.aborted)reject(Error('fixture backend timeout'))
    else signal.addEventListener('abort',()=>reject(Error('fixture backend timeout')),{once:true})
  })
}
const manager=install(ctx,{port:15142,password:'fixture-console-password-000000',directory,identity:{appId:'app',instanceId:'fixture'}}, {commit:'b'.repeat(40)},assets)
ctx.start().then(()=>manager.ready()).then(()=>console.log('CONSOLE_FIXTURE_READY'))
let closing
const close=()=>closing||=(async()=>{await client.telemetry.close();await manager.close();await ctx.stop()})()
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>close().then(()=>process.exit(0)))
