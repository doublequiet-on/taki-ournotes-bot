// Durable bounded management intents. No credentials, message bodies or platform sends.
const fs=require('node:fs'),path=require('node:path'),{randomUUID,createHash}=require('node:crypto')
const {isDeepStrictEqual:equal}=require('node:util')
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex')
const active=e=>e.control&&!e.restore_of&&!e.no_change&&!e.restored_by&&['intent','applied','conflict'].includes(e.state)
function validEntry(e){
  if(!e||typeof e.id!=='string'||!/^[a-f0-9]{32}$/.test(e.id)||!Number.isFinite(e.at)||typeof e.action!=='string'
    ||!['intent','applied','failed','not_applied','conflict'].includes(e.state))return false
  if(e.summary&&(!Array.isArray(e.summary.fields)||e.summary.fields.some(v=>typeof v!=='string')))return false
  if(e.control){
    if(!['maintenance','local'].includes(e.control)||!e.before||!e.after||typeof e.before!=='object'||typeof e.after!=='object')return false
    if(!Array.isArray(e.group_ids)||e.group_ids.length>128||e.group_ids.some(id=>typeof id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(id)))return false
    if(e.restore_of!==undefined&&(typeof e.restore_of!=='string'||!/^[a-f0-9]{32}$/.test(e.restore_of)))return false
    if(e.restored_by!==undefined&&(typeof e.restored_by!=='string'||!/^[a-f0-9]{32}$/.test(e.restored_by)))return false
    if(e.control==='maintenance'){
      for(const v of [e.before,e.after])if(Object.keys(v).sort().join()!=='notificationsEnabled,queriesEnabled'||typeof v.queriesEnabled!=='boolean'||typeof v.notificationsEnabled!=='boolean')return false
    }else{
      if(!Array.isArray(e.group_ids)||e.group_ids.length>128||e.group_ids.some(id=>typeof id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(id)))return false
      for(const v of [e.before,e.after]){
        if(Object.keys(v).sort().join()!==[...e.group_ids].sort().join())return false
        if(Object.values(v).some(p=>!p||Object.keys(p).join()!=='natural_query'||!['inherit','off','local','model'].includes(p.natural_query)))return false
      }
    }
  }
  return true
}
function currentFor(entry,management){
  if(entry.control==='local')return Object.fromEntries(entry.group_ids.map(id=>[id,{natural_query:management.groups.get(id).natural_query}]))
  if(entry.control==='maintenance'){const s=management.snapshot().settings;return{queriesEnabled:s.queriesEnabled,notificationsEnabled:s.notificationsEnabled}}
  return entry.group_id?management.groups.get(entry.group_id):management.snapshot().settings
}
class OperationJournal {
  constructor(directory,identity){
    this.file=path.join(directory,'operations.v1.json');this.identity=identity;this.error=null
    this.value={schema:1,identity,entries:[]}
    try{const info=fs.lstatSync(this.file);if(!info.isFile()||info.isSymbolicLink()||info.size>4*1024*1024)throw Error('ops_journal_invalid')
      const v=JSON.parse(fs.readFileSync(this.file,'utf8'))
      if(v.schema!==1||v.identity!==identity||!Array.isArray(v.entries)||v.entries.length>1000||v.entries.some(e=>!validEntry(e)))throw Error('ops_journal_invalid')
      this.value=v
    }catch(e){if(e.code!=='ENOENT')this.error='ops_journal_unavailable'}
  }
  save(){
    if(this.error)throw Error(this.error)
    const content=JSON.stringify(this.value)+'\n',temporary=this.file+'.tmp'
    if(Buffer.byteLength(content)>4*1024*1024)throw Error('ops_journal_capacity')
    try{fs.writeFileSync(temporary,content,{mode:0o600});const fd=fs.openSync(temporary,'r+');try{fs.fsyncSync(fd)}finally{fs.closeSync(fd)}fs.renameSync(temporary,this.file)}
    catch(e){try{if(fs.readFileSync(this.file,'utf8')===content)return}catch{}throw Error('ops_journal_write_unconfirmed')}
  }
  begin(action,before,after,extra={}){
    if(this.error)throw Error(this.error)
    const cutoff=Date.now()-30*86400000,previous=this.value
    let entries=previous.entries.filter(e=>e.at>=cutoff||e.state==='intent'||active(e))
    if(entries.length>=1000){const index=entries.findIndex(e=>e.state!=='intent'&&!active(e));if(index<0)throw Error('ops_journal_capacity');entries.splice(index,1)}
    const entry={id:randomUUID().replaceAll('-',''),at:Date.now(),action,before,after,state:'intent',...extra}
    this.value={...previous,entries:[...entries,entry]}
    try{this.save()}catch(e){this.value=previous;throw e}
    return entry
  }
  compact(entry){
    if(entry.control||entry.state==='intent'||!entry.before||!entry.after)return
    entry.summary={fields:Object.keys(entry.after).filter(k=>!equal(entry.before[k],entry.after[k])),before_digest:digest(entry.before),after_digest:digest(entry.after)}
    delete entry.before;delete entry.after
  }
  finish(entry,state,code=null){entry.state=state;entry.code=code;entry.finished_at=Date.now();this.compact(entry);this.save();return entry}
  apply(action,before,after,run,extra={}){
    const entry=this.begin(action,before,after,extra)
    let result
    try{result=run()}catch(e){const conflict=/conflict|invalid/.test(e.message);this.finish(entry,conflict?'failed':'intent',conflict?'conflict':'save_unconfirmed');throw e}
    if(result?.guard)entry.result_guard=result.guard
    else if(Number.isSafeInteger(result?.revision))entry.result_guard={group_revision:result.revision}
    this.finish(entry,'applied');return result
  }
  find(id){return this.value.entries.find(e=>e.id===id)}
  snapshot(){return{available:!this.error,reason_code:this.error,items:structuredClone(this.value.entries).reverse()}}
  publicSnapshot(){return{available:!this.error,reason_code:this.error,total:this.value.entries.length,items:this.value.entries.slice(-100).reverse().map(e=>({id:e.id,at:e.at,action:e.action,state:e.state,code:e.code,session:e.session,group_id:e.group_id,accepted_guard:e.accepted_guard,result_guard:e.result_guard,summary:e.summary||{fields:e.control==='local'?['natural_query']:Object.keys(e.after||{})}}))}}
  controls(){return this.value.entries.filter(active)}
  reconcile(management){
    if(this.error)return
    let changed=false
    for(const entry of this.value.entries){
      if(entry.state!=='intent')continue
      let current
      try{management.assertPersistent();if(entry.control==='local'||entry.group_id)management.groups.assertPersistent();current=currentFor(entry,management)}
      catch{entry.state='conflict';entry.code='persistent_values_changed';changed=true;continue}
      entry.state=equal(current,entry.after)?'applied':equal(current,entry.before)?'not_applied':'conflict'
      entry.code='startup_readback';changed=true
      if(entry.restore_of&&entry.state==='applied'){const target=this.find(entry.restore_of);if(target)target.restored_by=entry.id}
      this.compact(entry)
    }
    if(changed){try{this.save()}catch{this.error='ops_journal_reconcile_unconfirmed'}}
  }
}
module.exports={OperationJournal,currentFor}
