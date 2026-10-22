// Local-only management state. No QQ messages, credentials or business database are stored here.
import { mkdirSync, readFileSync, writeFileSync, renameSync, openSync, fsyncSync, closeSync, lstatSync } from 'node:fs'
import { randomUUID, createHash } from 'node:crypto'
import { join, isAbsolute } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import type { Inbound } from './ingress'
import { GroupPolicies, PolicySnapshot } from './group-policies'

export interface ManagementSettings {
  queriesEnabled: boolean
  privateEnabled: boolean
  groupsEnabled: boolean
  groupAllowlist: string[]
  notificationsEnabled: boolean
}
export const defaultSettings: ManagementSettings = {
  queriesEnabled: true, privateEnabled: true, groupsEnabled: true,
  groupAllowlist: [], notificationsEnabled: true,
}
export function validateSettings(value: unknown): ManagementSettings {
  const v = value as ManagementSettings
  if (!v || typeof v !== 'object' || Array.isArray(v)
      || Object.keys(v).sort().join() !== Object.keys(defaultSettings).sort().join()
      || ['queriesEnabled', 'privateEnabled', 'groupsEnabled', 'notificationsEnabled'].some(k => typeof (v as any)[k] !== 'boolean')
      || !Array.isArray(v.groupAllowlist) || v.groupAllowlist.length > 128
      || v.groupAllowlist.some(id => typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(id))
      || new Set(v.groupAllowlist).size !== v.groupAllowlist.length) throw Error('invalid management settings')
  return structuredClone(v)
}
export class Management {
  readonly groups: GroupPolicies
  private readonly epoch = randomUUID()
  private settings: ManagementSettings
  private revision = 0
  private events: { time: string; event: string }[] = []
  private file: string
  private persisted = false
  constructor(directory: string, identity?: { appId:string; instanceId:string }) {
    if (!isAbsolute(directory)) throw Error('console state directory must be absolute')
    mkdirSync(directory, { recursive: true, mode: 0o700 })
    this.file = join(directory, 'settings.json')
    this.groups = new GroupPolicies(directory,{platform:'qq',app_id:identity?.appId||'unconfigured',instance_id:identity?.instanceId||'unconfigured'})
    try { this.settings = validateSettings(JSON.parse(readFileSync(this.file, 'utf8')));this.persisted=true }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      this.settings = structuredClone(defaultSettings)
    }
    this.record('管理设置已加载')
    if(this.groups.error)this.record('群策略不可用，新群查询和通知已限制')
  }
  snapshot() { return { settings: structuredClone(this.settings), revision: this.revision,
    guard: { gateway_epoch: this.epoch, revision: this.revision }, events: [...this.events] } }
  record(event: string) {
    this.events.push({ time: new Date().toISOString(), event })
    if (this.events.length > 100) this.events.shift()
  }
  update(value: unknown, revision: number) {
    if (revision !== this.revision) throw Error('设置已更新，请刷新后重试')
    const next = validateSettings(value)
    this.assertPersistent()
    // A failed write must not change live admission. Atomic replacement also works on Windows.
    const temporary = this.file + '.tmp'
    const content = JSON.stringify(next, null, 2) + '\n'
    try {
      writeFileSync(temporary, content, { mode: 0o600 })
      const fd = openSync(temporary, 'r+'); try { fsyncSync(fd) } finally { closeSync(fd) }
      renameSync(temporary, this.file)
    } catch {
      try { if(readFileSync(this.file,'utf8')!==content)throw Error('different') }
      catch { throw Error('settings_save_unconfirmed') }
    }
    this.settings = next
    this.persisted = true
    this.revision++
    this.record('管理员保存了接收范围与通知设置')
    return this.snapshot()
  }
  assertPersistent() {
    try {
      if(!isDeepStrictEqual(validateSettings(JSON.parse(readFileSync(this.file,'utf8'))),this.settings))throw Error('settings_conflict')
    } catch(error) {
      if((error as NodeJS.ErrnoException).code!=='ENOENT'||this.persisted)throw Error('settings_conflict')
    }
  }
  persistenceTag() {
    const info=lstatSync(this.file,{bigint:true})
    if(!info.isFile()||info.isSymbolicLink())throw Error('settings_conflict')
    return {sha256:createHash('sha256').update(readFileSync(this.file)).digest('hex'),inode:String(info.ino),mtime:String(info.mtimeNs),size:String(info.size)}
  }
  assertGuard(guard:unknown) {
    const g = guard as { gateway_epoch?: string; revision?: number }
    if (!g || Object.keys(g).sort().join() !== 'gateway_epoch,revision'
        || g.gateway_epoch !== this.epoch || !Number.isSafeInteger(g.revision)
        || g.revision !== this.revision) throw Error('settings_conflict')
  }
  updateGuarded(value: unknown, guard: unknown) {
    this.assertGuard(guard)
    return this.update(value, this.revision)
  }
  accepts(input: Inbound): boolean {
    const s = this.settings
    return input.kind==='c2c'?s.queriesEnabled&&s.privateEnabled:this.groups.effective(input.conversationId,s).queries
  }
  groupAllowed(id: string): boolean {
    const s = this.settings
    return s.groupsEnabled && (!s.groupAllowlist.length || s.groupAllowlist.includes(id))
  }
  noticesAllowed(id?: string): boolean {
    return id===undefined?this.groups.noticeScope(this.settings).kind!=='none':this.groups.effective(id,this.settings).notifications
  }
  noticeGroups(): string[] { return [...this.settings.groupAllowlist] }
  noticeScope() { return this.groups.noticeScope(this.settings) }
  effective(id:string) { return this.groups.effective(id,this.settings) }
  policySnapshot(input:Inbound):PolicySnapshot {
    return Object.freeze({natural_query:input.kind==='group'?this.effective(input.conversationId).natural_query:'model',
      epoch:this.epoch,global_revision:this.revision,group_revision:this.groups.snapshot().revision})
  }
}
