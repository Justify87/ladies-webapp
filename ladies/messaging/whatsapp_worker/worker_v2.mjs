import makeWASocket, {
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  extractMessageContent,
  fetchLatestBaileysVersion,
  fetchLatestWaWebVersion,
  makeCacheableSignalKeyStore,
  proto,
  useMultiFileAuthState,
  WAMessageStubType,
} from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import pino from 'pino'
import readline from 'node:readline'
import process from 'node:process'
import { messageIdentity, providerKey, rawMessageId } from './provider_identity.mjs'

const authDir = process.env.LADIES_WHATSAPP_AUTH_DIR
if (!authDir) {
  process.stderr.write('LADIES_WHATSAPP_AUTH_DIR is missing\n')
  process.exit(2)
}

const dataRoot = path.resolve(authDir, '..')
const mediaDir = path.join(dataRoot, 'media')
const viewOnceDir = path.join(dataRoot, 'view-once')
const messageCacheDir = path.join(dataRoot, 'message-cache')
for (const directory of [mediaDir, viewOnceDir, messageCacheDir]) {
  fs.mkdirSync(directory, { recursive: true })
}

const logger = pino({ level: process.env.LADIES_BAILEYS_LOG_LEVEL || 'warn' }, pino.destination(2))
const reconnectDelays = [1000, 2000, 5000, 10000, 30000]
const WA_VERSION_CACHE_MS = 5 * 60 * 1000
const MAX_INCOMING_MEDIA = 96 * 1024 * 1024
const MAX_FETCH_HISTORY_MESSAGES = 100
const MAX_RAW_MESSAGE_CACHE = 4000
const HISTORY_BATCH_SIZE = 100
const INITIAL_SYNC_EMPTY_FALLBACK_MS = 22_000
const SERIAL_COMMANDS = new Set([
  'connect', 'disconnect', 'logout', 'send_text', 'send_media',
  'send_reaction', 'edit_message', 'delete_message', 'shutdown',
])

let reconnectAttempt = 0
let sock = null
let stopped = false
let manualDisconnect = false
let connecting = null
let localSendsInFlight = 0
let commandChain = Promise.resolve()
let rawCacheWrites = 0
let cachedWaVersion = null
let cachedWaVersionAt = 0
const socketContexts = new WeakMap()
const locallySentIds = new Map()
const localMessageIds = new Map()
const pendingOwnMessages = new Map()
const pendingMessageUpdates = []

const retryCounters = new Map()
const msgRetryCounterCache = {
  get: key => retryCounters.get(key),
  set: (key, value) => { retryCounters.set(key, value); return true },
  del: key => retryCounters.delete(key),
  flushAll: () => retryCounters.clear(),
}

const emit = event => process.stdout.write(`${JSON.stringify(event)}\n`)
const reply = (id, ok, payload = null, message = null) => emit({ type: 'response', id, ok, payload, message })
const isPhoneJid = jid => typeof jid === 'string' && jid.endsWith('@s.whatsapp.net')
const isLidJid = jid => typeof jid === 'string' && jid.endsWith('@lid')
const isDirectJid = jid => typeof jid === 'string' && jid !== 'status@broadcast' && !jid.endsWith('@g.us') && !jid.endsWith('@broadcast')

function rememberLocalSend(providerId, clientMessageId) {
  if (!providerId) return
  locallySentIds.set(providerId, Date.now())
  if (clientMessageId) localMessageIds.set(providerId, String(clientMessageId))
  setTimeout(() => {
    locallySentIds.delete(providerId)
    localMessageIds.delete(providerId)
  }, 5 * 60 * 1000).unref?.()
}

function statusCode(error) {
  try { return new Boom(error).output.statusCode } catch { return undefined }
}

function errorText(error) {
  return error?.message || String(error || 'unknown error')
}

function validWaVersion(value) {
  return Array.isArray(value) && value.length === 3 && value.every(part => Number.isInteger(part) && part >= 0)
}

async function resolveWaVersion() {
  const now = Date.now()
  if (cachedWaVersion && now - cachedWaVersionAt < WA_VERSION_CACHE_MS) return cachedWaVersion
  try {
    const live = await fetchLatestWaWebVersion()
    if (validWaVersion(live?.version) && !live?.error) {
      cachedWaVersion = live.version
      cachedWaVersionAt = now
      logger.debug({ version: live.version.join('.') }, 'using live WhatsApp Web version')
      return cachedWaVersion
    }
    throw new Error(live?.error?.message || 'invalid live WhatsApp Web version')
  } catch (error) {
    logger.warn({ err: errorText(error) }, 'live WhatsApp Web version unavailable; falling back to Baileys version')
    const fallback = await fetchLatestBaileysVersion()
    if (!validWaVersion(fallback?.version)) throw new Error('WhatsApp Web version konnte nicht ermittelt werden')
    cachedWaVersion = fallback.version
    cachedWaVersionAt = now
    return cachedWaVersion
  }
}

