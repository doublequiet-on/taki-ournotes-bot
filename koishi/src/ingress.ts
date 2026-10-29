// L3: Raw QQ events -> verified identity + original Session; no query/data access.
// Pos: Koishi transport probe. Effects: adapter decoding only, never connects/sends.
import { createHash } from 'node:crypto'
import { adaptSession, QQ, QQBot } from '@satorijs/adapter-qq'
import type { Session } from 'koishi'

export type ConversationKind = 'group' | 'c2c'
export interface Identity { instanceId: string; appId: string; selfId: string }
export interface ImageAttachment {
  id: string
  url: string
  content_type: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'
  size?: number
  width?: number
  height?: number
}
export interface Inbound {
  requestId: string
  instanceId: string
  appId: string
  platform: 'qq'
  kind: ConversationKind
  conversationId: string
  userId: string
  messageId: string
  timestampMs: number
  body: string
  senderName?: string
  attachments?: ImageAttachment[]
  trigger?: 'command' | 'ask' | 'quoted_reply' | 'observation' | 'proactive'
  proactiveId?: string
  reference?: { message_id:string; adapter_profile_version:string } | null
}
export type IngressResult =
  | { status: 'accepted'; inbound: Inbound; session: Session }
  | { status: 'ignored'; reason: string }
  | { status: 'rejected'; reason: string }

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined
}
function id(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 256
    && value.trim() === value && !/[\u0000-\u001f\u007f]/u.test(value)
}
function pair(current: unknown, legacy: unknown): string | undefined {
  if (current !== undefined && !id(current) || legacy !== undefined && !id(legacy)) return
  if (current !== undefined && legacy !== undefined && current !== legacy) return
  return (current ?? legacy) as string | undefined
}
function digest(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex')
}

const imageTypes = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])
/** Normalize only raw QQ attachments. A text URL is never promoted to an image. */
function imageAttachments(value: unknown, messageId: string): ImageAttachment[] | undefined {
  if (value === undefined) return []
  if (!Array.isArray(value) || value.length > 3) return
  const result: ImageAttachment[] = []
  for (let index = 0; index < value.length; index++) {
    const attachment = record(value[index])
    if (!attachment || typeof attachment.content_type !== 'string') return
    // Non-image files are not model image input.
    if (!attachment.content_type.startsWith('image/')) continue
    if (!imageTypes.has(attachment.content_type) || typeof attachment.url !== 'string'
        || attachment.url.length > 8192 || /[\s\u0000-\u001f\u007f]/u.test(attachment.url)) return
    let url: URL
    try { url = new URL(attachment.url) } catch { return }
    if (url.protocol !== 'https:' || url.hostname !== 'multimedia.nt.qq.com.cn' || url.port
        || url.username || url.password || url.hash || url.pathname !== '/download' || !url.search) return
    if (attachment.id !== undefined && !id(attachment.id)) return
    // Actual QQ payloads can omit id. This is a message-local transport id, not a new source fact.
    const normalized: ImageAttachment = {
      id: attachment.id as string | undefined ?? 'attachment-' + digest([messageId, index]).slice(0, 32),
      url: attachment.url, content_type: attachment.content_type as ImageAttachment['content_type'],
    }
    for (const key of ['size', 'width', 'height'] as const) {
      const number = attachment[key]
      if (number === undefined) continue
      if (typeof number !== 'number' || !Number.isSafeInteger(number) || number <= 0
          || number > (key === 'size' ? 6 * 1024 * 1024 : 8192)) return
      normalized[key] = number
    }
    if (normalized.width && normalized.height && normalized.width * normalized.height > 16_000_000) return
    result.push(normalized)
  }
  if (new Set(result.map(item => item.id)).size !== result.length
      || result.reduce((total, item) => total + (item.size || 0), 0) > 12 * 1024 * 1024) return
  return result
}

