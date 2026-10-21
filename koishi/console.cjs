// Official Koishi web UI with a bounded administrator surface; no general config/marketplace loader.
const { createHash, timingSafeEqual, randomUUID } = require('node:crypto')
const path = require('node:path')
const NodeConsole = require('@koishijs/plugin-console').default
const Server = require('@koishijs/plugin-server').default
const { Management, validateSettings } = require('./lib/management')
const { validateGroupPolicy } = require('./lib/group-policies')
const { OperationJournal } = require('./operation-journal.cjs')
const { readHostSnapshot } = require('./ops-host.cjs')

function configuration(env) {
  if (env.TAKI_CONSOLE_ENABLED !== '1') return
  const port = Number(env.TAKI_CONSOLE_PORT || 5140)
  const password = env.TAKI_CONSOLE_PASSWORD || ''
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw Error('console port')
  if (password.length < 20 || password.length > 256 || password === env.OURNOTES_BACKEND_TOKEN || password === env.QQ_APP_SECRET) throw Error('console requires a separate password of 20-256 characters')
  if (!env.TAKI_CONSOLE_STATE_DIR || !path.isAbsolute(env.TAKI_CONSOLE_STATE_DIR)) throw Error('console state directory required')
  const state=env.TAKI_POLICY_RECOVERY_STATE||'unknown',reason=env.TAKI_POLICY_RECOVERY_REASON||null
  if(!['unknown','current','restored','frozen','conflict'].includes(state)||reason&&!['settings_changed','policy_changed'].includes(reason))throw Error('policy recovery state')
  return { port, password, directory: env.TAKI_CONSOLE_STATE_DIR, recoveryPolicy:{state,reason} }
}

