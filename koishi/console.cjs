// Official Koishi web UI with a bounded administrator surface; no general config/marketplace loader.
const { createHash, timingSafeEqual } = require('node:crypto')
const path = require('node:path')
const NodeConsole = require('@koishijs/plugin-console').default
const Server = require('@koishijs/plugin-server').default
const { Management } = require('./lib/management')

function configuration(env) {
  if (env.TAKI_CONSOLE_ENABLED !== '1') return
  const port = Number(env.TAKI_CONSOLE_PORT || 5140)
  const password = env.TAKI_CONSOLE_PASSWORD || ''
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw Error('console port')
  if (password.length < 20 || password.length > 256 || password === env.OURNOTES_BACKEND_TOKEN || password === env.QQ_APP_SECRET) throw Error('console requires a separate password of 20-256 characters')
  if (!env.TAKI_CONSOLE_STATE_DIR || !path.isAbsolute(env.TAKI_CONSOLE_STATE_DIR)) throw Error('console state directory required')
  return { port, password, directory: env.TAKI_CONSOLE_STATE_DIR }
}

function install(ctx, config, manifest, assetsDirectory = path.join(__dirname, 'node_modules/taki-console-assets')) {
  const management = new Management(config.directory)
  const readHealth = config.readHealth || require('./health.cjs').healthReader(ctx, manifest, config.identity || {})
  const methods = ['overview', 'requests/list', 'requests/detail', 'sources', 'notices/list', 'query-metrics', 'groups/list', 'settings/update']
  const reads = new Map()
  async function readBackend(resource, params = {}) {
    const receiver = ctx.takiTransport?.receiver
    if (!receiver) throw Error('backend_unavailable')
    const query = new URLSearchParams(params).toString()
    const url = '/v1/admin/' + resource + (query ? '?' + query : '')
    const existing = reads.get(url)
    if (existing && Date.now() < existing.until) return existing.value
    const value = receiver.client.request(url, undefined, 131072, false, AbortSignal.timeout(2000))
    reads.set(url, { until: Date.now()+5000, value })
    if (reads.size > 64) reads.delete(reads.keys().next().value)
    try { return await value } catch (error) { reads.delete(url); throw Error(error.code || 'backend_unavailable') }
  }
  const sessions = new WeakMap()
  const hash = value => createHash('sha256').update(value).digest()
  const passwordHash = hash(config.password)
  let nextLogin = 0
  let closing = false, closePromise
  const valid = client => (sessions.get(client) || 0) > Date.now()
  // The console is reachable only through loopback (or an explicit SSH tunnel).
  // Reject foreign browser origins before the upstream WebSocket message dispatcher runs.
  class LocalConsole extends NodeConsole {
    broadcasts = new Set()
    broadcast(...args) {
      const task = super.broadcast(...args)
      this.broadcasts.add(task)
      task.then(() => this.broadcasts.delete(task), () => this.broadcasts.delete(task))
      return task
    }
    async drain() { while (this.broadcasts.size) await Promise.all([...this.broadcasts]) }
    accept(socket, request) {
      const host = request.headers.host
      const origin = request.headers.origin
      if (closing || ![`127.0.0.1:${config.port}`, `localhost:${config.port}`].includes(host)
          || origin && ![`http://127.0.0.1:${config.port}`, `http://localhost:${config.port}`].includes(origin)
          || Object.keys(this.clients).length >= 16) { socket.close(1008); return }
      super.accept(socket, request)
      const client = Object.values(this.clients).find(value => value.socket === socket)
      socket.removeEventListener('message', client.receive)
      // Upstream dispatch assumes valid JSON/args and logs unknown method arguments.
      // Keep this deployment's wire surface small and never log login payloads.
      socket.addEventListener('message', event => {
        let value
        try { value = JSON.parse(event.data.toString()) } catch { socket.close(1008); return }
        if (!value || !['ping', 'taki/login', 'taki/logout', 'taki/status', 'taki/settings', ...methods.map(m => 'taki/'+m)].includes(value.type)
            || !Array.isArray(value.args) || value.args.length > 2
            || typeof value.id !== 'string' || value.id.length > 128) { socket.close(1008); return }
        void client.receive(event).catch(() => socket.close(1011))
      })
    }
  }
  ctx.on('console/intercept', (client, listener) => !!listener.authority && !valid(client))
  const server = ctx.plugin(Server, { host: '127.0.0.1', port: config.port, maxPort: config.port })
  const consoleScope = ctx.plugin(LocalConsole, { uiPath: '', apiPath: '/status', open: false, devMode: false })
  const registration = ctx.inject(['console', 'server'], scoped => {
    const listen = (name, callback, options) => scoped.console.addListener(name, async function(...args) {
      try { return await callback.apply(this, args) }
      catch (error) {
        const safe = Error(typeof error === 'string' ? error : error.message || 'management_failed')
        safe.stack = safe.message
        throw safe
      }
    }, options)
    scoped.server._ws.options.maxPayload = 32768
    scoped.console.addEntry(assetsDirectory)
    listen('taki/login', function(password) {
      if (Date.now() < nextLogin) throw Error('请稍后再登录')
      nextLogin = Date.now() + 1000
      if (typeof password !== 'string' || password.length > 256 || !timingSafeEqual(hash(password), passwordHash)) throw Error('登录失败')
      sessions.set(this, Date.now() + 30 * 60 * 1000)
      management.record('管理员已登录')
      return true
    })
    listen('taki/logout', function() { sessions.delete(this); return true })
    listen('taki/status', function() {
      const receiver = ctx.takiTransport?.receiver
      const health = receiver?.health()
      const bot = ctx.bots.find(b => b.platform === 'qq' && b.config.id === receiver?.config.appId)
      return { ...management.snapshot(), release: manifest.commit, uptime: Math.floor(process.uptime()),
        handshake: !!health?.handshake, receiverActive: !!health?.active,
        backendNotices: !!health?.backend?.notices_enabled, botStatus: bot?.status ?? null }
    }, { authority: 5 })
    listen('taki/settings', function(value, revision) {
      throw Error('upgrade_required')
    }, { authority: 5 })
    listen('taki/settings/update', function(value, guard) {
      try { return management.updateGuarded(value, guard) }
      catch (error) {
        if (error.message === 'settings_conflict') throw error
        throw Error('保存失败：请检查设置格式和状态目录权限')
      }
    }, { authority: 5 })
    listen('taki/overview', async function() {
      const health = await readHealth()
      let backend
      try { backend = await readBackend('overview') }
      catch { backend = { sections: { backend: { availability: 'unavailable', data: null, reason_code: 'backend_unavailable', stale: true } } } }
      return { ...management.snapshot(), health, backend,
        gateway_observation: ctx.takiTransport?.receiver?.client.telemetry?.snapshot() || null }
    }, { authority: 5 })
    for (const [method, resource] of [['requests/list','requests'], ['sources','sources'], ['notices/list','notices'],
      ['query-metrics','query-metrics'], ['groups/list','groups']]) {
      listen('taki/'+method, function(params = {}) {
        if (!params || typeof params !== 'object' || Array.isArray(params) || Object.keys(params).length > 8
            || Object.values(params).some(v => typeof v !== 'string' || v.length > 2048)) throw Error('admin_parameters')
        return readBackend(resource, params)
      }, { authority: 5 })
    }
    listen('taki/requests/detail', function(id) {
      if (typeof id !== 'string' || !/^[a-f0-9]{64}$/.test(id)) throw Error('admin_request_id')
      return readBackend('requests/'+id)
    }, { authority: 5 })
  })
  return { management, async ready() {
    if (!ctx.console?.listeners['taki/status']) throw Error('console registration failed')
    if (!ctx.server._http.listening) await require('node:events').once(ctx.server._http, 'listening', { signal: AbortSignal.timeout(5000) })
  }, close() { return closePromise ||= (async () => {
    closing = true
    const console = ctx.console
    // Entry disposal broadcasts asynchronously. Finish connected clients and pending
    // broadcasts before removing the console service that EntryProvider still needs.
    await Promise.all(Object.values(console?.clients || {}).map(client => new Promise(resolve => {
      if (client.socket.readyState === 3) return resolve()
      const timer = setTimeout(() => client.socket.terminate(), 2000)
      client.socket.once('close', () => { clearTimeout(timer); resolve() })
      client.socket.close(1001)
    })))
    await console?.drain()
    await registration.dispose()
    await console?.drain()
    await consoleScope.dispose()
    await server.dispose()
  })() } }
}
module.exports = { configuration, install }
