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
// Delivery fault tests inject a material port; the real three-step material API is
// exercised separately below. Reuse the upload fixture's fault switch, never its payload in production.
function fixtureMaterials(api) {
  return {async acquire(bytes) {
    const result=await api.sendFileGuild('fixture-group',{file_type:1,srv_send_msg:false,file_data:Buffer.from(bytes).toString('base64')})
    if(!result?.file_info) throw Error('missing material fixture')
    return {url:'qqbot-media://image/v1/fixture',width:1,height:1,release(){}}
  }}
}
async function setup(kind = 'group', options = {}, extra = {}) {
  const env = createBot()
  const decoded = await decodeIncoming(env.bot, event({ kind, ...extra }), identity, recognizes)
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
    () => active, { ...candidateLimits, ...options }, () => now + 1, fixtureMaterials(env.bot.internal))
  return { ...env, decoded, delivery, events, records, trace, failAck(value) { failAck = value }, deactivate() { active = false } }
}
async function main() {
  await run('Official materials: prepare PUT complete, content reuse, restart and ownership',async()=>{
    const {MaterialStore,imageShape}=require('../lib/materials')
    const directory=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-material-'))
    const calls=[];let id=0,clock=Date.now()
    const api=async(method,url,data)=>{
      calls.push({method,url,data})
      if(url.endsWith('/prepare'))return{upload_id:'upload_'+(++id),presigned_url:'https://fixture.cos.ap-guangzhou.myqcloud.com/image'}
      if(url.endsWith('/complete'))return{image_id:'owned_'+id,url:'qqbot-media://image/v1/owned_'+id}
      if(method==='DELETE')return{}
      throw Error('unexpected API')
    }
    const put=async(url,bytes,type)=>{calls.push({method:'PUT',url,type});return crypto.createHash('md5').update(bytes).digest('hex')}
    try {
      const store=new MaterialStore(directory,'fixture-app',api,put,()=>clock)
      const [a,b]=await Promise.all([store.acquire(picture),store.acquire(picture)])
      assert.deepEqual(calls.map(c=>c.method),['POST','PUT','POST'])
      assert.equal(a.width,1);assert.equal(a.height,1);assert.equal(a.url,b.url)
      assert.equal(calls[0].data.content_type,'image/png')
      a.release();a.release();b.release()
      const restored=new MaterialStore(directory,'fixture-app',api,put,()=>clock)
      const c=await restored.acquire(picture);c.release();assert.equal(calls.length,3)
      clock+=90*86400000
      const d=await restored.acquire(picture);d.release()
      assert.equal(calls[3].url,'/v2/media-images/owned_1/delete')
      assert.equal(calls[3].method,'DELETE')
      assert.throws(()=>imageShape(Buffer.from('not an image')))
      const ledger=fs.readFileSync(path.join(directory,fs.readdirSync(directory)[0]),'utf8')
      assert.ok(!ledger.includes('presigned_url'));assert.ok(!ledger.includes('https://'))
    }finally{assert.ok(path.resolve(directory).startsWith(path.resolve(require('node:os').tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})}
  })
  await run('Material reclamation only deletes recorded idle IDs and protects active leases',async()=>{
    const {MaterialStore}=require('../lib/materials')
    const directory=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-material-'))
    const app='fixture-capacity',clock=Date.now(),removed=[]
    const key=crypto.createHash('sha256').update(picture).digest('hex')
    const entries=Array.from({length:64},(_,i)=>({key:i===0?key:i.toString(16).padStart(64,'0'),created:clock-700000,
      used:clock-600000+i,imageId:'owned_'+i,url:'qqbot-media://image/v1/owned_'+i}))
    const file=path.join(directory,crypto.createHash('sha256').update(app).digest('hex')+'.json')
    fs.writeFileSync(file,JSON.stringify({schema:1,appId:app,entries}))
    const api=async(method,url)=>{
      if(method==='DELETE'){removed.push(url);return{}}
      return url.endsWith('prepare')?{upload_id:'new_upload',presigned_url:'fixture'}:{image_id:'new_image',url:'qqbot-media://image/v1/new_image'}
    }
    try {
      const store=new MaterialStore(directory,app,api,async(url,bytes)=>crypto.createHash('md5').update(bytes).digest('hex'),()=>clock)
      const active=await store.acquire(picture)
      const another=await store.acquire(Buffer.concat([picture,Buffer.from('second')]))
      assert.deepEqual(removed,['/v2/media-images/owned_1/delete'])
      assert.equal(JSON.parse(fs.readFileSync(file)).entries.length,64)
      active.release();another.release()
    }finally{assert.ok(path.resolve(directory).startsWith(path.resolve(require('node:os').tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})}
  })
  await run('Lost material completion persists reservation; restart never retries or sends',async()=>{
    const {MaterialStore}=require('../lib/materials')
    const directory=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-material-'))
    let completes=0,prepares=0
    const api=async(method,url)=>{
      if(url.endsWith('/prepare')){prepares++;return{upload_id:'upload_lost',presigned_url:'fixture'}}
      completes++;throw Error('response lost')
    }
    const put=async()=>crypto.createHash('md5').update(picture).digest('hex')
    try {
      await assert.rejects(new MaterialStore(directory,'app',api,put).acquire(picture))
      await assert.rejects(new MaterialStore(directory,'app',api,put).acquire(picture),/unresolved/)
      assert.equal(completes,1);assert.equal(prepares,1)
      const wrong=path.join(directory,crypto.createHash('sha256').update('app').digest('hex')+'.json')
      fs.writeFileSync(wrong,'{broken')
      await assert.rejects(new MaterialStore(directory,'app',api,put).acquire(picture))
      assert.equal(prepares,1)
    }finally{assert.ok(path.resolve(directory).startsWith(path.resolve(require('node:os').tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})}
  })
  await run('Material PUT validates host, ETag transport is unsigned and redirects fail closed',async()=>{
    const {putMaterial}=require('../lib/materials')
    const prior=globalThis.fetch;let calls=0
    globalThis.fetch=async(url,options)=>{
      calls++;assert.deepEqual(options.headers,{'Content-Type':'image/png'});assert.equal(options.redirect,'error')
      assert.equal(options.method,'PUT');assert.equal(options.body.length,picture.length)
      return{ok:true,headers:new Headers({etag:'"abc"'}),body:{async cancel(){}}}
    }
    try {
      for(const url of ['http://fixture.cos.ap-guangzhou.myqcloud.com/x','https://127.0.0.1/x','https://a:b@fixture.cos.ap-guangzhou.myqcloud.com/x',
        'https://fixture.cos.ap-guangzhou.myqcloud.com.evil/x'])await assert.rejects(putMaterial(url,picture,'image/png'))
      assert.equal(calls,0)
      assert.equal(await putMaterial('https://fixture.cos.ap-guangzhou.myqcloud.com/x',picture,'image/png'),'abc')
      assert.equal(calls,1)
    }finally{globalThis.fetch=prior}
  })
  await run('Material ETag mismatch never completes; image send uncertainty never resends',async()=>{
    const {MaterialStore}=require('../lib/materials')
    const directory=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-material-'))
    let calls=0
    try {
      const store=new MaterialStore(directory,'app',async()=>{calls++;return{upload_id:'upload',presigned_url:'fixture'}},async()=>'bad')
      await assert.rejects(store.acquire(picture),/ETag/);assert.equal(calls,1)
      const e=await setup();e.setHandler(url=>url.endsWith('/files')?{file_info:'ok'}:{audit_id:'pending'})
      assert.equal((await e.delivery.deliver('one',0,png)).status,'uncertain')
      await e.delivery.deliver('one',0,png)
      assert.equal((await e.delivery.deliver('two',1,text)).status,'aborted')
      assert.equal(e.calls.filter(c=>c.url.endsWith('/messages')).length,1)
    }finally{assert.ok(path.resolve(directory).startsWith(path.resolve(require('node:os').tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})}
  })
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
  await run('group h.at is discarded by pinned adapter; not proof of a native mention', async () => {
    const env = createBot()
    await new Promise(resolve => setImmediate(resolve))
    const decoded = await decodeIncoming(env.bot, event(), identity, recognizes)
    const session = env.ctx.reflect.trace(decoded.session)
    session.timestamp = Date.now() // Keep the adapter's passive-reply window live in this fixture.
    await session.send([h.at('fixture-user'), h.text('mention protocol probe')])
    const sent = env.calls.filter(call => call.url.endsWith('/messages'))
    assert.equal(sent.length, 1)
    assert.equal(sent[0].data.content, 'mention protocol probe')
    assert.equal(sent[0].data.msg_id, 'fixture-message')
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
    assert.equal(kind === 'group' ? sends[1].data.markdown.content : sends[1].data.content, kind === 'group' ? '<qqbot-at-user id="fixture-user" />\n' + text.text : text.text)
    assert.equal(sends[0].data.msg_type, kind === 'group' ? 2 : 7)
    if(kind==='group') {
      assert.equal(sends[0].data.media,undefined)
      assert.equal(sends[0].data.markdown.content,'![查询结果 #1px #1px](qqbot-media://image/v1/fixture)\n<qqbot-at-user id="fixture-user" />\n'+png.caption)
      assert.equal(sends[0].data.markdown.force_verify_image_resource,true)
    } else assert.equal(sends[0].data.content,png.caption)
    assert.equal(sends[1].data.msg_type, kind === 'group' ? 2 : 0)
    if (kind === 'group') assert.equal(sends[1].data.content, undefined)
    assert.ok(sends.every(c => c.url.includes(kind === 'group' ? '/groups/fixture-group/' : '/users/fixture-user/')))
  })
  await run('upload failure permits explicit captured text fallback without consuming a slot', async () => {
    const e = await setup()
    e.setHandler(url => { if (url.endsWith('/files')) throw Error('upload timeout'); return { id: 'fallback' } })
    assert.equal((await e.delivery.deliver('image', 0, png)).status, 'upload_failed')
    assert.equal(e.delivery.usedSlots, 0); assert.equal(e.records.length, 0)
    assert.equal((await e.delivery.deliver('fallback', 0, text)).status, 'success')
    assert.equal(e.calls.at(-1).data.markdown.content, '<qqbot-at-user id="fixture-user" />\n' + text.text); assert.equal(e.calls.at(-1).data.msg_seq, 1)
  })
  await run('native group mention stays with each captured sender across interleaved text and image', async () => {
    const a = await setup('group', {}, { id: 'message-a', author: { id: 'user-a' } })
    const b = await setup('group', {}, { id: 'message-b', author: { id: 'user-b' } })
    a.decoded.inbound.userId = 'later-user'; a.decoded.inbound.messageId = 'later-message'
    await Promise.all([a.delivery.deliver('text', 0, text), b.delivery.deliver('image', 0, png)])
    assert.equal(a.calls.at(-1).data.markdown.content, '<qqbot-at-user id="user-a" />\n' + text.text)
    assert.equal(b.calls.at(-1).data.markdown.content,'![查询结果 #1px #1px](qqbot-media://image/v1/fixture)\n<qqbot-at-user id="user-b" />\n'+png.caption)
    assert.equal(a.calls.at(-1).data.msg_id, 'message-a'); assert.equal(b.calls.at(-1).data.msg_id, 'message-b')
    assert.equal(a.calls.at(-1).data.msg_seq, 1); assert.equal(b.calls.at(-1).data.msg_seq, 1)
  })
  await run('only transport may introduce mentions; final payload still obeys 1800 characters', async () => {
    const e = await setup('group', {}, { author: { id: 'x'.repeat(256) } })
    const bad = '<qqbot-at-user id="other" /><@other><qqbot-cmd-enter text="/问" />'
    assert.equal((await e.delivery.deliver('body', 0, { kind: 'text', text: bad })).status, 'success')
    assert.equal(e.calls.at(-1).data.markdown.content.match(/<qqbot-at-user /g).length, 1)
    assert.ok(e.calls.at(-1).data.markdown.content.endsWith('＜qqbot-at-user id="other" />＜@other>＜qqbot-cmd-enter text="/问" />'))
    assert.equal((await e.delivery.deliver('bounded', 1, { kind: 'text', text: '文'.repeat(1500) })).status, 'success')
    assert.ok([...e.calls.at(-1).data.markdown.content].length <= 1800)
    const before = e.calls.length
    assert.equal((await e.delivery.deliver('oversize', 2, { kind: 'text', text: '文'.repeat(1800) })).reason, 'part_size')
    assert.equal(e.calls.length, before)
    const privateReply = await setup('c2c')
    assert.equal((await privateReply.delivery.deliver('private', 0, { kind: 'text', text: '文'.repeat(1800) })).status, 'success')
    assert.equal(privateReply.calls.at(-1).data.content, '文'.repeat(1800))
  })
  await run('missing file_info is upload failure; no media send', async () => {
    const e = await setup('c2c'); e.setHandler(() => ({}))
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
  await run('M4 launcher defaults closed and requires explicit identity', async () => {
    const {configuration}=require('../run.cjs')
    assert.throws(()=>configuration({}))
    assert.throws(()=>configuration({TAKI_KOISHI_ENABLED:'1'}))
    const value=configuration({TAKI_KOISHI_ENABLED:'1',TAKI_CONSOLE_STATE_DIR:path.resolve('fixture-state'),QQ_APP_ID:'a',QQ_APP_SECRET:'fixture',QQ_SELF_ID:'s',OURNOTES_BACKEND_TOKEN:'x'.repeat(32),TAKI_INSTANCE_ID:'i'})
    assert.deepEqual(value.adapter.retryWhen,[])
    assert.equal(value.plugin.notifications,true)
  })
  await run('Query chat drops ordinary non-addressed group text before raw retention', async()=>{
    const env=createBot();let receiver
    const client={deactivate(){},async initialize(){},async retire(){}}
    const fork=env.ctx.plugin(ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:[],maxPending:8},client)})
    try {
      receiver.capture(env.bot.session({type:'internal',_type:'qq/group-message-create',_data:event({content:'ordinary private conversation'}).d}))
      assert.equal(receiver.raw.size,0)
      receiver.capture(env.bot.session({type:'internal',_type:'qq/group-at-message-create',_data:event().d}))
      assert.equal(receiver.raw.size,1)
      for(const record of receiver.raw.values())record.at=Date.now()-298001
      receiver.sweep();assert.equal(receiver.raw.size,0)
    }finally{await receiver.dispose();await fork.dispose();await env.ctx.stop()}
  })
  for(const revoked of ['authority','user-ignore','channel-ignore','pause','local']) {
    await run('Query chat current execution permission rechecks '+revoked, async()=>{
      const env=createBot();let receiver,changed=false,reads=0;const decisions=[]
      env.ctx.provide('database')
      env.ctx.database={
        async getChannel(){return{id:'fixture-group',platform:'qq',flag:changed&&revoked==='channel-ignore'?1:0,assignee:identity.selfId,guildId:'fixture-group',permissions:[],locales:[]}},
        async getUser(){reads++;return{id:1,flag:changed&&revoked==='user-ignore'?1:0,authority:changed&&revoked==='authority'?0:1,permissions:[],locales:[]}},
        async setChannel(){},async setUser(){},
      }
      const management={accepts:()=>!(changed&&revoked==='pause'),policySnapshot:()=>({natural_query:changed&&revoked==='local'?'local':'model',epoch:'policy',global_revision:changed?1:0,group_revision:0})}
      const client={readiness:{backend_epoch:'backend'},deactivate(){},async initialize(){},async retire(){},async modelPermission(challenge,policy,allowed){decisions.push({challenge,policy,allowed})}}
      const plugin=ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:['authority:1'],maxPending:8,management},client)}
      plugin.inject={optional:['database']};const fork=env.ctx.plugin(plugin)
      try {
        const session=env.ctx.reflect.trace(await adaptSession(env.bot,event()))
        assert.equal(await receiver.permitted(session),true)
        const input={...identity,requestId:'request',platform:'qq',kind:'group',conversationId:'fixture-group',userId:'fixture-user'}
        const job={input,session,permissionAttempts:new Set()}
        const challenge={request_id:'request',attempt_id:'a'.repeat(32),nonce:'b'.repeat(32),user_id:input.userId,gateway_epoch:receiver.epoch,backend_epoch:'backend',remaining_ms:3000,
          scope:{instance_id:identity.instanceId,app_id:identity.appId,platform:'qq',kind:'group',conversation_id:input.conversationId}}
        await receiver.authorizeModel(job,challenge)
        assert.equal(decisions[0].allowed,true)
        const oldReads=reads;changed=true
        await receiver.authorizeModel(job,{...challenge,attempt_id:'c'.repeat(32),nonce:'d'.repeat(32)})
        assert.equal(decisions[1].allowed,false)
        if(revoked!=='channel-ignore')assert.ok(reads>oldReads,'fresh user database read')
        await receiver.authorizeModel(job,challenge);assert.equal(decisions.length,2)
        for(const id of ['e','f']) await receiver.authorizeModel(job,{...challenge,attempt_id:id.repeat(32)})
        assert.equal(decisions.length,4);assert.ok(decisions.slice(1).every(d=>!d.allowed))
        await assert.rejects(()=>receiver.authorizeModel(job,{...challenge,attempt_id:'1'.repeat(32)}),/model_attempt_limit/)
        assert.equal(decisions.length,4)
        await assert.rejects(()=>receiver.authorizeModel(job,{...challenge,request_id:'another'}),/model_permission_binding/)
        assert.equal(env.calls.length,0)
      }finally{await receiver.dispose();await fork.dispose();await env.ctx.stop()}
    })
  }
  await run('M4 notices share strict receipt classification', async () => {
    const {classifyReceipt}=require('../lib')
    assert.equal(classifyReceipt({code:1,id:'not-success'}).status,'failure')
    assert.equal(classifyReceipt({id:'audited',data:{message_audit:{}}}).status,'uncertain')
    assert.equal(classifyReceipt({id:'receipt'}).status,'success')
  })
  await run('Query chat slow hint shares sequence owner while model permits continue polling',async()=>{
    const env=createBot();let receiver,stage=0,decisions=0
    const decoded=await decodeIncoming(env.bot,event(),identity,recognizes)
    const input={...decoded.inbound,timestampMs:Date.now()}
    let releaseHint
    const slow=new Promise(resolve=>{releaseHint=resolve})
    env.setHandler(async()=>{if(env.calls.length===1)await slow;return{id:'receipt-'+env.calls.length}})
    const part=(position,text,status='pending')=>({id:String(position),position,mode:'primary',status,kind:'text',text})
    const base={request_id:input.requestId,delivery_id:'a'.repeat(32),plan_version:1,send_allowed:true,
                expires_at:Date.now()/1000+298,processing_hint:true}
    const hint=()=>({...base,status:'preparing',parts:[part(0,'还在核实，请稍等。')]})
    const client={epoch:'epoch',deactivate(){},async initialize(){},async retire(){},
      async lookup(){
        if(stage===0)return{...hint(),pending_model_permission:{fixture:true}}
        if(stage===1)return{...base,status:'prepared',parts:[part(0,'还在核实，请稍等。','success'),part(1,'最终回答')]}
        return{...base,status:'finished',send_allowed:false,parts:[part(0,'提示','success'),part(1,'最终回答','success')]}
      },async intent(){return'granted'},async acknowledge(attempt){if(attempt.partId==='0')stage=1;else stage=2},async event(){}
    }
    const fork=env.ctx.plugin(ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:[],maxPending:8},client)})
    receiver.authorizeModel=async()=>{decisions++;releaseHint()}
    try{
      const job={input,plan:hint(),permissionAttempts:new Set()}
      await receiver.deliver(job,env.bot)
      assert.equal(decisions,1);assert.equal(env.calls.length,2)
      assert.deepEqual(env.calls.map(c=>c.data.msg_seq),[1,2])
        assert.deepEqual(env.calls.map(c=>c.data.markdown.content),['还在核实，请稍等。','最终回答'].map(t=>'<qqbot-at-user id="fixture-user" />\n'+t))
    }finally{releaseHint();await receiver.dispose();await fork.dispose();await env.ctx.stop()}
  })
  await run('Actual slow hint and four combined image mentions share five slots',async()=>{
    const env=createBot();let receiver,completed=0
    const decoded=await decodeIncoming(env.bot,event(),identity,recognizes)
    const input={...decoded.inbound,timestampMs:Date.now()}
    const parts=Array.from({length:5},(_,i)=>({id:String(i),position:i,mode:'primary',status:'pending',
      kind:i?'image':'text',text:i?'图片 '+i:'查询结果：'}))
    const plan=()=>({request_id:input.requestId,delivery_id:'a'.repeat(32),plan_version:1,
      status:completed===5?'finished':'prepared',send_allowed:completed<5,processing_hint:true,
      expires_at:Date.now()/1000+290,parts:parts.map((p,i)=>({...p,status:i<completed?'success':'pending'}))})
    const client={epoch:'epoch',deactivate(){},async initialize(){},async retire(){},async image(){return picture},
      async lookup(){return plan()},async intent(){return'granted'},async acknowledge(attempt){
        assert.equal(attempt.partId,String(completed));completed++
      },async event(){throw Error('unexpected abort')}}
    const fork=env.ctx.plugin(ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:[],maxPending:8},client)})
    try{
      receiver.materials=fixtureMaterials(env.bot.internal)
      await receiver.deliver({input,plan:plan(),permissionAttempts:new Set()},env.bot)
      const sends=env.calls.filter(c=>c.url.endsWith('/messages'))
      assert.equal(completed,5);assert.deepEqual(sends.map(c=>c.data.msg_seq),[1,2,3,4,5])
      assert.deepEqual(sends.map(c=>c.data.msg_type),[2,2,2,2,2])
      assert.equal(sends[0].data.markdown.content,'<qqbot-at-user id="fixture-user" />\n查询结果：')
      assert.ok(sends.slice(1).every(c=>c.data.markdown.content.includes('qqbot-at-user')))
      assert.equal(env.calls.filter(c=>c.url.endsWith('/files')).length,4)
    }finally{await receiver.dispose();await fork.dispose();await env.ctx.stop()}
  })
  await run('Receiver wires official material APIs to one passive Markdown image and real mention',async()=>{
    const env=createBot();let receiver,completed=false,puts=0
    const directory=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-material-receiver-'))
    const prior=globalThis.fetch
    globalThis.fetch=async(url,options)=>{
      puts++;assert.equal(new URL(url).hostname,'fixture.cos.ap-guangzhou.myqcloud.com')
      assert.deepEqual(options.headers,{'Content-Type':'image/png'})
      return{ok:true,body:{async cancel(){}},headers:new Headers({etag:crypto.createHash('md5').update(picture).digest('hex')})}
    }
    env.setHandler(url=>url.endsWith('/prepare')?{upload_id:'receiver_upload',presigned_url:'https://fixture.cos.ap-guangzhou.myqcloud.com/image'}:
      url.endsWith('/complete')?{image_id:'receiver_owned',url:'qqbot-media://image/v1/receiver_owned'}:{id:'receiver_message'})
    const decoded=await decodeIncoming(env.bot,event(),identity,recognizes)
    const input={...decoded.inbound,timestampMs:Date.now()}
    const plan=()=>({request_id:input.requestId,delivery_id:'a'.repeat(32),plan_version:1,status:completed?'finished':'prepared',
      send_allowed:!completed,expires_at:Date.now()/1000+290,
      parts:[{id:'0',position:0,mode:'primary',status:completed?'success':'pending',kind:'image',text:''}]})
    const client={epoch:'epoch',deactivate(){},async initialize(){},async retire(){},async image(){return picture},
      async lookup(){return plan()},async intent(){return'granted'},async acknowledge(){completed=true},async event(){throw Error('unexpected fallback')}}
    const fork=env.ctx.plugin(ctx=>{receiver=new Receiver(ctx,{...identity,materialStateDir:directory,
      backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:[],maxPending:8},client)})
    try{
      await receiver.deliver({input,plan:plan(),permissionAttempts:new Set()},env.bot)
      assert.equal(puts,1);assert.equal(completed,true)
      assert.deepEqual(env.calls.map(c=>c.url),['/v2/media-images/prepare','/v2/media-images/complete','/v2/groups/fixture-group/messages'])
      const body=env.calls.at(-1).data
      assert.equal(body.msg_type,2);assert.equal(body.msg_seq,1);assert.equal(body.msg_id,'fixture-message')
      assert.equal(body.markdown.content,'![查询结果 #1px #1px](qqbot-media://image/v1/receiver_owned)\n<qqbot-at-user id="fixture-user" />')
      assert.equal(body.markdown.force_verify_image_resource,true)
    }finally{
      globalThis.fetch=prior;await receiver.dispose();await fork.dispose();await env.ctx.stop()
      assert.ok(path.resolve(directory).startsWith(path.resolve(require('node:os').tmpdir())+path.sep));fs.rmSync(directory,{recursive:true})
    }
  })
  await run('M4 real plugin publishes and releases its scoped transport service', async () => {
    const ctx=new Context(),plugin=require('../lib')
    const fork=ctx.plugin(plugin,{enabled:false})
    assert.ok(ctx.takiTransport)
    assert.equal(ctx.takiTransport.receiver,undefined)
    await fork.dispose();assert.equal(ctx.takiTransport,undefined)
  })
  await run('M4 group capacity stops notification admission and health', async () => {
    const ctx=new Context(),{Notices}=require('../lib')
    const worker=new Notices(ctx,{},identity,new Promise(()=>{}))
    for(let i=0;i<4096;i++)worker.changed.set('g'+i,1)
    worker.capture({platform:'qq',bot:{config:{id:identity.appId},selfId:identity.selfId},
      event:{_type:'GROUP_MSG_REJECT',_data:{group_openid:'overflow',timestamp:now/1000}}})
    assert.equal(worker.healthy(),false)
    await worker.close();await ctx.stop()
  })
  const startupReady={ready:true,protocol:{major:2,minor:0},capability:'qq-query-chat-v3',
    management_capabilities:['group_policies_v1'],
    features:['classify','gateway_retire','ack_context_guard','notice_gateway_v1',
      'quoted_context_v1','fixed_snapshot_v1','current_model_permit_v1','independent_delivery_v1']}
  await run('startup survives delayed readiness and registration transport failure', async () => {
    let reads=0,registrations=0
    const epochs=[]
    const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','epoch',async(url,options)=>{
      if(url.endsWith('/ready'))return new Response(JSON.stringify(startupReady),{status:++reads<=4?503:200})
      epochs.push(JSON.parse(options.body).gateway_epoch)
      if(++registrations===1)throw Error('connection reset')
      return new Response('{}')
    })
    await client.initialize({timeoutMs:2000,retryMs:100})
    assert.ok(reads>=6);assert.equal(registrations,2);assert.deepEqual(epochs,['epoch','epoch'])
    assert.equal(client.readiness.ready,true)
  })
  await run('startup retries connection refused but rejects incompatible protocol and authentication immediately', async () => {
    let calls=0
    const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','epoch',async()=>{
      if(++calls===1)throw Error('connection refused')
      return new Response(JSON.stringify(startupReady))
    })
    await client.initialize({timeoutMs:1000,retryMs:5});assert.equal(calls,3)
    for(const status of [200,401,403]) {
      let attempts=0
      const bad=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','epoch',async()=>{
        attempts++;return new Response(JSON.stringify({...startupReady,protocol:{major:3,minor:0}}),{status})
      })
      await assert.rejects(bad.initialize({timeoutMs:500,retryMs:5}),e=>e.code===(status===200?'incompatible_backend':'backend_http_'+status))
      assert.equal(attempts,1)
    }
  })
  await run('startup deadline aborts a stalled request and leaves no handshake', async () => {
    let aborted=false
    const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','epoch',async(url,{signal})=>new Promise((resolve,reject)=>{
      signal.addEventListener('abort',()=>{aborted=true;reject(Error('aborted'))},{once:true})
    }))
    await assert.rejects(client.initialize({timeoutMs:30,retryMs:5}),e=>e.code==='startup_timeout')
    assert.equal(aborted,true);assert.equal(client.readiness,undefined)
  })
  await run('Protocol pairing rejects boolean and extra-key profiles before registration',async()=>{
    for(const profile of [{major:true,minor:1},{major:1,minor:1,extra:true}]){
      let calls=0
      const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','epoch',async()=>{
        calls++;return new Response(JSON.stringify({...startupReady,protocol:profile}))
      })
      await assert.rejects(client.initialize({timeoutMs:500,retryMs:5}),e=>e.code==='incompatible_backend')
      assert.equal(calls,1)
    }
  })
  await run('startup cancellation interrupts retry sleep without registering', async () => {
    let calls=0
    const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','epoch',async()=>{
      calls++;return new Response('{}',{status:503})
    })
    const pending=client.initialize({timeoutMs:1000,retryMs:500})
    setTimeout(()=>client.deactivate(),20)
    await assert.rejects(pending,e=>e.code==='startup_cancelled');assert.equal(calls,1)
  })
  for(const mode of ['timeout','incompatible'])await run('actual gateway startup rejects '+mode+' and disposes plugins',async()=>{
    const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-startup-probe-'))
    const {Bot}=require('koishi'),originalStart=Bot.prototype.start,originalRead=fs.readFileSync
    const originalInitialize=BackendClient.prototype.initialize,originalFetch=globalThis.fetch
    const token=process.env.OURNOTES_BACKEND_TOKEN
    Bot.prototype.start=async()=>{}
    fs.readFileSync=function(file,...args){return String(file).endsWith('combination.json')?JSON.stringify({commit:'b'.repeat(40)}):originalRead.call(this,file,...args)}
    BackendClient.prototype.initialize=function(){return originalInitialize.call(this,{timeoutMs:30,retryMs:5})}
    globalThis.fetch=async()=>new Response(JSON.stringify({...startupReady,protocol:{major:3,minor:0}}),{status:mode==='timeout'?503:200})
    process.env.OURNOTES_BACKEND_TOKEN='x'.repeat(32)
    try {
        await assert.rejects(require('../run.cjs').start({TAKI_KOISHI_ENABLED:'1',TAKI_CONSOLE_STATE_DIR:dir,QQ_APP_ID:'fixture-app',QQ_APP_SECRET:'fixture',QQ_SELF_ID:'fixture-self',OURNOTES_BACKEND_TOKEN:'x'.repeat(32),TAKI_INSTANCE_ID:'fixture'}),
        e=>e.code===(mode==='timeout'?'startup_timeout':'incompatible_backend'))
    } finally {
      Bot.prototype.start=originalStart;fs.readFileSync=originalRead;BackendClient.prototype.initialize=originalInitialize;globalThis.fetch=originalFetch
      if(token===undefined)delete process.env.OURNOTES_BACKEND_TOKEN;else process.env.OURNOTES_BACKEND_TOKEN=token
      fs.rmSync(dir,{recursive:true,force:true})
    }
  })
  const root = path.resolve(__dirname, '..')
  await run('console validates separate credentials and stays disabled by default',async()=>{
    const {configuration}=require('../console.cjs')
    assert.equal(configuration({}),undefined)
    assert.throws(()=>configuration({TAKI_CONSOLE_ENABLED:'1'}))
    const env={TAKI_CONSOLE_ENABLED:'1',TAKI_CONSOLE_PASSWORD:'fixture-console-password-000000',TAKI_CONSOLE_STATE_DIR:path.resolve('fixture-state')}
    assert.equal(configuration(env).port,5140)
    assert.throws(()=>configuration({...env,OURNOTES_BACKEND_TOKEN:env.TAKI_CONSOLE_PASSWORD}))
    assert.throws(()=>configuration({...env,TAKI_CONSOLE_PORT:'0'}))
  })
  await run('console pause blocks real receiver before backend submission',async()=>{
    const env=createBot();let submissions=0,receiver
    const client={epoch:'fixture-e',deactivate(){},async initialize(){},async recognizes(body){return recognizes(body)},async retire(){},async submit(){submissions++;throw Error('pause bypass')}}
    const fork=env.ctx.plugin(ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:['authority:1'],maxPending:8,management:{accepts:()=>false}},client)})
    try {
      const raw=event()
      await env.ctx.parallel('internal/session',env.bot.session({type:'internal',_type:'qq/group-at-message-create',_data:raw.d}))
      assert.equal(await receiver.receive(env.ctx.reflect.trace(await adaptSession(env.bot,raw))),true)
      assert.equal(submissions,0);assert.equal(env.calls.length,0);assert.equal(receiver.health().active,true)
      const ordinary=event({id:'ordinary-message',content:'ordinary chat'})
      await env.ctx.parallel('internal/session',env.bot.session({type:'internal',_type:'qq/group-at-message-create',_data:ordinary.d}))
      assert.equal(await receiver.receive(env.ctx.reflect.trace(await adaptSession(env.bot,ordinary))),false)
    }finally{await receiver.dispose();await fork.dispose()}
  })
  await run('console notice pause preserves group revocation synchronization',async()=>{
    const {Notices}=require('../lib'),ctx=new Context();const calls=[]
    const client={epoch:'e',readiness:{notices_enabled:true},async retry(url,body){calls.push({url,body});return{claims:[]}}}
    const worker=new Notices(ctx,client,identity,Promise.resolve(),()=>false)
    try {
      worker.capture({platform:'qq',bot:{config:{id:identity.appId},selfId:identity.selfId},event:{_type:'qq/group-msg-reject',_data:{id:'event-disabled',group_openid:'fixture-group',timestamp:Math.floor(Date.now()/1000)}}})
      // Discovery is also retained while paused; use the adapter's normal message envelope.
      worker.capture({platform:'qq',bot:{config:{id:identity.appId},selfId:identity.selfId},event:{_type:'qq/group-at-message-create',_data:{...event().d}}})
      await worker.groupTail;worker.tick();await Promise.resolve()
      assert.ok(calls.some(c=>c.url==='/v1/groups/events' && c.body.kind==='reject'));assert.ok(calls.every(c=>c.url!=='/v1/notices/claims'));assert.equal(worker.healthy(),true)
    } finally{await worker.close();await ctx.stop()}
  })
  await run('disabled backend notices preserve health and skip events before/after readiness',async()=>{
    const {Notices}=require('../lib'),ctx=new Context();let releaseReady,calls=0
    const client={epoch:'e',async retry(){calls++;throw Error('notifications_disabled')}}
    const ready=new Promise(resolve=>{releaseReady=resolve})
    const worker=new Notices(ctx,client,identity,ready)
    const message={platform:'qq',bot:{config:{id:identity.appId},selfId:identity.selfId},event:{_type:'qq/group-at-message-create',_data:{...event().d}}}
    try {
      worker.capture(message)
      client.readiness={notices_enabled:false};releaseReady()
      await worker.groupTail
      assert.equal(calls,0);assert.equal(worker.healthy(),true)
      const count=worker.changed.size
      worker.capture({...message,event:{_type:'qq/group-msg-reject',_data:{group_openid:'later-group',timestamp:Math.floor(Date.now()/1000)}}})
      await worker.groupTail;worker.tick();await worker.task
      assert.equal(calls,0);assert.equal(worker.changed.size,count);assert.equal(worker.healthy(),true)
    }finally{await worker.close();await ctx.stop()}
  })
  await run('console notice allowlist is sent before backend claims',async()=>{
    const {Notices}=require('../lib'),env=createBot(),calls=[]
    env.bot.status=1
    const client={epoch:'e',readiness:{notices_enabled:true},async retry(url,body){calls.push({url,body});return{claims:[]}}}
    const worker=new Notices(env.ctx,client,identity,Promise.resolve(),()=>true,()=>({kind:'selected',group_ids:['allowed-group']}))
    try {
      worker.tick();await worker.task
      assert.equal(calls.length,1)
      assert.equal(calls[0].url,'/v1/notices/claims')
      assert.deepEqual(calls[0].body.scope,{kind:'selected',group_ids:['allowed-group']})
      assert.equal(env.calls.length,0)
    } finally{await worker.close();await env.ctx.stop()}
  })
  await run('console settings persist, reject conflicts and constrain query/notice admission',async()=>{
    const {Management,defaultSettings}=require('../lib/management')
    const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-console-probe-'))
    try {
      const m=new Management(dir),group={kind:'group',conversationId:'allowed'},privateInput={kind:'c2c'}
      assert.equal(m.accepts(group),true)
      m.update({...defaultSettings,groupAllowlist:['allowed'],privateEnabled:false},0)
      assert.equal(m.accepts(group),true);assert.equal(m.accepts({...group,conversationId:'other'}),false)
      assert.equal(m.accepts(privateInput),false);assert.equal(m.noticesAllowed('other'),false)
      assert.throws(()=>m.update(defaultSettings,0));assert.throws(()=>m.update({...defaultSettings,extra:true},1))
      assert.deepEqual(new Management(dir).snapshot().settings,m.snapshot().settings)
      m.update({...defaultSettings,queriesEnabled:false,notificationsEnabled:false},1)
      assert.equal(m.accepts(group),false);assert.equal(m.noticesAllowed(),false)
      assert.throws(()=>m.update({...defaultSettings,groupAllowlist:['duplicate','duplicate']},2))
      fs.writeFileSync(path.join(dir,'settings.json'),'{invalid')
      assert.throws(()=>new Management(dir))
    } finally {fs.rmSync(dir,{recursive:true,force:true})}
  })
    await run('Console guards reject a previous process and preserve stale drafts',async()=>{
      const {Management,defaultSettings}=require('../lib/management')
      const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-console-guard-'))
      try {
        const a=new Management(dir),guard=a.snapshot().guard
        a.updateGuarded({...defaultSettings,privateEnabled:false},guard)
        assert.throws(()=>a.updateGuarded(defaultSettings,guard),/settings_conflict/)
        const b=new Management(dir)
        assert.throws(()=>b.updateGuarded(defaultSettings,guard),/settings_conflict/)
        assert.equal(b.snapshot().settings.privateEnabled,false)
      } finally {fs.rmSync(dir,{recursive:true,force:true})}
    })
    await run('Console telemetry queue is bounded and retries at most once',async()=>{
      const {Telemetry}=require('../lib/telemetry')
      let calls=0
      const telemetry=new Telemetry(async()=>{calls++;throw Error('fixture disconnect')})
      const span={request_id:'r',delivery_id:'d',kind:'send',position:0,attempt:'a',duration_ms:20}
      for(let i=0;i<300;i++)telemetry.enqueue(span)
      assert.equal(telemetry.snapshot().queued,256)
      assert.equal(telemetry.dropped,44)
      await telemetry.flush()
      assert.equal(calls,2)
      assert.equal(telemetry.dropped,76)
      await telemetry.close()
    })
    await run('Console monotonic spans separate upload send and receipt submission',async()=>{
      const spans=[],wait=ms=>new Promise(r=>setTimeout(r,ms))
      const api={async sendFileGuild(){await wait(20);return{file_info:'fixture'}},async sendMessage(){await wait(40);return{id:'fixture-message'}}}
      const inbound={requestId:'request',kind:'group',conversationId:'fixture-group',userId:'fixture-user',messageId:'fixture',timestampMs:Date.now()}
      const events={async intent(){return'granted'},async acknowledge(){await wait(10)},observe(span){spans.push(span)}}
      const delivery=new QQDelivery(api,inbound,'delivery','epoch',events,()=>true,undefined,undefined,fixtureMaterials(api))
      assert.equal((await delivery.deliver('0',0,png)).status,'success')
      assert.deepEqual(spans.map(s=>s.kind),['upload','send','receipt_submit'])
      assert.ok(spans.find(s=>s.kind==='send').duration_ms>=30)
      assert.equal(spans[0].attempt,null)
    })
    await run('Group policy revisions survive restart, cannot override globals and reject concurrent saves',async()=>{
      const {Management,defaultSettings}=require('../lib/management')
      const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-group-policy-'))
      const policy={label:'测试群',queries:'allow',notifications:'pause',natural_query:'local'}
      try {
        const m=new Management(dir,identity)
        m.groups.update('fixture-group',policy,0)
        assert.throws(()=>m.groups.update('fixture-group',policy,0),/group_policy_conflict/)
        assert.equal(new Management(dir,identity).groups.snapshot().revision,1)
        m.update({...defaultSettings,queriesEnabled:false,groupAllowlist:['fixture-group']},0)
        assert.equal(m.accepts({kind:'group',conversationId:'fixture-group'}),false)
        assert.equal(m.noticeScope().kind,'none')
        assert.equal(m.policySnapshot({kind:'group',conversationId:'fixture-group'}).natural_query,'local')
        m.update({...defaultSettings,groupAllowlist:['other']},1)
        assert.equal(m.effective('fixture-group').queries,false)
        assert.ok(m.effective('fixture-group').reasons.includes('global_group_excluded'))
        m.update(defaultSettings,2)
        fs.writeFileSync(path.join(dir,'group-policies.v1.json'),'{broken')
        const broken=new Management(dir,identity)
        assert.equal(broken.accepts({kind:'group',conversationId:'fixture-group'}),false)
        assert.equal(broken.noticeScope().kind,'none')
        assert.equal(broken.groups.snapshot().availability,'unavailable')
        assert.equal(broken.accepts({kind:'c2c'}),true)
      } finally {fs.rmSync(dir,{recursive:true,force:true})}
    })
    await run('Group policy lost rename response is resolved from disk; failed writes keep effective values',async()=>{
      const {GroupPolicies}=require('../lib/group-policies')
      const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-policy-save-'))
      const owner={platform:'qq',app_id:'fixture-app',instance_id:'fixture-instance'},policy={label:'',queries:'pause',notifications:'pause',natural_query:'off'}
      const rename=fs.renameSync,write=fs.writeFileSync
      try {
        const p=new GroupPolicies(dir,owner)
        fs.renameSync=(...args)=>{rename(...args);throw Error('lost completion')}
        assert.equal(p.update('g',policy,0).revision,1)
        fs.renameSync=rename
        fs.writeFileSync=()=>{throw Error('disk full')}
        assert.throws(()=>p.update('g',{...policy,queries:'allow'},1),/save_unconfirmed/)
        assert.equal(p.get('g').queries,'pause')
        assert.equal(p.snapshot().revision,1)
        fs.writeFileSync=write
        assert.equal(new GroupPolicies(dir,{...owner,app_id:'other'}).error,'group_policy_unavailable')
      } finally {fs.renameSync=rename;fs.writeFileSync=write;fs.rmSync(dir,{recursive:true,force:true})}
    })
    await run('Notice scope none does not become all or claim a report',async()=>{
      const {Notices}=require('../lib'),env=createBot();let calls=0
      env.bot.status=1
      const client={epoch:'e',readiness:{notices_enabled:true},async retry(){calls++;throw Error('none claimed')}}
      const worker=new Notices(env.ctx,client,identity,Promise.resolve(),()=>true,()=>({kind:'none'}))
      try {worker.tick();await worker.task;assert.equal(calls,0);assert.equal(env.calls.length,0)}
      finally {await worker.close();await env.ctx.stop()}
    })
    await run('Shared live health never reports last good readiness after current failure',async()=>{
      const {healthReader}=require('../health.cjs');let calls=0,fail=false
      const ready={ready:true,background_tasks:true,backend_epoch:'e',backend_code_digest:'digest'}
      const ctx={bots:[{platform:'qq',config:{id:'a'},selfId:'s',status:1}],takiTransport:{receiver:{health:()=>({active:true,handshake:true,backend:ready}),
        client:{async request(){calls++;if(fail)throw Error('offline');return ready}}}}}
      const read=healthReader(ctx,{commit:'b'.repeat(40),backend_code_digest:'digest'},{appId:'a',selfId:'s'})
      const results=await Promise.all([read(),read()]);assert.equal(calls,1);assert.equal(results[0].ready,true)
      fail=true;const current=await read({fresh:true});assert.equal(current.ready,false);assert.equal(current.backend_ready,false);assert.equal(current.stale,true)
    })
    await run('Request retry preserves admitted policy snapshot after external settings change',async()=>{
      const policy={natural_query:'local',epoch:'fixture',global_revision:1,group_revision:2},seen=[]
      const client=new BackendClient('http://127.0.0.1:8765','x'.repeat(32),'fixture','e',async(url,options)=>{
        seen.push(JSON.parse(options.body));policy.natural_query='model';policy.group_revision=3
        return seen.length===1?Response.json({error:'temporary'},{status:503}):Response.json({request_id:'fixture'})
      })
      try {
        await client.submit({requestId:'fixture',instanceId:'fixture',appId:'app',platform:'qq',kind:'group',conversationId:'g',userId:'u',messageId:'m',timestampMs:Date.now(),body:'/问 fixture'},1,policy)
        assert.equal(seen.length,2);assert.deepEqual(seen[0].policy,seen[1].policy);assert.equal(seen[1].policy.natural_query,'local')
      }finally{await client.telemetry.close()}
    })
    await run('Policy revocation after notice intent prevents send but still records aborted ACK',async()=>{
      const {Notices}=require('../lib'),env=createBot(),records=[];let enabled=true
      env.bot.status=1
      const claim={notice_id:'a'.repeat(64),claim_id:'b'.repeat(32),group_id:'fixture-group',group_version:0,expires_at:Date.now()/1000+120,text:'fixture',send_type:'active'}
      const client={epoch:'e',readiness:{notices_enabled:true},
        async retry(url,body){records.push({url,body});return url.endsWith('/claims')?{claims:[claim]}:{recorded:true}},
        async request(url,body){records.push({url,body});enabled=false;return{permission:'granted'}}}
      const worker=new Notices(env.ctx,client,identity,Promise.resolve(),()=>enabled)
      worker.pace=async()=>{}
      try {
        worker.tick();await worker.task
        assert.ok(records.some(r=>r.body.event==='intent'))
        assert.ok(records.some(r=>r.body.event==='ack'&&r.body.result==='aborted'))
        assert.equal(env.calls.filter(c=>c.data?.msg_type===0).length,0)
      }finally{await worker.close();await env.ctx.stop()}
    })
    await run('Pausing new queries leaves an admitted receiver job and its ACK running',async()=>{
      const env=createBot();let paused=false,submissions=0,acked=false,receiver,plan
      const management={accepts:()=>!paused,policySnapshot:()=>({natural_query:'local',epoch:'policy',global_revision:0,group_revision:0})}
      const client={epoch:'e',deactivate(){},async initialize(){},async recognizes(){return true},async retire(){},
        async submit(input){submissions++;paused=true;return plan={request_id:input.requestId,delivery_id:'d'.repeat(32),plan_version:1,status:'prepared',send_allowed:true,expires_at:Date.now()/1000+290,
          parts:[{id:'0',position:0,mode:'primary',status:'pending',kind:'text',text:'fixture'}]}},
        async intent(){return'granted'},async acknowledge(){acked=true},async lookup(){return{...plan,status:'finished',send_allowed:false,parts:[{...plan.parts[0],status:'success'}]}},async event(){return{}}}
      const fork=env.ctx.plugin(ctx=>{receiver=new Receiver(ctx,{...identity,backendUrl:'http://127.0.0.1:8765',tokenEnv:'unused',permissions:['authority:1'],maxPending:8,management},client)})
      receiver.permitted=async()=>true // Explicit positive platform-permission fixture; denial paths are separately probed.
      try {
        const raw=event({timestamp:new Date().toISOString()})
        await env.ctx.parallel('internal/session',env.bot.session({type:'internal',_type:'qq/group-at-message-create',_data:raw.d}))
        await receiver.receive(env.ctx.reflect.trace(await adaptSession(env.bot,raw)))
        assert.equal(submissions,1);assert.equal(acked,true);assert.equal(env.calls.filter(c=>c.data?.msg_type===2).length,1)
      }finally{await receiver.dispose();await fork.dispose();await env.ctx.stop()}
    })
    await run('Actual gateway keeps policy enforcement when the Console web UI is disabled',async()=>{
      const {Management}=require('../lib/management'),Bot=require('koishi').Bot
      const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'taki-console-off-'))
      new Management(dir,identity).groups.update('fixture-group',{label:'',queries:'pause',notifications:'pause',natural_query:'off'},0)
      const originalStart=Bot.prototype.start,originalInitialize=BackendClient.prototype.initialize,originalRetire=BackendClient.prototype.retire,read=fs.readFileSync
      const originalToken=process.env.OURNOTES_BACKEND_TOKEN;let runtime
      try {
        Bot.prototype.start=async function(){this.user={id:identity.selfId};this.status=1}
        BackendClient.prototype.initialize=async function(){this.readiness={notices_enabled:false,diagnostics_enabled:false}}
        BackendClient.prototype.retire=async()=>{}
        fs.readFileSync=function(file,...args){return String(file).endsWith('combination.json')?JSON.stringify({commit:'b'.repeat(40)}):read.call(this,file,...args)}
        process.env.OURNOTES_BACKEND_TOKEN='x'.repeat(32)
        runtime=await require('../run.cjs').start({TAKI_KOISHI_ENABLED:'1',TAKI_CONSOLE_ENABLED:'0',TAKI_CONSOLE_STATE_DIR:dir,
          TAKI_GATEWAY_HEALTH_PORT:'15144',QQ_APP_ID:identity.appId,QQ_APP_SECRET:'fixture',QQ_SELF_ID:identity.selfId,
          OURNOTES_BACKEND_TOKEN:'x'.repeat(32),TAKI_INSTANCE_ID:identity.instanceId})
        const management=runtime.ctx.takiTransport.receiver.config.management
        assert.equal(management.accepts({kind:'group',conversationId:'fixture-group'}),false)
        assert.equal(management.accepts({kind:'c2c'}),true)
        assert.equal(runtime.ctx.console,undefined)
      }finally{
        await runtime?.close();Bot.prototype.start=originalStart;BackendClient.prototype.initialize=originalInitialize;BackendClient.prototype.retire=originalRetire;fs.readFileSync=read
        if(originalToken===undefined)delete process.env.OURNOTES_BACKEND_TOKEN;else process.env.OURNOTES_BACKEND_TOKEN=originalToken
        fs.rmSync(dir,{recursive:true,force:true})
      }
    })
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
