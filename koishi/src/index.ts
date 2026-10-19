// L3: Koishi scoped lifecycle -> optional Python backend receiver and QQ delivery.
// Effects: disabled by default; enabled mode registers an epoch and handles addressed queries.
import { Context, Service, Schema } from 'koishi'
import { QQDelivery } from './delivery'
import { decodeIncoming, Identity } from './ingress'
import type { QQBot } from '@satorijs/adapter-qq'
import { Receiver, ReceiverConfig } from './receiver'
import { installGatewayCompatibility } from './gateway'

export * from './delivery'
export * from './ingress'
export * from './backend'
export * from './receiver'
export * from './gateway'
export * from './notices'
export const name = 'taki'
export const inject = { optional: ['database'] }

export interface Config extends ReceiverConfig { enabled: boolean }
export const Config: Schema<Config> = Schema.object({
  enabled: Schema.boolean().default(false).description('启用迁移候选接线；生产切换须另行确认'),
  instanceId: Schema.string().default('taki-test'),
  appId: Schema.string().default(''), selfId: Schema.string().default(''),
  backendUrl: Schema.string().default('http://127.0.0.1:8765'),
  tokenEnv: Schema.string().default('OURNOTES_BACKEND_TOKEN'),
  permissions: Schema.array(String).default(['authority:1']),
  notifications: Schema.boolean().default(true),
  maxPending: Schema.number().min(1).max(8).step(1).default(8),
})

declare module 'koishi' {
  interface Context { takiTransport: TakiTransport }
}
export class TakiTransport extends Service {
  receiver?: Receiver
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
export function apply(ctx: Context, config: Config): void {
  const transport=new TakiTransport(ctx)
  ctx.set('takiTransport',transport)
  if (!config?.enabled) return
  if (![config.instanceId, config.appId, config.selfId].every(value => value && value.length <= 256)
      || !Number.isInteger(config.maxPending) || config.maxPending < 1 || config.maxPending > 8) throw new Error('invalid receiver configuration')
  transport.receiver=new Receiver(ctx, config)
  const install = (bot: any) => {
    if (bot.platform === 'qq' && bot.config.id === config.appId) installGatewayCompatibility(ctx, bot)
  }
  for (const bot of ctx.bots) install(bot)
  ctx.on('bot-added', bot => { queueMicrotask(() => { if (ctx.scope.isActive) install(bot) }) })
}
