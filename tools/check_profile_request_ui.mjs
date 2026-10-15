import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  createProfileRequestEffects,
  profileRequestRefreshDelay,
  resolveProfileRequestListingId,
} from '../ladies/static/explorer/js/effects/profile_request.js';

const detail={listings:[
  {listing_id:11,advert_id:'advert-a'},
  {listing_id:22,advert_id:'advert-b'},
]};
assert.equal(resolveProfileRequestListingId(detail,'advert-b'),22,'opened advert must select its own listing/endpoint');
assert.equal(resolveProfileRequestListingId(detail,'missing'),null,'unknown advert must never fall back to the first listing');
assert.equal(resolveProfileRequestListingId({listings:[{listing_id:33,advert_id:'single'}]},null),33,'single-listing profiles may use their only listing');
assert.equal(resolveProfileRequestListingId(detail,null),null,'ambiguous multi-listing profiles must defer to canonical preferred endpoint selection');

const base=Date.now();
assert.equal(profileRequestRefreshDelay(new Date(base+250).toISOString(),base),250);
assert.equal(profileRequestRefreshDelay(new Date(base-250).toISOString(),base),0);
assert.equal(profileRequestRefreshDelay(null,base),null);

const effectsSource=readFileSync(new URL('../ladies/static/explorer/js/app/effects.js',import.meta.url),'utf8');
const backendSource=readFileSync(new URL('../ladies/messaging/profile_requests.py',import.meta.url),'utf8');
const integrationSource=readFileSync(new URL('../ladies/messaging/integration.py',import.meta.url),'utf8');
assert.match(effectsSource,/await regressions\.openProfile\(args\)/,'profile request loading must extend the canonical regression-safe profile opener');
assert.doesNotMatch(effectsSource,/await core\.openProfile\(args\)/,'profile request integration must not bypass canonical person resolution');
assert.match(backendSource,/status="PREPARED"/,'request metadata and local message must be committed before provider dispatch');
assert.match(backendSource,/self\.messaging\._dispatch_text\(message_id\)/,'automatic requests must reuse the canonical text dispatcher');
assert.match(backendSource,/row\.status = "FAILED" if retryable else "UNCERTAIN"/,'ambiguous provider outcomes must never become immediately retryable');
assert.match(backendSource,/message\.status = "PENDING"[\s\S]*erneuter Versand ist vorerst gesperrt/,'ambiguous sends must close the normal chat retry path');
assert.match(backendSource,/self\.messaging\.retry\(message_id\)/,'definite pre-send failures must reuse the canonical retry path and existing message');
assert.match(integrationSource,/update\(ProfileRequest\)[\s\S]*person_id=int\(target_id\)/,'person merges must move profile-request ownership');
assert.match(integrationSource,/ProfileRequest\.thread_id == int\(source_thread_id\)[\s\S]*thread_id=int\(target_thread_id\)/,'thread merges must preserve profile-request thread references');

const state={
  navigation:{stack:[{name:'profile',params:{personId:7,advertId:'advert-b'}}]},
  profiles:{details:{'7':detail}},
  messaging:{profileRequests:{}},
  ui:{toast:null},
};
const listeners=[];
const store={
  getState:()=>state,
  dispatch(action){
    if(action.type==='PROFILE_REQUEST_STATE'){
      const key=String(Number(action.personId));
      state.messaging.profileRequests[key]={...(state.messaging.profileRequests[key]||{}),...(action.value||{})};
    }
    if(action.type==='TOAST')state.ui.toast=action.value;
    for(const fn of listeners)fn(state,action);
  },
  subscribe(fn){listeners.push(fn);return()=>{};},
};

let fetchCalls=0;
const originalFetch=globalThis.fetch;
globalThis.fetch=async (url,options={})=>{
  fetchCalls+=1;
  assert.match(String(url),/\/api\/messaging\/person\/7\/request$/);
  if(String(options.method||'GET').toUpperCase()==='POST'){
    const body=JSON.parse(options.body);
    assert.equal(body.listing_id,22,'send must derive listing from current route advert, not listings[0]');
    return new Response(JSON.stringify({sent:true,can_send:false,last_request_at:new Date().toISOString(),cooldown_until:new Date(Date.now()+40).toISOString(),refresh_at:new Date(Date.now()+40).toISOString()}),{status:200,headers:{'content-type':'application/json'}});
  }
  if(fetchCalls===1){
    return new Response(JSON.stringify({can_send:false,pending:false,uncertain:false,refresh_at:new Date(Date.now()+30).toISOString(),cooldown_until:new Date(Date.now()+30).toISOString()}),{status:200,headers:{'content-type':'application/json'}});
  }
  return new Response(JSON.stringify({can_send:true,pending:false,uncertain:false,refresh_at:null,cooldown_until:null}),{status:200,headers:{'content-type':'application/json'}});
};

try{
  const effects=createProfileRequestEffects({store});
  await effects.loadProfileRequest(7);
  assert.equal(state.messaging.profileRequests['7'].can_send,false);
  await new Promise(resolve=>setTimeout(resolve,180));
  assert.ok(fetchCalls>=2,'cooldown/recheck timer must refresh status automatically');
  assert.equal(state.messaging.profileRequests['7'].can_send,true,'expired cooldown must reactivate without reopening profile');
  await effects.sendProfileRequest(7,11);
}finally{
  globalThis.fetch=originalFetch;
}
