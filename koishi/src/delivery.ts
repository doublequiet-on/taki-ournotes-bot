// L3: Captured parts + original inbound + durable-event port -> per-part outcomes.
// Pos: Thin QQ delivery adapter. Effects: official upload/send calls; no automatic retry.
import { createHash, randomUUID } from 'node:crypto'
import type { QQBot } from '@satorijs/adapter-qq'
import type { Inbound } from './ingress'

export type QQInternal = Pick<QQBot['internal'], 'sendFileGuild' | 'sendFilePrivate' | 'sendMessage' | 'sendPrivateMessage'>
export type Part =
  | { kind: 'text'; text: string }
  | { kind: 'image'; bytes: Uint8Array; caption: string }
export type Outcome = { status: 'success' | 'failure' | 'uncertain' | 'aborted' | 'upload_failed'; reason?: string; messageId?: string }
export interface Attempt {
  requestId: string; deliveryId: string; partId: string; attemptId: string; gatewayEpoch: string
}
export interface DeliveryEvents {
  intent(attempt: Attempt): Promise<'granted' | 'existing' | 'denied'>
  acknowledge(attempt: Attempt, result: Outcome): Promise<void>
}
export interface Limits {
  maxSends: number
  replyWindowMs: number
  maxImageBytes: number
  maxTextChars: number
}
export const candidateLimits: Readonly<Limits> = Object.freeze({
  maxSends: 5, replyWindowMs: 298_000, maxImageBytes: 3_000_000, maxTextChars: 1800,
})

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : undefined
}
export function classifyReceipt(value: unknown): Outcome {
  const data = object(value)
  if (data?.audit_id || object(data?.data)?.message_audit) return { status: 'uncertain', reason: 'audit_pending' }
  if (typeof data?.code === 'number' && data.code !== 0) return { status: 'failure', reason: 'platform_rejected' }
  if (typeof data?.id === 'string' && data.id.trim()) return { status: 'success', messageId: data.id }
  return { status: 'uncertain', reason: 'missing_receipt' }
}
function fingerprint(part: Part): string {
  return createHash('sha256').update(JSON.stringify([part.kind, part.kind === 'text' ? part.text : part.caption]))
    .update(part.kind === 'image' ? part.bytes : '').digest('hex')
}

