import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window={__LADIES_CONFIG__:{}};

const { initialState } = await import('../ladies/static/explorer/js/app/state.js');
const { reducer } = await import('../ladies/static/explorer/js/app/reducer.js');
const { createRouteLifecycle } = await import('../ladies/static/explorer/js/app/lifecycle.js');

const tick=()=>new Promise(resolve=>setImmediate(resolve));

function fakeStore(state){
  const listeners=new Set();
  return {
    getState:()=>state,
    setState:value=>{state=value;listeners.forEach(fn=>fn(state,{type:'TEST_SET'}));},
    dispatch:action=>{listeners.forEach(fn=>fn(state,action));return action;},
    subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);}
  };
}

test('reducer returns the same state for repeated loading no-ops',()=>{
  const loading=reducer(initialState,{type:'PROFILE_LOADING',key:'person:7',value:true});
  assert.notStrictEqual(loading,initialState);
  assert.strictEqual(reducer(loading,{type:'PROFILE_LOADING',key:'person:7',value:true}),loading);
  const idle=reducer(loading,{type:'PROFILE_LOADING',key:'person:7',value:false});
  assert.strictEqual(reducer(idle,{type:'PROFILE_LOADING',key:'person:7',value:false}),idle);
});

test('stale chat response cannot replace the active chat',()=>{
  const state={...initialState,navigation:{stack:[{key:'chat-2',name:'chat',params:{threadId:2}}],seq:2}};
  const stale=reducer(state,{type:'CURRENT_THREAD',threadId:1,value:{thread_id:1,messages:[]}});
  assert.strictEqual(stale,state);
});

test('profile hydration is not recursively re-entered by loading dispatches',async()=>{
  const state={...initialState,navigation:{stack:[{key:'profile-7',name:'profile',params:{personId:7}}],seq:2}};
  const store=fakeStore(state);let calls=0;
  const effects={async loadPerson(){calls+=1;store.dispatch({type:'PROFILE_LOADING',key:'person:7',value:true});await Promise.resolve();store.dispatch({type:'PROFILE_LOADING',key:'person:7',value:false});},rawSearchRowAt(){return null;}};
  const lifecycle=createRouteLifecycle({store,effects});
  await tick();await tick();lifecycle.dispose();assert.equal(calls,1);
});

test('chat hydration is not recursively re-entered by loading dispatches',async()=>{
  const state={...initialState,navigation:{stack:[{key:'chat-9',name:'chat',params:{threadId:9}}],seq:2}};
  const store=fakeStore(state);let calls=0;
  const effects={async loadThread(){calls+=1;store.dispatch({type:'THREAD_LOADING',threadId:9,value:true});await Promise.resolve();store.dispatch({type:'THREAD_LOADING',threadId:9,value:false});},rawSearchRowAt(){return null;}};
  const lifecycle=createRouteLifecycle({store,effects});
  await tick();await tick();lifecycle.dispose();assert.equal(calls,1);
});