function asProviderKey(value) {
  if (!value?.id) throw new Error('Provider-Nachrichten-Key fehlt')
  return {
    remoteJid: value.remoteJid || value.remote_jid || null,
    remoteJidAlt: value.remoteJidAlt || value.remote_jid_alt || null,
    participant: value.participant || null,
    participantAlt: value.participantAlt || value.participant_alt || null,
    id: value.id,
    fromMe: Boolean(value.fromMe ?? value.from_me),
  }
}

function messageCachePath(identity) {
  const digest = crypto.createHash('sha256').update(String(identity || '')).digest('hex')
  return path.join(messageCacheDir, `${digest}.bin`)
}

async function trimRawMessageCache() {
  try {
    const names = await fs.promises.readdir(messageCacheDir)
    if (names.length <= MAX_RAW_MESSAGE_CACHE) return
    const rows = await Promise.all(names.map(async name => {
      const target = path.join(messageCacheDir, name)
      try { return { target, mtime: (await fs.promises.stat(target)).mtimeMs } }
      catch { return null }
    }))
    const existing = rows.filter(Boolean).sort((a, b) => a.mtime - b.mtime)
    const excess = existing.slice(0, Math.max(0, existing.length - MAX_RAW_MESSAGE_CACHE))
    await Promise.all(excess.map(row => fs.promises.unlink(row.target).catch(() => {})))
  } catch (error) {
    logger.debug({ err: errorText(error) }, 'raw message cache cleanup failed')
  }
}

async function rememberRawMessage(msg) {
  const identity = messageIdentity(msg?.key)
  if (!identity || !msg?.message) return
  try {
    const target = messageCachePath(identity)
    const temporary = `${target}.${process.pid}.${crypto.randomUUID()}.tmp`
    const encoded = Buffer.from(proto.WebMessageInfo.encode(msg).finish())
    await fs.promises.writeFile(temporary, encoded, { flag: 'wx' })
    await fs.promises.rename(temporary, target)
    rawCacheWrites += 1
    if (rawCacheWrites % 100 === 0) void trimRawMessageCache()
  } catch (error) {
    logger.debug({ err: errorText(error), identity }, 'raw message cache write failed')
  }
}

async function cachedMessage(key) {
  const identity = messageIdentity(key)
  if (!identity) return undefined
  try {
    const encoded = await fs.promises.readFile(messageCachePath(identity))
    const decoded = proto.WebMessageInfo.decode(encoded)
    return decoded?.message || undefined
  } catch {
    return undefined
  }
}

function isViewOnceMessage(message, key = null) {
  return Boolean(
    key?.isViewOnce ||
    message?.viewOnceMessage ||
    message?.viewOnceMessageV2 ||
    message?.viewOnceMessageV2Extension
  )
}

function contentOf(message) {
  return extractMessageContent(message) || message || null
}

function contextInfo(message) {
  if (!message) return null
  return message.extendedTextMessage?.contextInfo
    ?? message.imageMessage?.contextInfo
    ?? message.videoMessage?.contextInfo
    ?? message.documentMessage?.contextInfo
    ?? message.audioMessage?.contextInfo
    ?? null
}

function messageText(message) {
  if (!message) return null
  return message.conversation
    ?? message.extendedTextMessage?.text
    ?? message.imageMessage?.caption
    ?? message.videoMessage?.caption
    ?? message.documentMessage?.caption
    ?? null
}

function messageType(message) {
  if (!message) return 'SYSTEM'
  if (message.imageMessage) return 'IMAGE'
  if (message.videoMessage) return 'VIDEO'
  if (message.audioMessage) return message.audioMessage.ptt ? 'VOICE' : 'AUDIO'
  if (message.documentMessage) return 'DOCUMENT'
  return 'TEXT'
}

function messageMedia(message) {
  if (message?.imageMessage) return {
    mime_type: message.imageMessage.mimetype || 'image/jpeg',
    caption: message.imageMessage.caption || null,
    width: Number(message.imageMessage.width || 0) || null,
    height: Number(message.imageMessage.height || 0) || null,
  }
  if (message?.videoMessage) return {
    mime_type: message.videoMessage.mimetype || 'video/mp4',
    caption: message.videoMessage.caption || null,
    width: Number(message.videoMessage.width || 0) || null,
    height: Number(message.videoMessage.height || 0) || null,
    seconds: Number(message.videoMessage.seconds || 0) || null,
  }
  if (message?.audioMessage) return {
    mime_type: message.audioMessage.mimetype || 'audio/ogg; codecs=opus',
    seconds: Number(message.audioMessage.seconds || 0) || null,
    voice_note: Boolean(message.audioMessage.ptt),
  }
  if (message?.documentMessage) return {
    mime_type: message.documentMessage.mimetype || 'application/octet-stream',
    file_name: message.documentMessage.fileName || null,
    caption: message.documentMessage.caption || null,
    byte_size: Number(message.documentMessage.fileLength || 0) || null,
  }
  return null
}

