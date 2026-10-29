// L3: raw-event sequence ownership -> Koishi scope/permissions -> Python -> QQDelivery.
// Effects: bounded, ephemeral observation metadata in authenticated activity(); no message content.
import { createHash, randomUUID } from 'node:crypto'
import { Context, Session, Channel, User } from 'koishi'
import type { QQBot } from '@satorijs/adapter-qq'
import { BackendClient, BackendError, Plan, ModelPermission } from './backend'
import { decodeIncoming, Identity, Inbound } from './ingress'
import { QQDelivery, QQInternal } from './delivery'
import { Notices } from './notices'
import { MaterialStore, materialRequest } from './materials'
import type { Management } from './management'

export interface ReceiverConfig extends Identity {
  management?: Management
  materialStateDir?: string
  notifications?: boolean; backendUrl: string; tokenEnv: string; permissions: string[]; maxPending: number
}
interface Raw { raw: unknown; seq: number; at: number; fingerprint: string; bytes:number; conflict?: boolean }
interface Observation {
  key:string; input:Inbound; session:Session; seq:number; fingerprint:string; bytes:number; attempt:number
  policyVersion:string
  cancelled:boolean; accepted:boolean; timer?:ReturnType<typeof setTimeout>; promise?:Promise<void>
}
interface ObservationDiagnostic {
  at: number; correlation: string; event_type: 'GROUP_MESSAGE_CREATE'; trigger: 'observation'; stage: 'decoded' | 'backend_response' | 'backend_error'; observed: boolean | null
}
interface Job { input: Inbound; promise: Promise<void>; delivery?: QQDelivery; plan?: Plan; session:Session; permissionAttempts:Set<string>; proactive?:Observation }
interface Flight { source: Raw; promise: Promise<boolean>; accepted: boolean }
const admissionOutcomes = ['duplicate', 'conflict', 'capacity_rejected_unclassified', 'decode_ignored', 'decode_rejected',
  'scope_denied', 'unrecognized', 'permission_denied', 'management_denied', 'disposed', 'accepted',
  'ledger_capacity', 'context_capacity', 'epoch_capacity', 'plan_capacity', 'byte_capacity',
  'backend_unavailable', 'backend_rejected', 'integration_error'] as const
type AdmissionOutcome = typeof admissionOutcomes[number]
function admissionFailure(error: unknown): AdmissionOutcome {
  if (!(error instanceof BackendError)) return 'integration_error'
  switch (error.code) {
    case 'ledger_capacity': case 'context_capacity': case 'epoch_capacity': case 'plan_capacity': case 'byte_capacity':
      return error.code
    default:
      if (error.code === 'startup_cancelled') return 'disposed'
      if (error.status >= 500 || ['backend_transport', 'startup_timeout', 'backend_not_ready', 'not_ready'].includes(error.code)) return 'backend_unavailable'
      return error.status >= 400 || error.code === 'incompatible_backend' ? 'backend_rejected' : 'integration_error'
  }
}