/** Recognizer comes from the business entry manifest; do not duplicate query parsing. */
export async function decodeIncoming(
  bot: QQBot, raw: unknown, identity: Identity, recognizes: (body: string) => boolean,
  observe = false,
): Promise<IngressResult> {
  if (![identity.instanceId, identity.appId, identity.selfId].every(id)) {
    throw new Error('invalid configured identity')
  }
  if (bot.config.id !== identity.appId || bot.selfId !== identity.selfId) {
    return { status: 'rejected', reason: 'bot_identity_mismatch' }
  }
  const envelope = record(raw)
  const type = envelope?.t
  if (!['GROUP_AT_MESSAGE_CREATE', 'GROUP_MESSAGE_CREATE', 'C2C_MESSAGE_CREATE'].includes(type as string)) {
    return { status: 'ignored', reason: 'other_event' }
  }
  const data = record(envelope?.d)
  const author = record(data?.author)
  if (!data || !author) return { status: 'rejected', reason: 'missing_identity' }
  if (author.bot === true) return { status: 'ignored', reason: 'bot_message' }
  const kind = type === 'C2C_MESSAGE_CREATE' ? 'c2c' : 'group'
  const userId = pair(author.id, kind === 'group' ? author.member_openid : author.user_openid)
  const conversationId = kind === 'c2c' ? userId : pair(data.group_id, data.group_openid)
  if (!userId || !conversationId || !id(data.id)) {
    return { status: 'rejected', reason: 'missing_or_conflicting_identity' }
  }
  if(userId===identity.selfId || userId===identity.appId)return {status:'ignored',reason:'bot_message'}
  if (typeof data.content !== 'string' || Buffer.byteLength(data.content) > 16_384) {
    return { status: 'rejected', reason: 'invalid_content' }
  }
  // This candidate profile accepts timestamp strings with explicit timezone only.
  if (typeof data.timestamp !== 'string' || !/(?:Z|[+-]\d\d:\d\d)$/u.test(data.timestamp)) {
    return { status: 'rejected', reason: 'invalid_timestamp' }
  }
  const timestampMs = Date.parse(data.timestamp)
  if (!Number.isFinite(timestampMs)) return { status: 'rejected', reason: 'invalid_timestamp' }
  const mentions = Array.isArray(data.mentions) ? data.mentions.map(record) : []
  const tags = [...data.content.matchAll(/<@!?(\w+)>/gu)].map(match => match[1])
  const addressed = type === 'GROUP_AT_MESSAGE_CREATE' || kind === 'c2c'
    || mentions.some(mention => mention?.is_you === true)
    || tags.includes(identity.appId) || tags.includes(identity.selfId)
  if (!addressed && !observe) return { status: 'ignored', reason: 'not_addressed' }
  // Read raw plain text, not Koishi's escaped element serialization.
  const body = (addressed?data.content.replace(/<@!?\w+>/gu,''):data.content).trim()
  const attachments = imageAttachments(data.attachments, data.id)
  if (!attachments) return { status: 'rejected', reason: 'invalid_attachments' }
  if (!attachments.length && (!body || addressed && !recognizes(body))) return { status: 'ignored', reason: 'not_taki' }
  const canonical = {
    ...envelope,
    d: { ...data, author: { ...author, id: userId }, ...(kind === 'group' ? { group_id: conversationId } : {}) },
  }
  // Narrow wire compatibility only. The original object is never mutated.
  const session = await adaptSession(bot, canonical as unknown as QQ.DispatchPayload) as unknown as Session
  if (!session || session.channelId !== conversationId || session.userId !== userId
      || session.messageId !== data.id || session.timestamp !== timestampMs
      || Boolean(session.isDirect) !== (kind === 'c2c')) {
    return { status: 'rejected', reason: 'adapter_identity_mismatch' }
  }
  const requestId = digest([identity.instanceId, identity.appId, 'qq', kind, conversationId, userId, data.id])
  return {
    status: 'accepted', session,
    inbound: { requestId, instanceId: identity.instanceId, appId: identity.appId, platform: 'qq',
      kind, conversationId, userId, messageId: data.id, timestampMs, body,
      ...(!addressed ? {trigger:'observation' as const} : {}),
      ...(id(author.username) ? {senderName:author.username} : {}),
      ...(attachments.length ? {attachments} : {}) },
  }
}

export interface GroupEvent {
  eventId: string
  instanceId: string
  appId: string
  groupId: string
  kind: 'add' | 'remove' | 'allow' | 'reject'
  timestampSeconds: number
}
/** Internal group events use seconds in the declared fixture profile (live gate open). */
export function decodeGroupEvent(raw: unknown, identity: Identity): GroupEvent | undefined {
  const envelope = record(raw), data = record(envelope?.d)
  const kinds: Record<string, GroupEvent['kind']> = {
    GROUP_ADD_ROBOT: 'add', GROUP_DEL_ROBOT: 'remove', GROUP_MSG_RECEIVE: 'allow', GROUP_MSG_REJECT: 'reject',
  }
  const kind = typeof envelope?.t === 'string' && Object.hasOwn(kinds, envelope.t) ? kinds[envelope.t] : undefined
  if (!kind || !data || !id(data.group_openid) || typeof data.timestamp !== 'number'
      || !Number.isFinite(data.timestamp) || data.timestamp < 0 || data.timestamp >= 100_000_000_000) return
  return { eventId: id(envelope?.id) ? envelope.id : digest([identity.appId, data.group_openid, kind, data.timestamp]),
    instanceId: identity.instanceId, appId: identity.appId, groupId: data.group_openid,
    kind, timestampSeconds: data.timestamp }
}
