import { api } from './api/client.js';
import * as S from './app/selectors.js';

const NOW_SLUG='jetzt-offen';
const TODAY_SLUG='heute-offen';
const LEGACY_NOW='jetzt geöffnet';
const LEGACY_TODAY='heute geöffnet';
const QUICK_TIME_VALUES=new Set([NOW_SLUG,TODAY_SLUG,LEGACY_NOW,LEGACY_TODAY]);
const MAX_SAVED_SEARCHES=25;

const clone=value=>structuredClone(value);
const toId=value=>Number(value||0)||null;
const savedSearches=state=>Array.isArray(state.settings?.values?.saved_searches)?state.settings.values.saved_searches:[];
const listingIdOf=row=>toId(row?.listing_id??row?.listingId);
const advertIdOf=row=>String(row?.advert_id??row?.advertId??'');

export function availabilityMode(filters={}) {
  const values=new Set(filters?.selected?.zeiten||[]);
  if(values.has(NOW_SLUG)||values.has(LEGACY_NOW))return 'NOW';
  if(values.has(TODAY_SLUG)||values.has(LEGACY_TODAY))return 'TODAY';
  return 'ALL';
}

export function normalizeAvailabilityFilters(filters={}) {
  const next=clone(filters||{});
  next.selected={...(next.selected||{})};
  const raw=Array.isArray(next.selected.zeiten)?next.selected.zeiten:[];
  const mode=availabilityMode(next);
  const values=[];
  for(const value of raw){
    const text=String(value);
    if(QUICK_TIME_VALUES.has(text))continue;
    if(!values.includes(text))values.push(text);
  }
  if(mode==='NOW')values.push(NOW_SLUG);
  else if(mode==='TODAY')values.push(TODAY_SLUG);
  if(values.length)next.selected.zeiten=values;
  else delete next.selected.zeiten;
  return next;
}

export function filtersWithAvailabilityMode(filters,mode='ALL') {
  const next=normalizeAvailabilityFilters(filters||{});
  const values=(next.selected.zeiten||[]).filter(value=>!QUICK_TIME_VALUES.has(String(value)));
  if(mode==='NOW')values.push(NOW_SLUG);
  else if(mode==='TODAY')values.push(TODAY_SLUG);
  if(values.length)next.selected.zeiten=values;
  else delete next.selected.zeiten;
  return next;
}

export function baselineFromResults(results,completedAt=new Date().toISOString()) {
  const people=[];
  const listings=[];
  const seenPeople=new Set();
  const seenListings=new Set();
  for(const row of results||[]){
    const personId=toId(row.person_id??row.personId);
    const listingId=listingIdOf(row);
    if(personId&&!seenPeople.has(personId)){
      seenPeople.add(personId);
      people.push(personId);
    }
    if(listingId&&!seenListings.has(listingId)){
      seenListings.add(listingId);
      listings.push({listing_id:listingId,person_id:personId,advert_id:advertIdOf(row)||null});
    }
  }
  return {completed_at:completedAt,people,listings};
}

function listingIdsFromBaseline(baseline){
  return (baseline?.listings||[]).map(item=>toId(item?.listing_id??item)).filter(Boolean);
}

function canonicalPerson(context,listingId,fallback=null){
  const mapped=context?.canonical_person_by_listing?.[String(listingId)];
  return toId(mapped)??toId(fallback);
}

function canonicalPeopleFromListings(listings,context){
  const people=new Set();
  for(const item of listings||[]){
    const listingId=toId(item?.listing_id??item);
    if(!listingId)continue;
    const personId=canonicalPerson(context,listingId,item?.person_id);
    if(personId)people.add(personId);
  }
  return people;
}

function canonicalRowsByPerson(results,context){
  const map=new Map();
  for(const row of results||[]){
    const listingId=listingIdOf(row);
    const personId=canonicalPerson(context,listingId,row.person_id??row.personId);
    if(personId&&!map.has(personId))map.set(personId,row);
  }
  return map;
}

