import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  extractMessageContent,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  proto,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import pino from 'pino'
import readline from 'node:readline'
import process from 'node:process'

const authDir = process.env.LADIES_WHATSAPP_AUTH_DIR
if (!authDir) {
  process.stderr.write('LADIES_WHATSAPP_AUTH_DIR is missing\n')
  process.exit(2)
}
const mediaDir = path.resolve(authDir, '..', 'media')
fs.mkdirSync(mediaDir, { recursive: true })

const logger = pino({ level: process.env.LADIES_BAILEYS_LOG_LEVEL || 'warn' }, pino.destination(2))
const reconnectDelays = [1000, 2000, 5000, 10000, 30000]
const MAX_INCOMING_MEDIA = 96 * 1024 * 1024
let reconnectAttempt = 0
let sock = null
let stopped = false
let manualDisconnect = false
let connecting = null
let localSendsInFlight = 0
const locallySentIds = new Map()
const localMessageIds = new Map()
const pendingOwnMessages = new Map()
const pendingMessageUpdates = []

const emit = event => process.stdout.write(`${JSON.stringify(event)}\n`)
const reply = (id, ok, payload = null, message = null) => emit({ type: 'response', id, ok, payload, message })
const isPhoneJid = jid => typeof jid === 'string' && jid.endsWith('@s.whatsapp.net')
const isLidJid = jid => typeof jid === 'string' && jid.endsWith('@lid')

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

