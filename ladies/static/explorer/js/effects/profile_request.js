import { request } from '../api/client.js';

const requestUrl=personId=>`/api/messaging/person/${encodeURIComponent(personId)}/request`;
const requestToken=()=>globalThis.crypto?.randomUUID?.() || `profile-request-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function resolveProfileRequestListingId(detail,advertId) {
  const listings=Array.isArray(detail?.listings)?detail.listings:[];
  const advert=String(advertId||'').trim();
  if(advert){
    const exact=listings.find(item=>String(item?.advert_id??item?.external_listing_id??'')===advert);
    return Number(exact?.listing_id||0)||null;
  }
  if(listings.length===1)return Number(listings[0]?.listing_id||0)||null;
  return null;
}

export function profileRequestRefreshDelay(value,now=Date.now()) {
  const target=value?new Date(value).getTime():NaN;
  if(!Number.isFinite(target))return null;
  return Math.max(0,target-Number(now||0));
}

export function createProfileRequestEffects({store}) {
  const state=()=>store.getState();
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const timers=new Map();
  const current=personId=>state().messaging.profileRequests?.[String(Number(personId))] || null;
  const patch=(personId,value)=>store.dispatch({type:'PROFILE_REQUEST_STATE',personId:Number(personId),value});

  function clearTimer(personId){const pid=Number(personId);const timer=timers.get(pid);if(timer)clearTimeout(timer);timers.delete(pid);}
  function scheduleRefresh(personId,data){
    const pid=Number(personId);clearTimer(pid);
    if(!pid||data?.can_send!==false)return;
    const delay=profileRequestRefreshDelay(data?.refresh_at||data?.cooldown_until);
    if(delay===null)return;
    timers.set(pid,setTimeout(()=>{timers.delete(pid);void loadProfileRequest(pid,{quiet:true});},Math.min(delay+100,2_147_000_000)));
  }
  function apply(personId,data){
    const unresolved=Boolean(data?.can_send===false&&(data?.pending||data?.uncertain));
    patch(personId,{...(data||{}),loading:unresolved,sending:false,error:null});
    scheduleRefresh(personId,data||{});
    return data;
  }

  async function loadProfileRequest(personId,{quiet=true}={}) {
    const pid=Number(personId||0);if(!pid)return null;
    patch(pid,{loading:true,error:null});
    try {
      return apply(pid,await request(requestUrl(pid)));
    } catch(error) {
      clearTimer(pid);
      patch(pid,{loading:false,sending:false,can_send:false,error:error.message||String(error)});
      if(!quiet)toast('Anfragestatus konnte nicht geladen werden.');
      return null;
    }
  }

  async function sendProfileRequest(personId,_legacyListingId=null) {
    const pid=Number(personId||0);if(!pid)return null;
    const before=current(pid)||{};
    if(before.loading||before.sending||before.can_send===false)return null;
    const route=state().navigation.stack.at(-1);
    const advertId=route?.name==='profile'?route.params?.advertId:null;
    const listingId=resolveProfileRequestListingId(state().profiles.details?.[String(pid)],advertId);
    patch(pid,{sending:true,error:null});
    try {
      const data=await request(requestUrl(pid),{
        method:'POST',
        body:{listing_id:listingId,request_token:requestToken()}
      });
      apply(pid,data||{});
      if(data?.sent)toast('Anfrage gesendet ✓');
      else if(data?.cooldown)toast('Du hast bereits kürzlich angefragt.');
      return data;
    } catch(error) {
      patch(pid,{sending:false,can_send:false,error:error.message||String(error)});
      await loadProfileRequest(pid,{quiet:true});
      toast('Anfrage konnte nicht gesendet werden.');
      return null;
    }
  }

  return Object.freeze({loadProfileRequest,sendProfileRequest});
}
