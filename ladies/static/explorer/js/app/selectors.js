import { searchResultVm, profileBundleVm, personDetailVm, listItemVm, threadVm, threadDetailVm } from '../data/adapters.js';

export const currentRoute = state => state.navigation.stack.at(-1) || {name:'search',params:{}};
export const isSwipeDiscover = state => currentRoute(state).name==='discover' && state.discover.mode==='swipe';
export const pricingExtractionEnabled = state => state.settings.pricing?.price_intelligence_enabled === true;
export const pricingRuntimeReady = state => state.settings.pricingRuntime?.extractor?.ready === true;
export const pricingExtractionAvailable = state => pricingExtractionEnabled(state) && pricingRuntimeReady(state);
export const pricingManualAvailable = state => pricingExtractionAvailable(state) && state.settings.pricing?.price_magic_button_enabled === true;

export function businessProfileEvidence(row,bundle) {
  const profile=bundle?.profile || bundle?.snapshot || bundle?.latest_snapshot || {};
  const storedAuxiliary=profile?.auxiliary && typeof profile?.auxiliary==='object' ? profile.auxiliary : {};
  const nestedAuxiliary=storedAuxiliary?.auxiliary && typeof storedAuxiliary.auxiliary==='object' ? storedAuxiliary.auxiliary : storedAuxiliary;
  const website=String(
    profile?.business_website_url ||
    nestedAuxiliary?.business_website_url ||
    row?.business_website_url ||
    ''
  ).trim();
  const explicit=profile?.is_business_profile ?? row?.is_business_profile;
  const knownFlag=profile?.business_profile_known ?? row?.business_profile_known;
  const known=knownFlag===undefined
    ? (Boolean(bundle) || explicit !== undefined || Boolean(website))
    : Boolean(knownFlag);
  return {
    isBusinessProfile:Boolean(explicit || website),
    businessWebsiteUrl:website,
    businessProfileKnown:known
  };
}

export const searchRows = state => (state.search.results || []).map(raw=>{
  const row=searchResultVm(raw);
  const bundle=state.profiles.bundles[String(row.advertId)] || null;
  const identity=bundle?.identity || {};
  const canonicalPersonId=Number(identity.person_id || row.personId || 0) || null;
  const business=businessProfileEvidence(row,bundle);
  return {
    ...row,
    personId:canonicalPersonId,
    preference:identity.preference || row.preference || 'NONE',
    favorite:Boolean(identity.favorite ?? row.favorite),
    ...business
  };
});
export const currentSearchRow = state => discoverQueueRows(state)[state.discover.index] || null;
export function visibleSearchRows(state) {
  const local=state.search.filters.local || {};
  return searchRows(state).filter(row=>{
    if(local.videoOnly && !row.hasVideo)return false;
    if(local.hideDislikes!==false && row.preference==='DISLIKE')return false;
    if(local.hideLikes===true && row.preference==='LIKE')return false;
    if(local.hideBusinessProfiles!==false && row.isBusinessProfile)return false;
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
  return visibleSearchRows(state).filter(row=>replay || row.preference!=='LIKE');
}

export const bundleFor = (state, advertId) => state.profiles.bundles[String(advertId)] || null;
export const currentProfileBundle = state => { const row=currentSearchRow(state); return row ? profileBundleVm(bundleFor(state,row.advertId),row) : null; };
export const detailFor = (state,personId) => { const raw=state.profiles.details[String(personId)]; return raw ? personDetailVm(raw) : null; };
export const listRows = state => {
  const rows=(state.lists.items || []).map(listItemVm);
  const sort=String(state.lists.sort||'DATE').toUpperCase();
  return [...rows].sort((left,right)=>{
    if(sort==='NAME')return String(left.name||'').localeCompare(String(right.name||''),'de',{sensitivity:'base'});
    const time=row=>Date.parse(row.updated_at||row.last_seen_at||row.person_last_seen_at||row.recent_change?.created_at||row.created_at||0)||0;
    return time(right)-time(left);
  });
};
const threadClassification=row=>String(row.classification || (row.assigned?'LADIES_CONFIRMED':'UNKNOWN')).toUpperCase();
export const threadRows = state => {
  const query=(state.messaging.query || '').trim().toLowerCase();
  const filter=String(state.messaging.viewFilter||'ALL').toUpperCase();
  return (state.messaging.threads || []).map(threadVm).filter(row=>{
    const classification=threadClassification(row);
    if(filter==='UNASSIGNED'){
      if(classification!=='UNKNOWN')return false;
    }else{
      if(classification!=='LADIES_CONFIRMED')return false;
      if(filter==='UNREAD' && !row.unread)return false;
    }
    return !query || `${row.name} ${row.preview} ${row.endpoint?.display_phone||''} ${row.endpoint?.normalized_phone||''}`.toLowerCase().includes(query);
  });
};
export const currentThread = state => {
  const raw=state.messaging.currentThread;
  if(!raw)return null;
  const vm=threadDetailVm(raw);
  const endpointId=Number(vm.selectedEndpoint?.endpoint_id||vm.selected_endpoint?.endpoint_id||vm.endpoint?.endpoint_id||vm.endpoints?.[0]?.endpoint_id||0);
  const contact=endpointId?state.messaging.contactByEndpoint?.[String(endpointId)]||null:null;
  vm.whatsappContact=contact;
  vm.whatsappAvatar=contact?.profile_picture_url||'';
  return vm;
};
export const unreadTotal = state => (state.messaging.threads || []).map(threadVm).filter(row=>threadClassification(row)==='LADIES_CONFIRMED').reduce((sum,row)=>sum+Number(row.unread || 0),0);
export const profileTab = (state,personId) => state.profileUi.tabByPerson[String(personId)] || 'info';
export const profileMediaIndex = (state,personId) => Number(state.profileUi.mediaByPerson[String(personId)] || 0);
export const rootNav = routeName => ({profile:null,chat:'messages',history:'settings',archive:'settings'}[routeName] ?? routeName;
