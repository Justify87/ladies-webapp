import { escapeHtml as e, escapeAttr as a } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';
import * as S from '../../app/selectors.js';
import { statusLabel } from '../../data/adapters.js';

const NAV=[['search','search','Suche & Filter'],['swipe','cards','Swipe'],['messages','chat','Nachrichten'],['favorites','heart','Favoriten'],['settings','settings','Einstellungen']];
const chatPresenceLabel=(state,thread)=>{const presence=state.messaging.presenceByThread?.[String(thread?.threadId||'')]?.state;if(presence==='composing')return 'tippt …';if(presence==='recording')return 'nimmt Sprachnachricht auf …';if(presence==='available')return 'online';return statusLabel(state.messaging.status.state);};

export function renderHeader(state,route){
  if(route.name==='chat'){
    const t=S.currentThread(state);const selected=t?.selectedEndpoint||t?.endpoint||t?.endpoints?.[0]||null;
    const phone=selected?.normalized_phone||selected?.display_phone||t?.phone||'';
    const call=phone?`<a class="icon-button" href="tel:${a(phone)}" aria-label="Anrufen">${icon('phone')}</a>`:'';
    const favorite=t?.personId?`<button class="icon-button ${t.favorite?'header-liked':''}" data-action="chat-favorite" aria-label="Favorit">${icon('heart')}</button>`:'';
    const endpointSwitcher=(t?.endpoints?.length||0)>1?`<button class="icon-button" data-action="chat-endpoint" title="WhatsApp-Nummer: ${a(selected?.display_phone||selected?.normalized_phone||'auswählen')}">${icon('phone')}</button>`:'';
    const recordingCancel=state.messaging.recording?`<button class="icon-button" data-action="voice-cancel" title="Sprachnachricht verwerfen">×</button>`:'';
    return `<button class="icon-button" data-action="back" aria-label="Zurück">${icon('back')}</button><button class="chat-avatar" data-action="chat-profile">${t?.avatar?`<img src="${a(t.avatar)}" alt="">`:icon('user')}</button><span class="chat-copy"><b>${e(t?.name||'Chat')}</b><small>${e(chatPresenceLabel(state,t))}</small></span>${recordingCancel}${call}${favorite}${endpointSwitcher}<button class="icon-button" data-action="pricing-manual" title="Preiserkennung">${icon('tag')}</button><button class="icon-button" data-action="chat-profile" aria-label="Profil öffnen">${icon('more')}</button>`;
  }
  if(route.name==='profile'||route.name==='history'||route.name==='archive')return '';
  const switcher=route.name==='swipe'?`<button class="icon-button view-switch" data-action="root-route" data-route="grid" aria-label="Rasteransicht">${icon('grid')}</button>`:route.name==='grid'?`<button class="icon-button view-switch" data-action="root-route" data-route="swipe" aria-label="Swipe-Ansicht">${icon('cards')}</button>`:'';
  return `<div class="brand">Ladies <strong>Explorer</strong>${icon('heart')}</div><div class="header-actions">${switcher}<button class="icon-button" data-action="root-route" data-route="search" aria-label="Suche öffnen">${icon('search')}</button></div>`;
}
export function renderBottomNav(state,route){const active=S.rootNav(route.name);if(['chat','profile','history','archive'].includes(route.name))return '';return NAV.map(([id,ico,label])=>`<button class="${active===id?'active':''}" data-action="root-route" data-route="${id}">${id==='messages'&&S.unreadTotal(state)?`<span class="nav-badge">${e(Math.min(99,S.unreadTotal(state)))}</span>`:''}${icon(ico)}<span>${e(label)}</span></button>`).join('');}
