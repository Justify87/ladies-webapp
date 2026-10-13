import * as S from '../app/selectors.js';
import { filterPayload } from '../data/adapters.js';
import { renderStandalone } from './profile.js';
import { renderSearch } from './screens/search.js';
import { renderGrid, renderSwipeScreen } from './screens/discover.js';
import { renderFavorites } from './screens/favorites.js';
import { renderMessages, renderChat } from './screens/messaging.js';
import { renderSettings } from './screens/settings.js';
import { renderHistory, renderArchive } from './screens/history.js';
import { renderSheet } from './screens/overlays.js';
import { renderHeader, renderBottomNav } from './screens/chrome.js';

export function renderApp(state) {
  const route=S.currentRoute(state);
  let body='';
  if(route.name==='search') body=renderSearch(state);
  else if(route.name==='swipe') body=renderSwipeScreen(state);
  else if(route.name==='grid') body=renderGrid(state);
  else if(route.name==='favorites') body=renderFavorites(state);
  else if(route.name==='messages') body=renderMessages(state);
  else if(route.name==='chat') body=renderChat(state);
  else if(route.name==='settings') body=renderSettings(state);
  else if(route.name==='history') body=renderHistory(state);
  else if(route.name==='archive') body=renderArchive(state,route);
  else if(route.name==='profile') {
    const pid=Number(route.params?.personId||0);
    const detail=S.detailFor(state,pid);
    body=detail ? renderStandalone(detail,{activeTab:S.profileTab(state,pid),mediaIndex:S.profileMediaIndex(state,pid),filters:state.search.filters}) : '<div class="screen-view"><div class="loading-state"><span class="spinner"></span>Profil wird geladen …</div></div>';
  } else body='<div class="screen-view"></div>';
  return {header:renderHeader(state,route),body,nav:renderBottomNav(state,route),sheet:renderSheet(state),toast:state.ui.toast?.message||''};
}

export { filterPayload };
