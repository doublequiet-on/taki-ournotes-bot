// L3: bounded authenticated loopback wire client; no business parsing or QQ credentials.
import type { Inbound } from './ingress'
import type { Attempt, DeliveryEvents, Outcome } from './delivery'
import { setTimeout as delay } from 'node:timers/promises'
import { Telemetry, Span } from './telemetry'
import type { PolicySnapshot } from './group-policies'

import { protocol, capability, BackendError, decodeReady, decodeClassify, decodeDirectChat, decodePlan, decodeRecorded,
  decodeAcknowledgement, decodeIntent, decodeRegistration, decodeRetirement, decodeChatObservation, decodeChatCandidate, object } from './wire'
import type { Ready, Plan, WirePart, ModelPermission, Recorded } from './wire'
export { protocol, capability, BackendError } from './wire'
export type { Plan, WirePart, ModelPermission } from './wire'
const safeBackendErrors = new Set([
  'ledger_capacity', 'context_capacity', 'epoch_capacity', 'plan_capacity', 'byte_capacity',
  'not_ready', 'inactive_epoch', 'retired_epoch', 'stale_admission', 'request_conflict',
  'message_expired', 'instance_mismatch', 'protocol_version', 'capability_profile',
])

function responseError(bytes: Buffer, status: number): BackendError {
  let code = 'backend_http_' + status
  try {
    const value = object(JSON.parse(bytes.toString('utf8')), 'backend_error_shape')
    const version = object(value.protocol, 'backend_error_shape')
    if (value && Object.keys(value).sort().join() === 'error,protocol'
        && typeof value.error === 'string' && safeBackendErrors.has(value.error)
        && Object.keys(version).sort().join() === 'major,minor'
        && version.major === protocol.major && version.minor === protocol.minor) code = value.error
  } catch { /* Unknown response bodies remain an HTTP status, never diagnostic text. */ }
  return new BackendError(code, status)
}
export class BackendClient implements DeliveryEvents {
  readonly telemetry = new Telemetry(events => {
    if (!this.readiness?.backend_epoch) return Promise.reject(new BackendError('not_ready'))
    return this.request('/v1/admin/telemetry', { backend_epoch: this.readiness.backend_epoch, events },
      131072, false, AbortSignal.timeout(1500))
  })
  observe(span: Span): void { if(this.readiness?.diagnostics_enabled!==false)this.telemetry.enqueue(span) }
  readonly base: string
  readiness?: Ready
  private contextAllowed = true
  private startup = new AbortController()
  deactivate(): void { this.contextAllowed = false; this.startup.abort() }
  constructor(base: string, private token: string, readonly instance: string, readonly epoch: string,
              private fetcher: typeof fetch = fetch) {
    const url = new URL(base)
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password
        || url.pathname !== '/' || url.search || url.hash || token.length < 32) throw new Error('invalid backend configuration')
    this.base = url.origin
  }
  request(path: string, body?: unknown, maximum?: number, binary?: false, signal?: AbortSignal): Promise<unknown>
  request(path: string, body: unknown, maximum: number, binary: true, signal?: AbortSignal): Promise<Uint8Array>
  async request(path: string, body?: unknown, maximum = 131072, binary = false, signal?: AbortSignal): Promise<unknown> {
    if (!path.startsWith('/v1/') || path.includes('..') || path.includes('\\')) throw new BackendError('invalid_resource')
    let response: Response
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10_000)
    try {
      response = await this.fetcher(this.base + path, { method: body === undefined ? 'GET' : 'POST',
        redirect: 'error', signal: signal ? AbortSignal.any([controller.signal, signal]) : controller.signal,
        headers: { Authorization: 'Bearer ' + this.token, 'X-Taki-Instance': this.instance,
          'X-Taki-Epoch': this.epoch, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body) })
      if (!response.body) throw new BackendError('empty_response')
      const reader = response.body.getReader(), chunks: Uint8Array[] = []
      let size = 0
      try {
        while (true) {
          const item = await reader.read()
          if (item.done) break
          size += item.value.length
          if (size > maximum) throw new BackendError('response_size')
          chunks.push(item.value)
        }
      } finally { await reader.cancel() }
      const bytes = Buffer.concat(chunks)
      if (!response.ok) throw responseError(bytes, response.status)
      if (binary) {
        const mime = response.headers.get('Content-Type')?.split(';')[0]
        if (mime !== 'image/png' && mime !== 'image/jpeg') throw new BackendError('image_mime')
        return bytes
      }
      try { return JSON.parse(bytes.toString('utf8')) } catch { throw new BackendError('invalid_json') }
    } catch (error) {
      if (error instanceof BackendError) throw error
      throw new BackendError('backend_transport') // never expose bearer/URLs/raw response
    } finally { clearTimeout(timeout) }
  }
  async retry(path: string, body?: unknown): Promise<unknown> {
    for (let attempt = 0; ; attempt++) {
      try { return await this.request(path, typeof body === 'function' ? body() : body) }
      catch (error) {
        if (!(error instanceof BackendError) || error.status && error.status < 500 || attempt >= 2) throw error
        await new Promise(resolve => setTimeout(resolve, 100 * (attempt + 1)))
      }
    }
  }
  async initialize({ timeoutMs = 60_000, retryMs = 1000 } = {}): Promise<void> {
    const deadline = new AbortController()
    const timer = setTimeout(() => deadline.abort(), timeoutMs)
    const signal = AbortSignal.any([deadline.signal, this.startup.signal])
    try {
      while (!signal.aborted) {
        try {
          const ready = decodeReady(await this.request('/v1/ready', undefined, 131072, false, signal))
          if (!ready.ready) throw new BackendError('backend_not_ready', 503)
          const registered=decodeRegistration(await this.request('/v1/gateways', { gateway_epoch: this.epoch }, 131072, false, signal))
          if(registered.gateway_epoch!==this.epoch || registered.backend_epoch!==ready.backend_epoch) throw new BackendError('gateway_binding')
          if (signal.aborted) break
          this.readiness = ready
          return
        } catch (error) {
          if (signal.aborted) break
          if (!(error instanceof BackendError) || (error.code !== 'backend_transport' && error.status < 500)) throw error
        }
        try { await delay(retryMs, undefined, { signal }) }
        catch { if (!signal.aborted) throw new BackendError('startup_wait_failed') }
      }
      throw new BackendError(this.startup.signal.aborted ? 'startup_cancelled' : 'startup_timeout')
    } finally { clearTimeout(timer) }
  }
  async recognizes(body: string): Promise<boolean> {
    return decodeClassify(await this.retry('/v1/classify', { body }))
  }
  async directChat(body: string): Promise<boolean> {
    return decodeDirectChat(await this.retry('/v1/classify', {body}))
  }
  private encode(input: Inbound, seq: number, policy: PolicySnapshot) {
    if (input.proactiveId!==undefined && (input.trigger!=='proactive' || !/^[a-f0-9]{32}$/.test(input.proactiveId))
        || input.trigger==='proactive' && input.proactiveId===undefined) throw new BackendError('proactive_identity')
    return { protocol, request_id: input.requestId, instance_id: input.instanceId,
      app_id: input.appId, platform: input.platform, kind: input.kind, conversation_id: input.conversationId,
      user_id: input.userId, message_id: input.messageId, timestamp_ms: input.timestampMs, body: input.body,
      ...(input.senderName===undefined ? {} : {sender_name:input.senderName}),
      ...(input.attachments===undefined ? {} : {attachments:input.attachments}),
      ...(input.proactiveId===undefined ? {} : {proactive_id:input.proactiveId}),
      capability, gateway_epoch: this.epoch, ingress_seq: seq, policy:structuredClone(policy),
      trigger:input.trigger ?? (/^[/／]?(?:问|ask)(?:\s|$)/i.test(input.body)?'ask':'command'),
      reference:input.reference ?? null, interaction:null }
  }
  async submit(input: Inbound, seq: number, policy: PolicySnapshot = {natural_query:'model',epoch:this.epoch,global_revision:0,group_revision:0}): Promise<Plan> {
    if (input.trigger==='observation' || input.trigger==='proactive') throw new BackendError('request_trigger')
    return decodePlan(await this.retry('/v1/requests', this.encode(input,seq,policy)))
  }
  async chatObservation(input: Inbound, seq: number, policy: PolicySnapshot) {
    if(input.kind!=='group' || input.trigger!=='observation' || input.proactiveId!==undefined) throw new BackendError('observation_identity')
    return decodeChatObservation(await this.retry('/v1/chat/observations',this.encode(input,seq,policy)))
  }
  async chatCandidate(input: Inbound, seq: number, policy: PolicySnapshot) {
    if(input.kind!=='group' || input.trigger!=='proactive') throw new BackendError('candidate_identity')
    return decodeChatCandidate(await this.retry('/v1/chat/candidates',this.encode(input,seq,policy)))
  }
  async modelPermission(challenge:ModelPermission, policy:PolicySnapshot, allowed:boolean):Promise<Recorded> {
    const { admission_policy_version, remaining_ms, ...binding } = challenge
    const { natural_query, ...current_policy_version } = policy
    return decodeRecorded(await this.request(`/v1/requests/${encodeURIComponent(challenge.request_id)}/model-permissions/${encodeURIComponent(challenge.attempt_id)}`,
      {...binding,decision:allowed?'allow':'deny',current_policy_version,reason:allowed?'model_allowed':'current_policy_denied',ttl_ms:1000},
      131072,false,AbortSignal.timeout(2500)))
  }
  async lookup(id: string): Promise<Plan> { return decodePlan(await this.retry('/v1/requests/' + encodeURIComponent(id))) }
  async image(plan: Plan, part: WirePart): Promise<Uint8Array> {
    const path = `/v1/deliveries/${plan.delivery_id}/parts/${part.id}/image`
    if (part.image !== path) throw new BackendError('image_identity')
    return this.request(path, undefined, 3_000_000, true)
  }
  async event(plan: Pick<Plan, 'request_id' | 'delivery_id'>, kind: 'upload_failed' | 'aborted' | 'batch_finished', part = 'batch', attempt = 'none'): Promise<Plan> {
    return decodePlan(await this.retry(`/v1/deliveries/${plan.delivery_id}/events`, { protocol,
      request_id: plan.request_id, plan_version: 1, gateway_epoch: this.epoch, event: kind, part_id: part, attempt_id: attempt }))
  }
  async intent(a: Attempt): Promise<'granted' | 'existing' | 'denied'> {
    // No retry: a lost permission reply must never become a second send.
    const value = decodeIntent(await this.request(`/v1/deliveries/${a.deliveryId}/events`, { protocol,
      request_id: a.requestId, plan_version: 1, gateway_epoch: a.gatewayEpoch,
      event: 'intent', part_id: a.partId, attempt_id: a.attemptId }))
    return value.permission
  }
  async acknowledge(a: Attempt, result: Outcome): Promise<void> {
    decodeAcknowledgement(await this.retry(`/v1/deliveries/${a.deliveryId}/events`, () => ({ protocol,
      request_id: a.requestId, plan_version: 1, gateway_epoch: a.gatewayEpoch,
      event: 'ack', part_id: a.partId, attempt_id: a.attemptId,
      result: result.status, message_id: result.messageId ?? null, allow_context: this.contextAllowed })))
  }
  async retire(): Promise<{retired:true}> { return decodeRetirement(await this.retry('/v1/gateways/retire', { gateway_epoch: this.epoch })) }
}
