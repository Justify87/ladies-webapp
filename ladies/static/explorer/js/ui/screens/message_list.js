import { escapeHtml as e, escapeAttr as a, fmtTime } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';
import * as S from '../../app/selectors.js';

const onlineState=(state,row)=>{
  const presence=state.messaging.presenceByThread?.[String(row.threadId)]?.state;
  return ['available','composing','recording'].includes(String(presence||'').toLowerCase())
    || row.online===true
    || row.is_online===true;
};

const whatsappUnavailable=state=>{
  const status=String(state.messaging.status?.state||'').toUpperCase();
  return Boolean(status&&status!=='UNKNOWN'&&status!=='CONNECTED');
};

export function renderMessages(state){
  const rows=S.threadRows(state);
  // The store always contains the canonical ALL thread set. These filters only
  // change the local projection and therefore cannot hide or evict conversations.
  const filters=[['ALL','Alle'],['UNREAD','Ungelesen'],['UNASSIGNED','Nicht zugeordnet']];
  const warning=whatsappUnavailable(state)
    ? '<div class="whatsapp-disconnected-banner" role="status">WhatsApp nicht verbunden</div>'
    : '';
  return `<div class="screen-view messages-screen"><div class="screen-scroll" data-scroll-root>${warning}<div class="message-filters">${filters.map(([id,label])=>`<button class="${state.messaging.viewFilter===id?'active':''}" data-action="message-view-filter" data-filter="${id}">${e(label)}</button>`).join('')}</div><label class="message-search">${icon('search')}<input data-message-search value="${a(state.messaging.query)}" placeholder="Nachrichten durchsuchen …"></label><div class="thread-list">${rows.length?rows.map(row=>`<button class="conversation-row" data-action="open-chat" data-thread-id="${a(row.threadId)}" data-person-id="${a(row.personId||'')}"><span class="conversation-avatar ${onlineState(state,row)?'online':''}">${row.avatar?`<img src="${a(row.avatar)}" alt="">`:`<span class="chat-avatar-fallback">${icon('user')}</span>`}</span><span class="conversation-copy"><b>${e(row.name)}</b><small>${e(row.preview||'Noch keine Nachricht')}</small></span><span class="conversation-meta"><small>${e(fmtTime(row.time))}</small>${row.unread?`<span class="unread-badge">${e(row.unread)}</span>`:''}</span></button>`).join(''):`<div class="list-empty">${icon('chat')}Keine Chats in dieser Ansicht.</div>`}</div></div></div>`;
}
