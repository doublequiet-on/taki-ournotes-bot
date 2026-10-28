const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict')
const [aCommit,bCommit,baselineCommit]=process.argv.slice(2)
assert.ok([aCommit,bCommit,baselineCommit].every(x=>/^[0-9a-f]{40}$/.test(x||'')), 'supply full A, B and baseline commit IDs')
const A=require('./materials-a.js').MaterialStore,B=require('./materials-b.js').MaterialStore,Old=require('./materials-baseline.js').MaterialStore
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'taki-material-rollback-')),app='rollback',now=Date.now()
const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64')
const hot=image,lost=Buffer.concat([image,Buffer.from('lost')]),gone=Buffer.concat([image,Buffer.from('gone')]),fresh=Buffer.concat([image,Buffer.from('fresh')])
const sha=x=>crypto.createHash('sha256').update(x).digest('hex'),file=path.join(directory,sha(app)+'.json')
const put=async(u,b)=>crypto.createHash('md5').update(b).digest('hex')
;(async()=>{
  fs.writeFileSync(file,JSON.stringify({schema:1,appId:app,entries:[{key:sha(hot),created:now,used:now,imageId:'hot',url:'qqbot-media://image/v1/hot'},
    {key:sha(gone),created:0,used:0,imageId:'gone',url:'qqbot-media://image/v1/gone'}]}))
  const never=async()=>{throw Error('no external action expected')}
  const aLegacy=new A(directory,app,never,put,()=>now);(await aLegacy.acquire(hot)).release();await aLegacy.close()
  assert.equal(JSON.parse(fs.readFileSync(file)).schema,1)
  const calls=[]
  const b=new B(directory,app,async(method,url)=>{
    calls.push([method,url]);if(method==='DELETE')throw Error('deleted remotely, lost response')
    if(url.endsWith('/prepare'))return{upload_id:'lost',presigned_url:'fixture'}
    throw Error('completed remotely, lost response')
  },put,()=>now);
  (await b.acquire(hot)).release();assert.equal(JSON.parse(fs.readFileSync(file)).schema,2)
  await assert.rejects(b.acquire(lost));await assert.rejects(b.acquire(gone));await b.close()
  const afterB=JSON.parse(fs.readFileSync(file)),states=afterB.entries.map(e=>e.state).sort()
  assert.deepEqual(states,['completion_unknown','deletion_unknown','owned'])
  const recovery=new A(directory,app,async(method,url)=>{
    assert.notEqual(method,'DELETE')
    return url.endsWith('/prepare')?{upload_id:'fresh',presigned_url:'fixture'}:{image_id:'fresh',url:'qqbot-media://image/v1/fresh'}
  },put,()=>now);
  (await recovery.acquire(hot)).release();await assert.rejects(recovery.acquire(lost),/unresolved/);await assert.rejects(recovery.acquire(gone),/unresolved/)
  ;(await recovery.acquire(fresh)).release();await recovery.close()
  const afterA=JSON.parse(fs.readFileSync(file));assert.equal(afterA.schema,2);assert.equal(afterA.entries.length,4)
  for(const e of afterB.entries)assert.deepEqual(afterA.entries.find(n=>n.key===e.key),e)
  const beforeOld=fs.readFileSync(file,'utf8');await assert.rejects(new Old(directory,app,never,put,()=>now).acquire(hot),/invalid material ledger/)
  assert.equal(fs.readFileSync(file,'utf8'),beforeOld)
  const report={node:process.version,platform:process.platform,evidence:'actual TypeScript-compiled A/B modules; synthetic files and platform only',
    revisions:{a:aCommit,b:bCommit,baseline:baselineCommit},
    compiled_sha256:Object.fromEntries(['materials-a.js','materials-b.js','materials-baseline.js'].map(name=>[name,sha(fs.readFileSync(path.join(__dirname,name)))])),
    A_legacy_write_schema:1,B_migrated_schema:2,B_states:states,A_after_rollback_schema:2,A_after_rollback_states:afterA.entries.map(e=>e.state).sort(),
    old_reader_refused_without_state_change:true,unresolved_preserved:true,new_ownership_added_after_rollback:true,external_calls_by_B:calls}
  fs.writeFileSync(path.join(__dirname,'compiled-rollback.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
})().finally(()=>{assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})}).catch(e=>{console.error(e);process.exitCode=1})
