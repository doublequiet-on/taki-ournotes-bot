// L3: Koishi scoped lifecycle -> transport capability for later backend integration.
// Effects: registers a service only; M1 does not install a receiver, query or send.
import { Context, Service } from 'koishi'
import { QQDelivery } from './delivery'
import { decodeIncoming, Identity } from './ingress'
import type { QQBot } from '@satorijs/adapter-qq'

export * from './delivery'
export * from './ingress'
export const name = 'taki'

declare module 'koishi' {
  interface Context { takiTransport: TakiTransport }
}
export class TakiTransport extends Service {
  private active = true
  private deliveries = new Set<QQDelivery>()
  constructor(ctx: Context) {
    super(ctx, 'takiTransport', true)
    ctx.on('dispose', () => {
      this.active = false
      for (const delivery of this.deliveries) delivery.dispose()
      this.deliveries.clear()
    })
  }
  async decode(bot: QQBot, raw: unknown, identity: Identity, recognizes: (body: string) => boolean) {
    if (!this.active) return { status: 'ignored' as const, reason: 'disposed' }
    const result = await decodeIncoming(bot, raw, identity, recognizes)
    if (!this.active || result.status === 'accepted' && !this.ctx.filter(result.session)) {
      return { status: 'ignored' as const, reason: 'scope_or_disposed' }
    }
    return result
  }
  /** Explicit ownership; caller must release at batch end. No background sender. */
  own(delivery: QQDelivery): () => void {
    if (!this.active) throw new Error('transport disposed')
    if (this.deliveries.size >= 64) throw new Error('transport capacity')
    this.deliveries.add(delivery)
    return () => { delivery.dispose(); this.deliveries.delete(delivery) }
  }
}
export function apply(ctx: Context): void { new TakiTransport(ctx) }
