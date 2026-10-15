export function providerKey(key) {
  if (!key) return null
  return {
    remoteJid: key.remoteJid || key.remote_jid || null,
    remoteJidAlt: key.remoteJidAlt || key.remote_jid_alt || null,
    participant: key.participant || null,
    participantAlt: key.participantAlt || key.participant_alt || null,
    id: key.id || null,
    fromMe: Boolean(key.fromMe ?? key.from_me),
  }
}

export function messageIdentity(key) {
  const normalized = providerKey(key)
  if (!normalized?.remoteJid || !normalized?.id) return null
  return `${normalized.remoteJid},${normalized.id},${normalized.fromMe ? '1' : '0'}`
}

export function rawMessageId(keyOrIdentity) {
  if (keyOrIdentity && typeof keyOrIdentity === 'object') return keyOrIdentity.id || null
  if (!keyOrIdentity) return null
  const text = String(keyOrIdentity)
  const parts = text.split(',')
  if (parts.length >= 3 && ['0', '1'].includes(parts.at(-1))) return parts.at(-2) || null
  return text
}