function mediaExtension(mime, type) {
  const m = String(mime || '').toLowerCase()
  if (m.includes('jpeg') || m.includes('jpg')) return '.jpg'
  if (m.includes('png')) return '.png'
  if (m.includes('webp')) return '.webp'
  if (m.includes('mp4')) return '.mp4'
  if (m.includes('webm')) return '.webm'
  if (m.includes('ogg') || m.includes('opus')) return '.ogg'
  if (m.includes('mpeg')) return type === 'AUDIO' ? '.mp3' : '.mpeg'
  if (m.includes('pdf')) return '.pdf'
  const subtype = m.split('/')[1]?.split(';')[0]?.replace(/[^a-z0-9.+-]/g, '')
  return subtype ? `.${subtype.slice(0, 12)}` : '.bin'
}

async function messageMediaWithFile(current, msg, content, viewOnce = false) {
  const media = messageMedia(content)
  if (!media) return null
  const root = viewOnce ? viewOnceDir : mediaDir
  const temporary = path.join(root, `.incoming-${crypto.randomUUID()}.part`)
  let handle = null
  let total = 0
  try {
    const stream = await downloadMediaMessage(
      msg,
      'stream',
      {},
      { logger, reuploadRequest: current.updateMediaMessage },
    )
    handle = await fs.promises.open(temporary, 'wx')
    const digest = crypto.createHash('sha256')
    for await (const chunk of stream) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
      total += buffer.length
      if (total > MAX_INCOMING_MEDIA) {
        const error = new Error('media_too_large')
        error.code = 'MEDIA_TOO_LARGE'
        throw error
      }
      digest.update(buffer)
      await handle.write(buffer)
    }
    await handle.close()
    handle = null
    if (!total) {
      await fs.promises.unlink(temporary).catch(() => {})
      return media
    }
    const hash = digest.digest('hex')
    const filename = `${hash}${mediaExtension(media.mime_type, messageType(content))}`
    const target = path.join(root, filename)
    if (fs.existsSync(target)) await fs.promises.unlink(temporary)
    else await fs.promises.rename(temporary, target)
    media.archive_name = filename
    media.byte_size = total
    media.sha256 = hash
    if (viewOnce) media.private_view_once = true
  } catch (error) {
    if (handle) await handle.close().catch(() => {})
    await fs.promises.unlink(temporary).catch(() => {})
    media.byte_size = total || media.byte_size || null
    if (error?.code === 'MEDIA_TOO_LARGE') {
      media.download_error = 'media_too_large'
      logger.warn({ bytes: total, type: messageType(content) }, 'incoming media exceeded local size limit')
    } else {
      logger.warn({ err: errorText(error), type: messageType(content) }, 'incoming media download failed')
      media.download_error = 'download_failed'
    }
  }
  return media
}

function quoteInfo(content) {
  const info = contextInfo(content)
  if (!info?.stanzaId) return null
  const quoted = info.quotedMessage ? contentOf(info.quotedMessage) : null
  return {
    provider_message_id: info.stanzaId,
    participant: info.participant || null,
    remote_jid: info.remoteJid || null,
    snapshot: quoted ? {
      message_type: messageType(quoted),
      text: messageText(quoted),
      media: messageMedia(quoted),
    } : null,
  }
}

function minimalQuotedMessage(quote) {
  if (!quote?.key?.id) return null
  const type = String(quote.message_type || 'TEXT').toUpperCase()
  let message
  if (type === 'IMAGE') message = { imageMessage: { caption: quote.text || quote.caption || '' } }
  else if (type === 'VIDEO') message = { videoMessage: { caption: quote.text || quote.caption || '' } }
  else if (type === 'VOICE' || type === 'AUDIO') message = { audioMessage: { ptt: type === 'VOICE' } }
  else if (type === 'DOCUMENT') message = { documentMessage: { fileName: quote.file_name || 'Dokument' } }
  else message = { conversation: String(quote.text || '') }
  return { key: asProviderKey(quote.key), message }
}

function mapReceiptStatus(value) {
  const n = Number(value)
  if (n >= 5) return 'PLAYED'
  if (n >= 4) return 'READ'
  if (n >= 3) return 'DELIVERED'
  if (n >= 2) return 'SENT'
  return null
}

async function resolveTargetJid(current, jid) {
  if (!isPhoneJid(jid)) return jid
  try {
    const mapping = current.signalRepository?.lidMapping
    const lid = mapping?.getLIDForPN ? await mapping.getLIDForPN(jid) : null
    return lid || jid
  } catch (error) {
    logger.debug({ err: errorText(error) }, 'PN to LID lookup failed')
    return jid
  }
}

async function resolvePhoneJid(current, jid) {
  if (isPhoneJid(jid)) return jid
  if (!isLidJid(jid)) return null
  try {
    const mapping = current.signalRepository?.lidMapping
    return mapping?.getPNForLID ? await mapping.getPNForLID(jid) : null
  } catch (error) {
    logger.debug({ err: errorText(error) }, 'LID to PN lookup failed')
    return null
  }
}

