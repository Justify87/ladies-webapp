import { api, logClient } from '../api/client.js';
import * as S from '../app/selectors.js';

export function createMessagingEffects({store,router,getEffects}) {
  let events=null;
  let recorder=null, recorderChunks=[], recorderStream=null;
  const presenceTimers=new Map();
  const contactProbeAt=new Map();
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const state=()=>store.getState();
  const threadTime=item=>Date.parse(item?.last_message_at||item?.last_message?.timestamp||item?.updated_at||0)||0;
  const newestThreads=items=>[...(items||[])].sort((left,right)=>threadTime(right)-threadTime(left)||Number(right?.thread_id||0)-Number(left?.thread_id||0));

  function applyPresence(payload){
    const threadId=Number(payload?.thread_id||0);if(!threadId)return;
    const presence=String(payload?.state||'').toLowerCase();
    const previous=state().messaging.presenceByThread?.[String(threadId)]||{};
    const lastSeen=payload?.last_seen ?? previous.lastSeen ?? null;
    const timer=presenceTimers.get(threadId);if(timer)clearTimeout(timer);presenceTimers.delete(threadId);
    if(!['composing','recording','available','paused','unavailable'].includes(presence)){
      if(lastSeen!=null)store.dispatch({type:'MESSAGE_PRESENCE',threadId,value:{state:'unavailable',lastSeen,updatedAt:Date.now()}});
      else store.dispatch({type:'MESSAGE_PRESENCE',threadId,value:null});
      return;
    }
    store.dispatch({type:'MESSAGE_PRESENCE',threadId,value:{state:presence,lastSeen,updatedAt:Date.now()}});
    if(presence==='composing'||presence==='recording')presenceTimers.set(threadId,setTimeout(()=>{
      presenceTimers.delete(threadId);
      const current=state().messaging.presenceByThread?.[String(threadId)]||{};
      store.dispatch({type:'MESSAGE_PRESENCE',threadId,value:current.lastSeen!=null?{...current,state:'paused',updatedAt:Date.now()}:null});
    },6500));
  }

  function retainSelectedEndpoint(fresh,current){if(!current||Number(current.thread_id)!==Number(fresh?.thread_id))return fresh;const selectedId=Number(current.selected_endpoint?.endpoint_id||0);if(!selectedId)return fresh;const endpoints=fresh.endpoints||[];const selected=endpoints.find(item=>Number(item.endpoint_id)===selectedId)||(Number(fresh.endpoint?.endpoint_id)===selectedId?fresh.endpoint:null);if(selected)fresh.selected_endpoint=selected;return fresh;}

  function retainLoadedHistory(fresh,current){if(!current||Number(current.thread_id)!==Number(fresh?.thread_id))return fresh;const oldMessages=current.messages||current.items||[];const newMessages=fresh.messages||fresh.items||[];if(oldMessages.length){const freshById=new Map(newMessages.map(message=>[Number(message.message_id),message]));const oldIds=new Set(oldMessages.map(message=>Number(message.message_id)));fresh.messages=oldMessages.map(message=>freshById.get(Number(message.message_id))||message);for(const message of newMessages)if(!oldIds.has(Number(message.message_id)))fresh.messages.push(message);fresh.has_more=Boolean(current.has_more);fresh.next_before=current.next_before||null;}return retainSelectedEndpoint(fresh,current);}

  function currentEndpointId(thread=S.currentThread(state())){return Number(thread?.selectedEndpoint?.endpoint_id||thread?.selected_endpoint?.endpoint_id||thread?.endpoint?.endpoint_id||thread?.endpoints?.[0]?.endpoint_id||0);}
  function mediaTypeFor(file,fallback='IMAGE'){const type=String(file?.type||'').toLowerCase();if(type.startsWith('image/'))return 'IMAGE';if(type.startsWith('video/'))return 'VIDEO';if(type.startsWith('audio/'))return 'AUDIO';if(type)return 'DOCUMENT';return String(fallback||'IMAGE').toUpperCase();}

  async function probeContact(endpointId,{force=false}={}){
    const id=Number(endpointId||0);if(!id)return null;
    const now=Date.now(),last=contactProbeAt.get(id)||0;
    if(!force&&now-last<60000)return state().messaging.contactByEndpoint?.[String(id)]||null;
    contactProbeAt.set(id,now);
    try{
      const data=await api.endpointProbe(id);
      store.dispatch({type:'MESSAGE_CONTACT_INFO',endpointId:id,value:{...(data||{}),fetchedAt:Date.now()}});
      return data;
    }catch(error){contactProbeAt.delete(id);console.debug('WhatsApp contact probe unavailable',error);return null;}
  }

  async function loadMessagingStatus(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingStatus()});}catch{} }

  async function connectWhatsapp(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingConnect()});contactProbeAt.clear();toast('WhatsApp-Verbindung gestartet');setTimeout(loadMessagingStatus,800);}catch(error){toast(error.message);} }

  async function disconnectWhatsapp(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingDisconnect()});toast('WhatsApp getrennt');}catch(error){toast(error.message);} }

  async function logoutWhatsapp(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingLogout()});contactProbeAt.clear();toast('WhatsApp abgemeldet');}catch(error){toast(error.message);} }

  async function showQr(){try{const d=await api.messagingQr();store.dispatch({type:'SHEET',value:{kind:'qr',payload:{qr:d?.qr}}});}catch(error){store.dispatch({type:'SHEET',value:{kind:'qr',payload:{qr:null}}});toast(error.message);} }

  async function loadThreads(filter=state().messaging.filter){store.dispatch({type:'THREADS_LOADING',value:true});try{const d=await api.threads(filter);store.dispatch({type:'THREADS',items:newestThreads(d?.items)});}catch(error){store.dispatch({type:'THREADS_LOADING',value:false});toast(error.message);} }

  async function loadThread(threadId,{before=null,appendOlder=false}={}){store.dispatch({type:'THREAD_LOADING',value:true});try{let d=await api.thread(threadId,{limit:100,before});const current=state().messaging.currentThread;if(appendOlder&&current&&Number(current.thread_id)===Number(threadId)){d.messages=[...(d.messages||d.items||[]),...(current.messages||current.items||[])];d=retainSelectedEndpoint(d,current);}else if(before==null){d=retainLoadedHistory(d,current);}store.dispatch({type:'CURRENT_THREAD',value:d});const endpoint=d.selected_endpoint||d.endpoint||d.endpoints?.[0];if(endpoint?.endpoint_id)void probeContact(endpoint.endpoint_id);if(Number(d.unread_count||0)>0){const messages=(d.messages||d.items||[]).filter(m=>String(m.direction).toUpperCase()==='IN').map(m=>m.message_id).filter(Boolean);api.markRead(threadId,messages.length?messages:null).catch(()=>{});}await getEffects().loadPricingForThread(threadId);}catch(error){store.dispatch({type:'THREAD_LOADING',value:false});toast(error.message);} }

  async function openChat(threadId,endpointId=null){router.push('chat',{threadId:Number(threadId)});await loadThread(Number(threadId));if(endpointId){store.dispatch({type:'THREAD_ENDPOINT',endpointId:Number(endpointId)});void probeContact(endpointId,{force:true});}}

  async function openPersonChat(personId,listingId=null){try{const d=await api.ensurePersonThread(personId,listingId);await openChat(d.thread_id,d.endpoint?.endpoint_id||null);}catch(error){toast(error.message);} }

  async function sendChat(form){const threadId=Number(form.dataset.threadId),fd=new FormData(form),text=String(fd.get('text')||'').trim();if(!text)return;const editing=state().messaging.editing,reply=state().messaging.replyTo;try{getEffects().sendPresence(threadId,'paused');if(editing)await api.editMessage(editing.id,text);else await api.sendText(threadId,{text,reply_to_message_id:reply?.id||null,endpoint_id:currentEndpointId()||null});form.reset();if(editing)store.dispatch({type:'EDITING',value:null});store.dispatch({type:'REPLY_TO',value:null});await Promise.all([loadThread(threadId),loadThreads()]);}catch(error){toast(error.message);} }

  async function sendAttachment(form){const threadId=Number(form.dataset.threadId),fd=new FormData(form);const endpoint=currentEndpointId();const file=fd.get('file');const mediaType=mediaTypeFor(file,fd.get('media_type'));fd.set('media_type',mediaType);if(endpoint)fd.set('endpoint_id',String(endpoint));if(state().messaging.replyTo?.id)fd.set('reply_to_message_id',String(state().messaging.replyTo.id));if(mediaType==='DOCUMENT')fd.delete('view_once');else fd.set('view_once',form.querySelector('[name="view_once"]')?.checked?'true':'false');try{await api.sendMedia(threadId,fd);store.dispatch({type:'SHEET',value:null});store.dispatch({type:'REPLY_TO',value:null});await Promise.all([loadThread(threadId),loadThreads()]);toast('Medium gesendet');}catch(error){toast(error.message);} }

  function findMessage(id){const t=S.currentThread(state());return t?.messages.find(m=>Number(m.id)===Number(id))||null;}

  async function reactMessage(id){const message=findMessage(id);const own=(message?.reactions||[]).find(r=>String(r.actor||'').toUpperCase()==='ME');const emoji=prompt('Reaktion (Emoji, leer = entfernen):',own?.emoji||'❤️');if(emoji===null)return;try{await api.reactMessage(id,String(emoji).trim());store.dispatch({type:'SHEET',value:null});const t=S.currentThread(state());if(t)await loadThread(t.threadId);}catch(error){toast(error.message);} }

  async function openViewOnce(id){const message=findMessage(id);if(!message||message.direction!=='IN'||message.viewOnceConsumed)return;const viewer=window.open('about:blank','_blank');if(!viewer)return toast('Einmal-Medium konnte nicht geöffnet werden. Popups für diese App erlauben und erneut versuchen.');try{viewer.opener=null;}catch{}const t=S.currentThread(state());try{const response=await fetch(`/api/messaging/messages/${encodeURIComponent(id)}/view-once`,{cache:'no-store'});if(!response.ok){let payload={};try{payload=await response.json();}catch{}throw new Error(payload.error||`HTTP ${response.status}`);}const blob=await response.blob();const url=URL.createObjectURL(blob);viewer.location.replace(url);setTimeout(()=>URL.revokeObjectURL(url),120000);if(t)await loadThread(t.threadId);}catch(error){try{viewer.close();}catch{}toast(error.message);if(t)await loadThread(t.threadId).catch(()=>{});} }

  async function deleteMessage(id){if(!confirm('Nachricht für alle löschen?'))return;try{await api.deleteMessage(id);store.dispatch({type:'SHEET',value:null});await loadThread(S.currentThread(state()).threadId);}catch(error){toast(error.message);} }

  async function retryMessage(id){try{await api.retryMessage(id);store.dispatch({type:'SHEET',value:null});await loadThread(S.currentThread(state()).threadId);}catch(error){toast(error.message);} }

  async function startVoice(){if(recorder)return;try{recorderStream=await navigator.mediaDevices.getUserMedia({audio:true});const type=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')?'audio/webm;codecs=opus':'audio/webm';recorderChunks=[];recorder=new MediaRecorder(recorderStream,{mimeType:type});recorder.ondataavailable=e=>{if(e.data?.size)recorderChunks.push(e.data);};recorder.start(250);store.dispatch({type:'MESSAGE_RECORDING',value:true});const t=S.currentThread(state());if(t)getEffects().sendPresence(t.threadId,'recording');toast('Sprachnachricht läuft …');}catch(error){toast(`Mikrofon nicht verfügbar: ${error.message}`);} }

  async function finishVoice(discard=false){if(!recorder)return;const r=recorder;const stream=recorderStream;const chunks=recorderChunks.slice();await new Promise(resolve=>{r.addEventListener('stop',resolve,{once:true});r.stop();});stream?.getTracks().forEach(t=>t.stop());recorder=null;recorderStream=null;recorderChunks=[];store.dispatch({type:'MESSAGE_RECORDING',value:false});const t=S.currentThread(state());if(t)getEffects().sendPresence(t.threadId,'paused');if(discard){toast('Sprachnachricht verworfen');return;}if(!t||!chunks.length)return;const blob=new Blob(chunks,{type:r.mimeType||'audio/webm'});const fd=new FormData();fd.set('file',new File([blob],`voice-${Date.now()}.webm`,{type:blob.type}));fd.set('media_type','VOICE');const endpoint=currentEndpointId(t);if(endpoint)fd.set('endpoint_id',String(endpoint));if(state().messaging.replyTo?.id)fd.set('reply_to_message_id',String(state().messaging.replyTo.id));try{await api.sendMedia(t.threadId,fd);store.dispatch({type:'REPLY_TO',value:null});await Promise.all([loadThread(t.threadId),loadThreads()]);toast('Sprachnachricht gesendet');}catch(error){toast(error.message);} }

  async function stopVoice(){return finishVoice(false);}
  async function cancelVoice(){return finishVoice(true);}

  async function openAssignment(endpointId){try{const [candidates,probe]=await Promise.all([api.assignmentCandidates(endpointId),api.endpointProbe(endpointId).catch(()=>null)]);const items=(candidates?.items||[]).map(candidate=>({...candidate,city:(candidate.evidence||[]).map(ev=>`${ev.decision||ev.kind}: ${ev.text||ev.kind||''}`).join(' · ')}));store.dispatch({type:'SHEET',value:{kind:'assign',payload:{endpointId,candidates:items,probe}}});}catch(error){toast(error.message);} }

  async function openAssignedThread(personId,endpointId){const summary=await api.personMessaging(Number(personId));await loadThreads();const threadId=Number(summary?.thread_id||0);if(!threadId)return;router.replace('chat',{threadId});await loadThread(threadId);if(endpointId){store.dispatch({type:'THREAD_ENDPOINT',endpointId:Number(endpointId)});void probeContact(endpointId,{force:true});}}

  async function assignEndpoint(endpointId,personId){try{const d=await api.assignEndpoint(endpointId,{person_id:Number(personId)});store.dispatch({type:'SHEET',value:null});await openAssignedThread(d?.person_id||personId,endpointId);toast('Kontakt zugeordnet');}catch(error){toast(error.message);} }

  async function createPersonAndAssign(form){const endpointId=Number(form.dataset.endpointId),name=String(new FormData(form).get('display_name')||'WhatsApp-Kontakt');try{const d=await api.assignEndpoint(endpointId,{create_person:true,display_name:name});store.dispatch({type:'SHEET',value:null});await openAssignedThread(d?.person_id,endpointId);toast('Person erstellt und zugeordnet');}catch(error){toast(error.message);} }

  function connectEvents(){if(events)return;try{events=new EventSource('/api/messaging/events');events.onopen=()=>store.dispatch({type:'MESSAGE_EVENTS',value:true});events.onerror=()=>store.dispatch({type:'MESSAGE_EVENTS',value:false});events.onmessage=async ev=>{let payload={};try{payload=JSON.parse(ev.data||'{}');}catch{};if(payload.type==='presence'){applyPresence(payload);return;}await Promise.allSettled([loadMessagingStatus(),loadThreads()]);const t=S.currentThread(state());if(t&&(payload.thread_id===t.threadId||payload.message?.thread_id===t.threadId||!payload.thread_id))loadThread(t.threadId).catch(()=>{});};}catch(error){logClient('warning','sse',error.message);}}

  return Object.freeze({loadMessagingStatus,connectWhatsapp,disconnectWhatsapp,logoutWhatsapp,showQr,loadThreads,loadThread,openChat,openPersonChat,sendChat,sendAttachment,findMessage,reactMessage,openViewOnce,deleteMessage,retryMessage,startVoice,stopVoice,cancelVoice,openAssignment,assignEndpoint,createPersonAndAssign,connectEvents,currentEndpointId,probeContact});
}
