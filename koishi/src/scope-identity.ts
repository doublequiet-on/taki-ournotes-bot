// Task05 synchronous consumer of task09 check_scope_binding; no C1 interpreter.
import { isDeepStrictEqual } from 'node:util'
import type { ScopeAuthority, ScopeDecision } from './scope-authority'

export const productIdentityChecker = '3dda6d29955328777f7e526c21bfd2828dbbea8a'
export interface TrustedScopeBindingCheck {
  binding: unknown
  decision: unknown // Exact check_scope_binding(binding, candidate, expected, observed) result.
  checkerCommit: string
}
const outcomes = {
  identity_verified:'allow', schema_incompatible:'manual', malformed_contract:'deny',
  identity_mismatch:'deny', unapproved_manifest:'deny', capability_exceeded:'deny',
  component_mismatch:'deny', protocol_incompatible:'deny', controller_too_old:'deny',
  rollback_denied:'deny', scope_binding_malformed:'deny', scope_binding_mismatch:'deny',
} as const
const hash = (v:unknown):v is string => typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)

/** In-memory session only. 08/10 must invalidate BEFORE changing any C1 input.
 * A ticket belongs to this check/session only; delayed results cannot reopen a newer
 * or invalidated session. Not persisted, not scope approval, not a transport API.
 */
export class ProductIdentitySession {
  private ticket?:object
  private snapshot?:TrustedScopeBindingCheck
  invalidate():void { this.ticket=undefined;this.snapshot=undefined }
  beginCheck():object { this.invalidate();return this.ticket={} }
  publish(ticket:object, snapshot:TrustedScopeBindingCheck):boolean {
    if(!this.ticket||ticket!==this.ticket)return false
    this.snapshot=structuredClone(snapshot);this.ticket=undefined
    return true
  }
  read=():TrustedScopeBindingCheck|undefined => this.snapshot&&structuredClone(this.snapshot)
}

/** Only a trusted session reader may be injected. Never deserialize a persisted
 * allow result as a snapshot; 09 evidence is an audit hash, not authorization.
 */
export function productIdentityAdapter(read:()=>TrustedScopeBindingCheck|undefined):ScopeAuthority['identity'] {
  return binding=>{
    const denied=(reason:string,evidence=''):ScopeDecision=>({outcome:'deny',reason,evidence})
    let check:TrustedScopeBindingCheck|undefined
    try{check=read()}catch{return denied('identity_authority_unavailable')}
    if(!check)return {outcome:'manual',reason:'identity_check_pending',evidence:''}
    if(Object.keys(check).sort().join()!=='binding,checkerCommit,decision'
      ||check.checkerCommit!==productIdentityChecker)return denied('identity_snapshot_invalid')
    if(!isDeepStrictEqual(binding,check.binding))return denied('identity_snapshot_binding_mismatch')
    const d=check.decision as {outcome?:unknown;reason?:unknown;evidence?:unknown}
    if(!d||typeof d!=='object'||Array.isArray(d)||Object.keys(d).sort().join()!=='evidence,outcome,reason'
      ||typeof d.reason!=='string'||!Object.hasOwn(outcomes,d.reason)
      ||outcomes[d.reason as keyof typeof outcomes]!==d.outcome)return denied('identity_result_invalid')
    // 09 returns empty evidence only for malformed C1 inputs, which already deny.
    if(!hash(d.evidence)&&!(d.outcome==='deny'&&d.reason==='malformed_contract'&&d.evidence===''))return denied('identity_evidence_invalid')
    if(d.outcome==='allow') {
      const b=binding as {contract_version?:unknown;manifest_sha256?:unknown}
      if(!b||typeof b!=='object'||Array.isArray(b)||Object.keys(b).sort().join()!=='contract_version,manifest_sha256'
        ||b.contract_version!==1||!hash(b.manifest_sha256))return denied('scope_binding_malformed')
    }
    return {outcome:d.outcome as ScopeDecision['outcome'],reason:d.reason,evidence:d.evidence as string}
  }
}

export function productScopeAuthority(read:()=>TrustedScopeBindingCheck|undefined,
  approval?:ScopeAuthority['approval']):ScopeAuthority {
  return {identity:productIdentityAdapter(read),approval:approval??((_candidate,digest)=>({
    outcome:'manual',reason:'scope_approval_policy_pending',evidence:digest,
  }))}
}
