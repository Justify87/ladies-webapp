import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import pino from 'pino'
import readline from 'node:readline'
import process from 'node:process'

const authDir = process.env.LADIES_WHATSAPP_AUTH_DIR
if (!authDir) {
  process.stderr.write('LADIES_WHATSAPP_AUTH_DIR is missing\n')
  process.exit(2)
}

const logger = pino({ level: process.env.LADIES_BAILEYS_LOG_LEVEL || 'warn' }, pino.destination(2))
const reconnectDelays = [1000, 2000, 5000, 10000, 30000]
let reconnectAttempt = 0
let sock = null
let stopped = false
let manualDisconnect = false
let connecting = null

const emit = (event) => process.stdout.write(`${JSON.stringify(event)}\n`)
const reply = (id, ok, payload = null, message = null) => emit({ type: 'response', id, ok, payload, message })

function statusCode(error) {
  try { return new Boom(error).output.statusCode } catch { return undefined }
}

function messageText(message) {
  if (!message) return null
  return message.conversation
    ?? message.extendedTextMessage?.text
    ?? message.imageMessage?.caption
    ?? message.videoMessage?.caption
    ?? null
}

function messageType(message) {
  if (!message) return 'SYSTEM'
  if (message.imageMessage) return 'IMAGE'
  if (message.videoMessage) return 'VIDEO'
  if (message.audioMessage) return 'AUDIO'
  return 'TEXT'
}

function messageMedia(message) {
  if (message?.imageMessage) return { mime_type: message.imageMessage.mimetype, caption: message.imageMessage.caption || null }
  if (message?.videoMessage) return { mime_type: message.videoMessage.mimetype, caption: message.videoMessage.caption || null }
  if (message?.audioMessage) return { mime_type: message.audioMessage.mimetype, seconds: message.audioMessage.seconds || null }
  return null
}

async function messageMediaWithBytes(current, msg) {
  const media = messageMedia(msg.message)
  if (!media || !msg.message?.imageMessage) return media
  try {
    const buffer = await downloadMediaMessage(
      msg,
      'buffer',
      {},
      { logger, reuploadRequest: current.updateMediaMessage },
    )
    if (Buffer.isBuffer(buffer) && buffer.length <= 15 * 1024 * 1024) {
      media.data_base64 = buffer.toString('base64')
    }
  } catch (error) {
    logger.warn({ err: error?.message || String(error) }, 'incoming image download failed')
  }
  return media
}

function mapReceiptStatus(value) {
  const n = Number(value)
  if (n >= 4) return 'READ'
  if (n >= 3) return 'DELIVERED'
  if (n >= 2) return 'SENT'
  return null
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
      shouldSyncHistoryMessage: () => false,
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
        if (code === DisconnectReason.loggedOut) {
          emit({ type: 'connection', state: 'LOGGED_OUT' })
          return
        }
        if (stopped || manualDisconnect) {
          emit({ type: 'connection', state: 'DISCONNECTED' })
          return
        }
        const delay = reconnectDelays[Math.min(reconnectAttempt, reconnectDelays.length - 1)]
        reconnectAttempt += 1
        emit({ type: 'connection', state: 'RECONNECTING', detail: `retry_in_${delay}ms` })
        setTimeout(() => {
          if (current === sock) sock = null
          connecting = null
          connect().catch(err => emit({ type: 'error', code: 'RECONNECT_FAILED', message: err?.message || String(err) }))
        }, delay)
      }
    })
    current.ev.on('messages.upsert', async event => {
      if (event.type !== 'notify') return
      for (const msg of event.messages || []) {
        const jid = msg.key?.remoteJid
        if (!jid || jid === 'status@broadcast' || jid.endsWith('@g.us')) continue
        const media = await messageMediaWithBytes(current, msg)
        emit({
          type: 'message',
          payload: {
            provider_message_id: msg.key?.id || null,
            jid,
            from_me: Boolean(msg.key?.fromMe),
            timestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000)),
            message_type: messageType(msg.message),
            text: messageText(msg.message),
            media,
          },
        })
      }
    })
    current.ev.on('messages.update', updates => {
      for (const item of updates || []) {
        const mapped = mapReceiptStatus(item.update?.status)
        if (!mapped || !item.key?.id) continue
        emit({ type: 'message_update', payload: { provider_message_id: item.key.id, status: mapped } })
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
    emit({ type: 'error', code: 'CONNECT_FAILED', message: err?.message || String(err) })
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
      case 'send_text': {
        if (!sock) await connect()
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        const sent = await sock.sendMessage(command.jid, { text: String(command.text || '') })
        reply(id, true, { provider_message_id: sent?.key?.id || null })
        break
      }
      case 'presence':
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        await sock.sendPresenceUpdate(command.state, command.jid)
        reply(id, true)
        break
      case 'subscribe_presence':
        if (!sock) throw new Error('WhatsApp ist nicht verbunden')
        await sock.presenceSubscribe(command.jid)
        reply(id, true)
        break
      case 'disconnect':
        manualDisconnect = true
        if (sock) sock.end(new Error('Ladies disconnect'))
        sock = null
        reply(id, true)
        break
      case 'logout':
        manualDisconnect = true
        if (sock) await sock.logout()
        sock = null
        emit({ type: 'connection', state: 'LOGGED_OUT' })
        reply(id, true)
        break
      case 'shutdown':
        stopped = true
        manualDisconnect = true
        if (sock) sock.end(new Error('Ladies shutdown'))
        sock = null
        reply(id, true)
        setTimeout(() => process.exit(0), 25)
        break
      default:
        reply(id, false, null, `Unknown command: ${command.type}`)
    }
  } catch (err) {
    reply(id, false, null, err?.message || String(err))
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
