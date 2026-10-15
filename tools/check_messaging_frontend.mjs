import assert from 'node:assert/strict'
import fs from 'node:fs'

globalThis.window={__LADIES_CONFIG__:{}}
const { initialState }=await import('../ladies/static/explorer/js/app/state.js')
const { reducer }=await import('../ladies/static/explorer/js/app/reducer.js')
const { threadRows, unreadTotal }=await import('../ladies/static/explorer/js/app/selectors.js')

const classificationSource=fs.readFileSync(new URL('../ladies/messaging/classification.py',import.meta.url),'utf8')
const classifiedServiceSource=fs.readFileSync(new URL('../ladies/messaging/service_classified.py',import.meta.url),'utf8')
const classifiedRoutesSource=fs.readFileSync(new URL('../ladies/messaging/routes_classified.py',import.meta.url),'utf8')
assert.ok(classificationSource.includes('LADIES_CONFIRMED = "LADIES_CONFIRMED"'))
assert.ok(classificationSource.includes('NON_LADIES = "NON_LADIES"'))
assert.ok(classificationSource.includes('if row.status in {LADIES_CONFIRMED, NON_LADIES}:'))
assert.ok(classificationSource.includes('if any(item.status == NON_LADIES'))
for(const forbidden of ['PersonUserState','favorite','preference','DISLIKE'])assert.ok(!classificationSource.includes(forbidden),`classification must not depend on ${forbidden}`)
assert.ok(classifiedServiceSource.includes('if not add_system_message and not self._auto_identity_allowed'))
assert.ok(classifiedServiceSource.includes('row.status != NON_LADIES'))
assert.ok(classifiedRoutesSource.includes('/api/messaging/inbox'))
assert.ok(classifiedRoutesSource.includes('/api/messaging/review'))
assert.ok(classifiedRoutesSource.includes('/classification'))

let state=structuredClone(initialState)
const threads=[
  {
    thread_id:1,
    person_id:11,
    classification:'LADIES_CONFIRMED',
    display_name:'Anna',
    unread_count:2,
    last_message_at:'2026-10-15T18:00:00Z',
    last_message:{message_type:'TEXT',text:'Hallo'},
    endpoint:{endpoint_id:1,display_phone:'+491111'},
  },
  {
    thread_id:2,
    person_id:null,
    classification:'UNKNOWN',
    display_name:'WhatsApp Kontakt',
    unread_count:4,
    last_message_at:'2026-10-15T17:00:00Z',
    last_message:{message_type:'TEXT',text:'Noch ungeklärt'},
    endpoint:{endpoint_id:2,display_phone:'+492222'},
  },
  {
    thread_id:3,
    person_id:null,
    classification:'NON_LADIES',
    display_name:'Privater Kontakt',
    unread_count:7,
    last_message_at:'2026-10-15T16:00:00Z',
    last_message:{message_type:'TEXT',text:'Privat'},
    endpoint:{endpoint_id:3,display_phone:'+493333'},
  },
  {
    thread_id:4,
    person_id:44,
    classification:'LADIES_CONFIRMED',
    display_name:'Bea',
    unread_count:0,
    last_message_at:'2026-10-15T15:00:00Z',
    last_message:{message_type:'TEXT',text:'Gelesen'},
    endpoint:{endpoint_id:4,display_phone:'+494444'},
  },
]
state=reducer(state,{type:'THREADS',items:threads})
const canonical=state.messaging.threads
assert.deepEqual(threadRows(state).map(row=>row.threadId),[1,4],'normal inbox contains confirmed Ladies chats only')
assert.equal(unreadTotal(state),2,'private and unknown unread counts never leak into Ladies badge')

state=reducer(state,{type:'MESSAGE_VIEW_FILTER',value:'UNREAD'})
assert.strictEqual(state.messaging.threads,canonical,'view filters must not replace the canonical product store')
assert.equal(state.messaging.filter,'ALL')
assert.deepEqual(threadRows(state).map(row=>row.threadId),[1],'unread is a projection of confirmed Ladies chats')

state=reducer(state,{type:'MESSAGE_VIEW_FILTER',value:'UNASSIGNED'})
assert.strictEqual(state.messaging.threads,canonical,'review queue must not replace the canonical product store')
assert.equal(state.messaging.filter,'ALL')
assert.deepEqual(threadRows(state).map(row=>row.threadId),[2],'review queue contains UNKNOWN only')

state=reducer(state,{type:'MESSAGE_VIEW_FILTER',value:'ALL'})
assert.strictEqual(state.messaging.threads,canonical)
assert.deepEqual(threadRows(state).map(row=>row.threadId),[1,4],'NON_LADIES never appears in the normal inbox')

console.log('messaging frontend/classification behavior checks passed')
