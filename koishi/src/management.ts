// Local-only management state. No QQ messages, credentials or business database are stored here.
import { mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs'
import { join, isAbsolute } from 'node:path'
import type { Inbound } from './ingress'

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
  private settings: ManagementSettings
  private revision = 0
  private events: { time: string; event: string }[] = []
  private file: string
  constructor(directory: string) {
    if (!isAbsolute(directory)) throw Error('console state directory must be absolute')
    mkdirSync(directory, { recursive: true, mode: 0o700 })
    this.file = join(directory, 'settings.json')
    try { this.settings = validateSettings(JSON.parse(readFileSync(this.file, 'utf8'))) }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      this.settings = structuredClone(defaultSettings)
    }
    this.record('管理设置已加载')
  }
  snapshot() { return { settings: structuredClone(this.settings), revision: this.revision, events: [...this.events] } }
  record(event: string) {
    this.events.push({ time: new Date().toISOString(), event })
    if (this.events.length > 100) this.events.shift()
  }
  update(value: unknown, revision: number) {
    if (revision !== this.revision) throw Error('设置已更新，请刷新后重试')
    const next = validateSettings(value)
    // A failed write must not change live admission. Atomic replacement also works on Windows.
    const temporary = this.file + '.tmp'
    writeFileSync(temporary, JSON.stringify(next, null, 2) + '\n', { mode: 0o600 })
    renameSync(temporary, this.file)
    this.settings = next
    this.revision++
    this.record('管理员保存了接收范围与通知设置')
    return this.snapshot()
  }
  accepts(input: Inbound): boolean {
    const s = this.settings
    return s.queriesEnabled && (input.kind === 'c2c' ? s.privateEnabled : this.groupAllowed(input.conversationId))
  }
  groupAllowed(id: string): boolean {
    const s = this.settings
    return s.groupsEnabled && (!s.groupAllowlist.length || s.groupAllowlist.includes(id))
  }
  noticesAllowed(id?: string): boolean {
    return this.settings.notificationsEnabled && this.settings.groupsEnabled && (id === undefined || this.groupAllowed(id))
  }
  noticeGroups(): string[] { return [...this.settings.groupAllowlist] }
}
