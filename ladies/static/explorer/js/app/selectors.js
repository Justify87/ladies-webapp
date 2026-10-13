import { searchResultVm, profileBundleVm, personDetailVm, listItemVm, threadVm, threadDetailVm } from '../data/adapters.js';

export const currentRoute = state => state.navigation.stack.at(-1) || {name:'search',params:{}};
export const searchRows = state => (state.search.results || []).map(searchResultVm);
export const currentSearchRow = state => searchRows(state)[state.discover.index] || null;
export const bundleFor = (state, advertId) => state.profiles.bundles[String(advertId)] || null;
export const currentProfileBundle = state => { const row=currentSearchRow(state); return row ? profileBundleVm(bundleFor(state,row.advertId),row) : null; };
export const detailFor = (state,personId) => { const raw=state.profiles.details[String(personId)]; return raw ? personDetailVm(raw) : null; };
export const listRows = state => (state.lists.items || []).map(listItemVm);
export const threadRows = state => {
  const query=(state.messaging.query || '').trim().toLowerCase();
  return (state.messaging.threads || []).map(threadVm).filter(row => !query || `${row.name} ${row.preview}`.toLowerCase().includes(query));
};
export const currentThread = state => state.messaging.currentThread ? threadDetailVm(state.messaging.currentThread) : null;
export const unreadTotal = state => (state.messaging.threads || []).reduce((sum,row)=>sum+Number(row.unread_count || row.unread || 0),0);
export const profileTab = (state,personId) => state.profileUi.tabByPerson[String(personId)] || 'info';
export const profileMediaIndex = (state,personId) => Number(state.profileUi.mediaByPerson[String(personId)] || 0);
export const rootNav = routeName => ({grid:'swipe',profile:null,chat:'messages',history:'settings',archive:'settings'}[routeName] ?? routeName);
