// L3: raw-event sequence ownership -> Koishi scope/permissions -> Python -> QQDelivery.
import { randomUUID } from 'node:crypto'
import { Context, Session, Channel, User } from 'koishi'
import type { QQBot } from '@satorijs/adapter-qq'
import { BackendClient, BackendError, Plan } from './backend'
import { decodeIncoming, Identity, Inbound } from './ingress'
import { QQDelivery, QQInternal } from './delivery'
import { Notices } from './notices'

export interface ReceiverConfig extends Identity {
  notifications?: boolean; backendUrl: string; tokenEnv: string; permissions: string[]; maxPending: number
}
interface Raw { raw: unknown; seq: number; at: number; fingerprint: string; conflict?: boolean }
interface Job { input: Inbound; promise: Promise<void>; delivery?: QQDelivery; plan?: Plan }

export class Receiver {
  readonly epoch = randomUUID()
  readonly client: BackendClient
  private active = true
  private notices?: Notices
  health() { return { active:this.active && (this.notices?.healthy() ?? true), handshake:!!this.client.readiness, epoch:this.client.epoch, backend:this.client.readiness } }
  private sequence = 0
  private raw = new Map<string, Raw>()
  private jobs = new Map<string, Job>()
  private entering = 0
  private admission: Promise<void> = Promise.resolve()
  readonly ready: Promise<void>
  private log: ReturnType<Context['logger']>
  constructor(private ctx: Context, readonly config: ReceiverConfig, client?: BackendClient) {
    this.client = client ?? new BackendClient(config.backendUrl, process.env[config.tokenEnv] ?? '', config.instanceId, this.epoch)
    this.log = ctx.logger('taki')
    this.ready = this.client.initialize()
    // Mark rejection observed even when no message arrives; ingress still sees the same rejection.
    void this.ready.catch(() => this.log.warn('backend initialization failed; admission disabled'))
    ctx.effect(() => ctx.root.on('internal/session', session => this.capture(session as Session)))
    ctx.effect(() => ctx.root.middleware(async (session, next) => {
      if (!await this.receive(session)) return next()
    }, true))
    if (config.notifications) this.notices=new Notices(ctx,this.client,config,this.ready)
    ctx.on('dispose', () => this.dispose())
  }
  capture(session: Session): void {
    if (!this.active || session.platform !== 'qq' || session.bot.config.id !== this.config.appId || session.type !== 'internal') return
    const types: Record<string, string> = { 'qq/group-at-message-create': 'GROUP_AT_MESSAGE_CREATE',
      'qq/group-message-create': 'GROUP_MESSAGE_CREATE', 'qq/c2c-message-create': 'C2C_MESSAGE_CREATE' }
    const type = types[session.event._type ?? ''], data = session.event._data as Record<string, unknown> | undefined
    if (!type || !data || typeof data.id !== 'string' || typeof data.content !== 'string'
        || Buffer.byteLength(data.content) > 16384 || Buffer.byteLength(JSON.stringify(data)) > 32768) return
    const now = Date.now()
    for (const [key, value] of this.raw) if (now - value.at > 298000) this.raw.delete(key)
    if (this.raw.size >= 512 && !this.raw.has(data.id)) return
    const fingerprint = JSON.stringify([type, data.content, data.timestamp, data.author, data.group_id, data.group_openid, data.mentions])
    const existing = this.raw.get(data.id)
    if (existing) {
      // Do not let a conflicting raw event replace the first identity/body while decode is waiting.
      if (existing.fingerprint !== fingerprint) existing.conflict = true
      return
    }
    // Assigned at raw arrival, before adapter async work, permissions or HTTP can reorder.
    this.raw.set(data.id, { raw: { t: type, d: structuredClone(data) }, seq: ++this.sequence, at: now, fingerprint })
  }
  private async permitted(session: Session): Promise<boolean> {
    if (!this.ctx.filter(session)) return false
    if (this.ctx.database) {
      if (!session.isDirect) {
        const fields = new Set<Channel.Field>(['flag', 'assignee', 'guildId', 'permissions', 'locales'])
        this.ctx.emit('before-attach-channel', session, fields)
        const channel = await session.observeChannel(fields)
        if (session.guildId) channel.guildId = session.guildId
        if (await this.ctx.serial(session, 'attach-channel', session)) return false
        if (channel.flag & Channel.Flag.ignore || channel.assignee !== session.selfId && !session.stripped.atSelf) return false
      }
      const fields = new Set<User.Field>(['id', 'flag', 'authority', 'permissions', 'locales'])
      this.ctx.emit('before-attach-user', session, fields)
      const user = await session.observeUser(fields)
      if (await this.ctx.serial(session, 'attach-user', session)) return false
      if (user.flag & User.Flag.ignore) return false
    }
    return this.ctx.permissions.test(this.config.permissions, session)
  }
  async receive(session: Session): Promise<boolean> {
    if (!this.active || session.platform !== 'qq' || session.bot.config.id !== this.config.appId) return false
    const source = this.raw.get(session.messageId ?? '')
    if (!source) return false // never reconstruct identity from an ordinary Koishi message
    if (source.conflict) return true
    if (this.entering + this.jobs.size >= this.config.maxPending) return true
    this.entering++
    const previous = this.admission
    let release!: () => void
    this.admission = new Promise<void>(resolve => { release = resolve })
    let input: Inbound | undefined
    try {
      // Serialize recognition/permissions/admission only; preparation and delivery run independently.
      await previous
      const decoded = await decodeIncoming(session.bot as unknown as QQBot, source.raw, this.config, () => true)
      if (decoded.status !== 'accepted') return false
      const canonical = this.ctx.reflect.trace(decoded.session)
      if (!this.ctx.filter(canonical)) return false
      await this.ready
      if (!this.active) return true
      if (!await this.client.recognizes(decoded.inbound.body)) return false
      if (!await this.permitted(canonical) || !this.active) return true
      input = decoded.inbound
      const existing = this.jobs.get(input.requestId)
      if (existing) {
        if (JSON.stringify(existing.input) !== JSON.stringify(input)) throw new BackendError('local_request_conflict')
        release()
        await existing.promise
        return true
      }
      const plan = await this.client.submit(input, source.seq)
      const job: Job = { input, plan, promise: Promise.resolve() }
      this.jobs.set(input.requestId, job)
      job.promise = this.deliver(job, session.bot as unknown as QQBot)
      release()
      await job.promise
      return true
    } catch (error) {
      this.log.warn('request stopped: %s', error instanceof BackendError ? error.code : 'integration_error')
      return true
    } finally { release(); this.entering-- }
  }
  private validate(plan: Plan, input: Inbound): void {
    if (plan.request_id !== input.requestId || !/^[a-f0-9]{32}$/.test(plan.delivery_id) || plan.plan_version !== 1
        || typeof plan.send_allowed !== 'boolean' || !Number.isFinite(plan.expires_at)
        || !Array.isArray(plan.parts) || plan.parts.length > 5
        || plan.parts.some((p, i) => p.position !== i || !new RegExp('^' + i + '(:f)?$').test(p.id))) {
      throw new BackendError('plan_shape')
    }
  }
  private async deliver(job: Job, bot: QQBot): Promise<void> {
    let completed = false
    const alive = () => this.active && Date.now() < job.input.timestampMs + 298000
    try {
      if (!job.plan) throw new BackendError('missing_admitted_plan')
      this.validate(job.plan, job.input)
      while (alive() && job.plan.status === 'preparing') {
        await new Promise(resolve => setTimeout(resolve, 100))
        job.plan = await this.client.lookup(job.input.requestId)
        this.validate(job.plan, job.input)
      }
      if (!alive() || !job.plan.send_allowed) return
      // Keep installed official methods and live token headers, but bound each transport call.
      // A per-delivery facade avoids changing other plugins' HTTP configuration.
      const api = Object.create(bot.internal) as QQInternal & { http: () => typeof bot.http }
      api.http = () => bot.http.extend({ timeout: 15000 })
      job.delivery = new QQDelivery(api, job.input, job.plan.delivery_id, this.client.epoch, this.client, alive)
      for (let index = 0; index < job.plan.parts.length; index++) {
        let part = job.plan.parts[index]
        // A resumed plan with any prior attempt belongs to the original sender; never rebuild sequence state.
        if (!alive() || part.status !== 'pending') return
        if (part.kind !== 'text' && part.kind !== 'image' || typeof part.text !== 'string') throw new BackendError('part_shape')
        const payload = part.kind === 'text' ? { kind: 'text' as const, text: part.text }
          : { kind: 'image' as const, bytes: await this.client.image(job.plan, part), caption: part.text }
        let result = await job.delivery.deliver(part.id, index, payload)
        if (result.status === 'upload_failed') {
          const fallback: Plan = await this.client.event(job.plan, 'upload_failed', part.id)
          this.validate(fallback, job.input)
          job.plan = fallback
          part = fallback.parts[index]
          if (!fallback.send_allowed || part.mode !== 'fallback' || part.kind !== 'text' || typeof part.text !== 'string') return
          result = await job.delivery.deliver(part.id, index, { kind: 'text', text: part.text })
        }
        if (job.delivery.acknowledgementPending && !await job.delivery.retryAcknowledgement()) return
        if (result.status !== 'success') return
        job.plan = await this.client.lookup(job.input.requestId)
        this.validate(job.plan, job.input)
        if (!job.plan.send_allowed) { completed = job.plan.status === 'finished'; return }
      }
      completed = true
    } finally {
      job.delivery?.dispose()
      if (job.plan && !completed) {
        try { await this.client.event(job.plan, 'aborted') }
        catch { this.log.warn('delivery close unconfirmed; backend expiry retains unresolved intent') }
      }
      this.jobs.delete(job.input.requestId)
    }
  }
  async dispose(): Promise<void> {
    this.active = false
    this.client.deactivate()
    const noticesClosed=this.notices?.close()
    this.raw.clear()
    for (const job of this.jobs.values()) job.delivery?.dispose()
    // Retire before waiting for in-flight sends. Their late ACK may record facts, never context.
    try { await this.ready; await this.client.retire() }
    catch { this.log.warn('gateway retirement unconfirmed; no new local sends') }
    await Promise.allSettled([...this.jobs.values()].map(job => job.promise))
    await noticesClosed
  }
}
