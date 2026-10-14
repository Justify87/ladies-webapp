import { escapeHtml as e, escapeAttr as a } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';
import * as S from '../../app/selectors.js';

const NAV=[['search','search','Suche & Filter'],['swipe','cards','Swipe'],['messages','chat','Nachrichten'],['favorites','heart','Favoriten'],['settings','settings','Einstellungen']];
function lastSeenLabel(value){
  if(value==null||value==='')return '';
  let date;
  const numeric=Number(value);
  if(Number.isFinite(numeric))date=new Date(numeric<1e12?numeric*1000:numeric);else date=new Date(value);
  if(Number.isNaN(date.getTime()))return '';
  const now=new Date(),sameDay=date.getFullYear()===now.getFullYear()&&date.getMonth()===now.getMonth()&&date.getDate()===now.getDate();
  const time=new Intl.DateTimeFormat('de-DE',{hour:'2-digit',minute:'2-digit'}).format(date);
  if(sameDay)return `zuletzt online heute um ${time}`;
  const day=new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'2-digit',year:date.getFullYear()===now.getFullYear()?undefined:'numeric'}).format(date);
  return `zuletzt online ${day} um ${time}`;
}
const chatPresenceLabel=(state,thread)=>{const presence=state.messaging.presenceByThread?.[String(thread?.threadId||'')]||{};if(presence.state==='composing')return 'tippt …';if(presence.state==='recording')return 'nimmt Sprachnachricht auf …';if(presence.state==='available')return 'online';return lastSeenLabel(presence.lastSeen);};

export function renderHeader(state,route){
  if(route.name==='chat'){
    const t=S.currentThread(state);const selected=t?.selectedEndpoint||t?.selected_endpoint||t?.endpoint||t?.endpoints?.[0]||null;
    const phone=selected?.display_phone||selected?.normalized_phone||t?.phone||'';
    const presence=chatPresenceLabel(state,t);
    const call=phone?`<a class="icon-button" href="tel:${a(selected?.normalized_phone||phone)}" aria-label="Anrufen">${icon('phone')}</a>`:'';
    const favorite=t?.personId?`<button class="icon-button ${t.favorite?'header-liked':''}" data-action="chat-favorite" aria-label="Favorit">${icon('heart')}</button>`:'';
    const endpointSwitcher=(t?.endpoints?.length||0)>1?`<button class="icon-button" data-action="chat-endpoint" title="WhatsApp-Nummer: ${a(phone||'auswählen')}">${icon('phone')}</button>`:'';
    const recordingCancel=state.messaging.recording?`<button class="icon-button" data-action="voice-cancel" title="Sprachnachricht verwerfen">×</button>`:'';
    return `<button class="icon-button" data-action="back" aria-label="Zurück">${icon('back')}</button><button class="chat-avatar whatsapp-avatar" data-action="chat-profile" aria-label="Profil öffnen">${t?.whatsappAvatar?`<img src="${a(t.whatsappAvatar)}" alt="WhatsApp-Profilbild">`:icon('user')}</button><span class="chat-copy"><b>${e(t?.name||'Chat')}</b>${phone?`<small class="chat-phone">${e(phone)}</small>`:''}${presence?`<small class="chat-presence">${e(presence)}</small>`:''}</span>${recordingCancel}${call}${favorite}${endpointSwitcher}<button class="icon-button" data-action="pricing-manual" title="Preiserkennung">${icon('tag')}</button><button class="icon-button" data-action="chat-profile" aria-label="Profil öffnen">${icon('more')}</button>`;
  }
  if(route.name==='profile'||route.name==='history'||route.name==='archive')return '';
  const switcher=route.name==='swipe'?`<button class="icon-button view-switch" data-action="root-route" data-route="grid" aria-label="Rasteransicht">${icon('grid')}</button>`:route.name==='grid'?`<button class="icon-button view-switch" data-action="root-route" data-route="swipe" aria-label="Swipe-Ansicht">${icon('cards')}</button>`:'';
  return `<div class="brand">Ladies <strong>Explorer</strong>${icon('heart')}</div><div class="header-actions">${switcher}<button class="icon-button" data-action="root-route" data-route="search" aria-label="Suche öffnen">${icon('search')}</button></div>`;
}
export function renderBottomNav(state,route){const active=S.rootNav(route.name);if(['chat','profile','history','archive'].includes(route.name))return '';return NAV.map(([id,ico,label])=>`<button class="${active===id?'active':''}" data-action="root-route" data-route="${id}">${id==='messages'&&S.unreadTotal(state)?`<span class="nav-badge">${e(Math.min(99,S.unreadTotal(state)))}</span>`:''}${icon(ico)}<span>${e(label)}</span></button>`).join('');}
