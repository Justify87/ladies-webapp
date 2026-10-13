import { api, logClient } from '../api/client.js';
import * as S from '../app/selectors.js';

export function createMessagingEffects({store,router,getEffects}) {
  let events=null;
  let recorder=null, recorderChunks=[], recorderStream=null;
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const state=()=>store.getState();

  async function loadMessagingStatus(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingStatus()});}catch{} }

  async function connectWhatsapp(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingConnect()});toast('WhatsApp-Verbindung gestartet');setTimeout(loadMessagingStatus,800);}catch(error){toast(error.message);} }

  async function disconnectWhatsapp(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingDisconnect()});toast('WhatsApp getrennt');}catch(error){toast(error.message);} }

  async function logoutWhatsapp(){try{store.dispatch({type:'MESSAGE_STATUS',value:await api.messagingLogout()});toast('WhatsApp abgemeldet');}catch(error){toast(error.message);} }

  async function showQr(){try{const d=await api.messagingQr();store.dispatch({type:'SHEET',value:{kind:'qr',payload:{qr:d?.qr}}});}catch(error){store.dispatch({type:'SHEET',value:{kind:'qr',payload:{qr:null}}});toast(error.message);} }

  async function loadThreads(filter=state().messaging.filter){store.dispatch({type:'THREADS_LOADING',value:true});try{const d=await api.threads(filter);store.dispatch({type:'THREADS',items:d?.items||[]});}catch(error){store.dispatch({type:'THREADS_LOADING',value:false});toast(error.message);} }

  async function loadThread(threadId,{before=null,appendOlder=false}={}){store.dispatch({type:'THREAD_LOADING',value:true});try{const d=await api.thread(threadId,{limit:100,before});if(appendOlder&&state().messaging.currentThread){const current=state().messaging.currentThread;d.messages=[...(d.messages||d.items||[]),...(current.messages||current.items||[])];}store.dispatch({type:'CURRENT_THREAD',value:d});const messages=(d.messages||d.items||[]).filter(m=>String(m.direction).toUpperCase()==='IN'&&String(m.status).toUpperCase()!=='READ').map(m=>m.message_id).filter(Boolean);api.markRead(threadId,messages.length?messages:null).catch(()=>{});await getEffects().loadPricingForThread(threadId);}catch(error){store.dispatch({type:'THREAD_LOADING',value:false});toast(error.message);} }

  async function openChat(threadId){router.push('chat',{threadId:Number(threadId)});await loadThread(Number(threadId));}

  async function openPersonChat(personId,listingId=null){try{const d=await api.ensurePersonThread(personId,listingId);await openChat(d.thread_id);}catch(error){toast(error.message);} }

  async function sendChat(form){const threadId=Number(form.dataset.threadId),fd=new FormData(form),text=String(fd.get('text')||'').trim();if(!text)return;const editing=state().messaging.editing,reply=state().messaging.replyTo;try{if(editing){await api.editMessage(editing.id,text);store.dispatch({type:'EDITING',value:null});}else await api.sendText(threadId,{text,reply_to_message_id:reply?.id||null,endpoint_id:state().messaging.currentThread?.selected_endpoint?.endpoint_id||null});store.dispatch({type:'REPLY_TO',value:null});form.reset();await Promise.all([loadThread(threadId),loadThreads()]);}catch(error){toast(error.message);} }

  async function sendAttachment(form){const threadId=Number(form.dataset.threadId),fd=new FormData(form);const endpoint=state().messaging.currentThread?.selected_endpoint?.endpoint_id;if(endpoint)fd.set('endpoint_id',String(endpoint));if(state().messaging.replyTo?.id)fd.set('reply_to_message_id',String(state().messaging.replyTo.id));try{await api.sendMedia(threadId,fd);store.dispatch({type:'SHEET',value:null});store.dispatch({type:'REPLY_TO',value:null});await Promise.all([loadThread(threadId),loadThreads()]);toast('Medium gesendet');}catch(error){toast(error.message);} }

  function findMessage(id){const t=S.currentThread(state());return t?.messages.find(m=>Number(m.id)===Number(id))||null;}

  async function reactMessage(id){const emoji=prompt('Reaktion (Emoji):','❤️');if(!emoji)return;try{await api.reactMessage(id,emoji);store.dispatch({type:'SHEET',value:null});await loadThread(S.currentThread(state()).threadId);}catch(error){toast(error.message);} }

  async function deleteMessage(id){if(!confirm('Nachricht für alle löschen?'))return;try{await api.deleteMessage(id);store.dispatch({type:'SHEET',value:null});await loadThread(S.currentThread(state()).threadId);}catch(error){toast(error.message);} }

  async function retryMessage(id){try{await api.retryMessage(id);store.dispatch({type:'SHEET',value:null});await loadThread(S.currentThread(state()).threadId);}catch(error){toast(error.message);} }

  async function startVoice(){if(recorder)return;try{recorderStream=await navigator.mediaDevices.getUserMedia({audio:true});const type=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')?'audio/webm;codecs=opus':'audio/webm';recorderChunks=[];recorder=new MediaRecorder(recorderStream,{mimeType:type});recorder.ondataavailable=e=>{if(e.data?.size)recorderChunks.push(e.data);};recorder.start(250);store.dispatch({type:'MESSAGE_RECORDING',value:true});toast('Sprachnachricht läuft …');}catch(error){toast(`Mikrofon nicht verfügbar: ${error.message}`);} }

  async function stopVoice(){if(!recorder)return;const r=recorder;const stream=recorderStream;const chunks=recorderChunks;await new Promise(resolve=>{r.addEventListener('stop',resolve,{once:true});r.stop();});stream?.getTracks().forEach(t=>t.stop());recorder=null;recorderStream=null;store.dispatch({type:'MESSAGE_RECORDING',value:false});const blob=new Blob(chunks,{type:r.mimeType||'audio/webm'});const t=S.currentThread(state());if(!t)return;const fd=new FormData();fd.set('file',new File([blob],`voice-${Date.now()}.webm`,{type:blob.type}));fd.set('media_type','VOICE');const endpoint=t.selectedEndpoint?.endpoint_id||t.selected_endpoint?.endpoint_id;if(endpoint)fd.set('endpoint_id',String(endpoint));if(state().messaging.replyTo?.id)fd.set('reply_to_message_id',String(state().messaging.replyTo.id));try{await api.sendMedia(t.threadId,fd);store.dispatch({type:'REPLY_TO',value:null});await Promise.all([loadThread(t.threadId),loadThreads()]);toast('Sprachnachricht gesendet');}catch(error){toast(error.message);} }

  async function openAssignment(endpointId){try{const d=await api.assignmentCandidates(endpointId);store.dispatch({type:'SHEET',value:{kind:'assign',payload:{endpointId,candidates:d?.items||[]}}});}catch(error){toast(error.message);} }

  async function assignEndpoint(endpointId,personId){try{await api.assignEndpoint(endpointId,{person_id:Number(personId)});store.dispatch({type:'SHEET',value:null});toast('Kontakt zugeordnet');await loadThreads();}catch(error){toast(error.message);} }

  async function createPersonAndAssign(form){const endpointId=Number(form.dataset.endpointId),name=String(new FormData(form).get('display_name')||'WhatsApp-Kontakt');try{await api.assignEndpoint(endpointId,{create_person:true,display_name:name});store.dispatch({type:'SHEET',value:null});toast('Person erstellt und zugeordnet');await loadThreads();}catch(error){toast(error.message);} }

  function connectEvents(){if(events)return;try{events=new EventSource('/api/messaging/events');events.onopen=()=>store.dispatch({type:'MESSAGE_EVENTS',value:true});events.onerror=()=>store.dispatch({type:'MESSAGE_EVENTS',value:false});events.onmessage=async ev=>{let payload={};try{payload=JSON.parse(ev.data||'{}');}catch{};await Promise.allSettled([loadMessagingStatus(),loadThreads()]);const t=S.currentThread(state());if(t&&(payload.thread_id===t.threadId||payload.message?.thread_id===t.threadId||!payload.thread_id))loadThread(t.threadId).catch(()=>{});};}catch(error){logClient('warning','sse',error.message);}}

  return Object.freeze({loadMessagingStatus,connectWhatsapp,disconnectWhatsapp,logoutWhatsapp,showQr,loadThreads,loadThread,openChat,openPersonChat,sendChat,sendAttachment,findMessage,reactMessage,deleteMessage,retryMessage,startVoice,stopVoice,openAssignment,assignEndpoint,createPersonAndAssign,connectEvents});
}
