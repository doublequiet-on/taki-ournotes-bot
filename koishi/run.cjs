// Two-process gateway entry; importing it starts nothing. Credentials come only from environment.
const fs=require('node:fs'), path=require('node:path'), http=require('node:http'), crypto=require('node:crypto')
const {Context,Universal}=require('koishi')
function configuration(env) {
  if(env.TAKI_KOISHI_ENABLED!=='1')throw Error('gateway requires explicit enable')
  for(const name of ['QQ_APP_ID','QQ_APP_SECRET','QQ_SELF_ID','OURNOTES_BACKEND_TOKEN','TAKI_INSTANCE_ID']) {
    if(!env[name])throw Error('missing '+name)
  }
  const port=Number(env.TAKI_GATEWAY_HEALTH_PORT||8766)
  if(!Number.isInteger(port)||port<1||port>65535)throw Error('health port')
  return {port,plugin:{enabled:true,notifications:true,instanceId:env.TAKI_INSTANCE_ID,appId:env.QQ_APP_ID,
    selfId:env.QQ_SELF_ID,backendUrl:env.TAKI_BACKEND_URL||'http://127.0.0.1:8765',tokenEnv:'OURNOTES_BACKEND_TOKEN',permissions:['authority:1'],maxPending:8},
    adapter:{id:env.QQ_APP_ID,secret:env.QQ_APP_SECRET,type:'public',protocol:'websocket',sandbox:false,
      intents:1<<25,endpoint:'https://api.bot.qq.com',retryWhen:[]}}
}
async function start(env=process.env) {
  const config=configuration(env)
  const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../combination.json'),'utf8'))
  if(!/^[a-f0-9]{40}$/.test(manifest.commit))throw Error('release identity')
  const ctx=new Context()
  ctx.plugin(require('@koishijs/plugin-http').default)
  const taki=ctx.plugin(require('./lib'),config.plugin)
  const adapter=ctx.plugin(require('@koishijs/plugin-adapter-qq').default,config.adapter)
  const server=http.createServer(async(req,res)=>{
    const supplied=Buffer.from(req.headers.authorization||''),expected=Buffer.from('Bearer '+env.OURNOTES_BACKEND_TOKEN)
    if(req.url!=='/health' || supplied.length!==expected.length || !crypto.timingSafeEqual(supplied,expected)){res.writeHead(403);res.end();return}
    try {
      const receiver=ctx.takiTransport?.receiver, local=receiver?.health()
      const backend=receiver ? await receiver.client.request('/v1/ready') : null
      const bot=ctx.bots.find(b=>b.platform==='qq' && b.config.id===config.plugin.appId)
      const ready=!!(local?.active && local.handshake && backend?.ready && backend.background_tasks
        && backend.backend_epoch===local.backend.backend_epoch && backend.backend_code_digest===manifest.backend_code_digest
        && bot?.selfId===config.plugin.selfId && bot.status===Universal.Status.ONLINE)
      res.writeHead(ready?200:503,{'Content-Type':'application/json','Cache-Control':'no-store'})
      res.end(JSON.stringify({ready,pid:process.pid,release_sha:manifest.commit,backend_ready:!!backend?.ready,backend_pid:backend?.pid,
        qq_online:bot?.status===Universal.Status.ONLINE,handshake:!!local?.handshake,backend_code_digest:backend?.backend_code_digest}))
    }catch{res.writeHead(503);res.end('{"ready":false}')}
  })
  try {
    await ctx.start()
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(config.port,'127.0.0.1',resolve)})
  } catch(error) {
    await taki.dispose();await adapter.dispose();await ctx.stop();throw error
  }
  let closing
  const close=()=>closing ||= (async()=>{const stopped=new Promise(resolve=>server.close(resolve));await taki.dispose();await adapter.dispose();await stopped;await ctx.stop()})()
  return {ctx,server,close}
}
module.exports={configuration,start}
if(require.main===module)start().then(runtime=>{
  for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>runtime.close().then(()=>process.exit(0)))
}).catch(()=>{console.error('Taki gateway startup failed');process.exitCode=1})
