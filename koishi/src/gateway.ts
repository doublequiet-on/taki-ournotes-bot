// L3: narrow per-bot discovery compatibility; preserves real discovery path/query.
import type { Context } from 'koishi'
import type { QQBot } from '@satorijs/adapter-qq'

export function normalizeGateway(value: string): string {
  const url = new URL(value)
  if (url.protocol !== 'wss:' || url.username || url.password) throw new Error('invalid QQ gateway')
  if (url.hostname === 'api.sgroup.qq.com') url.hostname = 'api.bot.qq.com'
  if (url.hostname === 'sandbox.api.sgroup.qq.com') url.hostname = 'sandbox.api.bot.qq.com'
  return url.toString()
}
export function installGatewayCompatibility(ctx: Context, bot: QQBot): void {
  if (bot.config.retryWhen?.length) throw new Error('Taki requires QQ retryWhen: []')
  if (bot.config.protocol !== 'websocket') return
  const config = bot.config
  if (config.gatewayUrl) {
    const original = config.gatewayUrl, normalized = normalizeGateway(original)
    config.gatewayUrl = normalized
    ctx.on('dispose', () => { if (config.gatewayUrl === normalized) config.gatewayUrl = original })
    return
  }
  const original = bot.internal.getGateway
  const wrapped = async () => {
    const result = await original.call(bot.internal)
    return { ...result, url: normalizeGateway(result.url) }
  }
  bot.internal.getGateway = wrapped
  ctx.on('dispose', () => { if (bot.internal.getGateway === wrapped) bot.internal.getGateway = original })
}