async function inspectContact(current, jid, includePicture = false) {
  const phoneJid = await resolvePhoneJid(current, jid)
  if (!phoneJid) {
    return { exists: null, jid, phone_jid: null, profile_picture_url: null, detail: 'phone_jid_unknown' }
  }
  let result = null
  try {
    const rows = await current.onWhatsApp(phoneJid)
    result = (rows || []).find(row => row?.jid === phoneJid) || rows?.[0] || null
  } catch (error) {
    logger.warn({ err: errorText(error) }, 'WhatsApp availability lookup failed')
    return { exists: null, jid, phone_jid: phoneJid, profile_picture_url: null, detail: 'availability_lookup_failed' }
  }
  const exists = Boolean(result?.exists)
  const registeredJid = result?.jid || phoneJid
  const target = exists ? await resolveTargetJid(current, registeredJid) : registeredJid
  let profilePictureUrl = null
  if (exists && includePicture) {
    try {
      profilePictureUrl = await current.profilePictureUrl(target, 'preview', 5000) || null
    } catch (error) {
      logger.debug({ err: errorText(error) }, 'WhatsApp profile picture lookup failed')
    }
  }
  return {
    exists,
    jid: target || registeredJid,
    phone_jid: phoneJid,
    profile_picture_url: profilePictureUrl,
    detail: exists ? 'registered' : 'not_registered',
  }
}

function directMessageAddresses(key) {
  const primary = key?.remoteJid || null
  const alternate = key?.remoteJidAlt || null
  const phoneJid = isPhoneJid(primary) ? primary : (isPhoneJid(alternate) ? alternate : null)
  return { primary, phoneJid }
}

async function buildMessagePayload(current, msg, { downloadMedia = true, rehydrated = false } = {}) {
  const raw = msg.message
  const content = contentOf(raw)
  if (!content || content.reactionMessage || content.protocolMessage) return null
  const { primary: jid, phoneJid: directPhoneJid } = directMessageAddresses(msg.key)
  if (!isDirectJid(jid)) return null
  const phoneJid = directPhoneJid || await resolvePhoneJid(current, jid)
  const viewOnce = isViewOnceMessage(raw, msg.key)
  const media = downloadMedia
    ? await messageMediaWithFile(current, msg, content, viewOnce)
    : messageMedia(content)
  return {
    provider_message_id: messageIdentity(msg.key),
    provider_raw_id: rawMessageId(msg.key),
    provider_key: providerKey(msg.key),
    jid,
    phone_jid: phoneJid,
    from_me: Boolean(msg.key?.fromMe),
    timestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000)),
    message_type: messageType(content),
    text: messageText(content),
    media,
    quote: quoteInfo(content),
    view_once: viewOnce,
    ...(rehydrated ? { rehydrated: true } : {}),
  }
}

async function emitMessage(current, msg, { rehydrated = false, downloadMedia = true } = {}) {
  await rememberRawMessage(msg)
  const payload = await buildMessagePayload(current, msg, { downloadMedia, rehydrated })
  if (payload) emit({ type: 'message', payload })
}

function peerDisplayName(item) {
  const verified = typeof item?.verifiedName === 'string' ? item.verifiedName : null
  return item?.notify || item?.name || verified || null
}

async function compactPeer(current, item, { chat = false } = {}) {
  const address = item?.id || item?.jid || null
  if (!isDirectJid(address)) return null
  const phoneJid = isPhoneJid(address) ? address : await resolvePhoneJid(current, address)
  const compact = {
    provider_address: address,
    phone_jid: phoneJid,
    display_name: peerDisplayName(item),
  }
  if (chat) {
    const unread = Number(item?.unreadCount)
    const timestamp = Number(item?.conversationTimestamp || 0)
    compact.unread_count = Number.isFinite(unread) ? Math.max(0, unread) : null
    compact.conversation_timestamp = Number.isFinite(timestamp) && timestamp > 0 ? timestamp : null
  }
  return compact
}

async function compactPeers(current, rows, options = {}) {
  const out = []
  for (const row of rows || []) {
    const compact = await compactPeer(current, row, options)
    if (compact) out.push(compact)
  }
  return out
}

async function emitHistory(current, event) {
  const ordered = [...(event?.messages || [])]
    .sort((a, b) => Number(a.messageTimestamp || 0) - Number(b.messageTimestamp || 0))
  const batch = []
  emit({ type: 'sync', state: 'HISTORY', progress: event?.progress ?? null, is_latest: Boolean(event?.isLatest) })
  for (const msg of ordered) {
    await rememberRawMessage(msg)
    const payload = await buildMessagePayload(current, msg, { downloadMedia: false, rehydrated: true })
    if (!payload) continue
    batch.push(payload)
    if (batch.length >= HISTORY_BATCH_SIZE) {
      emit({ type: 'history_batch', payloads: batch.splice(0, batch.length) })
    }
  }
  if (batch.length) emit({ type: 'history_batch', payloads: batch })
  const [contacts, chats] = await Promise.all([
    compactPeers(current, event?.contacts || []),
    compactPeers(current, event?.chats || [], { chat: true }),
  ])
  if (contacts.length || chats.length) {
    emit({ type: 'history_metadata', contacts, chats, is_latest: Boolean(event?.isLatest) })
  }
}

