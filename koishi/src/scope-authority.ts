// C5 independent validation and recovery boundary. No QQ or product identity authority.
import { readFileSync, lstatSync, openSync, closeSync, writeFileSync, renameSync, unlinkSync, fsyncSync } from 'node:fs'
import { join } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
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
export interface ScopeDecision { outcome:'allow'|'deny'|'manual'; reason:string; evidence:string; identity_evidence?:string }
export interface ScopeAuthority {
  // External expectation versus candidate binding, provided by 09.
  identity(binding:unknown):ScopeDecision
  // Must consult current authority outside the restored candidate; identity is the current C1 result.
  approval(candidate:Readonly<ScopeDocument>, digest:string, identity?:Readonly<ScopeDecision>):ScopeDecision
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
  // Trusted management review input only; never implies permission. Throws on unavailable data.
  readCandidate(){return this.read()}
  inspect():{decision:ScopeDecision;document?:ScopeDocument;digest?:string} {
    let candidate:ReturnType<ScopeStore['read']>
    try{candidate=this.read()}catch(e){return {decision:{outcome:'deny',reason:(e as NodeJS.ErrnoException).code==='ENOENT'?'scope_missing':'scope_unavailable',evidence:''}}}
    if(!this.authority)return {...candidate,decision:{outcome:'manual',reason:'identity_and_approval_pending',evidence:candidate.digest}}
    try {
      const identity=this.authority.identity(structuredClone(candidate.document.binding))
      if(identity.outcome!=='allow')return {...candidate,decision:identity}
      const decision=this.authority.approval(structuredClone(candidate.document),candidate.digest,identity)
      if(!['allow','deny','manual'].includes(decision.outcome)||!decision.evidence)throw Error('authority_invalid')
      return {...candidate,decision:{...decision,identity_evidence:identity.evidence}}
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

/** Trusted composition supplies fresh inputs outside the recovery tree. Deletion
 * evidence comes from 07's current authority/check, never a restored journal hash.
 * This object is not accepted from Console JSON or persisted as an approval.
 */
export interface ScopeApprovalReview {
  document:ScopeDocument
  digest:string
  revocation_generation:number
  identity_evidence:string
  deletion_evidence:string
}

/** Explicit human scope review, independent of login and 10's recovery review.
 * No persistence, RPC, authority service or automatic approval. Call revoke before
 * mutations/disconnect; every read rechecks the trusted current source as well.
 */
export class ManualScopeApproval {
  private readonly epoch=randomUUID()
  private generation=0
  private highestRevocation=0
  private current?:ScopeApprovalReview
  private ticket?:object
  private approved=false
  constructor(private readCurrent:()=>ScopeApprovalReview|undefined){}
  revoke():void {this.generation++;this.ticket=undefined;this.approved=false;this.current=undefined}
  private refresh():ScopeApprovalReview|undefined {
    let next:ScopeApprovalReview|undefined
    try {
      const value=this.readCurrent()
      if(value) {
        const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)
        if(Object.keys(value).sort().join()!=='deletion_evidence,digest,document,identity_evidence,revocation_generation'
          ||!Number.isSafeInteger(value.revocation_generation)||value.revocation_generation<1
          ||value.revocation_generation<this.highestRevocation
          ||![value.digest,value.identity_evidence,value.deletion_evidence].every(hash))throw Error('scope_review_invalid')
        next={...structuredClone(value),document:validateScopeDocument(value.document)}
        this.highestRevocation=value.revocation_generation
      }
    }catch{next=undefined}
    if(!isDeepStrictEqual(next,this.current)) {this.revoke();this.current=next}
    return next
  }
  begin():{ticket:object;review:ScopeApprovalReview}|undefined {
    this.revoke()
    const review=this.refresh()
    if(!review)return undefined
    this.ticket={}
    return {ticket:this.ticket,review:structuredClone(review)}
  }
  // Only the authenticated management action after human review may call confirm.
  // Boolean/serialized/replayed tickets cannot replace the in-process capability.
  confirm(ticket:object):boolean {
    this.refresh()
    if(!this.ticket||ticket!==this.ticket)return false
    this.ticket=undefined;this.approved=true;this.generation++
    return true
  }
  private revision():string {
    return createHash('sha256').update(JSON.stringify([this.epoch,this.generation,this.current])).digest('hex')
  }
  // Synchronous current generation for 10 revisions.scope; not a permission token.
  read=():{revision:string;approved:boolean;revocation_generation?:number}=>{
    this.refresh()
    return {revision:this.revision(),
      approved:this.approved,revocation_generation:this.current?.revocation_generation}
  }
  approval:ScopeAuthority['approval']=(candidate,digest,identity)=>{
    const current=this.refresh(),evidence=this.revision()
    if(!current||!this.approved)return {outcome:'manual',reason:'scope_manual_review_required',evidence}
    if(digest!==current.digest||!isDeepStrictEqual(candidate,current.document)
      ||identity?.outcome!=='allow'||identity.evidence!==current.identity_evidence) {
      this.revoke()
      return {outcome:'deny',reason:'scope_review_mismatch',evidence:this.revision()}
    }
    return {outcome:'allow',reason:'scope_manually_approved',evidence}
  }
}