export function computeSavedSearchDelta(savedSearch,results,context={},completedAt=new Date().toISOString(),identityRefreshFailed=0) {
  const current=baselineFromResults(results,completedAt);
  const previous=savedSearch?.baseline;
  if(!previous||!Array.isArray(previous.listings)){
    return {
      initial:true,
      current_people_count:canonicalPeopleFromListings(current.listings,context).size,
      current_listing_count:current.listings.length,
      new_people_count:0,
      returned_people_count:0,
      known_people_new_listing_count:0,
      new_listings_count:0,
      disappeared_people_count:0,
      observed_changed_people_count:0,
      identity_refresh_failed_count:Number(identityRefreshFailed||0),
      new_people_preview:[]
    };
  }

  const previousListings=new Set(listingIdsFromBaseline(previous));
  const seenListingIds=new Set((savedSearch?.seen_listing_ids?.length?savedSearch.seen_listing_ids:listingIdsFromBaseline(previous)).map(toId).filter(Boolean));
  const currentListings=new Set(current.listings.map(item=>item.listing_id));
  const previousPeople=canonicalPeopleFromListings(previous.listings,context);
  const seenPeople=canonicalPeopleFromListings([...seenListingIds].map(listing_id=>({listing_id})),context);
  const currentPeople=canonicalPeopleFromListings(current.listings,context);
  const byPerson=canonicalRowsByPerson(results,context);
  const newPeople=new Set([...currentPeople].filter(personId=>!seenPeople.has(personId)));
  const returnedPeople=new Set([...currentPeople].filter(personId=>seenPeople.has(personId)&&!previousPeople.has(personId)));
  const knownPeopleWithNewListing=new Set();

  for(const row of results||[]){
    const listingId=listingIdOf(row);
    if(!listingId||seenListingIds.has(listingId))continue;
    const personId=canonicalPerson(context,listingId,row.person_id??row.personId);
    if(personId&&seenPeople.has(personId))knownPeopleWithNewListing.add(personId);
  }

  const disappearedPeople=[...previousPeople].filter(personId=>!currentPeople.has(personId));
  const newListingCount=[...currentListings].filter(listingId=>!seenListingIds.has(listingId)).length;
  const observedChanged=new Set((context?.observed_changed_person_ids||[]).map(toId).filter(personId=>personId&&currentPeople.has(personId)));
  const newPeoplePreview=[...newPeople].slice(0,4).map(personId=>{
    const row=byPerson.get(personId)||{};
    return String(row.name||row.display_name||`Person #${personId}`);
  });

  return {
    initial:false,
    current_people_count:currentPeople.size,
    current_listing_count:current.listings.length,
    new_people_count:newPeople.size,
    returned_people_count:returnedPeople.size,
    known_people_new_listing_count:knownPeopleWithNewListing.size,
    new_listings_count:newListingCount,
    disappeared_people_count:disappearedPeople.length,
    observed_changed_people_count:observedChanged.size,
    identity_refresh_failed_count:Number(identityRefreshFailed||0),
    new_people_preview:newPeoplePreview
  };
}

export function formatSavedSearchDelta(delta,{compact=false}={}) {
  if(!delta)return 'Noch nicht ausgeführt';
  if(delta.initial)return compact?`Basis ${delta.current_people_count||0}`:`Vergleichsbasis gespeichert · ${delta.current_people_count||0} Personen`;
  const parts=[];
  if(delta.new_people_count)parts.push(`+${delta.new_people_count} neu`);
  if(delta.returned_people_count)parts.push(`${delta.returned_people_count} wieder da`);
  if(delta.known_people_new_listing_count)parts.push(`${delta.known_people_new_listing_count} neue Anzeige${delta.known_people_new_listing_count===1?'':'n'}`);
  if(delta.observed_changed_people_count)parts.push(`${delta.observed_changed_people_count} lokal geändert`);
  if(delta.disappeared_people_count)parts.push(`${delta.disappeared_people_count} weg`);
  if(delta.identity_refresh_failed_count)parts.push(`${delta.identity_refresh_failed_count} Identität offen`);
  if(!parts.length)parts.push('Keine Änderungen');
  return parts.join(' · ');
}

