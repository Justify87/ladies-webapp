import assert from 'node:assert/strict'
import { messageIdentity, providerKey, rawMessageId } from '../ladies/messaging/whatsapp_worker/provider_identity.mjs'

const key={remoteJid:'491701234567@s.whatsapp.net',id:'ABC123',fromMe:false}
assert.equal(messageIdentity(key),'491701234567@s.whatsapp.net,ABC123,0')
assert.equal(messageIdentity({...key,fromMe:true}),'491701234567@s.whatsapp.net,ABC123,1')
assert.equal(messageIdentity({...key,remoteJid:'12345@lid'}),'12345@lid,ABC123,0')
assert.equal(rawMessageId(messageIdentity(key)),'ABC123')
assert.deepEqual(providerKey({remote_jid:key.remoteJid,id:key.id,from_me:true}),{
  remoteJid:key.remoteJid,
  remoteJidAlt:null,
  participant:null,
  participantAlt:null,
  id:key.id,
  fromMe:true,
})
console.log('messaging provider identity checks passed')
