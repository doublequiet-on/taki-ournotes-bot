// Instance-bound policy state. No business ledger or platform permission ownership.
import { readFileSync, writeFileSync, renameSync, openSync, fsyncSync, closeSync, lstatSync } from 'node:fs'
import { join } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import { createHash } from 'node:crypto'
import type { ManagementSettings } from './management'
export type Mode = 'off' | 'local' | 'model'
export interface GroupPolicy { label: string; queries: 'inherit' | 'allow' | 'pause'; notifications: 'inherit' | 'allow' | 'pause'; natural_query: 'inherit' | Mode }
export interface PolicySnapshot { natural_query: Mode; epoch: string; global_revision: number; group_revision: number }
export type NoticeScope = { kind:'all'; exclude_group_ids:string[] } | { kind:'selected'; group_ids:string[] } | { kind:'none' }
const inherited: GroupPolicy = { label:'', queries:'inherit', notifications:'inherit', natural_query:'inherit' }
const gid = (id: unknown): id is string => typeof id==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(id)
export function validateGroupPolicy(value: unknown): GroupPolicy {
  const p=value as GroupPolicy
  if(!p || typeof p!=='object' || Array.isArray(p) || Object.keys(p).sort().join()!=='label,natural_query,notifications,queries'
    || typeof p.label!=='string' || [...p.label].length>80 || /[\u0000-\u001f\u007f]/.test(p.label)
    || !['inherit','allow','pause'].includes(p.queries) || !['inherit','allow','pause'].includes(p.notifications)
    || !['inherit','off','local','model'].includes(p.natural_query))throw Error('invalid_group_policy')
  return structuredClone(p)
}
interface Document { schema:1; instance:{platform:'qq';app_id:string;instance_id:string};revision:number;groups:Record<string,GroupPolicy> }
export class GroupPolicies {
  private value: Document
  private file: string
  error: string | null = null
  constructor(directory: string, private identity: Document['instance']) {
    this.file=join(directory,'group-policies.v1.json')
    this.value={schema:1,instance:identity,revision:0,groups:{}}
    try { this.value=this.read() }
    catch(error) { if((error as NodeJS.ErrnoException).code!=='ENOENT')this.error='group_policy_unavailable' }
  }
  private read(): Document {
    const info=lstatSync(this.file)
    if(!info.isFile()||info.isSymbolicLink()||info.size>131072)throw Error('invalid_group_document')
    const p=JSON.parse(readFileSync(this.file,'utf8')) as Document
    if(!p || Object.keys(p).sort().join()!=='groups,instance,revision,schema' || p.schema!==1
      || !p.instance || Object.keys(p.instance).sort().join()!=='app_id,instance_id,platform'
      || Object.keys(this.identity).some(k=>(p.instance as any)[k] !== (this.identity as any)[k])
      || !Number.isSafeInteger(p.revision) || p.revision<0 || !p.groups || typeof p.groups!=='object'
      || Array.isArray(p.groups) || Object.keys(p.groups).length>128)throw Error('invalid_group_document')
    for(const [id,policy] of Object.entries(p.groups)){if(!gid(id))throw Error('invalid_group_id');validateGroupPolicy(policy)}
    return p
  }
  snapshot(){return {...structuredClone(this.value),availability:this.error?'unavailable':'available',reason_code:this.error}}
  get(id:string):GroupPolicy { return Object.hasOwn(this.value.groups,id)?structuredClone(this.value.groups[id]):{...inherited} }
  assertPersistent() {
    let disk=this.value
    try{disk=this.read()}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT'||this.value.revision!==0)throw Error('group_policy_conflict')}
    if(!isDeepStrictEqual(disk,this.value))throw Error('group_policy_conflict')
  }
  persistenceTag() {
    const info=lstatSync(this.file,{bigint:true})
    if(!info.isFile()||info.isSymbolicLink())throw Error('group_policy_conflict')
    return {sha256:createHash('sha256').update(readFileSync(this.file)).digest('hex'),inode:String(info.ino),mtime:String(info.mtimeNs),size:String(info.size)}
  }
  update(id:unknown,policy:unknown,revision:unknown) {
    if(!gid(id))throw Error('invalid_group_id')
    return this.updateMany({[id]:validateGroupPolicy(policy)},revision)
  }
  updateMany(policies:Record<string,GroupPolicy>,revision:unknown) {
    if(this.error)throw Error('group_policy_unavailable')
    const entries=Object.entries(policies)
    if(!entries.length||entries.length>128)throw Error('invalid_group_batch')
    for(const [id,policy] of entries){if(!gid(id))throw Error('invalid_group_id');validateGroupPolicy(policy)}
    let disk=this.value
    try{disk=this.read()}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT'){this.error='group_policy_unavailable';throw Error('group_policy_unavailable')}
      if(this.value.revision!==0)throw Error('group_policy_conflict')}
    if(revision!==disk.revision || revision!==this.value.revision || !isDeepStrictEqual(disk,this.value))throw Error('group_policy_conflict')
    if(disk.revision>=Number.MAX_SAFE_INTEGER)throw Error('group_policy_revision_capacity')
    const groups={...disk.groups,...structuredClone(policies)}
    if(Object.keys(groups).length>128)throw Error('group_policy_capacity')
    const next:Document={...disk,revision:disk.revision+1,groups}
    const text=JSON.stringify(next,null,2)+'\n',temporary=this.file+'.tmp'
    try {
      writeFileSync(temporary,text,{mode:0o600});const fd=openSync(temporary,'r+')
      try{fsyncSync(fd)}finally{closeSync(fd)}
      renameSync(temporary,this.file)
      if(process.platform!=='win32'){const dir=openSync(join(this.file,'..'),'r');try{fsyncSync(dir)}finally{closeSync(dir)}}
    } catch {
      // A lost write result is resolved from persistent content, never blindly retried.
      try { if(readFileSync(this.file,'utf8')!==text)throw Error('different') }
      catch { throw Error('group_policy_save_unconfirmed') }
    }
    this.value=next
    return this.snapshot()
  }
  effective(id:string,settings:ManagementSettings) {
    const p=this.get(id),reasons:string[]=[],noticeReasons:string[]=[]
    const globalGroup=settings.groupsEnabled&&(!settings.groupAllowlist.length||settings.groupAllowlist.includes(id))
    if(this.error)reasons.push(this.error)
    if(!settings.queriesEnabled)reasons.push('global_queries_disabled')
    if(!globalGroup)reasons.push('global_group_excluded')
    if(p.queries==='pause')reasons.push('group_queries_paused')
    if(this.error)noticeReasons.push(this.error)
    if(!settings.notificationsEnabled)noticeReasons.push('global_notifications_disabled')
    if(!globalGroup)noticeReasons.push('global_group_excluded')
    if(p.notifications==='pause')noticeReasons.push('group_notifications_paused')
    return { queries:!this.error&&settings.queriesEnabled&&globalGroup&&p.queries!=='pause',
      notifications:!this.error&&settings.notificationsEnabled&&globalGroup&&p.notifications!=='pause',
      natural_query:p.natural_query==='inherit'?'model':p.natural_query,reasons,notification_reasons:noticeReasons,
      model_ceiling:'backend_configuration_and_quota',policy:p }
  }
  noticeScope(settings:ManagementSettings):NoticeScope {
    if(this.error||!settings.notificationsEnabled||!settings.groupsEnabled)return{kind:'none'}
    if(settings.groupAllowlist.length){
      const ids=settings.groupAllowlist.filter(id=>this.effective(id,settings).notifications)
      return ids.length?{kind:'selected',group_ids:ids}:{kind:'none'}
    }
    return{kind:'all',exclude_group_ids:Object.keys(this.value.groups).filter(id=>!this.effective(id,settings).notifications)}
  }
}
