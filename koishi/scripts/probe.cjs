#!/usr/bin/env node
// M1 deliverable: deterministic offline probe of the installed adapter and Taki transport.
// No credentials, application startup, real socket or QQ connection. JSON is evidence, not live acceptance.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const net = require('node:net')
const deny = () => { throw new Error('probe forbids real network access') }
net.Socket.prototype.connect = deny
require('node:http').request = deny
require('node:https').request = deny
globalThis.fetch = deny
const { Context, h } = require('koishi')
const { QQBot, adaptSession, WsClient, QQ } = require('@satorijs/adapter-qq')
const { QQDelivery, candidateLimits, decodeIncoming, decodeGroupEvent, TakiTransport, BackendClient, Receiver, normalizeGateway } = require('../lib')
const identity = { instanceId: 'fixture-instance', appId: 'fixture-app', selfId: 'fixture-self' }
const now = Date.parse('2026-10-19T12:00:00+08:00')
const picture = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const png = { kind: 'image', bytes: picture, caption: '图片 1/2；/继续 发送其余图片。' }
const text = { kind: 'text', text: '固定备用文字 <image src="literal"> & 正文' }
const recognizes = body => /^(?:[／/]?查曲|[／/]?继续)(?:\s|$)/u.test(body)
const results = []
function event({ kind = 'group', legacy = false, type, content = '/查曲 固定夹具', ...extra } = {}) {
  return { op: 0, s: 1, id: 'fixture-event', t: type || (kind === 'c2c' ? 'C2C_MESSAGE_CREATE' : 'GROUP_AT_MESSAGE_CREATE'), d: {
    id: 'fixture-message', timestamp: new Date(now).toISOString(), content,
    author: legacy ? { [kind === 'group' ? 'member_openid' : 'user_openid']: 'fixture-user' } : { id: 'fixture-user' },
    ...(kind === 'group' ? { [legacy ? 'group_openid' : 'group_id']: 'fixture-group' } : {}), ...extra,
  } }
}
function createBot() {
  const ctx = new Context()
  const calls = []
  let handler = (url) => url.endsWith('/files') ? { file_info: 'fixture-file' } : { id: 'receipt-' + calls.length, timestamp: new Date(now).toISOString() }
  const http = Object.assign(async (url, options) => {
    calls.push({ url, method: options.method, data: structuredClone(options.data) })
    return { data: await handler(url, options, calls), headers: new Headers() }
  }, { extend() { return this }, isError(error) { return Boolean(error?.response) } })
  ctx.provide('http'); ctx.http = http
  const bot = new QQBot(ctx, { id: identity.appId, secret: 'fixture-not-a-secret', type: 'public', sandbox: false,
    intents: 0, protocol: 'websocket', endpoint: 'https://api.bot.qq.com', retryWhen: [], uploadThreshold: 4_000_000 })
  bot.user = { id: identity.selfId }
  return { ctx, bot, calls, setHandler(fn) { handler = fn } }
}
async function run(name, task) {
  try { await task(); results.push({ name, status: 'passed' }) }
  catch (error) { results.push({ name, status: 'failed', error: String(error.stack || error) }) }
}
async function setup(kind = 'group', options = {}) {
  const env = createBot()
  const decoded = await decodeIncoming(env.bot, event({ kind }), identity, recognizes)
  assert.equal(decoded.status, 'accepted')
  const records = [], trace = []
  let failAck = false, active = true
  const events = {
    async intent(attempt) { trace.push('intent'); records.push({ type: 'intent', ...attempt }); return 'granted' },
    async acknowledge(attempt, outcome) {
      trace.push('ack'); records.push({ type: 'ack', ...attempt, outcome })
      if (failAck) throw Error('simulated ACK response lost')
    },
  }
  const originalHandler = (url) => url.endsWith('/files') ? { file_info: 'fixture-file' } : { id: 'receipt-' + env.calls.length }
  env.setHandler((url, ...args) => { trace.push(url.endsWith('/files') ? 'upload' : 'send'); return originalHandler(url, ...args) })
  const delivery = new QQDelivery(env.bot.internal, decoded.inbound, 'fixture-delivery', 'fixture-epoch', events,
    () => active, { ...candidateLimits, ...options }, () => now + 1)
  return { ...env, decoded, delivery, events, records, trace, failAck(value) { failAck = value }, deactivate() { active = false } }
}
async function main() {
  for (const kind of ['group', 'c2c']) for (const legacy of [false, true]) {
    await run(`${kind}/${legacy ? 'legacy' : 'modern'} raw event -> installed adapter Session`, async () => {
      const env = createBot(), raw = event({ kind, legacy }), before = structuredClone(raw)
      const decoded = await decodeIncoming(env.bot, raw, identity, recognizes)
      assert.equal(decoded.status, 'accepted'); assert.deepEqual(raw, before)
      assert.equal(decoded.session.userId, 'fixture-user')
      assert.equal(decoded.session.channelId, kind === 'group' ? 'fixture-group' : 'fixture-user')
      assert.equal(decoded.session.timestamp, now); assert.equal(decoded.session.messageId, 'fixture-message')
      const again = await decodeIncoming(env.bot, raw, identity, recognizes)
      assert.equal(again.inbound.requestId, decoded.inbound.requestId)
      const changed = await decodeIncoming(env.bot, { ...raw, d: { ...raw.d, content: '/查曲 changed' } }, identity, recognizes)
      assert.equal(changed.inbound.requestId, decoded.inbound.requestId) // backend must reject the changed digest
      assert.equal(env.calls.length, 0)
    })
  }
  await run('unshimmed installed adapter exposes legacy identity gap', async () => {
    const env = createBot(), session = await adaptSession(env.bot, event({ legacy: true }))
    assert.equal(session.channelId, undefined); assert.equal(session.userId, undefined)
  })
  await run('GROUP_MESSAGE_CREATE self mention / foreign mention / ordinary / bot filters', async () => {
    const env = createBot()
    const sample = data => event({ type: 'GROUP_MESSAGE_CREATE', ...data })
    assert.equal((await decodeIncoming(env.bot, sample({}), identity, recognizes)).status, 'ignored')
    assert.equal((await decodeIncoming(env.bot, sample({ content: '<@someone_else> /查曲 a', mentions: [{ is_you: false }] }), identity, recognizes)).status, 'ignored')
    assert.equal((await decodeIncoming(env.bot, sample({ content: '<@fixtureapp> 普通聊天' }), identity, recognizes)).status, 'ignored')
    const accepted = await decodeIncoming(env.bot, sample({ content: '／查曲 a\n/继续', mentions: [{ is_you: true }] }), identity, recognizes)
    assert.equal(accepted.status, 'accepted'); assert.equal(accepted.inbound.body, '／查曲 a\n/继续')
    assert.equal((await decodeIncoming(env.bot, sample({ author: { id: 'fixture-user', bot: true }, mentions: [{ is_you: true }] }), identity, recognizes)).status, 'ignored')
  })
  await run('missing/conflicting IDs, invalid times and oversized input rejected', async () => {
    const env = createBot()
    for (const data of [{ group_openid: 'conflict' }, { author: {} }, { author: { id: 'a', member_openid: 'b' } },
      { timestamp: '2026-10-19 12:00:00' }, { timestamp: 123 }, { content: 'x'.repeat(16_385) }]) {
      assert.equal((await decodeIncoming(env.bot, event(data), identity, recognizes)).status, 'rejected')
    }
  })
  await run('Koishi scope filtering and plugin disposal', async () => {
    const env = createBot(), scope = env.ctx.guild('different-group'), transport = new TakiTransport(scope)
    const result = await transport.decode(env.bot, event(), identity, recognizes)
    assert.equal(result.status, 'ignored')
    const ownScope = env.ctx.guild('fixture-group'), own = new TakiTransport(ownScope)
    assert.equal((await own.decode(env.bot, event(), identity, recognizes)).status, 'accepted')
    // Use a child plugin scope so disposing it exercises Cordis' actual lifecycle.
    let child
    const fork = env.ctx.plugin(ctx => { child = new TakiTransport(ctx) })
    const e = await setup()
    child.own(e.delivery)
    await fork.dispose()
    assert.equal((await child.decode(env.bot, event(), identity, recognizes)).status, 'ignored')
    assert.equal((await e.delivery.deliver('one', 0, text)).reason, 'inactive_or_expired')
    assert.equal(e.calls.length, 0)
  })
  await run('actual WsClient dispatch exposes permission/rejection internal events', async () => {
    const env = createBot(), observed = [], listeners = {}
    env.bot.dispatch = session => observed.push(session)
    const ws = Object.create(WsClient.prototype)
    ws.bot = env.bot; ws.socket = { addEventListener(name, callback) { listeners[name] = callback } }
    await ws.accept()
    for (const type of ['GROUP_MSG_RECEIVE', 'GROUP_MSG_REJECT']) {
      const raw = { op: 0, s: 1, t: type, d: { group_openid: 'fixture-group', timestamp: now / 1000 } }
      await listeners.message({ data: JSON.stringify(raw) })
      const normalized = decodeGroupEvent(raw, identity)
      assert.equal(normalized.kind, type.endsWith('RECEIVE') ? 'allow' : 'reject')
      assert.equal(normalized.timestampSeconds, now / 1000)
    }
    assert.deepEqual(observed.map(s => s.event._type), ['qq/group-msg-receive', 'qq/group-msg-reject'])
    assert.equal(decodeGroupEvent({ t: 'GROUP_MSG_RECEIVE', d: { group_openid: 'g', timestamp: now } }, identity), undefined)
    assert.equal(decodeGroupEvent({ t: 'constructor', d: { group_openid: 'g', timestamp: now / 1000 } }, identity), undefined)
  })
  await run('actual gateway discovery preserves legacy host; explicit gateway override works', async () => {
    const env = createBot(), urls = []
    env.bot.getAccessToken = async () => 'fixture-token'
    env.setHandler(() => ({ url: 'wss://api.sgroup.qq.com/websocket?fixture=1' }))
    env.bot.http.ws = url => { urls.push(url); return {} }
    const ws = Object.create(WsClient.prototype); ws.bot = env.bot
    await ws.prepare()
    assert.equal(urls[0], 'wss://api.sgroup.qq.com/websocket?fixture=1')
    env.bot.config.gatewayUrl = 'wss://api.bot.qq.com/websocket?fixture=1'
    await ws.prepare()
    assert.equal(urls[1], env.bot.config.gatewayUrl)
    assert.equal(env.calls.length, 1)
  })
  await run('actual HELLO subscribes USER_MESSAGE; raw group and C2C dispatch identity', async () => {
    const env = createBot(), listeners = {}, sent = [], observed = []
    env.bot.config.intents = QQ.Intents.USER_MESSAGE
    env.bot.getAccessToken = async () => 'fixture-token'
    env.bot.dispatch = session => observed.push(session)
    const ws = Object.create(WsClient.prototype); ws.bot = env.bot
    ws.socket = { addEventListener(name, callback) { listeners[name] = callback }, send(data) { sent.push(JSON.parse(data)) } }
    await ws.accept()
    try {
      await listeners.message({ data: JSON.stringify({ op: 10, d: { heartbeat_interval: 60_000 } }) })
      assert.equal(sent[0].op, 2); assert.equal(sent[0].d.intents, 1 << 25)
      for (const kind of ['group', 'c2c']) await listeners.message({ data: JSON.stringify(event({ kind })) })
      assert.deepEqual(observed.filter(s => s.type === 'message').map(s => [s.channelId, s.userId, s.timestamp]),
        [['fixture-group', 'fixture-user', now], ['fixture-user', 'fixture-user', now]])
    } finally { clearInterval(ws._ping) }
  })
  await run('high-level Session.send loses partial-success return on later error', async () => {
    const env = createBot()
    // Cordis initializes the Koishi service asynchronously; no ctx.start/login.
    await new Promise(resolve => setImmediate(resolve))
    assert.ok(env.ctx.koishi)
    const decoded = await decodeIncoming(env.bot, event(), identity, recognizes)
    let sends = 0
    env.setHandler(url => {
      if (url.endsWith('/files')) return { file_info: 'fixture-file' }
      if (++sends === 1) return { id: 'accepted-first', timestamp: new Date(now).toISOString() }
      throw Object.assign(Error('fixture explicit rejection'), { response: { data: { code: 12345 }, headers: new Headers() } })
    })
    // Cordis traces event arguments before calling plugin listeners.
    const session = env.ctx.reflect.trace(decoded.session)
    const ids = await session.send([h.text('caption'), h.image(picture, 'image/png')])
    assert.deepEqual(ids, []); assert.equal(sends, 2)
  })
  for (const kind of ['group', 'c2c']) await run(`${kind} upload + two actual sends share original message and increasing sequence`, async () => {
    const e = await setup(kind)
    assert.equal((await e.delivery.deliver('image', 0, png)).status, 'success')
    assert.equal((await e.delivery.deliver('text', 1, text)).status, 'success')
    assert.deepEqual(e.trace, ['upload', 'intent', 'send', 'ack', 'intent', 'send', 'ack'])
    assert.equal(e.calls[0].data.srv_send_msg, false)
    assert.equal(e.calls[0].data.file_data, picture.toString('base64'))
    const sends = e.calls.filter(c => c.url.endsWith('/messages'))
    assert.deepEqual(sends.map(c => c.data.msg_seq), [1, 2])
    assert.ok(sends.every(c => c.data.msg_id === 'fixture-message'))
    assert.equal(sends[1].data.content, text.text) // literal, no message markup evaluation
    assert.ok(sends.every(c => c.url.includes(kind === 'group' ? '/groups/fixture-group/' : '/users/fixture-user/')))
  })
  await run('upload failure permits explicit captured text fallback without consuming a slot', async () => {
    const e = await setup()
    e.setHandler(url => { if (url.endsWith('/files')) throw Error('upload timeout'); return { id: 'fallback' } })
    assert.equal((await e.delivery.deliver('image', 0, png)).status, 'upload_failed')
    assert.equal(e.delivery.usedSlots, 0); assert.equal(e.records.length, 0)
    assert.equal((await e.delivery.deliver('fallback', 0, text)).status, 'success')
    assert.equal(e.calls.at(-1).data.content, text.text); assert.equal(e.calls.at(-1).data.msg_seq, 1)
  })
  await run('missing file_info is upload failure; no media send', async () => {
    const e = await setup(); e.setHandler(() => ({}))
    assert.equal((await e.delivery.deliver('image', 0, png)).reason, 'missing_file_info')
    assert.equal(e.calls.length, 1); assert.equal(e.records.length, 0)
  })
  for (const mode of ['rejected', 'timeout', 'empty', 'audit']) await run(`partial success + ${mode} stops without resending first part`, async () => {
    const e = await setup(); let sends = 0
    e.setHandler(() => {
      if (++sends === 1) return { id: 'first' }
      if (mode === 'timeout') throw Error('simulated timeout after send')
      return mode === 'rejected' ? { code: 12345 } : mode === 'audit' ? { id: 'pending', audit_id: 'fixture-audit' } : {}
    })
    assert.equal((await e.delivery.deliver('one', 0, text)).status, 'success')
    const second = await e.delivery.deliver('two', 1, text)
    assert.equal(second.status, mode === 'rejected' ? 'failure' : 'uncertain')
    assert.equal((await e.delivery.deliver('three', 2, text)).status, 'aborted')
    assert.equal((await e.delivery.deliver('one', 0, text)).messageId, 'first')
    assert.equal(sends, 2); assert.equal(e.records.filter(r => r.type === 'ack').length, 2)
  })
  await run('ACK response loss blocks next part; only same ACK is retried', async () => {
    const e = await setup(); e.failAck(true)
    assert.equal((await e.delivery.deliver('one', 0, text)).status, 'success')
    assert.equal(e.delivery.acknowledgementPending, true)
    assert.equal((await e.delivery.deliver('two', 1, text)).status, 'aborted')
    assert.equal(e.calls.length, 1)
    e.failAck(false); assert.equal(await e.delivery.retryAcknowledgement(), true)
    assert.equal((await e.delivery.deliver('two', 1, text)).status, 'success')
    assert.equal(e.calls.length, 2)
    const acks = e.records.filter(r => r.type === 'ack')
    assert.equal(acks[0].attemptId, acks[1].attemptId)
  })
  await run('concurrent duplicate part has one sender; conflicting repeat is rejected', async () => {
    const e = await setup()
    const [a, b] = await Promise.all([e.delivery.deliver('one', 0, text), e.delivery.deliver('one', 0, text)])
    assert.deepEqual(a, b); assert.equal(e.calls.length, 1)
    await assert.rejects(e.delivery.deliver('one', 0, { kind: 'text', text: 'different' }), /conflicting/)
  })
  await run('lost/existing intent never authorizes another send', async () => {
    for (const response of ['existing', 'denied', 'lost']) {
      const e = await setup()
      e.events.intent = async () => { if (response === 'lost') throw Error('lost'); return response }
      assert.equal((await e.delivery.deliver('one', 0, text)).status, 'aborted')
      assert.equal(e.calls.length, 0)
    }
  })
  await run('dispose during upload prevents send; dispose during send still records fact', async () => {
    const e = await setup()
    e.setHandler(() => { e.delivery.dispose(); return { file_info: 'fixture-file' } })
    assert.equal((await e.delivery.deliver('one', 0, png)).status, 'aborted')
    assert.equal(e.calls.length, 1); assert.equal(e.records.length, 0)
    const f = await setup()
    f.setHandler(() => { f.delivery.dispose(); return { id: 'already-sent' } })
    assert.equal((await f.delivery.deliver('one', 0, text)).status, 'success')
    assert.equal(f.records.at(-1).outcome.messageId, 'already-sent')
    assert.equal((await f.delivery.deliver('two', 1, text)).status, 'aborted')
    assert.equal(f.calls.length, 1)
  })
  await run('send budget, expiry, future time, bytes and part order bounded', async () => {
    const e = await setup('group', { maxSends: 1 })
    assert.equal((await e.delivery.deliver('bad-order', 1, text)).reason, 'out_of_order')
    assert.equal((await e.delivery.deliver('one', 0, text)).status, 'success')
    assert.equal((await e.delivery.deliver('two', 1, text)).reason, 'send_budget')
    for (const age of [candidateLimits.replyWindowMs, -100]) {
      const f = await setup()
      const delivery = new QQDelivery(f.bot.internal, { ...f.decoded.inbound, timestampMs: now - age }, 'd', 'e', f.events, () => true, candidateLimits, () => now)
      assert.equal((await delivery.deliver('one', 0, text)).reason, 'inactive_or_expired')
      assert.equal(f.calls.length, 0)
    }
    const f = await setup()
    assert.equal((await f.delivery.deliver('huge', 0, { ...png, bytes: Buffer.alloc(3_000_001) })).reason, 'part_size')
    assert.equal(f.calls.length, 0)
  })
  await run('M3 loopback client rejects public endpoints and redirect/resource escape', async () => {
    assert.throws(() => new BackendClient('http://example.com','x'.repeat(32),'i','e'))
    assert.throws(() => new BackendClient('http://127.0.0.1/path','x'.repeat(32),'i','e'))
    const client = new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'i','e',async () => { throw Error('must not fetch') })
    await assert.rejects(client.request('/v1/../secret'))
    await assert.rejects(client.image({delivery_id:'d'}, {id:'0',image:'http://example.com/image'}))
  })
  await run('M3 real client bounds streamed payload and refuses incompatible readiness', async () => {
    const client = new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'i','e',async () => new Response('x'.repeat(1024)))
    await assert.rejects(client.request('/v1/health',undefined,100),/response_size/)
    const incompatible = new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'i','e',async () => Response.json({ready:true,protocol:{major:2,minor:0}}))
    await assert.rejects(incompatible.initialize(),/incompatible_backend/)
  })
  await run('M3 ACK retry is bounded; intent loss has one HTTP attempt', async () => {
    let calls=0
    const client = new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'i','e',async () => {calls++;throw Error('lost')})
    const attempt={requestId:'r',deliveryId:'d',partId:'0',attemptId:'a',gatewayEpoch:'e'}
    await assert.rejects(client.intent(attempt));assert.equal(calls,1)
    calls=0;await assert.rejects(client.acknowledge(attempt,{status:'success',messageId:'m'}));assert.equal(calls,3)
  })
  await run('M3 ACK retry rechecks disposal context guard', async () => {
    const guards=[]
    const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'i','e',async (url,opts)=>{
      guards.push(JSON.parse(opts.body).allow_context)
      if(guards.length===1){client.deactivate();throw Error('lost before commit')}
      return new Response('{}')
    })
    await client.acknowledge({requestId:'r',deliveryId:'d',partId:'0',attemptId:'a',gatewayEpoch:'e'},{status:'success',messageId:'m'})
    assert.deepEqual(guards,[true,false])
  })
  await run('M3 gateway normalization retains actual path and query', async () => {
    assert.equal(normalizeGateway('wss://api.sgroup.qq.com/socket/path?a=1&b=two'),'wss://api.bot.qq.com/socket/path?a=1&b=two')
    assert.equal(normalizeGateway('wss://other.example/socket?a=1'),'wss://other.example/socket?a=1')
    assert.throws(()=>normalizeGateway('ws://api.sgroup.qq.com/'))
  })
  for (const denied of ['channel-ignore','user-ignore','authority']) {
    await run('M3 actual Koishi observation and permission gate: '+denied, async () => {
      const env=createBot();let submissions=0,receiver
      env.ctx.provide('database')
      env.ctx.database={
        async getChannel(){return {id:'fixture-group',platform:'qq',flag:denied==='channel-ignore'?1:0,
          assignee:identity.selfId,guildId:'fixture-group',permissions:[],locales:[]}},
        async getUser(){return {id:1,flag:denied==='user-ignore'?1:0,authority:denied==='authority'?0:1,permissions:[],locales:[]}},
        async setChannel(){},async setUser(){},
      }
      const client={epoch:'fixture-e',deactivate(){},async initialize(){},async recognizes(){return true},async retire(){},
        async submit(){submissions++;throw Error('permission bypass')}}
      const plugin=ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:['authority:1'],maxPending:8},client)}
      plugin.inject={optional:['database']}
      const fork=env.ctx.plugin(plugin),raw=event()
      await env.ctx.parallel('internal/session',env.bot.session({type:'internal',_type:'qq/group-at-message-create',_data:raw.d}))
      assert.equal(await receiver.receive(env.ctx.reflect.trace(await adaptSession(env.bot,raw))),true)
      assert.equal(submissions,0);assert.equal(env.calls.length,0);await receiver.dispose();await fork.dispose()
    })
  }
  const root = path.resolve(__dirname, '..')
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json')))
  const versions = Object.fromEntries(['koishi', '@koishijs/core', '@koishijs/plugin-adapter-qq', '@satorijs/adapter-qq', '@satorijs/core', 'typescript'].map(name => {
    const version = JSON.parse(fs.readFileSync(path.join(root, 'node_modules', name, 'package.json'))).version
    assert.equal(version, lock.packages['node_modules/' + name].version, 'installed version differs: ' + name)
    return [name, version]
  }))
  const report = { evidence: 'installed-package + synthetic fixtures + intercepted HTTP; no live QQ', node: process.version,
    versions, lockSha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, 'package-lock.json'))).digest('hex'),
    passed: results.filter(r => r.status === 'passed').length, failed: results.filter(r => r.status === 'failed').length, results }
  if (process.argv[2]) fs.writeFileSync(path.resolve(process.argv[2]), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report, null, 2))
  process.exitCode = report.failed ? 1 : 0
}
main().catch(error => { console.error(error); process.exitCode = 1 })
