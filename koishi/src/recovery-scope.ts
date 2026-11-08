// Trusted pre-start composition only. Not a Console RPC or a persisted grant.
import { isDeepStrictEqual } from 'node:util'
import { ManualScopeApproval, ScopeStore } from './scope-authority'
import { ProductIdentitySession, productIdentityAdapter, productScopeAuthority } from './scope-identity'

export interface RecoveryAdministrators {
  product:'personal'|'custom'
  instance:string
  subjects:string[] // Explicitly designated by the owner/recipient, never login users.
  revision:string
  identity_evidence:string // Bind the host's product/instance mapping to current C1.
}
export interface RecoveryOperator {
  subject:string
  session:string
  product:'personal'|'custom'
  instance:string
}
export interface RecoveryScopeSources {
  readAdministrators:()=>RecoveryAdministrators|undefined
  readOperator:()=>RecoveryOperator|undefined
  readRevocationGeneration:()=>number|undefined
  // Current result of the Python composition's deletion_scope_review, not C6's
  // arbitrary evidence_version string and never a restored journal digest.
  readDeletionReview:()=>{status:string; review_sha256?:string}|undefined
}
const hash=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const text=(value:unknown):value is string=>typeof value==='string'&&value.length>0&&value.length<=256

/** The host authenticates the operator and supplies independent current sources.
 * All callbacks are trusted local functions, not request/config deserializers.
 * Missing configuration is denied; ordinary Console authentication is unrelated.
 */
export function createRecoveryScope(directory:string, identity:ProductIdentitySession,
  sources:RecoveryScopeSources) {
  let owner:{administrators:RecoveryAdministrators;operator:RecoveryOperator}|undefined
  let ticket:object|undefined
  function authenticated() {
    const administrators=sources.readAdministrators(),operator=sources.readOperator()
    if(!administrators||!operator
      ||Object.keys(administrators).sort().join()!=='identity_evidence,instance,product,revision,subjects'
      ||Object.keys(operator).sort().join()!=='instance,product,session,subject'
      ||!['personal','custom'].includes(administrators.product)
      ||!text(administrators.instance)||!hash(administrators.revision)||!hash(administrators.identity_evidence)
      ||!Array.isArray(administrators.subjects)||!administrators.subjects.length
      ||!administrators.subjects.every(text)||new Set(administrators.subjects).size!==administrators.subjects.length
      ||!text(operator.subject)||!text(operator.session)
      ||operator.product!==administrators.product||operator.instance!==administrators.instance
      ||!administrators.subjects.includes(operator.subject))return undefined
    return structuredClone({administrators,operator})
  }
  const checkIdentity=productIdentityAdapter(identity.read)
  const manual:ManualScopeApproval=new ManualScopeApproval(()=>{
    if(!owner||!isDeepStrictEqual(owner,authenticated()))return undefined
    const candidate=scope.readCandidate(),current=checkIdentity(candidate.document.binding)
    const deletion=sources.readDeletionReview(),generation=sources.readRevocationGeneration()
    if(current.outcome!=='allow'||current.evidence!==owner.administrators.identity_evidence
      ||deletion?.status!=='allow'||!hash(deletion.review_sha256)
      ||!Number.isSafeInteger(generation)||generation!<1)return undefined
    return {...candidate,revocation_generation:generation!,identity_evidence:current.evidence,
      deletion_evidence:deletion.review_sha256}
  })
  const scope:ScopeStore=new ScopeStore(directory,productScopeAuthority(identity.read,manual.approval))
  function revoke(){ticket=undefined;owner=undefined;manual.revoke()}
  return {
    scope,
    begin() {
      revoke()
      try{owner=authenticated()}catch{owner=undefined}
      if(!owner)return undefined
      const pending=manual.begin()
      if(pending)ticket=pending.ticket
      return pending&&{...pending,operator:structuredClone(owner.operator)}
    },
    confirm(value:object) {
      // Even a ticket from another administrator/session cannot be transferred.
      if(!ticket||value!==ticket)return false
      ticket=undefined
      return manual.confirm(value)
    },
    revoke,
    // Host invokes BEFORE identity/permissions/deletions or authentication change.
    invalidate(){revoke();identity.invalidate()},
    read:manual.read,
    checkScope:()=>scope.recovery(),
  }
}