/** One original inbound, one sequence owner. The event port is implemented by M2. */
export class QQDelivery {
  private sequence = 0
  private disposed = false
  private halted = false
  private nextIndex = 0
  private tail: Promise<unknown> = Promise.resolve()
  private readonly parts = new Map<string, { fingerprint: string; index: number; promise: Promise<Outcome> }>()
  private pendingAck?: { attempt: Attempt; result: Outcome; advance: boolean }
  constructor(
    private readonly api: QQInternal,
    private readonly inbound: Inbound,
    private readonly deliveryId: string,
    private readonly gatewayEpoch: string,
    private readonly events: DeliveryEvents,
    private readonly alive: () => boolean,
    private readonly limits: Readonly<Limits> = candidateLimits,
    private readonly now: () => number = Date.now,
  ) {
    if (!deliveryId || !gatewayEpoch || !Number.isInteger(limits.maxSends) || limits.maxSends < 1
        || limits.maxSends > 5 || !Number.isFinite(limits.replyWindowMs) || limits.replyWindowMs <= 0
        || limits.replyWindowMs > candidateLimits.replyWindowMs
        || !Number.isInteger(limits.maxImageBytes) || limits.maxImageBytes < 1 || limits.maxImageBytes > candidateLimits.maxImageBytes
        || !Number.isInteger(limits.maxTextChars) || limits.maxTextChars < 1 || limits.maxTextChars > candidateLimits.maxTextChars) {
      throw new Error('invalid delivery limits')
    }
  }
  dispose(): void { this.disposed = true }
  get usedSlots(): number { return this.sequence }
  private valid(): boolean {
    const age = this.now() - this.inbound.timestampMs
    return !this.disposed && this.alive() && age >= 0 && age < this.limits.replyWindowMs
  }
  deliver(partId: string, index: number, part: Part): Promise<Outcome> {
    if (!partId || !Number.isInteger(index) || index < 0) return Promise.reject(new Error('invalid part identity'))
    if (part.kind === 'image'
      ? !part.bytes.length || part.bytes.length > this.limits.maxImageBytes || [...part.caption].length > this.limits.maxTextChars
      : !part.text || [...part.text].length > this.limits.maxTextChars) {
      return Promise.resolve({ status: 'aborted', reason: 'part_size' })
    }
    const key = fingerprint(part), existing = this.parts.get(partId)
    if (existing) {
      if (existing.fingerprint !== key || existing.index !== index) return Promise.reject(new Error('conflicting part replay'))
      return existing.promise
    }
    if (this.parts.size >= this.limits.maxSends * 2) return Promise.resolve({ status: 'aborted', reason: 'part_capacity' })
    // Capture bytes now; queued callers cannot alter the payload after its digest.
    const captured: Part = part.kind === 'image' ? { ...part, bytes: Uint8Array.from(part.bytes) } : { ...part }
    const promise = this.tail.then(() => this.send(partId, index, captured)).then(result => {
      // No upload/intent/send occurred: a caller may retry after the prerequisite ACK.
      if (result.reason === 'previous_part_unresolved' || result.reason === 'out_of_order') this.parts.delete(partId)
      return result
    })
    this.tail = promise.catch(() => undefined)
    this.parts.set(partId, { fingerprint: key, index, promise })
    return promise
  }
  private async ack(attempt: Attempt, result: Outcome, advance: boolean): Promise<Outcome> {
    try { await this.events.acknowledge(attempt, result) }
    catch {
      this.pendingAck = { attempt, result, advance }
      this.halted = true
      // Preserve the real platform fact; callers must inspect acknowledgement state.
      return result
    }
    if (advance) this.nextIndex++
    return result
  }
  get acknowledgementPending(): boolean { return Boolean(this.pendingAck) }
  async retryAcknowledgement(): Promise<boolean> {
    // Serialize with deliver(), including repeated ACK recovery callbacks.
    const work = this.tail.then(async () => {
      const pending = this.pendingAck
      if (!pending) return true
      try { await this.events.acknowledge(pending.attempt, pending.result) } catch { return false }
      this.pendingAck = undefined
      if (pending.advance) this.nextIndex++
      this.halted = pending.result.status !== 'success'
      return true
    })
    this.tail = work.catch(() => undefined)
    return work
  }
  private async send(partId: string, index: number, part: Part): Promise<Outcome> {
    if (index !== this.nextIndex) return { status: 'aborted', reason: 'out_of_order' }
    if (this.halted || this.pendingAck) return { status: 'aborted', reason: 'previous_part_unresolved' }
    if (!this.valid()) return { status: 'aborted', reason: 'inactive_or_expired' }
    if (this.sequence >= this.limits.maxSends) return { status: 'aborted', reason: 'send_budget' }
    let media: { file_info: string } | undefined
    if (part.kind === 'image') {
      try {
        const payload = { file_type: 1, srv_send_msg: false, file_data: Buffer.from(part.bytes).toString('base64') }
        const uploaded = this.inbound.kind === 'group'
          ? await this.api.sendFileGuild(this.inbound.conversationId, payload)
          : await this.api.sendFilePrivate(this.inbound.userId, payload)
        if (!uploaded || typeof uploaded.file_info !== 'string' || !uploaded.file_info.trim()) {
          return { status: 'upload_failed', reason: 'missing_file_info' }
        }
        media = { file_info: uploaded.file_info }
      } catch { return { status: 'upload_failed', reason: 'upload_error' } }
    }
    if (!this.valid()) return { status: 'aborted', reason: 'inactive_or_expired' }
    const attempt: Attempt = { requestId: this.inbound.requestId, deliveryId: this.deliveryId, partId,
      attemptId: randomUUID(), gatewayEpoch: this.gatewayEpoch }
    let permission: 'granted' | 'existing' | 'denied'
    try { permission = await this.events.intent(attempt) }
    catch {
      this.halted = true
      return { status: 'aborted', reason: 'intent_unconfirmed' }
    }
    if (permission !== 'granted') {
      this.halted = true
      return { status: 'aborted', reason: 'intent_not_granted' }
    }
    if (!this.valid()) {
      this.halted = true
      return this.ack(attempt, { status: 'aborted', reason: 'inactive_or_expired' }, false)
    }
    const payload = { msg_id: this.inbound.messageId, msg_seq: ++this.sequence,
      msg_type: part.kind === 'image' ? 7 : 0,
      content: part.kind === 'image' ? part.caption || ' ' : part.text,
      ...(media ? { media } : {}) }
    let result: Outcome
    try {
      result = classifyReceipt(this.inbound.kind === 'group'
        ? await this.api.sendMessage(this.inbound.conversationId, payload)
        : await this.api.sendPrivateMessage(this.inbound.userId, payload))
    } catch (error) {
      const data = object(object(error)?.response)?.data
      result = object(data) ? classifyReceipt(data) : { status: 'uncertain', reason: 'transport_error' }
    }
    if (result.status !== 'success') this.halted = true
    return this.ack(attempt, result, result.status === 'success')
  }
}
