import { request } from '../api/client.js';

const requestUrl=personId=>`/api/messaging/person/${encodeURIComponent(personId)}/request`;
const requestToken=()=>globalThis.crypto?.randomUUID?.() || `profile-request-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function createProfileRequestEffects({store}) {
  const state=()=>store.getState();
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const current=personId=>state().messaging.profileRequests?.[String(Number(personId))] || null;
  const patch=(personId,value)=>store.dispatch({type:'PROFILE_REQUEST_STATE',personId:Number(personId),value});

  async function loadProfileRequest(personId,{quiet=true}={}) {
    const pid=Number(personId||0);if(!pid)return null;
    patch(pid,{loading:true,error:null});
    try {
      const data=await request(requestUrl(pid));
      patch(pid,{...(data||{}),loading:false,sending:false,error:null});
      return data;
    } catch(error) {
      patch(pid,{loading:false,sending:false,error:error.message||String(error)});
      if(!quiet)toast('Anfragestatus konnte nicht geladen werden.');
      return null;
    }
  }

  async function sendProfileRequest(personId,listingId=null) {
    const pid=Number(personId||0);if(!pid)return null;
    const before=current(pid)||{};
    if(before.loading||before.sending||before.can_send===false)return null;
    patch(pid,{sending:true,error:null});
    try {
      const data=await request(requestUrl(pid),{
        method:'POST',
        body:{listing_id:Number(listingId||0)||null,request_token:requestToken()}
      });
      patch(pid,{...(data||{}),loading:false,sending:false,error:null});
      if(data?.sent)toast('Anfrage gesendet ✓');
      else if(data?.cooldown)toast('Du hast bereits kürzlich angefragt.');
      return data;
    } catch(error) {
      patch(pid,{sending:false,error:error.message||String(error)});
      toast('Anfrage konnte nicht gesendet werden.');
      return null;
    }
  }

  return Object.freeze({loadProfileRequest,sendProfileRequest});
}
