// L3: untrusted HTTP JSON -> validated protocol 2.2 values; no IO or authorization.
import type { PolicySnapshot } from './group-policies'

export const protocol = { major: 2, minor: 2 } as const
export const capability = 'qq-query-chat-v4'
export class BackendError extends Error {
  constructor(public readonly code: string, public readonly status = 0) { super(code) }
}
export function object(value: unknown, code: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new BackendError(code)
  return value as Record<string, unknown>
}
function requireWire(condition: unknown, code: string): asserts condition {
  if (!condition) throw new BackendError(code)
}
// Match backend_protocol.identity: Unicode code points and edge whitespace.
function identity(value: unknown, code: string): string {
  requireWire(typeof value === 'string' && value.length > 0 && [...value].length <= 256
    && !/^\p{White_Space}|\p{White_Space}$/u.test(value) && !/[\x00-\x1f\x7f]/.test(value), code)
  return value
}
function hex(value: unknown, length: number, code: string): string {
  requireWire(typeof value === 'string' && new RegExp('^[a-f0-9]{'+length+'}$').test(value), code)
  return value
}
function integer(value: unknown, minimum: number, maximum: number, code: string): number {
  requireWire(typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum, code)
  return value
}
function finite(value: unknown, code: string): number {
  requireWire(typeof value === 'number' && Number.isFinite(value), code)
  return value
}
function boolean(value: unknown, code: string): boolean {
  requireWire(typeof value === 'boolean', code)
  return value
}
function text(value: unknown, code: string): string {
  requireWire(typeof value === 'string', code)
  return value
}
function choice<T extends string>(value: unknown, values: readonly T[], code: string): T {
  for (const candidate of values) if (value === candidate) return candidate
  throw new BackendError(code)
}
function exact(value: Record<string, unknown>, keys: string[], code: string) {
  requireWire(Object.keys(value).sort().join() === keys.sort().join(), code)
}
function wireProtocol(value: unknown, code = 'protocol_version') {
  const p = object(value, code)
  exact(p, ['major','minor'], code)
  requireWire(p.major === protocol.major && p.minor === protocol.minor, code)
  return protocol
}
function strings(value: unknown, code: string): string[] {
  requireWire(Array.isArray(value) && value.every(v => typeof v === 'string'), code)
  return value.map(v => text(v, code))
}
export interface Ready {
  protocol: typeof protocol; ready: boolean; capability: typeof capability
  backend_epoch: string; backend_code_digest: string; notices_enabled: boolean; diagnostics_enabled: boolean
  features: string[]; management_capabilities: string[]
}
export function decodeReady(value: unknown): Ready {
  const v = object(value, 'ready_shape')
  const result = { protocol:wireProtocol(v.protocol,'incompatible_backend'), ready:boolean(v.ready,'ready_shape'),
    capability:choice(v.capability,[capability],'incompatible_backend'), backend_epoch:hex(v.backend_epoch,32,'ready_shape'),
    backend_code_digest:hex(v.backend_code_digest,64,'ready_shape'), notices_enabled:boolean(v.notices_enabled,'ready_shape'),
    diagnostics_enabled:boolean(v.diagnostics_enabled,'ready_shape'), features:strings(v.features,'ready_shape'),
    management_capabilities:strings(v.management_capabilities,'ready_shape') }
  requireWire(result.management_capabilities.includes('group_policies_v1') && ['classify','gateway_retire','ack_context_guard',
    'notice_gateway_v1','quoted_context_v1','fixed_snapshot_v1','current_model_permit_v1','independent_delivery_v1']
    .every(feature => result.features.includes(feature)), 'incompatible_backend')
  return result
}
export function decodeClassify(value: unknown): boolean { return boolean(object(value,'classify_shape').recognized,'classify_shape') }
export function decodeDirectChat(value: unknown): boolean { return boolean(object(value,'classify_shape').direct_chat,'classify_shape') }
export interface ModelPermission {
  request_id: string; attempt_id: string; nonce: string; user_id: string
  backend_epoch: string; gateway_epoch: string; scope: Record<string,string>
  admission_policy_version: Omit<PolicySnapshot,'natural_query'>; remaining_ms: number
}
export function decodeModelPermission(value: unknown): ModelPermission {
  const v = object(value,'model_permission_shape'), scope = object(v.scope,'model_permission_shape')
  exact(scope,['instance_id','app_id','platform','kind','conversation_id'],'model_permission_shape')
  const version=object(v.admission_policy_version,'model_permission_shape')
  exact(version,['epoch','global_revision','group_revision'],'model_permission_shape')
  return { request_id:hex(v.request_id,64,'model_permission_shape'), attempt_id:hex(v.attempt_id,32,'model_permission_shape'),
    nonce:hex(v.nonce,32,'model_permission_shape'), user_id:identity(v.user_id,'model_permission_shape'),
    backend_epoch:hex(v.backend_epoch,32,'model_permission_shape'), gateway_epoch:identity(v.gateway_epoch,'model_permission_shape'),
    scope:{instance_id:identity(scope.instance_id,'model_permission_shape'),app_id:identity(scope.app_id,'model_permission_shape'),
      platform:choice(scope.platform,['qq'],'model_permission_shape'),kind:choice(scope.kind,['group','c2c'],'model_permission_shape'),
      conversation_id:identity(scope.conversation_id,'model_permission_shape')},
    admission_policy_version:{epoch:identity(version.epoch,'model_permission_shape'),
      global_revision:integer(version.global_revision,0,Number.MAX_SAFE_INTEGER,'model_permission_shape'),
      group_revision:integer(version.group_revision,0,Number.MAX_SAFE_INTEGER,'model_permission_shape')},
    // A positive sub-millisecond lifetime is rounded down by the producer.
    // Current authorization still rejects zero; decoding never grants permission.
    remaining_ms:integer(v.remaining_ms,0,3000,'model_permission_shape') }
}
export type RequestStatus = 'preparing' | 'prepared' | 'finished' | 'failure' | 'uncertain' | 'aborted' | 'expired' | 'failed'
export type PartStatus = 'pending' | 'sending' | 'success' | 'failure' | 'uncertain' | 'aborted' | 'expired'
interface PartFacts {
  id: string; position: number; mode: 'primary' | 'fallback'; status: PartStatus
  attempt: string | null; message: string | null
}
export interface FactPart extends PartFacts { kind?: never; text?: never; image?: never; mime?: never; business_status?: never }
export interface TextPart extends PartFacts { kind: 'text'; text: string; business_status: string; image?: never; mime?: never }
export interface ImagePart extends PartFacts { kind: 'image'; mode: 'primary'; text: string; business_status: string; image: string; mime: 'image/png' | 'image/jpeg' }
export type WirePart = FactPart | TextPart | ImagePart
interface PlanBase {
  protocol: typeof protocol; request_id: string; delivery_id: string; plan_version: 1; expires_at: number
  reply_mention: boolean
  processing_hint?: boolean; pending_model_permission?: ModelPermission
}
export type Plan = PlanBase & (
  { status: RequestStatus; send_allowed: false; parts: FactPart[] } |
  { status: 'prepared'; send_allowed: true; parts: (TextPart | ImagePart)[] } |
  { status: 'preparing'; send_allowed: true; processing_hint: true; parts: [TextPart] }
)
function partFacts(value: unknown, position: number): PartFacts {
  const p = object(value,'part_shape'), mode=choice(p.mode,['primary','fallback'],'part_shape')
  requireWire(p.position === position && p.id === String(position)+(mode==='fallback'?':f':''), 'part_shape')
  return { id:identity(p.id,'part_shape'), position, mode,
    status:choice(p.status,['pending','sending','success','failure','uncertain','aborted','expired'],'part_shape'),
    attempt:p.attempt===null?null:identity(p.attempt,'part_shape'),message:p.message===null?null:identity(p.message,'part_shape') }
}
function payloadPart(value: unknown, position: number): TextPart | ImagePart {
  const p=object(value,'part_shape'), facts=partFacts(value,position), body=text(p.text,'part_shape'), business_status=text(p.business_status,'part_shape')
  if(p.kind==='text') {
    requireWire(p.image===undefined && p.mime===undefined,'part_shape')
    return {...facts,kind:'text',text:body,business_status}
  }
  requireWire(p.kind==='image' && facts.mode==='primary','part_shape')
  return {...facts,mode:'primary',kind:'image',text:body,business_status,image:text(p.image,'part_shape'),
    mime:choice(p.mime,['image/png','image/jpeg'],'part_shape')}
}
export function decodePlan(value: unknown): Plan {
  const v=object(value,'plan_shape'), status=choice(v.status,['preparing','prepared','finished','failure','uncertain','aborted','expired','failed'],'plan_shape')
  requireWire(v.plan_version===1 && Array.isArray(v.parts) && v.parts.length<=5,'plan_shape')
  const base:PlanBase={protocol:wireProtocol(v.protocol),request_id:hex(v.request_id,64,'plan_shape'),
    delivery_id:hex(v.delivery_id,32,'plan_shape'),plan_version:1,expires_at:finite(v.expires_at,'plan_shape'),
    reply_mention:boolean(v.reply_mention,'plan_shape')}
  if(v.processing_hint!==undefined)base.processing_hint=boolean(v.processing_hint,'plan_shape')
  if(v.pending_model_permission!==undefined)base.pending_model_permission=decodeModelPermission(v.pending_model_permission)
  if(!boolean(v.send_allowed,'plan_shape')) {
    // Inactive plans contain durable facts only, including prepared plans after
    // an unsuccessful terminal save. Never expose optional payload as sendable.
    return {...base,status,send_allowed:false,parts:v.parts.map((value,index)=>{
      const p=object(value,'part_shape')
      requireWire(['kind','text','image','mime','business_status'].every(key=>p[key]===undefined),'part_shape')
      return partFacts(value,index)
    })}
  }
  const parts=v.parts.map(payloadPart)
  if(status==='prepared')return {...base,status,send_allowed:true,parts}
  requireWire(status==='preparing' && base.processing_hint===true && parts.length===1 && parts[0].kind==='text','plan_shape')
  return {...base,status,send_allowed:true,processing_hint:true,parts:[parts[0]]}
}
export interface ChatObservation { protocol: typeof protocol; observed: boolean; delay_ms: number }
export function decodeChatObservation(value: unknown): ChatObservation {
  const v=object(value,'observation_shape')
  exact(v,['protocol','observed','delay_ms'],'observation_shape')
  return {protocol:wireProtocol(v.protocol),observed:boolean(v.observed,'observation_shape'),
    delay_ms:integer(v.delay_ms,0,2000,'observation_shape')}
}
export type ChatCandidate = { protocol: typeof protocol; candidate: false }
  | { protocol: typeof protocol; candidate: true; plan: Plan }
