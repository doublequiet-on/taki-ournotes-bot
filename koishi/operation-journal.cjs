// Durable bounded management intents. No credentials, message bodies or platform sends.
const fs=require('node:fs'),path=require('node:path'),{randomUUID}=require('node:crypto')
const {isDeepStrictEqual:equal}=require('node:util')
class OperationJournal {
  constructor(directory,identity){
    this.file=path.join(directory,'operations.v1.json');this.identity=identity;this.error=null
    this.value={schema:1,identity,entries:[]}
    try{const info=fs.lstatSync(this.file);if(!info.isFile()||info.isSymbolicLink()||info.size>4*1024*1024)throw Error('ops_journal_invalid')
      const v=JSON.parse(fs.readFileSync(this.file,'utf8'))
      if(v.schema!==1||v.identity!==identity||!Array.isArray(v.entries)||v.entries.length>1000)throw Error('ops_journal_invalid')
      this.value=v
    }catch(e){if(e.code!=='ENOENT')this.error='ops_journal_unavailable'}
  }
  save(){
    if(this.error)throw Error(this.error)
    const content=JSON.stringify(this.value)+'\n',temporary=this.file+'.tmp'
    try{fs.writeFileSync(temporary,content,{mode:0o600});const fd=fs.openSync(temporary,'r+');try{fs.fsyncSync(fd)}finally{fs.closeSync(fd)}fs.renameSync(temporary,this.file)}
    catch(e){try{if(fs.readFileSync(this.file,'utf8')===content)return}catch{}throw Error('ops_journal_write_unconfirmed')}
  }
  begin(action,before,after,extra={}){
    if(this.error)throw Error(this.error)
    const cutoff=Date.now()-30*86400000
    this.value.entries=this.value.entries.filter(e=>e.at>=cutoff||e.state==='intent').slice(-999)
    const entry={id:randomUUID().replaceAll('-',''),at:Date.now(),action,before,after,state:'intent',...extra}
    this.value.entries.push(entry);this.save();return entry
  }
  finish(entry,state,code=null){entry.state=state;entry.code=code;entry.finished_at=Date.now();this.save();return entry}
  apply(action,before,after,run,extra={}){
    const entry=this.begin(action,before,after,extra)
    let result
    try{result=run()}catch(e){this.finish(entry,'failed',/conflict/.test(e.message)?'conflict':'save_unconfirmed');throw e}
    this.finish(entry,'applied');return result
  }
  snapshot(){return{available:!this.error,reason_code:this.error,items:structuredClone(this.value.entries).reverse()}}
  reconcile(management){
    if(this.error)return
    let changed=false
    for(const entry of this.value.entries){
      if(entry.state!=='intent')continue
      const current=entry.group_id?management.groups.get(entry.group_id):management.snapshot().settings
      entry.state=equal(current,entry.after)?'applied':equal(current,entry.before)?'not_applied':'conflict'
      entry.code='startup_readback';changed=true
    }
    if(changed){try{this.save()}catch{this.error='ops_journal_reconcile_unconfirmed'}}
  }
}
module.exports={OperationJournal}