function providerKey(key) {
  if (!key) return null
  return {
    remoteJid: key.remoteJid || null,
    remoteJidAlt: key.remoteJidAlt || null,
    participant: key.participant || null,
    participantAlt: key.participantAlt || null,
    id: key.id || null,
    fromMe: Boolean(key.fromMe),
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

async function messageMediaWithFile(current, msg, content) {
  const media = messageMedia(content)
  if (!media) return null
  try {
    const buffer = await downloadMediaMessage(
      msg,
      'buffer',
      {},
      { logger, reuploadRequest: current.updateMediaMessage },
    )
    if (!Buffer.isBuffer(buffer) || !buffer.length) return media
    if (buffer.length > MAX_INCOMING_MEDIA) {
      media.download_error = 'media_too_large'
      media.byte_size = buffer.length
      return media
    }
    const digest = crypto.createHash('sha256').update(buffer).digest('hex')
    const filename = `${digest}${mediaExtension(media.mime_type, messageType(content))}`
    const target = path.join(mediaDir, filename)
    if (!fs.existsSync(target)) fs.writeFileSync(target, buffer)
    media.archive_name = filename
    media.byte_size = buffer.length
    media.sha256 = digest
  } catch (error) {
    logger.warn({ err: errorText(error), type: messageType(content) }, 'incoming media download failed')
    media.download_error = 'download_failed'
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

async function emitMessage(current, msg) {
  const raw = msg.message
  const content = contentOf(raw)
  if (!content || content.reactionMessage || content.protocolMessage) return
  const { primary: jid, phoneJid } = directMessageAddresses(msg.key)
  if (!jid || jid === 'status@broadcast' || jid.endsWith('@g.us')) return
  const media = await messageMediaWithFile(current, msg, content)
  emit({
    type: 'message',
    payload: {
      provider_message_id: msg.key?.id || null,
      provider_key: providerKey(msg.key),
      jid,
      phone_jid: phoneJid,
      from_me: Boolean(msg.key?.fromMe),
      timestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000)),
      message_type: messageType(content),
      text: messageText(content),
      media,
      quote: quoteInfo(content),
      view_once: isViewOnceMessage(raw, msg.key),
    },
  })
}

async function flushBufferedOwnMessages() {
  if (localSendsInFlight > 0) return
  const items = [...pendingOwnMessages.values()]
  pendingOwnMessages.clear()
  for (const { current, msg } of items) {
    const providerId = msg.key?.id || null
    if (providerId && locallySentIds.has(providerId)) continue
    await emitMessage(current, msg)
  }
}

function emitMessageUpdate(item) {
  const mapped = mapReceiptStatus(item.update?.status)
  const providerId = item.key?.id || null
  if (!mapped || !providerId) return
  emit({
    type: 'message_update',
    payload: {
      provider_message_id: providerId,
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
    const providerId = sent?.key?.id || null
    rememberLocalSend(providerId, command.client_message_id)
    reply(id, true, {
      provider_message_id: providerId,
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
  if (!filePath || !fs.existsSync(filePath)) throw new Error('Mediendatei nicht gefunden')
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

async function connect() {
  if (stopped) return
  if (connecting) return connecting
  connecting = (async () => {
    emit({ type: 'connection', state: 'CONNECTING' })
    const { state, saveCreds } = await useMultiFileAuthState(authDir)
    const { version } = await fetchLatestBaileysVersion()
    const current = makeWASocket({
      version,
      logger,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
      // Keep bootstrap/mapping records required by Baileys 7 PN<->LID resolution.
      shouldSyncHistoryMessage: ({ syncType }) => syncType !== proto.HistorySync.HistorySyncType.FULL,
      generateHighQualityLinkPreview: false,
    })
    sock = current
    current.ev.on('creds.update', saveCreds)
    current.ev.on('connection.update', async update => {
      if (current !== sock) return
      if (update.qr) emit({ type: 'qr', value: update.qr })
      if (update.connection === 'open') {
        reconnectAttempt = 0
        manualDisconnect = false
        emit({ type: 'connection', state: 'CONNECTED', account: current.user?.id || null })
      }
      if (update.connection === 'close') {
        const code = statusCode(update.lastDisconnect?.error)
        const reason = errorText(update.lastDisconnect?.error)
        if (code === DisconnectReason.loggedOut) {
          logger.warn({ code, reason }, 'WhatsApp session logged out')
          emit({ type: 'connection', state: 'LOGGED_OUT', detail: `code_${code || 'unknown'}` })
          return
        }
        if (stopped || manualDisconnect) {
          emit({ type: 'connection', state: 'DISCONNECTED' })
          return
        }
        const delay = reconnectDelays[Math.min(reconnectAttempt, reconnectDelays.length - 1)]
        reconnectAttempt += 1
        logger.warn({ code, reason, retry_ms: delay }, 'WhatsApp connection closed; reconnecting')
        emit({ type: 'connection', state: 'RECONNECTING', detail: `code_${code || 'unknown'};retry_in_${delay}ms` })
        setTimeout(() => {
          if (current === sock) sock = null
          connecting = null
          connect().catch(err => emit({ type: 'error', code: 'RECONNECT_FAILED', message: errorText(err) }))
        }, delay)
      }
    })
    current.ev.on('messages.upsert', async event => {
      if (event.type !== 'notify') return
      for (const msg of event.messages || []) {
        const providerId = msg.key?.id || null
        if (msg.key?.fromMe && providerId && locallySentIds.has(providerId)) continue
        if (msg.key?.fromMe && localSendsInFlight > 0 && providerId) {
          pendingOwnMessages.set(providerId, { current, msg })
          continue
        }
        await emitMessage(current, msg)
      }
    })
    current.ev.on('messages.update', updates => {
      for (const item of updates || []) {
        if (localSendsInFlight > 0) pendingMessageUpdates.push(item)
        else emitMessageUpdate(item)
      }
    })
    current.ev.on('messages.reaction', reactions => {
      for (const item of reactions || []) {
        emit({
          type: 'reaction',
          payload: {
            target_key: providerKey(item.key),
            reaction_key: providerKey(item.reaction?.key),
            emoji: item.reaction?.text || '',
            from_me: Boolean(item.reaction?.key?.fromMe),
            timestamp_ms: Number(item.reaction?.senderTimestampMs || Date.now()),
          },
        })
      }
    })
    current.ev.on('presence.update', update => {
      const jid = update.id
      for (const [participant, presence] of Object.entries(update.presences || {})) {
        emit({
          type: 'presence',
          payload: {
            jid,
            participant,
            state: presence?.lastKnownPresence || null,
            last_seen: presence?.lastSeen || null,
          },
        })
      }
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
        rememberLocalSend(sent?.key?.id || null, null)
        reply(id, true, { provider_key: providerKey(sent?.key), provider_address: target })
        break
      }
      case 'edit_message': {
        const target = await connectedTarget(command)
        const key = asProviderKey(command.target_key)
        const sent = await sock.sendMessage(target, { text: String(command.text || ''), edit: key })
        rememberLocalSend(sent?.key?.id || null, null)
        reply(id, true, { provider_key: providerKey(sent?.key), provider_address: target })
        break
      }
      case 'delete_message': {
        const target = await connectedTarget(command)
        const key = asProviderKey(command.target_key)
        const sent = await sock.sendMessage(target, { delete: key })
        rememberLocalSend(sent?.key?.id || null, null)
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

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
rl.on('line', line => {
  try { handle(JSON.parse(line)) }
  catch { emit({ type: 'error', code: 'INVALID_COMMAND', message: 'Ungültiges JSON-Kommando' }) }
})

process.on('SIGTERM', () => { stopped = true; try { sock?.end(new Error('SIGTERM')) } finally { process.exit(0) } })
process.on('SIGINT', () => { stopped = true; try { sock?.end(new Error('SIGINT')) } finally { process.exit(0) } })

emit({ type: 'ready' })
connect().catch(() => {})
