#!/usr/bin/env node
// Real Koishi Receiver/QQDelivery with loopback Backend; every QQ call is a fixture.
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { performance, monitorEventLoopDelay } = require('node:perf_hooks')
const { Context } = require('koishi')
const { QQBot, adaptSession } = require('@satorijs/adapter-qq')
const { Receiver } = require('../lib')
const { MaterialStore } = require('../lib/materials')
const [port, output, scenario, concurrencyValue, countValue] = process.argv.slice(2)
const concurrency = Number(concurrencyValue), count = Number(countValue)
const token = 'benchmark-only-token-never-a-real-credential'
const identity = { instanceId: 'benchmark-instance', appId: 'benchmark-app', selfId: 'benchmark-self' }
const base = `http://127.0.0.1:${port}`
const originalFetch = globalThis.fetch
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const timings = { upload: [], image: [], send: [], admission_wait: [], material_hit: [], material_miss: [] }
let uploads = 0, messages = 0, serial = 0, imageCount = 0
const deliveries = new Map()
// Fail closed: only the benchmark backend and explicitly simulated COS PUT exist.
globalThis.fetch = async (url, options) => {
  const parsed = new URL(url)
  if (parsed.origin === base) return originalFetch(url, options)
  if (parsed.hostname !== 'benchmark.cos.ap-guangzhou.myqcloud.com' || options.method !== 'PUT')
    throw Error('benchmark forbids external network')
  const start = performance.now(); uploads++
  await delay(120)
  timings.upload.push(performance.now()-start)
  return { ok: true, body: { async cancel() {} }, headers: new Headers({ etag: crypto.createHash('md5').update(options.body).digest('hex') }) }
}
const ctx = new Context()
ctx.provide('bots'); ctx.bots = []
const http = Object.assign(async (url, options) => {
  const body = options.data
  if (url.endsWith('/prepare')) return { data: { upload_id: `upload_${++serial}`, presigned_url: 'https://benchmark.cos.ap-guangzhou.myqcloud.com/file' } }
  if (url.endsWith('/complete')) return { data: { image_id: body.upload_id, url: `qqbot-media://image/v1/${body.upload_id}` } }
  if (options.method === 'DELETE') return { data: {} }
  if (!url.endsWith('/messages')) throw Error('unexpected fixture QQ route')
  const start = performance.now(); await delay(5); timings.send.push(performance.now()-start)
  const content = body.markdown?.content || body.content || ''
  const values = deliveries.get(body.msg_id) || []
  values.push({ image: content.includes('qqbot-media://'), text: content })
  deliveries.set(body.msg_id, values)
  return { data: { id: `receipt_${++messages}` }, headers: new Headers() }
}, { extend() { return this }, isError(error) { return Boolean(error?.response) } })
ctx.provide('http'); ctx.http = http
const bot = new QQBot(ctx, { id: identity.appId, secret: 'fixture-only', type: 'public', sandbox: false,
  intents: 0, protocol: 'websocket', endpoint: 'https://api.bot.qq.com', retryWhen: [], uploadThreshold: 4000000 })
bot.user = { id: identity.selfId }
process.env.TAKI_BENCHMARK_TOKEN = token
let receiver
const plugin = scope => { receiver = new Receiver(scope, { ...identity, backendUrl: base,
  tokenEnv: 'TAKI_BENCHMARK_TOKEN', permissions: [], maxPending: 8, materialStateDir: path.join(output, 'materials') }) }
