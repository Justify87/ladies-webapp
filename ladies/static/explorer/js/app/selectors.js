import { searchResultVm, profileBundleVm, personDetailVm, listItemVm, threadVm, threadDetailVm } from '../data/adapters.js';

export const currentRoute = state => state.navigation.stack.at(-1) || {name:'search',params:{}};
export const searchRows = state => (state.search.results || []).map(searchResultVm);
export const currentSearchRow = state => discoverQueueRows(state)[state.discover.index] || null;
export function visibleSearchRows(state) {
  const local=state.search.filters.local || {};
  return searchRows(state).filter(row=>{
    if(local.videoOnly && !row.hasVideo)return false;
    if(local.likesOnly && row.preference!=='LIKE')return false;
    if(local.photosOnly && !(row.thumbnail || row.media?.length))return false;
    const mode=String(local.imageMode || 'ALL').toUpperCase();
    const markerSource=row.markers ?? row.image_markers ?? '';
    const markers=(markerSource && typeof markerSource==='object' ? JSON.stringify(markerSource) : String(markerSource)).toLowerCase();
    if(mode==='AI' && !row.has_ai_content)return false;
    if(mode==='NATURAL' && row.has_ai_content)return false;
    if(mode==='STUDIO' && !markers.includes('studio'))return false;
    if(mode==='FACEAPP' && !markers.includes('face'))return false;
    return true;
  });
}
export function discoverQueueRows(state) {
  const replay=state.settings.values.replay_likes !== false;
  return visibleSearchRows(state).filter(row=>row.preference!=='DISLIKE' && (replay || row.preference!=='LIKE'));
}

export const bundleFor = (state, advertId) => state.profiles.bundles[String(advertId)] || null;
export const currentProfileBundle = state => { const row=currentSearchRow(state); return row ? profileBundleVm(bundleFor(state,row.advertId),row) : null; };
export const detailFor = (state,personId) => { const raw=state.profiles.details[String(personId)]; return raw ? personDetailVm(raw) : null; };
export const listRows = state => {
  const rows=(state.lists.items || []).map(listItemVm);
  const sort=String(state.lists.sort||'DATE').toUpperCase();
  return [...rows].sort((left,right)=>{
    if(sort==='NAME')return String(left.name||'').localeCompare(String(right.name||''),'de',{sensitivity:'base'});
    if(sort==='DISTANCE'){
      const a=Number(left.distance_km ?? left.distance ?? Number.POSITIVE_INFINITY);
      const b=Number(right.distance_km ?? right.distance ?? Number.POSITIVE_INFINITY);
      return a-b;
    }
    const time=row=>Date.parse(row.updated_at||row.last_seen_at||row.person_last_seen_at||row.recent_change?.created_at||row.created_at||0)||0;
    return time(right)-time(left);
  });
};
export const threadRows = state => {
  const query=(state.messaging.query || '').trim().toLowerCase();
  const filter=String(state.messaging.viewFilter||'ALL').toUpperCase();
  return (state.messaging.threads || []).map(threadVm).filter(row=>{
    if(filter==='UNREAD' && !row.unread)return false;
    if(filter==='UNASSIGNED' && row.assigned)return false;
    if(filter==='FAVORITE' && !row.favorite)return false;
    if(filter==='APPOINTMENT' && !(row.appointment_at||row.next_appointment_at||row.has_appointment||row.appointment))return false;
    if(filter==='ARCHIVED' && !(row.archived||row.is_archived||row.archived_at))return false;
    return !query || `${row.name} ${row.preview} ${row.endpoint?.display_phone||''} ${row.endpoint?.normalized_phone||''}`.toLowerCase().includes(query);
  });
};
export const currentThread = state => state.messaging.currentThread ? threadDetailVm(state.messaging.currentThread) : null;
export const unreadTotal = state => (state.messaging.threads || []).reduce((sum,row)=>sum+Number(row.unread_count || row.unread || 0),0);
export const profileTab = (state,personId) => state.profileUi.tabByPerson[String(personId)] || 'info';
export const profileMediaIndex = (state,personId) => Number(state.profileUi.mediaByPerson[String(personId)] || 0);
export const rootNav = routeName => ({grid:'swipe',profile:null,chat:'messages',history:'settings',archive:'settings'}[routeName] ?? routeName);