async function flushBufferedOwnMessages() {
  if (localSendsInFlight > 0) return
  const items = [...pendingOwnMessages.values()]
  pendingOwnMessages.clear()
  for (const { current, msg } of items) {
    const providerId = messageIdentity(msg.key)
    if (providerId && locallySentIds.has(providerId)) continue
    await emitMessage(current, msg)
  }
}

function emitMessageUpdate(item) {
  const providerId = messageIdentity(item.key)
  if (!providerId) return
  const key = providerKey(item.key)

  const editedRaw = item.update?.message?.editedMessage?.message
  const edited = editedRaw ? contentOf(editedRaw) : null
  if (edited) {
    emit({
      type: 'message_content_update',
      payload: {
        provider_message_id: providerId,
        provider_key: key,
        action: 'EDIT',
        text: messageText(edited),
        message_type: messageType(edited),
      },
    })
  }

  const revoked = item.update?.message === null
    && Number(item.update?.messageStubType) === Number(WAMessageStubType.REVOKE)
  if (revoked) {
    emit({
      type: 'message_content_update',
      payload: { provider_message_id: providerId, provider_key: key, action: 'DELETE' },
    })
  }

  const mapped = mapReceiptStatus(item.update?.status)
  if (!mapped) return
  emit({
    type: 'message_update',
    payload: {
      provider_message_id: providerId,
      provider_key: key,
      client_message_id: localMessageIds.get(providerId) || null,
      status: mapped,
    },
  })
}

function flushBufferedUpdates() {
  if (localSendsInFlight > 0) return
  const items = pendingMessageUpdates.splice(0, pendingMessageUpdates.length)
  for (const item of items) emitMessageUpdate(item)
}

async function connectedTarget(command) {
  if (!sock) await connect()
  if (!sock) throw new Error('WhatsApp ist nicht verbunden')
  const info = await inspectContact(sock, command.jid, false)
  if (info.exists === false) throw new Error('Diese Nummer ist nicht bei WhatsApp registriert')
  return resolveTargetJid(sock, info.jid || command.jid)
}

async function sendAndReply(id, command, content, options = {}) {
  const target = await connectedTarget(command)
  localSendsInFlight += 1
  try {
    const quoted = minimalQuotedMessage(command.quote)
    const sent = await sock.sendMessage(target, content, quoted ? { ...options, quoted } : options)
    await rememberRawMessage(sent)
    const providerId = messageIdentity(sent?.key)
    rememberLocalSend(providerId, command.client_message_id)
    reply(id, true, {
      provider_message_id: providerId,
      provider_raw_id: rawMessageId(sent?.key),
      provider_key: providerKey(sent?.key),
      provider_address: target,
    })
  } finally {
    localSendsInFlight = Math.max(0, localSendsInFlight - 1)
    if (localSendsInFlight === 0) {
      await flushBufferedOwnMessages()
      flushBufferedUpdates()
    }
  }
}

function mediaContent(command) {
  const filePath = path.resolve(String(command.file_path || ''))
  const relative = path.relative(mediaDir, filePath)
  if (!filePath || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('Mediendatei liegt außerhalb des freigegebenen Verzeichnisses')
  }
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) throw new Error('Mediendatei nicht gefunden')
  const mediaType = String(command.media_type || '').toUpperCase()
  const source = { url: filePath }
  const caption = command.caption ? String(command.caption) : undefined
  const mimetype = command.mime_type ? String(command.mime_type) : undefined
  const viewOnce = Boolean(command.view_once)
  if (mediaType === 'IMAGE') return { image: source, caption, mimetype, viewOnce }
  if (mediaType === 'VIDEO') return { video: source, caption, mimetype, viewOnce }
  if (mediaType === 'AUDIO' || mediaType === 'VOICE') {
    return { audio: source, mimetype: mimetype || 'audio/ogg; codecs=opus', ptt: mediaType === 'VOICE', viewOnce }
  }
  if (mediaType === 'DOCUMENT') {
    return {
      document: source,
      mimetype: mimetype || 'application/octet-stream',
      fileName: String(command.file_name || 'Dokument'),
      caption,
    }
  }
  throw new Error(`Nicht unterstützter Medientyp: ${mediaType}`)
}

function emitSyncReady(current) {
  const context = socketContexts.get(current)
  if (!context || current !== sock || !context.pendingReceived || context.readyEmitted || context.catchUpFailed) return
  context.readyEmitted = true
  if (context.noHistoryTimer) {
    clearTimeout(context.noHistoryTimer)
    context.noHistoryTimer = null
  }
  emit({ type: 'sync', state: 'READY', progress: 100, is_latest: true })
}

