// Fixed-action client for a separately installed, root-owned Unix controller.
// No subprocess, user URL, retries or command payloads. Intents precede submission.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http')
const {isDeepStrictEqual:equal}=require('node:util')
const ID=/^[a-f0-9]{32}$/,SHA=/^[a-f0-9]{40}$/,HASH=/^[a-f0-9]{64}$/
function check(value,code){if(!value)throw Error(code)}
function exchange(socket,method,resource,body){
  check(process.platform!=='win32'&&socket==='/run/taki-actions/control.sock','controller_not_installed')
  for(const dir of ['/run','/run/taki-actions']){
    const s=fs.lstatSync(dir);check(s.isDirectory()&&!s.isSymbolicLink()&&s.uid===0&&(s.mode&0o022)===0,'controller_untrusted')
  }
  const s=fs.lstatSync(socket);check(s.isSocket()&&!s.isSymbolicLink()&&s.uid===0&&(s.mode&0o007)===0,'controller_untrusted')
  return new Promise((resolve,reject)=>{
    const data=body?JSON.stringify(body):null
    const req=http.request({socketPath:socket,path:resource,method,headers:data?{'Content-Type':'application/json','Content-Length':Buffer.byteLength(data)}:{}},res=>{
      let size=0,chunks=[]
      res.on('data',chunk=>{size+=chunk.length;if(size>65536)req.destroy(Error('controller_response_size'));else chunks.push(chunk)})
      res.on('end',()=>{try{check(res.statusCode===200,'controller_rejected');resolve(JSON.parse(Buffer.concat(chunks)))}catch{reject(Error('controller_response_invalid'))}})
      res.on('error',()=>reject(Error('controller_unavailable')))
    })
    const timer=setTimeout(()=>req.destroy(Error('controller_timeout')),1800)
    req.on('close',()=>clearTimeout(timer));req.on('error',()=>reject(Error('controller_unavailable')))
    req.end(data)
  })
}
function planValid(p,instance){
  return p&&p.instance_id===instance&&ID.test(p.plan_id)&&['restart','update'].includes(p.action)
    &&SHA.test(p.from_sha)&&SHA.test(p.target_sha)&&HASH.test(p.config_digest)&&HASH.test(p.backend_digest)
    &&Number.isFinite(p.expires_at)&&p.inflight_policy==='drain'
    &&p.before_pids&&Object.keys(p.before_pids).sort().join()==='backend,gateway'
    &&Object.values(p.before_pids).every(v=>Number.isSafeInteger(v)&&v>=0)
    &&p.gates?.ci===true&&p.gates?.compatibility===true&&p.gates?.combination===true
}
class TrustedActions {
  constructor(management,journal,{socket,instance,transport}={}){
    this.management=management;this.journal=journal;this.instance=instance;this.socket=socket
    this.transport=transport||((method,resource,body)=>exchange(socket,method,resource,body))
    this.installed=!!socket||!!transport
  }
  async status(){
    if(!this.installed)return{available:false,reason_code:'controller_not_installed',plans:[],items:[]}
    try{
      const v=await this.transport('GET','/v1/actions')
      check(v?.schema==='taki-trusted-actions/1'&&v.instance_id===this.instance&&Array.isArray(v.plans)&&v.plans.length<=2
        &&v.plans.every(p=>planValid(p,this.instance))&&Array.isArray(v.items)&&v.items.length<=25,'controller_response_invalid')
      const items=v.items.map(r=>{
        check(ID.test(r.operation_id)&&['intent','running','succeeded','failed','unknown'].includes(r.state),'controller_response_invalid')
        const entry=this.journal.value.entries.find(e=>e.controller_operation===r.operation_id)
        const plan=entry?.controller_plan
        // Submission is not recovery. A success requires matching process identities and health.
        const health=r.health
        const verified=!!plan&&r.state==='succeeded'&&health?.ready===true&&health.release_sha===plan.target_sha
          &&health.backend_digest===plan.backend_digest&&Number.isSafeInteger(health.backend_pid)&&health.backend_pid>0
          &&Number.isSafeInteger(health.gateway_pid)&&health.gateway_pid>0
          &&health.backend_pid!==plan.before_pids.backend&&health.gateway_pid!==plan.before_pids.gateway
        if(entry&&entry.state==='intent'&&verified)this.journal.finish(entry,'applied','runtime_verified')
        if(entry&&entry.state==='intent'&&r.state==='failed')this.journal.finish(entry,'failed','controller_failed')
        return {operation_id:r.operation_id,state:r.state==='succeeded'&&!verified?'unknown':r.state,
          verified,health:verified?{ready:true,release_sha:health.release_sha,backend_pid:health.backend_pid,gateway_pid:health.gateway_pid}:null}
      })
      return{available:!this.journal.error,reason_code:this.journal.error,plans:v.plans,items}
    }catch{return{available:false,reason_code:'controller_unavailable',plans:[],items:[]}}
  }
  async prepare(action){
    check(['restart','update'].includes(action),'controller_action')
    check(this.installed&&!this.journal.error,'controller_not_installed')
    await this.status()
    check(!this.journal.value.entries.some(e=>e.action.startsWith('trusted/')&&e.state==='intent'),'controller_outcome_unknown')
    await this.transport('POST','/v1/actions/prepare',{action,instance_id:this.instance})
    return this.status()
  }
  async start(value,session){
    check(value&&Object.keys(value).sort().join()==='confirmation,operation_id,plan'&&ID.test(value.operation_id)
      &&planValid(value.plan,this.instance),'controller_shape')
    const p=value.plan
    check(value.confirmation===`${p.action}:${this.instance}:${p.target_sha}`,'controller_confirmation')
    const previous=this.journal.value.entries.find(e=>e.controller_operation===value.operation_id)
    if(previous){check(equal(previous.controller_plan,p),'controller_operation_conflict');return this.status()}
    const status=await this.status()
    check(!this.journal.value.entries.some(e=>e.action.startsWith('trusted/')&&e.state==='intent'),'controller_outcome_unknown')
    check(status.available&&p.expires_at>Date.now()/1000&&status.plans.some(plan=>equal(plan,p)),'controller_plan_expired')
    this.management.assertPersistent()
    const s=this.management.snapshot().settings
    check(!s.queriesEnabled&&!s.notificationsEnabled,'controller_maintenance_required')
    // The controller independently validates maintenance, in-flight work, gates and configuration.
    const entry=this.journal.begin('trusted/'+p.action,{}, {},{session,controller_operation:value.operation_id,controller_plan:p})
    try{await this.transport('POST','/v1/actions/execute',value)}catch{return{available:false,reason_code:'controller_outcome_unknown',plans:[],items:[{operation_id:value.operation_id,state:'unknown'}]}}
    return this.status()
  }
}
module.exports={TrustedActions,exchange,planValid}
