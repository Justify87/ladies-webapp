import { api } from './api/client.js';
import { searchTransient } from './api/search_runtime.js';
import { filterPayload } from './data/adapters.js';
import * as S from './app/selectors.js';
import { filtersWithAvailabilityMode, normalizeAvailabilityFilters } from './search_intelligence.js';

const MAX_SAVED_SEARCHES=25;
const BACKGROUND_REFRESH_MS=15*60*1000;
const BACKGROUND_CHECK_MS=60*1000;
const clone=value=>structuredClone(value);
const savedSearches=state=>Array.isArray(state.settings?.values?.saved_searches)?state.settings.values.saved_searches:[];
const resultKey=row=>String(row?.advert_id??row?.advertId??row?.external_listing_id??'').trim();

function uniqueResultKeys(results){
  const seen=new Set();
  const keys=[];
  for(const row of results||[]){const key=resultKey(row);if(key&&!seen.has(key)){seen.add(key);keys.push(key);}}
  return keys;
}

function baselineFromItem(item){
  if(Array.isArray(item?.result_baseline?.keys))return item.result_baseline;
  const legacy=(item?.baseline?.listings||[]).map(entry=>String(entry?.advert_id||'').trim()).filter(Boolean);
  return legacy.length?{completed_at:item?.baseline?.completed_at||item?.last_run_at||null,keys:[...new Set(legacy)]}:null;
}

export function baselineFromSavedSearchResults(results,completedAt=new Date().toISOString()){
  return {completed_at:completedAt,keys:uniqueResultKeys(results)};
}

export function computeSavedResultDelta(item,results,completedAt=new Date().toISOString()){
  const current=baselineFromSavedSearchResults(results,completedAt);
  const previous=baselineFromItem(item);
  if(!previous)return {initial:true,current_result_count:current.keys.length,new_results_count:0,removed_results_count:0,new_result_keys:[]};
  const previousKeys=new Set(previous.keys||[]);
  const seenKeys=new Set((item?.seen_result_keys?.length?item.seen_result_keys:previous.keys||[]).map(String));
  const currentKeys=new Set(current.keys);
  const newKeys=current.keys.filter(key=>!seenKeys.has(key));
  const removed=[...previousKeys].filter(key=>!currentKeys.has(key));
  return {initial:false,current_result_count:current.keys.length,new_results_count:newKeys.length,removed_results_count:removed.length,new_result_keys:newKeys};
}

export function formatSavedResultDelta(delta){
  if(!delta)return 'Noch nicht ausgeführt';
  if(delta.initial)return `Vergleichsbasis · ${Number(delta.current_result_count||0)} Treffer`;
  const parts=[];
  if(delta.new_results_count)parts.push(`+${delta.new_results_count} neu`);
  if(delta.removed_results_count)parts.push(`${delta.removed_results_count} nicht mehr dabei`);
  return parts.length?parts.join(' · '):'Keine neuen Treffer';
}

export function savedSearchHasUnread(state){
  return savedSearches(state).some(item=>Boolean(item?.unread_new_results));
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
  return `${city} · ${Number(filters?.radius||20)} km`;
}

function filtersEqual(left,right){
  return JSON.stringify(normalizeAvailabilityFilters(left||{}))===JSON.stringify(normalizeAvailabilityFilters(right||{}));
}