function scheduleReadyAfterBaileysFlush(current) {
  const context = socketContexts.get(current)
  if (!context || context.readyScheduled || context.readyEmitted || context.catchUpFailed) return
  context.readyScheduled = true
  setTimeout(() => {
    context.readyScheduled = false
    if (current !== sock || context.readyEmitted || context.catchUpFailed) return
    const tail = context.eventTail
    context.eventTail = tail.then(() => emitSyncReady(current))
  }, 0)
}

async function handleConnectionUpdate(current, update) {
  if (current !== sock || !update) return
  const context = socketContexts.get(current) || { registeredAtStart: false, qrSeen: false }
  if (update.qr) {
    context.qrSeen = true
    emit({ type: 'qr', value: update.qr })
  }
  if (update.connection === 'open') {
    reconnectAttempt = 0
    manualDisconnect = false
    emit({ type: 'connection', state: 'CONNECTED', account: current.user?.id || null })
  }
  if (update.connection !== 'close') return
  if (context.noHistoryTimer) {
    clearTimeout(context.noHistoryTimer)
    context.noHistoryTimer = null
  }

  const code = statusCode(update.lastDisconnect?.error)
  const reason = errorText(update.lastDisconnect?.error)
  if (current === sock) sock = null
  connecting = null
  if (code === DisconnectReason.loggedOut) {
    logger.warn({ code, reason }, 'WhatsApp session logged out')
    emit({ type: 'connection', state: 'LOGGED_OUT', detail: `code_${code || 'unknown'}` })
    return
  }
  if (stopped || manualDisconnect) {
    emit({ type: 'connection', state: 'DISCONNECTED' })
    return
  }
  if (code === DisconnectReason.restartRequired) {
    reconnectAttempt = 0
    logger.info({ code }, 'WhatsApp pairing requires socket restart')
    emit({ type: 'connection', state: 'CONNECTING', detail: 'pairing_restart' })
    void connect().catch(err => emit({ type: 'error', code: 'RESTART_FAILED', message: errorText(err) }))
    return
  }
  if (!context.registeredAtStart) {
    reconnectAttempt = 0
    logger.warn({ code, reason, qr_seen: context.qrSeen }, 'WhatsApp pairing socket closed before registration')
    emit({ type: 'connection', state: 'ERROR', detail: `pairing_failed_code_${code || 'unknown'}` })
    return
  }
  const delay = reconnectDelays[Math.min(reconnectAttempt, reconnectDelays.length - 1)]
  reconnectAttempt += 1
  logger.warn({ code, reason, retry_ms: delay }, 'WhatsApp connection closed; reconnecting')
  emit({ type: 'connection', state: 'RECONNECTING', detail: `code_${code || 'unknown'};retry_in_${delay}ms` })
  setTimeout(() => {
    if (stopped || manualDisconnect) return
    connecting = null
    connect().catch(err => emit({ type: 'error', code: 'RECONNECT_FAILED', message: errorText(err) }))
  }, delay)
}