plugin.inject = { optional: ['database'] }
const fork = ctx.plugin(plugin)
// Observe the existing admission chain without replacing its serialization or gate.
let admission = receiver.admission
Object.defineProperty(receiver, 'admission', { get() {
  const started = performance.now()
  return admission.then(value => { timings.admission_wait.push(performance.now()-started); return value })
}, set(value) { admission = value } })
const acquire = MaterialStore.prototype.acquire
MaterialStore.prototype.acquire = async function(bytes) {
  const key = crypto.createHash('sha256').update(bytes).digest('hex')
  const hit = this.entries?.some(entry => entry.key === key && entry.imageId)
  const started = performance.now()
  try { return await acquire.call(this, bytes) }
  finally { timings[hit ? 'material_hit' : 'material_miss'].push(performance.now()-started) }
}
const cards = [1, 26, 31, 51, 61, 62, 64]
const queries = {
  light: () => '/帮助', hot: () => '/查曲 100091',
  cold: i => `/查留影卡 ${cards[i % cards.length]}`,
  chart: () => '/查谱面 100091',
  mixed: i => i % 3 === 0 ? '/查谱面 100091' : i % 3 === 1 ? '/帮助' : '/查曲 100091',
  'ask-mixed': i => i % 2 ? '/问 查曲 100091' : '/查曲 100091',
}
function percentile(values, q) {
  const ordered = [...values].sort((a, b) => a-b)
  return ordered.length ? ordered[Math.max(0, Math.ceil(q*ordered.length)-1)] : 0
}
async function send(command, id) {
  const raw = { op: 0, s: 1, t: 'GROUP_AT_MESSAGE_CREATE', d: { id,
    timestamp: new Date(Date.now()-10).toISOString(), content: command,
    author: { id: 'benchmark-user' }, group_id: 'benchmark-group' } }
  receiver.capture(bot.session({ type: 'internal', _type: 'qq/group-at-message-create', _data: raw.d }))
  const session = ctx.reflect.trace(await adaptSession(bot, raw))
  const start = performance.now()
  await receiver.receive(session)
  return performance.now()-start
}
async function main() {
  if (!queries[scenario]) throw Error('unknown benchmark scenario')
  await receiver.ready
  const getImage = receiver.client.image.bind(receiver.client)
  receiver.client.image = async (...args) => {
    const start = performance.now(), bytes = await getImage(...args)
    timings.image.push(performance.now()-start)
    if (imageCount++ < 2) fs.writeFileSync(path.join(output, `image-${imageCount}.${bytes[0] === 137 ? 'png' : 'jpg'}`), bytes)
    return bytes
  }
  // Hot material is warmed through the same full request/delivery/ACK path.
  if (['hot', 'mixed', 'ask-mixed'].includes(scenario)) await send('/查曲 100091', 'warmup')
  const startUploads = uploads, startImages = imageCount
  const loop = monitorEventLoopDelay({ resolution: 10 }); loop.enable()
  const start = performance.now()
  let peakPending = 0
  const occupancy = setInterval(() => {
    const activity = receiver.activity()
    peakPending = Math.max(peakPending, activity.pending_total ?? activity.accepted_inflight+activity.admission_inflight)
  }, 25)
  occupancy.unref()
  let cursor = 0
  const samples = []
  await Promise.all(Array.from({ length: concurrency }, async () => {
    while (cursor < count) {
      const i = cursor++, command = queries[scenario](i), id = `sample_${i}`
      const duration = await send(command, id)
      samples.push({ id, command, duration_ms: duration, parts: deliveries.get(id) || [] })
    }
  }))
  const duration = performance.now()-start
  clearInterval(occupancy)
  loop.disable()
  // Drain observation after the timed requests, before taking the ledger snapshot.
  // The real queue remains bounded; failed telemetry is still reported as dropped.
  for (let i=0; i<10; i++) {
    await receiver.client.telemetry?.flush()
    if (!receiver.client.telemetry?.snapshot().queued) break
  }
  const backend = await originalFetch(base+'/v1/benchmark', { headers: { Authorization: 'Bearer '+token, 'X-Taki-Instance': identity.instanceId } }).then(r => r.json())
  for (const sample of samples) {
    const rid = crypto.createHash('sha256').update(JSON.stringify([identity.instanceId, identity.appId, 'qq', 'group', 'benchmark-group', 'benchmark-user', sample.id])).digest('hex')
    const record = backend.requests.find(row => row.id === rid)
    sample.ledger_status = record?.status || 'not_admitted'
    sample.business = record?.diagnostics?.parts || null
  }
  const successful = samples.filter(s => s.ledger_status === 'finished' && s.parts.length > 0
    && !s.parts.some(p => /暂时失败|稍后重试|没有找到|未找到/.test(p.text))
    && (s.command === '/帮助' || s.parts.some(p => p.image)
      && s.business?.length && s.business.every(p => p.business_status === 'success')))
  const success = successful.length
  const rejected = samples.filter(s => !s.parts.length).length
  const degraded = samples.filter(s => s.command !== '/帮助' && s.parts.length && !s.parts.some(p => p.image)).length
  const values = successful.map(s => s.duration_ms)
  const result = { scenario, concurrency, count, duration_ms: duration, success, rejected, degraded,
    completed_images: imageCount-startImages, uploads: uploads-startUploads,
    successful_throughput: success*1000/duration, p50_ms: percentile(values, .5), p95_ms: percentile(values, .95),
    rejected_latency_ms: samples.filter(s => !s.parts.length).map(s => s.duration_ms),
    cold_policy: 'empty material cache, cycle seven distinct support cards; record actual upload count',
    loop_p95_ms: loop.percentile(95)/1e6, peak_reported_pending: peakPending, activity: receiver.activity(),
    telemetry: receiver.client.telemetry?.snapshot(), timings, backend, samples }
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2))
}
main().catch(error => { console.error(error); process.exitCode = 1 }).finally(async () => {
  // The fixture bot was never started; dispose the receiver's timers and client only.
  await receiver.dispose(); await fork.dispose()
})