function install(ctx, config, manifest, assetsDirectory = path.join(__dirname, 'node_modules/taki-console-assets')) {
  const management = new Management(config.directory, config.identity)
  const readHealth = config.readHealth || require('./health.cjs').healthReader(ctx, manifest, config.identity || {})
  const methods = ['overview', 'requests/list', 'requests/detail', 'sources', 'notices/list', 'query-metrics', 'groups/list', 'groups/update', 'settings/update',
    'ops/jobs/list','ops/jobs/detail','ops/jobs/start','ops/export','ops/audit','ops/host','ops/notice-target']
  const journal = new OperationJournal(config.directory, management.groups.snapshot().instance ? JSON.stringify(management.groups.snapshot().instance) : manifest.commit)
  journal.reconcile(management)
  const reads = new Map()
  const groupPages = new Map()
  let lastObservedGroups, lastNoticeGroups
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
  // Failed guesses must not reserve the login budget of other connections.
  const nextLogin = new WeakMap()
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
      // Observe ws protocol errors (including maxPayload) without logging frame contents.
      socket.on('error', () => { if(socket.readyState===1)socket.close(1008) })
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
      if (Date.now() < (nextLogin.get(this) || 0)) throw Error('请稍后再登录')
      nextLogin.set(this, Date.now() + 1000)
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
      try { value=validateSettings(value);return journal.apply('settings/update',management.snapshot().settings,value,()=>management.updateGuarded(value, guard)) }
      catch (error) {
        if (error.message === 'settings_conflict') throw error
        throw Error(error.message==='invalid management settings'?'invalid_settings':'settings_save_unconfirmed')
      }
    }, { authority: 5 })
    listen('taki/overview', async function() {
      const [health,backend] = await Promise.all([readHealth(),readBackend('overview').catch(() =>
        ({ sections: { backend: { availability: 'unavailable', data: null, reason_code: 'backend_unavailable', stale: true } } }))])
      return { ...management.snapshot(), health, backend,
        recovery_policy:config.recoveryPolicy||null,
        policy_health:{availability:management.groups.error?'unavailable':'available',reason_code:management.groups.error},
        gateway_observation: ctx.takiTransport?.receiver?.client.telemetry?.snapshot() || null }
    }, { authority: 5 })
    listen('taki/ops/jobs/list', function() { return readBackend('ops/jobs') }, {authority:5})
    listen('taki/ops/jobs/detail', function(id) {
      if(typeof id!=='string'||!/^[a-f0-9]{32}$/.test(id))throw Error('ops_identity')
      return readBackend('ops/jobs/'+id)
    }, {authority:5})
    listen('taki/ops/jobs/start', async function(value) {
      if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join()!=='action,operation_id,params')throw Error('ops_shape')
      const client=ctx.takiTransport?.receiver?.client
      if(!client)throw Error('backend_unavailable')
      const result=await client.request('/v1/admin/ops/jobs',value,131072,false,AbortSignal.timeout(2000))
      reads.delete('/v1/admin/ops/jobs');return result
    }, {authority:5})
    listen('taki/ops/audit', function() { return journal.snapshot() }, {authority:5})
    listen('taki/ops/notice-target', function(id) {
      if(typeof id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(id))throw Error('admin_group')
      return readBackend('ops/notice-targets/'+id)
    }, {authority:5})
    listen('taki/ops/host', async function() {
      const snapshot=readHostSnapshot(config.hostSnapshotPath)
      const health=await readHealth()
      const observed=snapshot.data?.processes,controller=snapshot.data?.controller
      return {...snapshot,data:{...snapshot.data,runtime:{gateway_sha:manifest.commit,gateway_pid:process.pid,
        backend_pid:health.backend_pid??null,backend_code_digest:health.backend_code_digest??null,observed_at:health.observed_at,stale:health.stale,
        backend_digest_matches:!health.stale&&health.backend_code_digest&&manifest.backend_code_digest?health.backend_code_digest===manifest.backend_code_digest:null,
        controller_matches_gateway:!snapshot.stale&&observed?.gateway?.pid===process.pid&&controller?.current_sha?controller.current_sha===manifest.commit:null,
        expected_backend_digest:manifest.backend_code_digest??null}}}
    }, {authority:5})
    listen('taki/ops/export', async function(id) {
      if(id!==undefined&&(typeof id!=='string'||!/^[a-f0-9]{64}$/.test(id)))throw Error('admin_request_id')
      const result=await readBackend('ops/export'+(id?'/'+id:''))
      const settings=management.snapshot(),policy=management.groups.snapshot(),audit=journal.snapshot()
      return {...result,gateway:{release_sha:manifest.commit,protocol:{major:1,minor:1},health:await readHealth(),
        controls:{queries_enabled:settings.settings.queriesEnabled,notifications_enabled:settings.settings.notificationsEnabled,
          configured_group_count:settings.settings.groupAllowlist.length,policy_revision:policy.revision,policy_health:policy.reason_code,
          recovery_policy:config.recoveryPolicy||null}},host:readHostSnapshot(config.hostSnapshotPath),
        management_audit:{available:audit.available,items:audit.items.slice(0,20).map(({id,at,action,state,code})=>({id,at,action,state,code}))}}
    }, {authority:5})
    for (const [method, resource] of [['requests/list','requests'], ['sources','sources'], ['notices/list','notices'],
      ['query-metrics','query-metrics']]) {
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
    listen('taki/groups/list', async function(params = {}) {
      if(!params||typeof params!=='object'||Array.isArray(params)||Object.keys(params).some(k=>!['limit','cursor'].includes(k))
        || Object.values(params).some(v=>typeof v!=='string'||v.length>256))throw Error('admin_parameters')
      const limit=Number(params.limit||25)
      if(!Number.isInteger(limit)||limit<1||limit>100)throw Error('admin_limit')
      const candidates = new Map()
      let observation = null, notice = null, stale = false
      await Promise.all([
        readBackend('groups').then(value=>{observation=value;lastObservedGroups=value}).catch(()=>{observation=lastObservedGroups;stale=true}),
        readBackend('notices').then(value=>{notice=value;lastNoticeGroups=value}).catch(()=>{notice=lastNoticeGroups;stale=true})])
      const policy=management.groups.snapshot(),guard=management.snapshot().guard
      for (const row of observation?.sections?.groups?.data || []) candidates.set(row.group_id, {...row, origins:['observed']})
      for (const row of notice?.sections?.notices?.data?.groups || []) candidates.set(row.group_id,
        {...candidates.get(row.group_id),...row,origins:[...(candidates.get(row.group_id)?.origins||[]),'notice']})
      for (const id of [...management.snapshot().settings.groupAllowlist,...Object.keys(policy.groups)])
        candidates.set(id,{...candidates.get(id),group_id:id,origins:[...(candidates.get(id)?.origins||[]),'configured']})
      const sorted=[...candidates.keys()].sort((a,b)=>Number(Object.hasOwn(policy.groups,b))-Number(Object.hasOwn(policy.groups,a))||a.localeCompare(b))
      for(const [key,entry] of groupPages)if(entry.until<Date.now())groupPages.delete(key)
      let ids=sorted,offset=0
      if(params.cursor){
        const saved=groupPages.get(params.cursor)
        if(!saved||saved.owner!==this.id||saved.revision!==policy.revision||saved.guard!==JSON.stringify(guard))throw Error('group_cursor_invalid')
        ids=saved.ids;offset=saved.offset
      }
      let next_cursor=null
      if(offset+limit<ids.length){
        next_cursor=randomUUID();groupPages.set(next_cursor,{ids,offset:offset+limit,owner:this.id,revision:policy.revision,guard:JSON.stringify(guard),until:Date.now()+300000})
        while(groupPages.size>16)groupPages.delete(groupPages.keys().next().value)
      }
      return {policy,generated_at:Date.now()/1000,sections:{groups:{availability:policy.reason_code?'unavailable':'partial',reason_code:policy.reason_code||'observed_groups_not_platform_roster',
        observed_at:observation?.generated_at||null,stale:stale||!observation,next_cursor,total:ids.length,
        data:ids.slice(offset,offset+limit).map(id=>({...candidates.get(id),group_id:id,
          policy:management.groups.get(id),effective:management.effective(id)}))}}}
    }, { authority:5 })
    listen('taki/groups/update', function(value) {
      if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join()!=='group_id,policy,revision')throw Error('group_update_shape')
      if(typeof value.group_id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(value.group_id))throw Error('invalid_group_id')
      validateGroupPolicy(value.policy)
      const result=journal.apply('groups/update',management.groups.get(value.group_id),value.policy,
        ()=>management.groups.update(value.group_id,value.policy,value.revision),{group_id:value.group_id})
      management.record('管理员保存了分群策略')
      return result
    }, { authority:5 })
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
