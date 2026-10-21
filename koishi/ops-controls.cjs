// Reversible application controls. All configuration writes retain existing guards.
const {isDeepStrictEqual:equal}=require('node:util'),{createHash}=require('node:crypto')
const {currentFor}=require('./operation-journal.cjs')
const ident=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value)
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value
const signature=value=>createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex')
class Controls {
  constructor(management,journal){this.management=management;this.journal=journal}
  validate(value,restore=false){
    const fields=restore?'group_revision,guard,operation_id,target_id':'action,group_ids,group_revision,guard,operation_id'
    if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join()!==fields||!ident(value.operation_id)
      ||!Number.isSafeInteger(value.group_revision)||value.group_revision<0)throw Error('control_shape')
    const g=value.guard
    if(!g||typeof g!=='object'||Object.keys(g).sort().join()!=='gateway_epoch,revision'||typeof g.gateway_epoch!=='string'||g.gateway_epoch.length>80||!Number.isSafeInteger(g.revision)||g.revision<0)throw Error('control_guard')
    if(restore){if(!ident(value.target_id))throw Error('control_target');return}
    if(!['maintenance','local'].includes(value.action)||!Array.isArray(value.group_ids)||value.group_ids.length>128||new Set(value.group_ids).size!==value.group_ids.length
      ||value.group_ids.some(id=>typeof id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(id))||
      (value.action==='maintenance'?value.group_ids.length!==0:value.group_ids.length===0))throw Error('control_parameters')
  }
  guard(value,local){
    this.management.assertGuard(value.guard);this.management.assertPersistent()
    if(local){this.management.groups.assertPersistent();if(this.management.groups.snapshot().revision!==value.group_revision)throw Error('group_policy_conflict')}
  }
  existing(value){
    if(this.journal.error)throw Error(this.journal.error)
    const old=this.journal.find(value.operation_id)
    if(old&&old.signature!==signature(value))throw Error('control_id_conflict')
    return old
  }
  result(entry){return{id:entry.id,state:entry.state,control:entry.control,no_change:!!entry.no_change,restored_by:entry.restored_by||null,code:entry.code||null}}
  tag(control){return control==='local'?this.management.groups.persistenceTag():this.management.persistenceTag()}
  tagMatches(entry){try{return!!entry.post_tag&&equal(this.tag(entry.control),entry.post_tag)}catch{return false}}
  start(value,session){
    this.validate(value);this.journal.reconcile(this.management)
    const old=this.existing(value);if(old)return this.result(old)
    if(this.journal.controls().length>=128)throw Error('control_capacity')
    let before,after,groups={}
    const s=this.management.snapshot().settings
    if(value.action==='maintenance'){
      before={queriesEnabled:s.queriesEnabled,notificationsEnabled:s.notificationsEnabled};after={queriesEnabled:false,notificationsEnabled:false}
    }else{
      for(const id of [...value.group_ids].sort()){
        const p=this.management.groups.get(id)
        if(this.management.effective(id).natural_query==='model')groups[id]={...p,natural_query:'local'}
      }
      before=Object.fromEntries(Object.keys(groups).map(id=>[id,{natural_query:this.management.groups.get(id).natural_query}]))
      after=Object.fromEntries(Object.keys(groups).map(id=>[id,{natural_query:'local'}]))
    }
    const no_change=equal(before,after)
    const extra={id:value.operation_id,signature:signature(value),control:value.action,group_ids:Object.keys(groups),session,no_change,
      accepted_guard:{...this.management.snapshot().guard,group_revision:this.management.groups.snapshot().revision}}
    this.journal.apply('control/'+value.action,before,after,()=>{
      this.guard(value,value.action==='local')
      if(no_change)return
      const result=value.action==='maintenance'?this.management.updateGuarded({...s,...after},value.guard):this.management.groups.updateMany(groups,value.group_revision)
      this.journal.find(value.operation_id).post_tag=this.tag(value.action)
      return result
    },extra)
    return this.result(this.journal.find(value.operation_id))
  }
  restore(value,session){
    this.validate(value,true);this.journal.reconcile(this.management)
    const old=this.existing(value);if(old)return this.result(old)
    const target=this.journal.find(value.target_id)
    if(!target||!['maintenance','local'].includes(target.control)||target.no_change||target.restore_of||target.state!=='applied')throw Error('control_not_restorable')
    if(target.restored_by)return this.result(this.journal.find(target.restored_by)||target)
    const before=currentFor(target,this.management),after=target.before
    this.journal.apply('control/restore',before,after,()=>{
      this.guard(value,target.control==='local')
      if(!equal(before,target.after)||!this.tagMatches(target))throw Error('control_restore_conflict')
      const result=target.control==='maintenance'?this.management.updateGuarded({...this.management.snapshot().settings,...after},value.guard):
        this.management.groups.updateMany(Object.fromEntries(target.group_ids.map(id=>[id,{...this.management.groups.get(id),natural_query:after[id].natural_query}])),value.group_revision)
      target.restored_by=value.operation_id
      return result
    },{id:value.operation_id,signature:signature(value),control:target.control,group_ids:target.group_ids,restore_of:target.id,session})
    return this.result(this.journal.find(value.operation_id))
  }
  snapshot(){
    let settingsPersistent=true,groupsPersistent=true
    try{this.management.assertPersistent()}catch{settingsPersistent=false}
    try{this.management.groups.assertPersistent()}catch{groupsPersistent=false}
    return {available:!this.journal.error,persistent_matches_live:settingsPersistent&&groupsPersistent,
      settings_persistent_matches_live:settingsPersistent,groups_persistent_matches_live:groupsPersistent,guard:this.management.snapshot().guard,
      group_revision:this.management.groups.snapshot().revision,items:this.journal.controls().map(e=>({id:e.id,at:e.at,control:e.control,state:e.state,
        group_ids:(e.group_ids||[]).slice(0,5),group_count:e.group_ids?.length||0,restorable:e.state==='applied'&&equal(currentFor(e,this.management),e.after)&&this.tagMatches(e),
        persistence_matches:this.tagMatches(e),
        current_matches:equal(currentFor(e,this.management),e.after),
        before:e.control==='maintenance'?e.before:null,
        before_modes:e.control==='local'?Object.values(e.before).reduce((out,p)=>({...out,[p.natural_query]:(out[p.natural_query]||0)+1}),{}):null}))}
  }
}
module.exports={Controls}
