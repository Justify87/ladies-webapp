import { searchResultVm, profileBundleVm, personDetailVm, listItemVm, threadVm, threadDetailVm } from '../data/adapters.js';

export const currentRoute = state => state.navigation.stack.at(-1) || {name:'search',params:{}};

let searchSource=null, searchCache=[];
export const searchRows = state => {
  const source=state.search.results||[];
  if(source!==searchSource){searchSource=source;searchCache=source.map(searchResultVm);}
  return searchCache;
};

let visibleRowsSource=null, visibleLocal=null, visibleRowsCache=[];
export function visibleSearchRows(state) {
  const rows=searchRows(state), local=state.search.filters.local||{};
  if(rows===visibleRowsSource&&local===visibleLocal)return visibleRowsCache;
  visibleRowsSource=rows;visibleLocal=local;
  visibleRowsCache=rows.filter(row=>{
    if(local.videoOnly&&!row.hasVideo)return false;
    if(local.likesOnly&&row.preference!=='LIKE')return false;
    if(local.photosOnly&&!(row.thumbnail||row.media?.length))return false;
    const mode=String(local.imageMode||'ALL').toUpperCase();
    const markerSource=row.markers??row.image_markers??'';
    const markers=(markerSource&&typeof markerSource==='object'?JSON.stringify(markerSource):String(markerSource)).toLowerCase();
    if(mode==='AI'&&!row.has_ai_content)return false;
    if(mode==='NATURAL'&&row.has_ai_content)return false;
    if(mode==='STUDIO'&&!markers.includes('studio'))return false;
    if(mode==='FACEAPP'&&!markers.includes('face'))return false;
    return true;
  });
  return visibleRowsCache;
}

let queueRowsSource=null, queueReplay=null, queueRowsCache=[];
export function discoverQueueRows(state) {
  const rows=visibleSearchRows(state), replay=state.settings.values.replay_likes!==false;
  if(rows===queueRowsSource&&replay===queueReplay)return queueRowsCache;
  queueRowsSource=rows;queueReplay=replay;
  queueRowsCache=rows.filter(row=>row.preference!=='DISLIKE'&&(replay||row.preference!=='LIKE'));
  return queueRowsCache;
}

export const currentSearchRow = state => discoverQueueRows(state)[state.discover.index]||null;
export const bundleFor = (state, advertId) => state.profiles.bundles[String(advertId)]||null;
export const currentProfileBundle = state => {const row=currentSearchRow(state);return row?profileBundleVm(bundleFor(state,row.advertId),row):null;};

const detailCache=new WeakMap();
export const detailFor=(state,personId)=>{
  const raw=state.profiles.details[String(personId)];
  if(!raw)return null;
  if(!detailCache.has(raw))detailCache.set(raw,personDetailVm(raw));
  return detailCache.get(raw);
};

let listSource=null,listCache=[];
export const listRows=state=>{
  const source=state.lists.items||[];
  if(source!==listSource){listSource=source;listCache=source.map(listItemVm);}
  return listCache;
};

let threadSource=null,threadQuery=null,threadCache=[];
export const threadRows=state=>{
  const source=state.messaging.threads||[], query=(state.messaging.query||'').trim().toLowerCase();
  if(source===threadSource&&query===threadQuery)return threadCache;
  threadSource=source;threadQuery=query;
  threadCache=source.map(threadVm).filter(row=>!query||`${row.name} ${row.preview} ${row.endpoint?.display_phone||''} ${row.endpoint?.normalized_phone||''}`.toLowerCase().includes(query));
  return threadCache;
};

const currentThreadCache=new WeakMap();
export const currentThread=state=>{
  const raw=state.messaging.currentThread;
  if(!raw)return null;
  if(!currentThreadCache.has(raw))currentThreadCache.set(raw,threadDetailVm(raw));
  return currentThreadCache.get(raw);
};

export const unreadTotal = state => (state.messaging.threads||[]).reduce((sum,row)=>sum+Number(row.unread_count||row.unread||0),0);
export const profileTab = (state,personId) => state.profileUi.tabByPerson[String(personId)]||'info';
export const profileMediaIndex = (state,personId) => Number(state.profileUi.mediaByPerson[String(personId)]||0);
export const rootNav = routeName => ({grid:'swipe',profile:null,chat:'messages',history:'settings',archive:'settings'}[routeName]??routeName);