function savedSearchId(){
  if(globalThis.crypto?.randomUUID)return globalThis.crypto.randomUUID();
  return `saved-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
}

async function persistSavedSearches(store,items){
  const limited=items.slice(0,MAX_SAVED_SEARCHES);
  const response=await api.setSetting('saved_searches',limited);
  const current=store.getState().settings.values||{};
  store.dispatch({type:'SETTINGS',values:response?.settings||{...current,saved_searches:limited}});
  return limited;
}

function defaultSavedName(filters){
  const city=String(filters?.city||'Suche').trim()||'Suche';
  const radius=Number(filters?.radius||20);
  const mode=availabilityMode(filters);
  const suffix=mode==='NOW'?' · Jetzt':mode==='TODAY'?' · Heute':'';
  return `${city} · ${radius} km${suffix}`;
}

function historicalListingIds(item){
  const previous=listingIdsFromBaseline(item?.baseline);
  const seen=Array.isArray(item?.seen_listing_ids)?item.seen_listing_ids.map(toId).filter(Boolean):[];
  return [...new Set([...seen,...previous])];
}

async function refreshNewListingIdentities(item,results){
  if(!item?.baseline)return 0;
  const known=new Set(historicalListingIds(item));
  const fresh=(results||[]).filter(row=>{
    const listingId=listingIdOf(row);
    return listingId&&!known.has(listingId)&&advertIdOf(row);
  });
  let failed=0;
  for(const row of fresh){
    try{await api.profile(advertIdOf(row));}
    catch(error){failed+=1;console.warn('Saved-search identity refresh failed',advertIdOf(row),error);}
  }
  return failed;
}

export function installSearchIntelligence({store,effects,router}) {
  if(document.documentElement.dataset.searchIntelligenceInstalled==='1')return;
  document.documentElement.dataset.searchIntelligenceInstalled='1';

  async function saveCurrentSearch(){
    const filters=normalizeAvailabilityFilters(store.getState().search.filters);
    const proposed=defaultSavedName(filters);
    const answer=prompt('Name für die gespeicherte Suche',proposed);
    if(answer===null)return;
    const name=String(answer).trim();
    if(!name)return effects.toast('Bitte einen Namen eingeben');
    const now=new Date().toISOString();
    const item={
      id:savedSearchId(),
      name,
      filters,
      created_at:now,
      updated_at:now,
      last_run_at:null,
      baseline:null,
      seen_listing_ids:[],
      last_delta:null
    };
    await persistSavedSearches(store,[item,...savedSearches(store.getState())]);
    effects.toast('Suche gespeichert');
  }

  async function deleteSavedSearch(id){
    const items=savedSearches(store.getState());
    const item=items.find(entry=>String(entry.id)===String(id));
    if(!item)return;
    if(!confirm(`Gespeicherte Suche „${item.name||'Suche'}“ löschen?`))return;
    await persistSavedSearches(store,items.filter(entry=>String(entry.id)!==String(id)));
    effects.toast('Gespeicherte Suche gelöscht');
  }

  async function runSavedSearch(id){
    const items=savedSearches(store.getState());
    const item=items.find(entry=>String(entry.id)===String(id));
    if(!item?.filters)return effects.toast('Gespeicherte Suche nicht gefunden');
    const normalizedFilters=normalizeAvailabilityFilters(item.filters);
    store.dispatch({type:'SEARCH_FILTERS',filters:clone(normalizedFilters)});
    effects.schedulePreview({immediate:true});
    await effects.performSearch();
    let after=store.getState();
    if(after.search.error)return;

    const sourceResults=after.search.results||[];
    const identityRefreshFailed=await refreshNewListingIdentities(item,sourceResults);
    after=store.getState();
    const completedAt=new Date().toISOString();
    const currentBaseline=baselineFromResults(sourceResults,completedAt);
    const previousIds=historicalListingIds(item);
    const currentIds=listingIdsFromBaseline(currentBaseline);
    const allIds=[...new Set([...previousIds,...currentIds])];
    const context=await api.savedSearchContext({
      listing_ids:allIds,
      since:item?.baseline?.completed_at||item?.last_run_at||null
    });
    const delta=computeSavedSearchDelta(item,sourceResults,context,completedAt,identityRefreshFailed);
    const seenListingIds=[...new Set([...previousIds,...currentIds])];
    const next=items.map(entry=>String(entry.id)===String(id)?{
      ...entry,
      filters:clone(normalizedFilters),
      updated_at:completedAt,
      last_run_at:completedAt,
      baseline:currentBaseline,
      seen_listing_ids:seenListingIds,
      last_delta:delta
    }:entry);
    await persistSavedSearches(store,next);
    const latest=store.getState();
    store.dispatch({type:'SEARCH_RESULTS',results:latest.search.results||[],meta:{
      ...(latest.search.meta||{}),
      saved_search_id:item.id,
      saved_search_name:item.name,
      saved_search_delta:delta
    }});
    effects.toast(formatSavedSearchDelta(delta));
  }

  async function setAvailability(mode,{run=false}={}){
    const beforeRoute=S.currentRoute(store.getState()).name;
    const filters=filtersWithAvailabilityMode(store.getState().search.filters,mode);
    store.dispatch({type:'SEARCH_FILTERS',filters});
    effects.schedulePreview({immediate:true});
    if(!run)return;
    await effects.performSearch();
    if(beforeRoute==='grid')router.replaceRoot('grid');
  }

  document.addEventListener('click',async event=>{
    const el=event.target.closest('[data-search-intelligence-action]');
    if(!el)return;
    event.preventDefault();
    event.stopPropagation();
    try{
      const action=el.dataset.searchIntelligenceAction;
      if(action==='save-current')await saveCurrentSearch();
      else if(action==='run-saved')await runSavedSearch(el.dataset.savedId);
      else if(action==='delete-saved')await deleteSavedSearch(el.dataset.savedId);
      else if(action==='availability-select')await setAvailability(el.dataset.mode||'ALL',{run:false});
      else if(action==='availability-run')await setAvailability(el.dataset.mode||'ALL',{run:true});
    }catch(error){
      console.error(error);
      effects.toast(error?.message||String(error));
    }
  });
}
