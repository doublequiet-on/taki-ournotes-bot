// L3: Active group notices; no passive msg_id/msg_seq or local completion ledger.
import { randomUUID } from 'node:crypto'
import { Context, Session, Universal } from 'koishi'
import type { QQBot } from '@satorijs/adapter-qq'
import { BackendClient, protocol } from './backend'
import { decodeNoticeClaims, decodeNoticePermission } from './wire'
import type { NoticeClaim as Claim } from './wire'
import { classifyReceipt } from './delivery'
import { decodeGroupEvent, Identity } from './ingress'
import type { NoticeScope } from './group-policies'

export class Notices {
  private active = true
  healthy(): boolean { return this.active }
  private sequence = 0
  private queued = 0
  private groupTail = Promise.resolve()
  private seen = new Set<string>()
  private changed = new Map<string, number>()
  private task?: Promise<void>
  private nextRequest = 0
  constructor(private ctx: Context, private client: BackendClient, private identity: Identity, private ready: Promise<void>,
    private enabled: (id?: string) => boolean = () => true, private groups: () => NoticeScope = () => ({kind:'all',exclude_group_ids:[]})) {
    ctx.effect(() => ctx.root.on('internal/session', session => this.capture(session as Session)))
    ctx.setInterval(() => this.tick(), 30000)
    void ready.then(() => this.tick()).catch(() => undefined)
  }
  private base() { return { protocol, instance_id: this.identity.instanceId, app_id: this.identity.appId, gateway_epoch: this.client.epoch } }
  capture(session: Session): void {
    if (!this.active || this.client.readiness?.notices_enabled === false || session.platform !== 'qq'
        || session.bot.config.id !== this.identity.appId || session.bot.selfId !== this.identity.selfId) return
    const type = (session.event._type ?? '').replace(/^qq\//,'').replaceAll('-','_').toUpperCase()
    const data = session.event._data as any
    let event = decodeGroupEvent({ t:type, d:data },this.identity)
    let kind: string | undefined = event?.kind
    let gid = event?.groupId
    let stamp = event ? event.timestampSeconds*1000 : 0
    if (!event && ['GROUP_AT_MESSAGE_CREATE','GROUP_MESSAGE_CREATE'].includes(type)) {
      gid = data?.group_openid ?? data?.group_id
      if (typeof gid !== 'string' || this.seen.has(gid)) return
      kind = 'discover'; stamp = Date.parse(data.timestamp)
    }
    if (!kind || !gid || !/^[A-Za-z0-9_-]{1,128}$/.test(gid) || !Number.isFinite(stamp)) return
    // Local revocation takes effect before its asynchronous HTTP write or an in-flight intent.
    if (this.changed.size >= 4096 && !this.changed.has(gid)) { this.active=false; this.ctx.logger('taki').warn('notice group capacity reached; reload required'); return }
    this.changed.set(gid,(this.changed.get(gid) ?? 0)+1)
    if (this.queued >= 128) { this.active=false; this.ctx.logger('taki').warn('notice event queue full; reload required'); return }
    this.seen.add(gid)
    const body = { ...this.base(), event_id:event?.eventId ?? randomUUID(), group_id:gid, kind,
      timestamp_ms:stamp, ingress_seq:++this.sequence }
    this.queued++
    this.groupTail = this.groupTail.then(async () => {
      await this.ready
      if (this.active && this.client.readiness?.notices_enabled !== false) await this.client.retry('/v1/groups/events',body)
    }).catch(() => { this.active=false; this.ctx.logger('taki').warn('notice group synchronization failed; reload required') })
      .finally(() => { this.queued-- })
  }
  tick(): void {
    if (!this.active || this.task || !this.enabled()) return
    this.task=this.poll().catch(() => this.ctx.logger('taki').warn('notice poll failed; unresolved sends will not be replayed'))
      .finally(() => { this.task=undefined })
  }
  private async pace() {
    await new Promise(resolve => setTimeout(resolve,Math.max(0,this.nextRequest-Date.now())))
    this.nextRequest=Date.now()+2100
  }
  private event(c: Claim, event: string, extra: object = {}, retry=true) {
    const payload={...this.base(),claim_id:c.claim_id,event_id:randomUUID(),event,group_version:c.group_version,...extra}
    return retry ? this.client.retry(`/v1/notices/${c.notice_id}/events`,payload)
      : this.client.request(`/v1/notices/${c.notice_id}/events`,payload)
  }
  private async poll(): Promise<void> {
    await this.ready
    if (!this.client.readiness?.notices_enabled) return
    const bot = this.ctx.bots.find(b=>b.platform==='qq' && b.config.id===this.identity.appId && b.selfId===this.identity.selfId) as QQBot | undefined
    if (!bot || bot.status !== Universal.Status.ONLINE || !this.active) return
    await this.groupTail
    if (!this.active || !this.enabled()) return
    const groups = this.groups()
    if(groups.kind==='none')return
    const response = await this.client.retry('/v1/notices/claims',{...this.base(),claim_request_id:randomUUID(),limit:1,
      scope:groups})
    for (const c of decodeNoticeClaims(response)) {
      const version=this.changed.get(c.group_id)
      const alive=()=>this.active && this.enabled(c.group_id) && bot.status===Universal.Status.ONLINE && Date.now()<c.expires_at*1000 && this.changed.get(c.group_id)===version
      const api=Object.create(bot.internal) as typeof bot.internal
      Object.assign(api,{http:()=>bot.http.extend({timeout:15000})})
      await this.pace()
      if (!alive()) return
      let allowed: boolean | undefined
      try { const state=await api.getGuildBotState(c.group_id); if (typeof state?.allow_proactive_msg==='boolean') allowed=state.allow_proactive_msg } catch { /* whitelist-only; POST retains platform permission enforcement */ }
      await this.groupTail
      if (!alive()) return
      if (allowed!==undefined) await this.event(c,'permission',{allowed,observed_at_ms:Date.now()})
      await this.pace()
      await this.groupTail
      if (!alive()) return
      const attempt_id=randomUUID()
      const intent=await this.event(c,'intent',{attempt_id},false)
      if (decodeNoticePermission(intent)!=='granted') return
      if (!alive()) { await this.event(c,'ack',{attempt_id,result:'aborted',message_id:null}); return }
      let result='uncertain', message_id: string | null=null
      try {
        const receipt=await api.sendMessage(c.group_id,{msg_type:0,content:c.text})
        const outcome=classifyReceipt(receipt)
        result=outcome.status;message_id=outcome.messageId ?? null
      } catch { /* Persist ambiguous outcome; no automatic send retry. */ }
      await this.event(c,'ack',{attempt_id,result,message_id})
    }
  }
  async close(): Promise<void> {
    this.active=false
    await Promise.allSettled([this.groupTail, ...(this.task ? [this.task] : [])])
  }
}
