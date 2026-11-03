// C5 independent validation and recovery boundary. No QQ or product identity authority.
import { readFileSync, lstatSync, openSync, closeSync, writeFileSync, renameSync, unlinkSync, fsyncSync } from 'node:fs'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'

export type Scope = { kind:'none' } | { kind:'all' } | { kind:'selected'; ids:string[] }
export interface ScopeDocument {
  schema:1
  generation:number
  approval:string
  binding:unknown // Opaque C1 payload; only task09 may interpret/validate it.
  groups:Scope
  c2c:Scope
  announcements:Scope
}
export interface ScopeDecision { outcome:'allow'|'deny'|'manual'; reason:string; evidence:string }
export interface ScopeAuthority {
  // External expectation versus candidate binding, provided by 09.
  identity(binding:unknown):ScopeDecision
  // Must consult current authority outside the restored candidate. Policy is not chosen here.
  approval(candidate:Readonly<ScopeDocument>, digest:string):ScopeDecision
}
function scope(value:unknown):Scope {
  const v=value as Scope
  if(!v || typeof v!=='object' || Array.isArray(v))throw Error('scope_invalid')
  if((v.kind==='none'||v.kind==='all') && Object.keys(v).join()==='kind')return {...v}
  if(v.kind!=='selected'||Object.keys(v).sort().join()!=='ids,kind'||!Array.isArray(v.ids)
    ||v.ids.length===0||v.ids.length>128||new Set(v.ids).size!==v.ids.length
    ||v.ids.some(id=>typeof id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(id)))throw Error('scope_invalid')
  return {kind:'selected',ids:[...v.ids]}
}
export function validateScopeDocument(value:unknown):ScopeDocument {
  const v=value as ScopeDocument
  if(!v||typeof v!=='object'||Array.isArray(v)
    ||Object.keys(v).sort().join()!=='announcements,approval,binding,c2c,generation,groups,schema'
    ||v.schema!==1||!Number.isSafeInteger(v.generation)||v.generation<1
    ||typeof v.approval!=='string'||!v.approval||v.approval.length>256
    ||!v.binding||typeof v.binding!=='object'||Array.isArray(v.binding))throw Error('scope_document_invalid')
  return {...structuredClone(v),groups:scope(v.groups),c2c:scope(v.c2c),announcements:scope(v.announcements)}
}
export function scopeIncludes(value:Scope,id:string):boolean {
  return value.kind==='all'||value.kind==='selected'&&value.ids.includes(id)
}
export class ScopeStore {
  private file:string
  constructor(directory:string,private authority?:ScopeAuthority){this.file=join(directory,'approved-scope.v1.json')}
  private read(){
    const info=lstatSync(this.file)
    if(!info.isFile()||info.isSymbolicLink()||info.size>131072)throw Error('scope_document_invalid')
    const raw=readFileSync(this.file)
    if(raw.length>131072)throw Error('scope_document_invalid')
    return {document:validateScopeDocument(JSON.parse(raw.toString('utf8'))),digest:createHash('sha256').update(raw).digest('hex')}
  }
  inspect():{decision:ScopeDecision;document?:ScopeDocument;digest?:string} {
    let candidate:ReturnType<ScopeStore['read']>
    try{candidate=this.read()}catch(e){return {decision:{outcome:'deny',reason:(e as NodeJS.ErrnoException).code==='ENOENT'?'scope_missing':'scope_unavailable',evidence:''}}}
    if(!this.authority)return {...candidate,decision:{outcome:'manual',reason:'identity_and_approval_pending',evidence:candidate.digest}}
    try {
      const identity=this.authority.identity(structuredClone(candidate.document.binding))
      if(identity.outcome!=='allow')return {...candidate,decision:identity}
      const decision=this.authority.approval(structuredClone(candidate.document),candidate.digest)
      if(!['allow','deny','manual'].includes(decision.outcome)||!decision.evidence)throw Error('authority_invalid')
      return {...candidate,decision}
    }catch{return {...candidate,decision:{outcome:'deny',reason:'authority_unavailable',evidence:candidate.digest}}}
  }
  allows(channel:'groups'|'c2c'|'announcements',id:string){
    const result=this.inspect()
    return result.decision.outcome==='allow'&&!!result.document&&scopeIncludes(result.document[channel],id)
  }
  // Authenticated management may stage recovery even while all QQ access is denied.
  // Saving never approves/activates a candidate; the independent authority is checked on every use.
  stage(value:unknown,expectedDigest:string|null){
    const document=validateScopeDocument(value),lock=this.file+'.lock'
    const fd=openSync(lock,'wx',0o600),temporary=this.file+'.'+randomUUID()+'.tmp'
    try {
      const current=this.stagingTag()
      if(current!==expectedDigest)throw Error('scope_save_conflict')
      const content=JSON.stringify(document,null,2)+'\n'
      if(Buffer.byteLength(content)>131072)throw Error('scope_document_invalid')
      const output=openSync(temporary,'wx',0o600)
      try{writeFileSync(output,content);fsyncSync(output)}finally{closeSync(output)}
      renameSync(temporary,this.file)
      if(process.platform!=='win32'){const directory=openSync(join(this.file,'..'),'r');try{fsyncSync(directory)}finally{closeSync(directory)}}
      return this.inspect()
    }finally{closeSync(fd);unlinkSync(lock);try{unlinkSync(temporary)}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e}}
  }
  // Digest-only repair token also works for malformed JSON; it is not approval evidence.
  stagingTag():string|null {
    try {
      const info=lstatSync(this.file)
      if(!info.isFile()||info.isSymbolicLink()||info.size>131072)throw Error('scope_document_invalid')
      return createHash('sha256').update(readFileSync(this.file)).digest('hex')
    }catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return null;throw e}
  }
  recovery(){return this.inspect().decision}
}
