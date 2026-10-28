const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),cp=require('node:child_process'),assert=require('node:assert/strict')
const root=__dirname
if(!process.argv[2]) {
  const results=[]
  for(let cycle=0;cycle<5;cycle++)for(const variant of ['baseline','a','a','baseline']) {
    const child=cp.spawnSync(process.execPath,[__filename,variant],{encoding:'utf8'})
    if(child.status!==0)throw Error(child.stderr||child.stdout)
    results.push({...JSON.parse(child.stdout),cycle})
  }
  const quantile=(values,q)=>{const sorted=values.slice().sort((a,b)=>a-b);return sorted[Math.min(sorted.length-1,Math.floor(sorted.length*q))]}
  const summary=Object.fromEntries(['baseline','a'].map(variant=>{
    const rows=results.filter(r=>r.variant===variant),values=rows.flatMap(r=>r.hotMs)
    return[variant,{requests:values.length,p50_ms:quantile(values,.5),p95_ms:quantile(values,.95),
      cohort_p50_ms:rows.map(r=>quantile(r.hotMs,.5)),peak_rss_kib:rows.map(r=>r.resources.maxRSS),
      cpu_ms:rows.map(r=>(r.resources.userCPUTime+r.resources.systemCPUTime)/1000),failures:0,cold_max_concurrency:1}]
  }))
  const report={node:process.version,platform:process.platform,scenario:'40 sequential hot hits during one paused cold prepare; 16 owned entries; 5 ABBA cycles; separate process per cohort; local disk only',summary,results}
  fs.writeFileSync(path.join(root,'sync-benchmark.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report.summary));process.exit()
}
const variant=process.argv[2],{MaterialStore}=require(path.join(root,'materials-'+variant+'.js'))
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'taki-material-sync-')),app='benchmark'
const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64')
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),clock=Date.now(),file=path.join(directory,sha(app)+'.json')
fs.writeFileSync(file,JSON.stringify({schema:1,appId:app,entries:Array.from({length:16},(_,i)=>({key:i?sha(String(i)):sha(image),created:clock,used:clock,imageId:'i'+i,url:'qqbot-media://image/v1/i'+i}))}))
;(async()=>{
  let release,entered,completes=0,prepare=0
  const hold=new Promise(r=>release=r),start=new Promise(r=>entered=r)
  const store=new MaterialStore(directory,app,async(method,url)=>{
    if(url.endsWith('/prepare')){prepare++;entered();await hold;return{upload_id:'cold',presigned_url:'fixture'}}
    completes++;return{image_id:'cold',url:'qqbot-media://image/v1/cold'}
  },async(u,b)=>crypto.createHash('md5').update(b).digest('hex'))
  const cold=store.acquire(Buffer.concat([image,Buffer.from('cold')]));await start
  const hotMs=[]
  for(let i=0;i<40;i++){const start=performance.now(),lease=await store.acquire(image);hotMs.push(performance.now()-start);lease.release();assert.equal(completes,0)}
  release();(await cold).release();await store.close?.();assert.equal(prepare,1);assert.equal(completes,1)
  console.log(JSON.stringify({variant,hotMs,resources:process.resourceUsage()}))
})().finally(()=>{assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})}).catch(e=>{console.error(e);process.exitCode=1})