export function decodeChatCandidate(value: unknown): ChatCandidate {
  const v=object(value,'candidate_shape'), active=boolean(v.candidate,'candidate_shape')
  exact(v,active?['protocol','candidate','plan']:['protocol','candidate'],'candidate_shape')
  const version=wireProtocol(v.protocol)
  return active?{protocol:version,candidate:true,plan:decodePlan(v.plan)}:{protocol:version,candidate:false}
}
export interface Recorded { recorded: true; duplicate: boolean }
export function decodeRecorded(value: unknown): Recorded {
  const v=object(value,'recorded_shape');requireWire(v.recorded===true,'recorded_shape')
  return {recorded:true,duplicate:boolean(v.duplicate,'recorded_shape')}
}
export function decodeAcknowledgement(value: unknown): Recorded & Plan { return {...decodeRecorded(value),...decodePlan(value)} }
export function decodeIntent(value: unknown): {permission:'granted'|'existing'|'denied';msg_seq:number} {
  const v=object(value,'intent_shape')
  return {permission:choice(v.permission,['granted','existing','denied'],'intent_shape'),msg_seq:integer(v.msg_seq,1,5,'intent_shape')}
}
export function decodeRegistration(value: unknown): {gateway_epoch:string;backend_epoch:string} {
  const v=object(value,'gateway_shape')
  return {gateway_epoch:identity(v.gateway_epoch,'gateway_shape'),backend_epoch:hex(v.backend_epoch,32,'gateway_shape')}
}
export function decodeRetirement(value: unknown): {retired:true} {
  requireWire(object(value,'retire_shape').retired===true,'retire_shape');return {retired:true}
}
export interface NoticeClaim { notice_id:string; claim_id:string; group_id:string; group_version:number;expires_at:number;text:string;send_type:'active' }
export function decodeNoticeClaims(value: unknown): NoticeClaim[] {
  const claims=object(value,'notice_claims').claims
  requireWire(Array.isArray(claims) && claims.length<=1,'notice_claims')
  return claims.map(value=>{
    const c=object(value,'notice_shape'),body=text(c.text,'notice_shape'),group=identity(c.group_id,'notice_shape')
    requireWire(/^[A-Za-z0-9_-]{1,128}$/.test(group) && body.length>0 && [...body].length<=120,'notice_shape')
    return {notice_id:hex(c.notice_id,64,'notice_shape'),claim_id:hex(c.claim_id,32,'notice_shape'),group_id:group,
      group_version:integer(c.group_version,0,Number.MAX_SAFE_INTEGER,'notice_shape'),expires_at:finite(c.expires_at,'notice_shape'),
      text:body,send_type:choice(c.send_type,['active'],'notice_shape')}
  })
}
export function decodeNoticePermission(value: unknown): 'granted' | 'existing' | 'denied' {
  return choice(object(value,'notice_intent').permission,['granted','existing','denied'],'notice_intent')
}