async function processEvents(current, events, saveCreds) {
  const context = socketContexts.get(current)
  if (events['creds.update']) await saveCreds()
  const connectionUpdate = events['connection.update']
  if (connectionUpdate) await handleConnectionUpdate(current, connectionUpdate)

  const pendingReceivedNow = connectionUpdate?.receivedPendingNotifications === true && current === sock && context
  if (pendingReceivedNow) {
    context.pendingReceived = true
    const expectedHistory = context.accountSyncCounterAtStart <= 0
    emit({ type: 'sync', state: 'PENDING', progress: null, is_latest: false, expected_history: expectedHistory })
  }

  const historyStatus = events['messaging-history.status']
  if (historyStatus && context) {
    context.historyStarted = true
    if (context.noHistoryTimer) {
      clearTimeout(context.noHistoryTimer)
      context.noHistoryTimer = null
    }
    emit({
      type: 'sync',
      state: 'HISTORY_WAIT',
      progress: null,
      is_latest: false,
      history_status: historyStatus.status || null,
      explicit: Boolean(historyStatus.explicit),
      sync_type: historyStatus.syncType ?? null,
    })
  }

  const historySet = events['messaging-history.set']
  if (historySet) {
    if (context) {
      context.historyStarted = true
      if (context.noHistoryTimer) {
        clearTimeout(context.noHistoryTimer)
        context.noHistoryTimer = null
      }
    }
    await emitHistory(current, historySet)
  }

  const upsert = events['messages.upsert']
  if (upsert) {
    const rehydrated = upsert.type !== 'notify'
    for (const msg of upsert.messages || []) {
      await rememberRawMessage(msg)
      const providerId = messageIdentity(msg.key)
      if (msg.key?.fromMe && providerId && locallySentIds.has(providerId)) continue
      if (!rehydrated && msg.key?.fromMe && localSendsInFlight > 0 && providerId) {
        pendingOwnMessages.set(providerId, { current, msg })
        continue
      }
      await emitMessage(current, msg, { rehydrated, downloadMedia: !rehydrated })
    }
  }

  for (const item of events['messages.update'] || []) {
    if (localSendsInFlight > 0) pendingMessageUpdates.push(item)
    else emitMessageUpdate(item)
  }

  const deletedMessages = events['messages.delete']
  for (const key of deletedMessages?.keys || []) {
    const providerId = messageIdentity(key)
    if (!providerId) continue
    emit({
      type: 'message_content_update',
      payload: {
        provider_message_id: providerId,
        provider_key: providerKey(key),
        action: 'DELETE',
      },
    })
  }

  for (const item of events['messages.reaction'] || []) {
    emit({
      type: 'reaction',
      payload: {
        target_provider_message_id: messageIdentity(item.key),
        target_key: providerKey(item.key),
        reaction_key: providerKey(item.reaction?.key),
        emoji: item.reaction?.text || '',
        from_me: Boolean(item.reaction?.key?.fromMe),
        timestamp_ms: Number(item.reaction?.senderTimestampMs || Date.now()),
      },
    })
  }

  const contactRows = [...(events['contacts.upsert'] || []), ...(events['contacts.update'] || [])]
  if (contactRows.length) {
    const items = await compactPeers(current, contactRows)
    if (items.length) emit({ type: 'provider_contacts', items })
  }

  const chatRows = [...(events['chats.upsert'] || []), ...(events['chats.update'] || [])]
  if (chatRows.length) {
    const items = await compactPeers(current, chatRows, { chat: true })
    if (items.length) emit({ type: 'provider_chats', items })
  }

  const chatDeletes = (events['chats.delete'] || []).filter(isDirectJid)
  if (chatDeletes.length) {
    emit({ type: 'provider_chat_deleted', addresses: chatDeletes })
  }

  const presence = events['presence.update']
  if (presence) {
    const jid = presence.id
    for (const [participant, value] of Object.entries(presence.presences || {})) {
      emit({
        type: 'presence',
        payload: {
          jid,
          participant,
          state: value?.lastKnownPresence || null,
          last_seen: value?.lastSeen || null,
        },
      })
    }
  }

  if (pendingReceivedNow) {
    if (context.accountSyncCounterAtStart > 0) {
      scheduleReadyAfterBaileysFlush(current)
    } else if (!context.historyStarted && !context.noHistoryTimer) {
      context.noHistoryTimer = setTimeout(() => {
        context.noHistoryTimer = null
        if (context.historyStarted || current !== sock || context.readyEmitted || context.catchUpFailed) return
        scheduleReadyAfterBaileysFlush(current)
      }, INITIAL_SYNC_EMPTY_FALLBACK_MS)
      context.noHistoryTimer.unref?.()
    }
  }

  if (context?.pendingReceived && historySet?.isLatest) emitSyncReady(current)
  if (context?.pendingReceived && historyStatus?.status === 'paused') emitSyncReady(current)
}

async function connect() {
  if (stopped || sock) return
  if (connecting) return connecting
  connecting = (async () => {
    emit({ type: 'connection', state: 'CONNECTING' })
    const { state, saveCreds } = await useMultiFileAuthState(authDir)
    const version = await resolveWaVersion()
    const current = makeWASocket({
      version,
      logger,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      browser: Browsers.ubuntu('Chrome'),
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: true,
      shouldSyncHistoryMessage: () => true,
      getMessage: cachedMessage,
      msgRetryCounterCache,
      maxMsgRetryCount: 5,
      generateHighQualityLinkPreview: false,
    })
    sock = current
    const context = {
      registeredAtStart: Boolean(state.creds?.registered),
      qrSeen: false,
      accountSyncCounterAtStart: Number(state.creds?.accountSyncCounter || 0),
      pendingReceived: false,
      historyStarted: false,
      readyScheduled: false,
      readyEmitted: false,
      catchUpFailed: false,
      noHistoryTimer: null,
      eventTail: Promise.resolve(),
    }
    socketContexts.set(current, context)
    current.ev.process(events => {
      const task = context.eventTail.then(() => processEvents(current, events, saveCreds))
      context.eventTail = task.catch(error => {
        if (context.pendingReceived || context.historyStarted) context.catchUpFailed = true
        logger.error({ err: errorText(error) }, 'Baileys event batch failed')
        emit({ type: 'error', code: 'EVENT_BATCH_FAILED', message: errorText(error) })
      })
      return context.eventTail
    })
  })().catch(err => {
    logger.error({ err: errorText(err) }, 'WhatsApp connect failed')
    emit({ type: 'error', code: 'CONNECT_FAILED', message: errorText(err) })
    emit({ type: 'connection', state: 'ERROR', detail: 'connect_failed' })
    throw err
  }).finally(() => { connecting = null })
  return connecting
}