export function installSavedSearches({store,effects,router}){
  let refreshing=false;
  let settingsReady=Boolean(store.getState().settings.loaded);

  async function markAllSeen(){
    const items=savedSearches(store.getState());
    if(!items.some(item=>item?.unread_new_results))return;
    await persistSavedSearches(store,items.map(item=>item?.unread_new_results?{...item,unread_new_results:false}:item));
  }

  async function saveCurrent(){
    const filters=normalizeAvailabilityFilters(store.getState().search.filters);
    const answer=prompt('Name für die gespeicherte Suche',defaultSavedName(filters));
    if(answer===null)return;
    const name=String(answer).trim();
    if(!name)return effects.toast('Bitte einen Namen eingeben');
    const now=new Date().toISOString();
    const item={id:savedSearchId(),name,filters:clone(filters),created_at:now,updated_at:now,last_run_at:null,result_baseline:null,seen_result_keys:[],last_delta:null,unread_new_results:false,new_results_at:null};
    await persistSavedSearches(store,[item,...savedSearches(store.getState())]);
    effects.toast('Suche gespeichert');
  }

  async function deleteSaved(id){
    const items=savedSearches(store.getState());
    const item=items.find(entry=>String(entry.id)===String(id));
    if(!item)return;
    if(!confirm(`Gespeicherte Suche „${item.name||'Suche'}“ löschen?`))return;
    await persistSavedSearches(store,items.filter(entry=>String(entry.id)!==String(id)));
    effects.toast('Gespeicherte Suche gelöscht');
  }

  async function runSaved(id,{silent=false}={}){
    const items=savedSearches(store.getState());
    const item=items.find(entry=>String(entry.id)===String(id));
    if(!item?.filters)return null;
    const normalized=normalizeAvailabilityFilters(item.filters);
    const data=await searchTransient(filterPayload(normalized));
    const completedAt=new Date().toISOString();
    const baseline=baselineFromSavedSearchResults(data?.results||[],completedAt);
    const delta=computeSavedResultDelta(item,data?.results||[],completedAt);
    const previous=baselineFromItem(item);
    const seen=[...new Set([...(item.seen_result_keys||[]),...(previous?.keys||[]),...baseline.keys])];
    const visible=S.currentRoute(store.getState()).name==='saved-searches';
    const next=items.map(entry=>String(entry.id)===String(id)?{
      ...entry,
      filters:clone(normalized),
      updated_at:completedAt,
      last_run_at:completedAt,
      result_baseline:baseline,
      seen_result_keys:seen,
      last_delta:delta,
      unread_new_results:visible?false:Boolean(entry.unread_new_results||delta.new_results_count>0),
      new_results_at:delta.new_results_count>0?completedAt:(entry.new_results_at||null)
    }:entry);
    await persistSavedSearches(store,next);
    if(!silent)effects.toast(formatSavedResultDelta(delta));
    return delta;
  }

  async function saveEdit(form){
    const id=String(form.dataset.savedId||'');
    const items=savedSearches(store.getState());
    const item=items.find(entry=>String(entry.id)===id);
    if(!item)return effects.toast('Gespeicherte Suche nicht gefunden');
    const fd=new FormData(form);
    let filters=clone(item.filters||{});
    filters.city=String(fd.get('city')||'').trim()||'Leipzig';
    filters.radius=Number(fd.get('radius')||20);
    filters.category=String(fd.get('category')||'');
    filters.ageMin=Number(fd.get('ageMin')||18);
    filters.ageMax=Number(fd.get('ageMax')||60);
    filters.heightMin=Number(fd.get('heightMin')||140);
    filters.heightMax=Number(fd.get('heightMax')||200);
    filters.selected={};
    for(const button of form.querySelectorAll('[data-saved-group-choice].selected')){
      const group=button.dataset.group,value=button.dataset.value;
      if(!group||!value)continue;
      (filters.selected[group]??=[]).push(value);
    }
    const availability=form.querySelector('[data-saved-availability].selected')?.dataset.mode||'ALL';
    filters=filtersWithAvailabilityMode(filters,availability);
    filters.local={
      ...(filters.local||{}),
      photosOnly:Boolean(form.querySelector('[data-saved-local="photosOnly"]')?.checked),
      videoOnly:Boolean(form.querySelector('[data-saved-local="videoOnly"]')?.checked),
      hideDislikes:Boolean(form.querySelector('[data-saved-local="hideDislikes"]')?.checked),
      hideBusinessProfiles:Boolean(form.querySelector('[data-saved-local="hideBusinessProfiles"]')?.checked),
      imageMode:form.querySelector('[data-saved-image-mode].selected')?.dataset.value||'ALL'
    };
    filters=normalizeAvailabilityFilters(filters);
    const name=String(fd.get('name')||'').trim();
    if(!name)return effects.toast('Bitte einen Namen eingeben');
    const changed=!filtersEqual(item.filters,filters);
    const now=new Date().toISOString();
    const updated=items.map(entry=>String(entry.id)===id?{
      ...entry,
      name,
      filters,
      updated_at:now,
      ...(changed?{last_run_at:null,result_baseline:null,seen_result_keys:[],last_delta:null,unread_new_results:false,new_results_at:null}:{})
    }:entry);
    await persistSavedSearches(store,updated);
    router.replace('saved-searches');
    effects.toast(changed?'Gespeicherte Suche aktualisiert · Vergleich wird neu aufgebaut':'Name aktualisiert');
  }

  async function refreshDue(){
    if(refreshing||!store.getState().settings.loaded)return;
    const route=S.currentRoute(store.getState());
    if(route.name==='saved-searches'&&route.params?.edit)return;
    refreshing=true;
    try{
      const snapshot=savedSearches(store.getState());
      const now=Date.now();
      for(const item of snapshot){
        const last=Date.parse(item?.last_run_at||0)||0;
        if(last&&now-last<BACKGROUND_REFRESH_MS)continue;
        try{await runSaved(item.id,{silent:true});}catch(error){console.warn('Saved search background refresh failed',item?.id,error);}
      }
    }finally{refreshing=false;}
  }

  document.addEventListener('click',async event=>{
    const el=event.target.closest('[data-saved-search-action]');
    if(!el)return;
    event.preventDefault();
    event.stopPropagation();
    const action=el.dataset.savedSearchAction;
    try{
      if(action==='open'){
        router.push('saved-searches');
        await markAllSeen();
      }else if(action==='save-current')await saveCurrent();
      else if(action==='run')await runSaved(el.dataset.savedId);
      else if(action==='delete')await deleteSaved(el.dataset.savedId);
      else if(action==='edit')router.replace('saved-searches',{edit:el.dataset.savedId});
      else if(action==='cancel-edit')router.replace('saved-searches');
      else if(action==='edit-filter-choice'){
        el.classList.toggle('selected');
        el.setAttribute('aria-pressed',el.classList.contains('selected')?'true':'false');
      }else if(action==='edit-availability'){
        el.closest('[data-saved-availability-group]')?.querySelectorAll('[data-saved-availability]').forEach(button=>button.classList.toggle('selected',button===el));
      }else if(action==='edit-image-mode'){
        el.closest('[data-saved-image-group]')?.querySelectorAll('[data-saved-image-mode]').forEach(button=>button.classList.toggle('selected',button===el));
      }
    }catch(error){console.error(error);effects.toast(error?.message||String(error));}
  });

  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(!form.matches('[data-saved-search-form]'))return;
    event.preventDefault();
    try{await saveEdit(form);}catch(error){console.error(error);effects.toast(error?.message||String(error));}
  });

  store.subscribe(()=>{
    if(!settingsReady&&store.getState().settings.loaded){settingsReady=true;setTimeout(refreshDue,800);}
  });
  if(settingsReady)setTimeout(refreshDue,800);
  setInterval(refreshDue,BACKGROUND_CHECK_MS);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void refreshDue();});
}
