// Shared current health, bounded and coalesced. Last-good data never implies ready.
const { Universal } = require('koishi')
function healthReader(ctx, manifest, identity) {
  let current, expires = 0, pending
  return async function read({ fresh = false } = {}) {
    if (!fresh && current && Date.now() < expires) return current
    if (pending) return pending
    pending = (async () => {
      const receiver = ctx.takiTransport?.receiver, local = receiver?.health()
      const bot = ctx.bots.find(b => b.platform === 'qq' && b.config.id === identity.appId)
      const base = { observed_at: Date.now()/1000, pid: process.pid, release_sha: manifest.commit,
        qq_online: bot?.status === Universal.Status.ONLINE, handshake: !!local?.handshake,
        receiver_active: !!local?.active }
      try {
        const backend = receiver ? await receiver.client.request('/v1/ready', undefined, 131072, false, AbortSignal.timeout(2000)) : null
        return { ...base, ready: !!(local?.active && local.handshake && backend?.ready && backend.background_tasks
          && backend.backend_epoch === local.backend.backend_epoch && backend.backend_code_digest === manifest.backend_code_digest
          && bot?.selfId === identity.selfId && base.qq_online), backend_ready: !!backend?.ready,
          backend_pid: backend?.pid, backend_code_digest: backend?.backend_code_digest, stale: false }
      } catch { return { ...base, ready: false, backend_ready: false, stale: true, reason_code: 'backend_unavailable' } }
    })()
    try { current = await pending; expires = Date.now()+5000; return current }
    finally { pending = undefined }
  }
}
module.exports = { healthReader }