async function handle(command) {
  const id = command.id
  try {
    switch (command.type) {
      case 'connect':
        manualDisconnect = false
        await connect()
        reply(id, true)
        break
      case 'contact_info': {
        if (!sock) await connect()
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        const info = await inspectContact(sock, command.jid, command.include_profile_picture !== false)
        reply(id, true, info)
        break
      }
      case 'fetch_recent_history': {
        if (!sock) await connect()
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        const count = Math.max(1, Math.min(MAX_FETCH_HISTORY_MESSAGES, Number(command.count || 20)))
        const target = await connectedTarget(command)
        const oldestKey = asProviderKey(command.oldest_key)
        if (!oldestKey.remoteJid) oldestKey.remoteJid = target
        const oldestTimestampMs = Number(command.oldest_timestamp_ms)
        if (!Number.isFinite(oldestTimestampMs) || oldestTimestampMs <= 0) {
          throw new Error('Historien-Anker hat keinen gültigen Zeitstempel')
        }
        const requestId = await sock.fetchMessageHistory(count, oldestKey, oldestTimestampMs)
        reply(id, true, { request_id: requestId, count })
        break
      }
      case 'send_text':
        await sendAndReply(id, command, { text: String(command.text || '') })
        break
      case 'send_media':
        await sendAndReply(id, command, mediaContent(command))
        break
      case 'mark_read': {
        if (!sock) await connect()
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        const keys = (command.keys || []).map(asProviderKey).filter(key => key.id)
        if (keys.length) await sock.readMessages(keys)
        reply(id, true, { count: keys.length })
        break
      }
      case 'send_reaction': {
        const target = await connectedTarget(command)
        const key = asProviderKey(command.target_key)
        const sent = await sock.sendMessage(target, { react: { text: String(command.emoji || ''), key } })
        await rememberRawMessage(sent)
        rememberLocalSend(messageIdentity(sent?.key), null)
        reply(id, true, { provider_key: providerKey(sent?.key), provider_address: target })
        break
      }
      case 'edit_message': {
        const target = await connectedTarget(command)
        const key = asProviderKey(command.target_key)
        const sent = await sock.sendMessage(target, { text: String(command.text || ''), edit: key })
        await rememberRawMessage(sent)
        rememberLocalSend(messageIdentity(sent?.key), null)
        reply(id, true, { provider_key: providerKey(sent?.key), provider_address: target })
        break
      }
      case 'delete_message': {
        const target = await connectedTarget(command)
        const key = asProviderKey(command.target_key)
        const sent = await sock.sendMessage(target, { delete: key })
        await rememberRawMessage(sent)
        rememberLocalSend(messageIdentity(sent?.key), null)
        reply(id, true, { provider_key: providerKey(sent?.key), provider_address: target })
        break
      }
      case 'presence': {
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        const target = await resolveTargetJid(sock, command.jid)
        await sock.sendPresenceUpdate(command.state, target)
        reply(id, true, { provider_address: target })
        break
      }
      case 'subscribe_presence': {
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        const target = await resolveTargetJid(sock, command.jid)
        await sock.presenceSubscribe(target)
        reply(id, true, { provider_address: target })
        break
      }
      case 'disconnect': {
        manualDisconnect = true
        const current = sock
        sock = null
        if (current) current.end(new Error('Ladies disconnect'))
        emit({ type: 'connection', state: 'DISCONNECTED' })
        reply(id, true)
        break
      }
      case 'logout': {
        manualDisconnect = true
        const current = sock
        sock = null
        if (current) await current.logout()
        emit({ type: 'connection', state: 'LOGGED_OUT' })
        reply(id, true)
        break
      }
      case 'shutdown': {
        stopped = true
        manualDisconnect = true
        const current = sock
        sock = null
        if (current) current.end(new Error('Ladies shutdown'))
        reply(id, true)
        setTimeout(() => process.exit(0), 25)
        break
      }
      default:
        reply(id, false, null, `Unknown command: ${command.type}`)
    }
  } catch (err) {
    logger.warn({ command: command.type, err: errorText(err) }, 'WhatsApp worker command failed')
    reply(id, false, null, errorText(err))
  }
}

function dispatchCommand(command) {
  if (!SERIAL_COMMANDS.has(command.type)) {
    void handle(command)
    return
  }
  commandChain = commandChain
    .then(() => handle(command))
    .catch(err => logger.error({ err: errorText(err) }, 'serialized worker command failed'))
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
rl.on('line', line => {
  try { dispatchCommand(JSON.parse(line)) }
  catch { emit({ type: 'error', code: 'INVALID_COMMAND', message: 'Ungültiges JSON-Kommando' }) }
})

process.on('SIGTERM', () => {
  stopped = true
  try { sock?.end(new Error('SIGTERM')) } finally { process.exit(0) }
})
process.on('SIGINT', () => {
  stopped = true
  try { sock?.end(new Error('SIGINT')) } finally { process.exit(0) }
})

emit({ type: 'ready' })
