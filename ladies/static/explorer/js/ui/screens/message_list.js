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

const classificationOf=row=>String(row.classification || (row.assigned?'LADIES_CONFIRMED':'UNKNOWN')).toUpperCase();

function conversationButton(state,row){
  return `<button class="conversation-row" data-action="open-chat" data-thread-id="${a(row.threadId)}" data-person-id="${a(row.personId||'')}"><span class="conversation-avatar ${onlineState(state,row)?'online':''}">${row.avatar?`<img src="${a(row.avatar)}" alt="">`:`<span class="chat-avatar-fallback">${icon('user')}</span>`}</span><span class="conversation-copy"><b>${e(row.name)}</b><small>${e(row.preview||'Noch keine Nachricht')}</small></span><span class="conversation-meta"><small>${e(fmtTime(row.time))}</small>${row.unread?`<span class="unread-badge">${e(row.unread)}</span>`:''}</span></button>`;
}

export function renderMessages(state){
  const rows=S.threadRows(state);
  const unknownCount=(state.messaging.threads||[]).filter(row=>classificationOf(row)==='UNKNOWN').length;
  const filters=[['ALL','Ladies'],['UNREAD','Ungelesen'],['UNASSIGNED',`Nicht zugeordnet${unknownCount?` · ${unknownCount}`:''}`]];
  const warning=whatsappUnavailable(state)
    ? '<div class="whatsapp-disconnected-banner" role="status">WhatsApp nicht verbunden</div>'
    : '';
  const reviewMode=String(state.messaging.viewFilter||'ALL').toUpperCase()==='UNASSIGNED';
  const list=rows.length?rows.map(row=>{
    if(!reviewMode)return conversationButton(state,row);
    const endpointId=Number(row.endpoint?.endpoint_id||0);
    return `<div class="conversation-review-row">${conversationButton(state,row)}<div class="conversation-review-actions">${endpointId?`<button data-action="review-assign" data-endpoint-id="${a(endpointId)}">Ladies-Person zuordnen</button>`:''}<button data-action="review-non-ladies" data-thread-id="${a(row.threadId)}">Kein Ladies-Kontakt</button></div></div>`;
  }).join(''):`<div class="list-empty">${icon('chat')}${reviewMode?'Keine ungeklärten WhatsApp-Chats.':'Keine Chats in dieser Ansicht.'}</div>`;
  return `<div class="screen-view messages-screen"><div class="screen-scroll" data-scroll-root>${warning}<div class="message-filters">${filters.map(([id,label])=>`<button class="${state.messaging.viewFilter===id?'active':''}" data-action="message-view-filter" data-filter="${id}">${e(label)}</button>`).join('')}</div>${reviewMode?'<div class="message-review-hint">Hier erscheinen WhatsApp-Chats, die noch keiner Ladies-Person sicher zugeordnet werden konnten. Die Entscheidung wird dauerhaft gespeichert.</div>':''}<label class="message-search">${icon('search')}<input data-message-search value="${a(state.messaging.query)}" placeholder="Nachrichten durchsuchen …"></label><div class="thread-list">${list}</div></div></div>`;
}