export class Receiver {
  readonly epoch = randomUUID()
  readonly client: BackendClient
  private active = true
  private notices?: Notices
  private materials?: MaterialStore
  health() { return { active:this.active && (this.notices?.healthy() ?? true), handshake:!!this.client.readiness, epoch:this.client.epoch, backend:this.client.readiness } }
  activity() {
    const accepted = [...this.flights.values()].filter(flight => flight.accepted).length
      + [...this.observationTasks].filter(task=>task.accepted).length
    const total=this.flights.size+this.observationTasks.size
    return { accepted_inflight:accepted, admission_inflight:total-accepted,
      pending_total:total, capacity:this.config.maxPending, epoch:this.epoch,
      started_at:this.startedAt, startup_error:this.startupError ?? null, admission_attempts:{...this.admissionCounts},
      materials:this.materials?.diagnostics() ?? null, observation_pending:this.observations.size,
      observation_bytes:this.observationBytes(),
      observation_diagnostics:this.observationDiagnostics() }
  }
  private readonly diagnosticSalt = randomUUID()
  private diagnosticRows: ObservationDiagnostic[] = []
  private observationDiagnostics(): ObservationDiagnostic[] {
    const cutoff=Date.now()/1000-900
    this.diagnosticRows=this.diagnosticRows.filter(row=>row.at>cutoff)
    return this.diagnosticRows.map(row=>({...row}))
  }
  private diagnoseObservation(input:Inbound,stage:ObservationDiagnostic['stage'],observed:boolean|null=null):void {
    this.observationDiagnostics()
    this.diagnosticRows.push({at:Date.now()/1000,event_type:'GROUP_MESSAGE_CREATE',trigger:'observation',
      correlation:createHash('sha256').update(this.diagnosticSalt+'\0'+input.requestId).digest('hex').slice(0,24),
      stage,observed})
    if(this.diagnosticRows.length>64)this.diagnosticRows.shift()
  }
  private readonly startedAt = Date.now()/1000
  private startupError?: AdmissionOutcome
  private admissionCounts = Object.fromEntries(admissionOutcomes.map(outcome => [outcome, 0])) as Record<AdmissionOutcome,number>
  private observed(outcome: AdmissionOutcome): void {
    this.admissionCounts[outcome] = Math.min(Number.MAX_SAFE_INTEGER, this.admissionCounts[outcome]+1)
  }
  private sequence = 0
  private raw = new Map<string, Raw>()
  private jobs = new Map<string, Job>()
  private flights = new Map<string, Flight>()
  private observations = new Map<string, Observation>()
  private observationTasks = new Set<Observation>()
  private groupArrivals = new Map<string,{seq:number;at:number}>()
  private admission: Promise<void> = Promise.resolve()
  readonly ready: Promise<void>
  private log: ReturnType<Context['logger']>
  constructor(private ctx: Context, readonly config: ReceiverConfig, client?: BackendClient) {
    this.client = client ?? new BackendClient(config.backendUrl, process.env[config.tokenEnv] ?? '', config.instanceId, this.epoch)
    this.log = ctx.logger('taki')
    this.ready = this.client.initialize()
    // Mark rejection observed even when no message arrives; ingress still sees the same rejection.
    void this.ready.catch(error => {
      this.startupError = admissionFailure(error)
      this.log.warn('backend initialization failed; admission disabled')
    })
    ctx.effect(() => ctx.root.on('internal/session', session => this.capture(session as Session)))
    ctx.effect(() => ctx.root.middleware(async (session, next) => {
      if (!await this.receive(session)) return next()
    }, true))
    if (config.notifications) this.notices=new Notices(ctx,this.client,config,this.ready,
      id => config.management?.noticesAllowed(id) ?? true, () => config.management?.noticeScope() ?? {kind:'all',exclude_group_ids:[]})
    ctx.on('dispose', () => this.dispose())
    const sweep=setInterval(()=>this.sweep(),1000)
    sweep.unref()
    ctx.on('dispose',()=>clearInterval(sweep))
  }
  private sweep():void {
    this.observationDiagnostics()
    const now=Date.now()
    for(const [key,value] of this.raw) if(now-value.at>=298000)this.raw.delete(key)
    for(const [key,value] of this.groupArrivals)if(now-value.at>=298000)this.groupArrivals.delete(key)
    for(const task of this.observations.values()) if(!this.observationAlive(task))this.cancelObservation(task.key)
  }
  private groupKey(group:string):string {
    return JSON.stringify([this.config.instanceId,this.config.appId,'qq','group',group])
  }
  private observationBytes():number {
    const sources=new Set([...this.raw.values(),...[...this.flights.values()].map(flight=>flight.source)])
    return [...sources].reduce((total,item)=>total+item.bytes,0)
      + [...this.observationTasks].reduce((total,item)=>total+item.bytes,0)
  }
  private cancelObservation(key:string):void {
    const task=this.observations.get(key)
    if(!task)return
    task.cancelled=true
    if(task.timer)clearTimeout(task.timer)
    task.timer=undefined
    this.observations.delete(key)
    for(const job of this.jobs.values())if(job.proactive===task)job.delivery?.dispose()
    if(!task.promise)this.observationTasks.delete(task)
  }
  private observationAlive(task:Observation):boolean {
    const management=this.config.management
    return this.active && !task.cancelled && this.observations.get(task.key)===task
      && this.groupArrivals.get(task.key)?.seq===task.seq
      && Date.now()<task.input.timestampMs+298000 && (management?.accepts(task.input)??true)
      && (management?.policySnapshot(task.input).natural_query??'model')==='model'
      && this.policyVersion(task.input)===task.policyVersion
  }
  private policy(input:Inbound) {
    return this.config.management?.policySnapshot(input)
      ?? {natural_query:'model' as const,epoch:this.epoch,global_revision:0,group_revision:0}
  }
  private policyVersion(input:Inbound):string {
    const policy=this.policy(input)
    return JSON.stringify([policy.epoch,policy.global_revision,policy.group_revision,policy.natural_query])
  }
  private scheduleObservation(task:Observation,delayMs:number):void {
    task.timer=setTimeout(()=>{
      task.timer=undefined
      task.promise=this.runObservation(task).finally(()=>{
        task.promise=undefined
        if(task.cancelled)this.observationTasks.delete(task)
      })
    },delayMs)
    task.timer.unref()
  }
  private queueObservation(input:Inbound,session:Session,source:Raw,delayMs:number):void {
    const key=this.groupKey(input.conversationId)
    if(this.groupArrivals.get(key)?.seq!==source.seq)return
    this.cancelObservation(key)
    const bytes=Buffer.byteLength(JSON.stringify(input))+Buffer.byteLength(JSON.stringify(session.event))
    // The current ingress flight releases its slot on return; the timer takes it.
    if(this.flights.size+this.observationTasks.size-1>=this.config.maxPending
        || this.observationBytes()+bytes>8*1024*1024)return
    const task:Observation={key,input,session,seq:source.seq,fingerprint:source.fingerprint,bytes,
      attempt:0,cancelled:false,accepted:false,policyVersion:this.policyVersion(input)}
    this.observations.set(key,task)
    this.observationTasks.add(task)
    this.scheduleObservation(task,delayMs)
  }
  private async runObservation(task:Observation):Promise<void> {
    let again=false
    try {
      if(!this.observationAlive(task) || task.attempt>=3)return
      const current=this.ctx.reflect.trace(task.session.bot.session({...task.session.event}))
      if(!await this.permitted(current) || !this.observationAlive(task))return
      const proactiveId=randomUUID().replaceAll('-','')
      const input:Inbound={...task.input,trigger:'proactive',proactiveId,
        requestId:createHash('sha256').update(JSON.stringify([task.input.instanceId,task.input.appId,'qq','group',
          task.input.conversationId,task.input.userId,task.input.messageId,proactiveId])).digest('hex')}
      task.attempt++
      const candidate=await this.client.chatCandidate(input,task.seq,this.policy(input))
      if(!candidate.candidate)return
      const job:Job={input,plan:candidate.plan,session:task.session,permissionAttempts:new Set(),
        promise:Promise.resolve(),proactive:task}
      this.jobs.set(input.requestId,job)
      task.accepted=true
      job.promise=this.deliver(job,task.session.bot as unknown as QQBot)
      await job.promise
      task.accepted=false
      again=this.observationAlive(task) && task.attempt<3 && job.plan?.status==='finished'
    } catch(error) {
      this.log.warn('proactive candidate stopped: %s',error instanceof BackendError?error.code:'integration_error')
    } finally {
      task.accepted=false
      if(again)this.scheduleObservation(task,5000)
      else if(this.observations.get(task.key)===task)this.cancelObservation(task.key)
    }
  }
  capture(session: Session): void {
    if (!this.active || session.platform !== 'qq' || session.bot.config.id !== this.config.appId || session.type !== 'internal') return
    const types: Record<string, string> = { 'qq/group-at-message-create': 'GROUP_AT_MESSAGE_CREATE',
      'qq/group-message-create': 'GROUP_MESSAGE_CREATE', 'qq/c2c-message-create': 'C2C_MESSAGE_CREATE' }
    const type = types[session.event._type ?? ''], data = session.event._data as Record<string, unknown> | undefined
    if (!type || !data || typeof data.id !== 'string' || !data.id || data.id.length>256 || typeof data.content !== 'string'
        || Buffer.byteLength(data.content) > 16384 || Buffer.byteLength(JSON.stringify(data)) > 32768) return
    if(data.author && typeof data.author==='object' && (data.author as {bot?:unknown}).bot===true)return
    if(data.author && typeof data.author==='object') {
      const author=data.author as Record<string,unknown>
      if([author.id,author.member_openid,author.user_openid].some(value=>value===this.config.selfId || value===this.config.appId))return
    }
    const now = Date.now()
    for (const [key, value] of this.raw) if (now - value.at > 298000) this.raw.delete(key)
    const fingerprint = JSON.stringify([type, data.content, data.timestamp, data.author, data.group_id, data.group_openid, data.mentions, data.attachments])
    const existing = this.raw.get(data.id)
    if (existing) {
      // Do not let a conflicting raw event replace the first identity/body while decode is waiting.
      if (existing.fingerprint !== fingerprint) existing.conflict = true
      return
    }
    const group=data.group_id??data.group_openid
    const seq=this.sequence+1
    if(type!=='C2C_MESSAGE_CREATE' && typeof group==='string' && group.length>0 && group.length<=256) {
      const current=this.observations.get(this.groupKey(group))
      if(current?.input.messageId===data.id && current.fingerprint===fingerprint)return
      // Invalidate locally at raw arrival, before permissions/HTTP can reorder.
      this.cancelObservation(this.groupKey(group))
      this.groupArrivals.delete(this.groupKey(group))
      this.groupArrivals.set(this.groupKey(group),{seq,at:now})
      while(this.groupArrivals.size>512) {
        const oldest=this.groupArrivals.keys().next().value!
        this.cancelObservation(oldest)
        this.groupArrivals.delete(oldest)
      }
    }
    this.sequence=seq
    const bytes=Buffer.byteLength(JSON.stringify(data))+Buffer.byteLength(fingerprint)
    if(this.raw.size>=512 || this.observationBytes()+bytes>8*1024*1024)return
    // Assigned at raw arrival, before adapter async work, permissions or HTTP can reorder.
    this.raw.set(data.id, { raw: { t: type, d: structuredClone(data) }, seq, at: now, fingerprint,bytes })
  }
  private async permitted(session: Session): Promise<boolean> {
    if (!this.ctx.filter(session)) return false
    if (this.ctx.database) {
      if (!session.isDirect) {
        const fields = new Set<Channel.Field>(['flag', 'assignee', 'guildId', 'permissions', 'locales'])
        this.ctx.emit('before-attach-channel', session, fields)
        const channel = await session.observeChannel(fields)
        if (session.guildId) channel.guildId = session.guildId
        if (await this.ctx.serial(session, 'attach-channel', session)) return false
        if (channel.flag & Channel.Flag.ignore || channel.assignee !== session.selfId && !session.stripped.atSelf) return false
      }
      const fields = new Set<User.Field>(['id', 'flag', 'authority', 'permissions', 'locales'])
      this.ctx.emit('before-attach-user', session, fields)
      const user = await session.observeUser(fields)
      if (await this.ctx.serial(session, 'attach-user', session)) return false
      if (user.flag & User.Flag.ignore) return false
    }
    return this.ctx.permissions.test(this.config.permissions, session)
  }
  async receive(session: Session): Promise<boolean> {
    if (!this.active || session.platform !== 'qq' || session.bot.config.id !== this.config.appId) return false
    const source = this.raw.get(session.messageId ?? '')
    if (!source) return false // never reconstruct identity from an ordinary Koishi message
    if (source.conflict) { this.observed('conflict'); return true }
    const key = session.messageId!, existing = this.flights.get(key)
    if (existing) {
      if (existing.source.fingerprint !== source.fingerprint) { this.observed('conflict'); return true }
      this.observed('duplicate')
      return existing.promise
    }
    if (this.flights.size+this.observationTasks.size >= this.config.maxPending) { this.observed('capacity_rejected_unclassified'); return true }
    // Reserve once before the first await. Retries join this owner even while admission is waiting.
    const flight: Flight = { source, promise:Promise.resolve(true), accepted:false }
    this.flights.set(key, flight)
    flight.promise = this.receiveOwned(session, key, flight)
    return flight.promise
  }
  private async receiveOwned(session: Session, key: string, flight: Flight): Promise<boolean> {
    const source = flight.source
    const previous = this.admission
    let release!: () => void
    this.admission = new Promise<void>(resolve => { release = resolve })
    let input: Inbound | undefined
    try {
      // Serialize recognition/permissions/admission only; preparation and delivery run independently.
      await previous
      if (!this.active) { this.observed('disposed'); return true }
      if (source.conflict) { this.observed('conflict'); return true }
      const decoded = await decodeIncoming(session.bot as unknown as QQBot, source.raw, this.config, () => true,true)
      if (decoded.status !== 'accepted') { this.observed(decoded.status === 'ignored' ? 'decode_ignored' : 'decode_rejected'); return false }
      input=decoded.inbound
      if(input.trigger==='observation')this.diagnoseObservation(input,'decoded')
      const canonical = this.ctx.reflect.trace(decoded.session)
      if (!this.ctx.filter(canonical)) { this.observed('scope_denied'); return false }
      await this.ready
      if (!this.active) { this.observed('disposed'); return true }
      if (decoded.inbound.trigger!=='observation' && (!decoded.inbound.attachments?.length || /^[/／]/u.test(decoded.inbound.body))
          && !await this.client.recognizes(decoded.inbound.body)) { this.observed('unrecognized'); return false }
      if (!await this.permitted(canonical)) { this.observed('permission_denied'); return input.trigger!=='observation' }
      if (!this.active) { this.observed('disposed'); return true }
      if (this.config.management && !this.config.management.accepts(decoded.inbound)) { this.observed('management_denied'); return input.trigger!=='observation' }
      if (source.conflict) { this.observed('conflict'); return true }
      input = decoded.inbound
      if(input.trigger==='observation') {
        const policy=this.policy(input)
        if(policy.natural_query!=='model')return false
        const version=this.policyVersion(input)
        let observed
        try {
          observed=await this.client.chatObservation(input,source.seq,policy)
        } catch(error) {
          this.diagnoseObservation(input,'backend_error')
          throw error
        }
        this.diagnoseObservation(input,'backend_response',observed.observed)
        if(observed.observed && this.active && !source.conflict
            && (this.config.management?.accepts(input)??true) && this.policyVersion(input)===version) {
          this.queueObservation(input,canonical,source,observed.delay_ms)
        }
        return false // Observation never consumes another plugin's ordinary message.
      }
      const existing = this.jobs.get(input.requestId)
      if (existing) {
        if (JSON.stringify(existing.input) !== JSON.stringify(input)) throw new BackendError('local_request_conflict')
        release()
        await existing.promise
        return true
      }
      const plan = await this.client.submit(input, source.seq, this.config.management?.policySnapshot(input))
      const job: Job = { input, plan, promise: Promise.resolve(), session:canonical, permissionAttempts:new Set() }
      this.jobs.set(input.requestId, job)
      flight.accepted = true
      this.observed('accepted')
      job.promise = this.deliver(job, session.bot as unknown as QQBot)
      release()
      await job.promise
      return true
    } catch (error) {
      this.observed(admissionFailure(error))
      this.log.warn('request stopped: %s', error instanceof BackendError ? error.code : 'integration_error')
      return input?.trigger!=='observation'
    } finally {
      if (this.raw.get(key) === source) this.raw.delete(key)
      release()
      if (this.flights.get(key) === flight) this.flights.delete(key)
    }
  }
  private validate(plan: Plan, input: Inbound): void {
    if (plan.request_id !== input.requestId || !/^[a-f0-9]{32}$/.test(plan.delivery_id) || plan.plan_version !== 1
        || typeof plan.send_allowed !== 'boolean' || !Number.isFinite(plan.expires_at)
        || !Array.isArray(plan.parts) || plan.parts.length > 5
        || plan.parts.some((p, i) => p.position !== i || !new RegExp('^' + i + '(:f)?$').test(p.id))) {
      throw new BackendError('plan_shape')
    }
  }
  private async deliver(job: Job, bot: QQBot): Promise<void> {
    let completed = false
    let hint: Promise<boolean> | undefined
    const alive = () => this.active && Date.now() < job.input.timestampMs + 298000
      && (!job.proactive || this.observationAlive(job.proactive))
    try {
      if (!job.plan) throw new BackendError('missing_admitted_plan')
      this.validate(job.plan, job.input)
      if(job.proactive && job.plan.status==='finished' && !job.plan.parts.length) {completed=true;return}
      if(job.proactive && job.plan.processing_hint)throw new BackendError('proactive_hint')
      // The same delivery owner counts the slow hint and final answer together.
      const api = Object.create(bot.internal) as QQInternal & { http: () => typeof bot.http }
      api.http = () => bot.http.extend({ timeout: 15000 })
      if (!this.materials && this.config.materialStateDir) {
        this.materials = new MaterialStore(this.config.materialStateDir, this.config.appId,
          (method, path, data) => materialRequest(()=>bot.http(path, { method, data, timeout: 15000 })))
      }
      const events=job.proactive ? {
        intent:async(attempt:Parameters<BackendClient['intent']>[0])=>
          alive() && await this.currentProactivePermission(job) && alive()?this.client.intent(attempt):'denied' as const,
        acknowledge:this.client.acknowledge.bind(this.client),observe:this.client.observe.bind(this.client),
      }:this.client
      job.delivery = new QQDelivery(api, job.input, job.plan.delivery_id, this.client.epoch, events, alive,
        undefined, undefined, this.materials)
      while (alive() && job.plan.status === 'preparing') {
        if(job.plan.pending_model_permission) await this.authorizeModel(job,job.plan.pending_model_permission)
        if(job.plan.processing_hint && job.plan.send_allowed && !hint) {
          const part=job.plan.parts[0]
          if(job.plan.parts.length!==1 || part.id!=='0' || part.status!=='pending' || part.kind!=='text' || typeof part.text!=='string') return
          const delivery=job.delivery, text=part.text
          // Keep polling permits while QQ is uploading/sending/acknowledging.
          hint=(async()=>{
            try {
              const result=await delivery.deliver('0',0,{kind:'text',text})
              if(delivery.acknowledgementPending && !await delivery.retryAcknowledgement()) return false
              return result.status==='success'
            } catch { return false }
          })()
        }
        await new Promise(resolve => setTimeout(resolve, 100))
        job.plan = await this.client.lookup(job.input.requestId)
        this.validate(job.plan, job.input)
      }
      if(hint) {
        if(!await hint) return
        job.plan=await this.client.lookup(job.input.requestId)
        this.validate(job.plan,job.input)
      }
      if (!alive() || !job.plan.send_allowed) return
      // Keep installed official methods and live token headers, but bound each transport call.
      // A per-delivery facade avoids changing other plugins' HTTP configuration.
      for (let index = 0; index < job.plan.parts.length; index++) {
        let part = job.plan.parts[index]
        if(index===0 && hint && job.plan.processing_hint && part.status==='success') continue
        // A resumed plan with any prior attempt belongs to the original sender; never rebuild sequence state.
        if (!alive() || part.status !== 'pending') return
        if (part.kind !== 'text' && part.kind !== 'image' || typeof part.text !== 'string') throw new BackendError('part_shape')
        const payload = part.kind === 'text' ? { kind: 'text' as const, text: part.text }
          : { kind: 'image' as const, bytes: await this.client.image(job.plan, part), caption: part.text }
        let result = await job.delivery.deliver(part.id, index, payload)
        if (result.status === 'upload_failed') {
          const fallback: Plan = await this.client.event(job.plan, 'upload_failed', part.id)
          this.validate(fallback, job.input)
          job.plan = fallback
          if (!fallback.send_allowed) return
          part = fallback.parts[index]
          if (part.mode !== 'fallback' || part.kind !== 'text' || typeof part.text !== 'string') return
          result = await job.delivery.deliver(part.id, index, { kind: 'text', text: part.text })
        }
        if (job.delivery.acknowledgementPending && !await job.delivery.retryAcknowledgement()) return
        if (result.status !== 'success') return
        job.plan = await this.client.lookup(job.input.requestId)
        this.validate(job.plan, job.input)
        if (!job.plan.send_allowed) { completed = job.plan.status === 'finished'; return }
      }
      completed = true
    } finally {
      job.delivery?.dispose()
      if (job.plan && !completed) {
        try { await this.client.event(job.plan, 'aborted') }
        catch { this.log.warn('delivery close unconfirmed; backend expiry retains unresolved intent') }
      }
      // Polling may fail while the hint still owns a send/ACK. Stop new sends
      // and close the plan first, then retain its reservation until facts settle.
      if (hint) await hint
      this.jobs.delete(job.input.requestId)
    }
  }
  private async currentProactivePermission(job:Job):Promise<boolean> {
    if(!job.proactive || !this.observationAlive(job.proactive))return false
    const current=this.ctx.reflect.trace(job.session.bot.session({...job.session.event}))
    return await this.permitted(current) && this.observationAlive(job.proactive)
  }
  private async authorizeModel(job:Job, challenge:ModelPermission):Promise<void> {
    const input=job.input
    const scope={instance_id:input.instanceId,app_id:input.appId,platform:input.platform,
      kind:input.kind,conversation_id:input.conversationId}
    if(challenge.request_id!==input.requestId || challenge.gateway_epoch!==this.epoch
      || challenge.backend_epoch!==this.client.readiness?.backend_epoch || challenge.user_id!==input.userId
      || !/^[a-f0-9]{32}$/.test(challenge.attempt_id) || !/^[a-f0-9]{32}$/.test(challenge.nonce)
      || !Number.isInteger(challenge.remaining_ms) || challenge.remaining_ms<=0 || challenge.remaining_ms>3000
      || !challenge.scope || Object.keys(challenge.scope).length!==Object.keys(scope).length
      || Object.entries(scope).some(([k,v])=>challenge.scope[k]!==v)) throw new BackendError('model_permission_binding')
    if(job.permissionAttempts.has(challenge.attempt_id)) return
    job.permissionAttempts.add(challenge.attempt_id)
    if(job.permissionAttempts.size>6) throw new BackendError('model_attempt_limit')
    // Re-evaluate current plugin scope/platform permission and current management state.
    // observeUser reuses the old Session's loaded fields. A new Session forces a
    // fresh database read; reusing the admitted Session would miss a revoked user.
    const current=this.ctx.reflect.trace(job.session.bot.session({...job.session.event}))
    const permitted=this.active && await this.permitted(current)
    const management=this.config.management
    const policy=management?.policySnapshot(input) ?? {natural_query:'model' as const,epoch:this.epoch,global_revision:0,group_revision:0}
    const allowed=permitted && this.active && (!job.proactive || this.observationAlive(job.proactive))
      && (management?.accepts(input) ?? true) && policy.natural_query==='model'
    await this.client.modelPermission(challenge,policy,allowed)
  }
  private closing?: Promise<void>
  dispose(): Promise<void> {
    return this.closing ??= this.drain()
  }
  private async drain(): Promise<void> {
    this.active = false
    this.client.deactivate()
    const noticesClosed=this.notices?.close()
    this.raw.clear()
    for(const key of this.observations.keys())this.cancelObservation(key)
    this.groupArrivals.clear()
    for (const job of this.jobs.values()) job.delivery?.dispose()
    const materialsClosed=this.materials?.close()
    // Retire before waiting for in-flight sends. Their late ACK may record facts, never context.
    try { await this.ready; await this.client.retire() }
    catch { this.log.warn('gateway retirement unconfirmed; no new local sends') }
    await Promise.allSettled([...this.flights.values()].map(flight => flight.promise))
    await Promise.allSettled([...this.observationTasks].map(task=>task.promise))
    await (materialsClosed ?? this.materials?.close())
    await noticesClosed
    await this.client.telemetry?.close()
  }
}
