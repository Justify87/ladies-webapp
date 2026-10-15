import assert from 'node:assert/strict'

globalThis.window={__LADIES_CONFIG__:{}}
const { initialState }=await import('../ladies/static/explorer/js/app/state.js')
const { reducer }=await import('../ladies/static/explorer/js/app/reducer.js')
const { threadRows }=await import('../ladies/static/explorer/js/app/selectors.js')

let state=structuredClone(initialState)
const threads=[
  {
    thread_id:1,
    person_id:11,
    display_name:'Anna',
    unread_count:2,
    last_message_at:'2026-10-15T18:00:00Z',
    last_message:{message_type:'TEXT',text:'Hallo'},
    endpoint:{endpoint_id:1,display_phone:'+491111'},
  },
  {
    thread_id:2,
    person_id:null,
    display_name:'WhatsApp Kontakt',
    unread_count:0,
    last_message_at:'2026-10-15T17:00:00Z',
    last_message:{message_type:'TEXT',text:'Unassigned'},
    endpoint:{endpoint_id:2,display_phone:'+492222'},
  },
]
state=reducer(state,{type:'THREADS',items:threads})
const canonical=state.messaging.threads
assert.equal(threadRows(state).length,2)

state=reducer(state,{type:'MESSAGE_VIEW_FILTER',value:'UNREAD'})
assert.strictEqual(state.messaging.threads,canonical,'view filters must not replace the canonical thread store')
assert.equal(state.messaging.filter,'ALL')
assert.deepEqual(threadRows(state).map(row=>row.threadId),[1])

state=reducer(state,{type:'MESSAGE_VIEW_FILTER',value:'UNASSIGNED'})
assert.strictEqual(state.messaging.threads,canonical,'unassigned must stay a projection over ALL threads')
assert.equal(state.messaging.filter,'ALL')
assert.deepEqual(threadRows(state).map(row=>row.threadId),[2])

state=reducer(state,{type:'MESSAGE_VIEW_FILTER',value:'ALL'})
assert.strictEqual(state.messaging.threads,canonical)
assert.deepEqual(threadRows(state).map(row=>row.threadId),[1,2])

console.log('messaging frontend behavior checks passed')
