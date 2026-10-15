import { escapeHtml as e, escapeAttr as a } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';
import * as S from '../../app/selectors.js';
import { renderSwipe } from '../profile.js';

export function renderGrid(state){const rows=S.visibleSearchRows(state);return `<div class="screen-view grid-screen"><div class="screen-scroll" data-scroll-root><div class="cards-grid">${rows.length?rows.map(row=>`<article class="grid-card" data-action="open-profile" data-person-id="${a(row.personId||'')}" data-advert-id="${a(row.advertId)}">${row.thumbnail?`<img src="${a(row.thumbnail)}" alt="${a(row.name)}">`:`<div class="media-fallback">${icon('image')}</div>`}<div class="grid-card-copy"><h3>${e(row.name)} <small>${e(row.age)}</small></h3></div><div class="grid-card-actions"><button class="grid-card-heart ${row.preference==='LIKE'?'selected':''}" data-action="grid-like" data-person-id="${a(row.personId||'')}" data-advert-id="${a(row.advertId)}" aria-label="Like" title="Like">${icon('heart')}</button></div></article>`).join(''):`<div class="list-empty">${icon('users')}Keine Treffer.</div>`}</div></div></div>`;}

export function renderSwipeScreen(state){
  const rows=S.discoverQueueRows(state);
  const row=rows[state.discover.index] || null;
  if(!row){
    const searched=S.searchRows(state).length>0;
    return `<div class="screen-view"><div class="empty-state">${icon('users')}<h2>${searched?'Du hast alle Profile angeschaut.':'Keine Profile gefunden.'}</h2><button data-action="root-route" data-route="search">Filter anpassen</button>${searched?'<button data-action="discover-reset">Von vorn</button>':''}</div></div>`;
  }
  const bundle=state.profiles.bundles[row.advertId];
  const vm=bundle?S.currentProfileBundle({...state,search:{...state.search,results:[row]},discover:{...state.discover,index:0}}):row;
  const pid=vm?.personId||row.personId;
  const detail=pid?S.detailFor(state,pid):null;
  const index=Number(state.profileUi.mediaByPerson[String(pid)] ?? state.discover.mediaIndex[row.advertId] ?? 0);
  const swipe=renderSwipe(vm,detail,{activeTab:S.profileTab(state,pid),mediaIndex:index,filters:state.search.filters,autoplay:!!state.settings.values.autoplay_videos,showDescriptionChanges:!!state.settings.values.show_description_changes});
  return `<div class="swipe-shell">${swipe}<button class="swipe-undo" data-action="discover-undo" ${state.discover.history.length?'':'disabled'} title="Letzte Entscheidung zurücknehmen">${icon('undo')}</button></div>`;
}
