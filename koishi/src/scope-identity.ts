// Task05 adapter for task09 check_identity v1. No identity interpretation or activation.
// Trusted controller supplies a fresh check of strict-parsed manifest bytes, external
// expected authority and independent observations; never load a checker from a backup.
import { isDeepStrictEqual } from 'node:util'
import type { ScopeAuthority, ScopeDecision } from './scope-authority'

export interface TrustedIdentityCheck {
  candidate: unknown
  verdict: unknown
  manifestSha256: string
  checkerCommit: string
  expectedRevision: string
  observationRevision: string
}
const reasons = {
  identity_verified:'allow', schema_incompatible:'manual', malformed_contract:'reject',
  identity_mismatch:'reject', unapproved_manifest:'reject', capability_exceeded:'reject',
  component_mismatch:'reject', protocol_incompatible:'reject', controller_too_old:'reject',
  rollback_denied:'reject',
} as const
const digest = (v:unknown,n:number):v is string => typeof v==='string'&&new RegExp(`^[a-f0-9]{${n}}$`).test(v)

/** Binding is the exact candidate object parsed by 09.read_json, not a verdict.
 * The callback has no candidate-controlled path/argument and must read current trusted
 * controller state on every invocation. Its absence keeps C1 pending, not approved.
 */
export function productIdentityAdapter(read:()=>TrustedIdentityCheck|undefined):ScopeAuthority['identity'] {
  return binding=>{
    const denied=(reason:string,evidence=''):ScopeDecision=>({outcome:'deny',reason,evidence})
    let check:TrustedIdentityCheck|undefined
    try{check=read()}catch{return denied('identity_authority_unavailable')}
    if(!check)return {outcome:'manual',reason:'identity_check_pending',evidence:''}
    if(!digest(check.manifestSha256,64)||!digest(check.checkerCommit,40)
      ||!digest(check.expectedRevision,64)||!digest(check.observationRevision,64))return denied('identity_evidence_invalid')
    const evidence=`c1/v1:${check.checkerCommit}:${check.manifestSha256}:${check.expectedRevision}:${check.observationRevision}`
    // Do not let a valid receipt for candidate A approve a restored binding B.
    if(!isDeepStrictEqual(binding,check.candidate))return denied('identity_binding_mismatch',evidence)
    const v=check.verdict as {status?:unknown;reason?:unknown;contract_version?:unknown}
    if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join()!=='contract_version,reason,status'
      ||v.contract_version!==1||typeof v.reason!=='string'||!Object.hasOwn(reasons,v.reason)
      ||reasons[v.reason as keyof typeof reasons]!==v.status)return denied('identity_result_invalid',evidence)
    return {outcome:v.status==='reject'?'deny':v.status as 'allow'|'manual',reason:v.reason,evidence}
  }
}

/** Preflight allow is only C1. Until a separately chosen C5 authority is supplied,
 * even a valid identity remains manual and cannot make ScopeStore.allows return true.
 */
export function productScopeAuthority(read:()=>TrustedIdentityCheck|undefined,
  approval?:ScopeAuthority['approval']):ScopeAuthority {
  return {identity:productIdentityAdapter(read),approval:approval??((_candidate,digest)=>({
    outcome:'manual',reason:'scope_approval_policy_pending',evidence:digest,
  }))}
}
