// Reads an independently installed root-owned snapshot; never invokes a controller.
const fs=require('node:fs')
function readHostSnapshot(file='/run/taki-ops/host-v1.json'){
  const empty=reason=>({availability:'unavailable',observed_at:null,stale:true,data:null,reason_code:reason})
  try{
    const info=fs.lstatSync(file)
    if(!info.isFile()||info.isSymbolicLink()||info.size>131072||(process.platform!=='win32'&&(info.uid!==0||(info.mode&0o022))))return empty('host_snapshot_untrusted')
    const value=JSON.parse(fs.readFileSync(file,'utf8')),now=Date.now()/1000
    if(value.schema!=='taki-host-ops-v1'||typeof value.observed_at!=='number'||!Number.isFinite(value.observed_at)||value.observed_at<=0||value.observed_at>now+300) return empty('host_snapshot_invalid')
    const age=now-value.observed_at
    return{availability:'partial',observed_at:value.observed_at,stale:age<0||age>90,
      data:Object.fromEntries(['controller','services','processes','disk','sizes','backup_coverage','business_recovery'].map(k=>[k,value[k]??null])),reason_code:age<0?'host_clock_changed':age>90?'host_snapshot_stale':null}
  }catch(e){return empty(e.code==='ENOENT'?'host_collector_not_installed':'host_snapshot_unavailable')}
}
module.exports={readHostSnapshot}
