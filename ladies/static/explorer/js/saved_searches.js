import { api } from './api/client.js';
import * as S from './app/selectors.js';
import { filtersWithAvailabilityMode, normalizeAvailabilityFilters } from './search_intelligence.js';

const STATUS_POLL_MS=60*1000;
const clone=value=>structuredClone(value);
const savedSearches=state=>Array.isArray(state.savedSearches?.items)?state.savedSearches.items:[];

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

function defaultSavedName(filters){
  const city=String(filters?.city||'Suche').trim()||'Suche';
  return `${city} · ${Number(filters?.radius||20)} km`;
}

export function installSavedSearches({store,effects,router}){
  let refreshPromise=null;
  let mutationChain=Promise.resolve();
  let mutationActive=0;
  let stateEpoch=0;
  const resultRequests=new Map();

  const applyItems=items=>store.dispatch({type:'SAVED_SEARCH_ITEMS',items:Array.isArray(items)?items:[]});
  const applyResults=(savedId,items)=>store.dispatch({type:'SAVED_SEARCH_RESULTS',savedId,items:Array.isArray(items)?items:[]});

  async function mutate(work,apply){
    const execute=async()=>{
      mutationActive+=1;
      stateEpoch+=1;
      try{
        const data=await work();
        stateEpoch+=1;
        apply?.(data);
        return data;
      }finally{
        mutationActive=Math.max(0,mutationActive-1);
        stateEpoch+=1;
      }
    };
    const pending=mutationChain.then(execute,execute);
    mutationChain=pending.catch(()=>{});
    return pending;
  }

  async function loadResults(savedId){
    const id=String(savedId||'');
    if(!id)return;
    const token=(resultRequests.get(id)||0)+1;
    resultRequests.set(id,token);
    const epoch=stateEpoch;
    store.dispatch({type:'SAVED_SEARCH_LOADING',savedId:id,value:true});
    try{
      const data=await api.savedSearchResults(id);
      if(resultRequests.get(id)!==token||epoch!==stateEpoch)return;
      applyResults(id,data?.items||[]);
    }finally{
      if(resultRequests.get(id)===token)store.dispatch({type:'SAVED_SEARCH_LOADING',savedId:id,value:false});
    }
  }

  async function refresh(){
    if(refreshPromise||mutationActive)return refreshPromise;
    const epoch=stateEpoch;
    refreshPromise=(async()=>{
      const data=await api.savedSearches();
      if(mutationActive||epoch!==stateEpoch)return;
      applyItems(data?.items||[]);
      const route=S.currentRoute(store.getState());
      if(route.name==='saved-search-results'&&route.params?.id)await loadResults(route.params.id);
    })();
    try{await refreshPromise;}finally{refreshPromise=null;}
  }

  async function markAllSeen(){
    await mutate(
      ()=>api.acknowledgeSavedSearches(),
      data=>applyItems(data?.items||[])
    );
  }

  async function saveCurrent(){
    const filters=normalizeAvailabilityFilters(store.getState().search.filters);
    const answer=prompt('Name für die gespeicherte Suche',defaultSavedName(filters));
    if(answer===null)return;
    const name=String(answer).trim();
    if(!name)return effects.toast('Bitte einen Namen eingeben');
    await mutate(
      ()=>api.createSavedSearch({name,filters:clone(filters)}),
      data=>applyItems(data?.items||[])
    );
    effects.toast('Suche gespeichert');
  }

  async function deleteSaved(id){
    const item=savedSearches(store.getState()).find(entry=>String(entry.id)===String(id));
    if(!item)return;
    if(!confirm(`Gespeicherte Suche „${item.name||'Suche'}“ löschen?`))return;
    await mutate(
      ()=>api.deleteSavedSearch(id),
      data=>applyItems(data?.items||[])
    );
    effects.toast('Gespeicherte Suche gelöscht');
  }

  async function runSaved(id,{silent=false}={}){
    const data=await mutate(
      ()=>api.runSavedSearch(id),
      response=>applyItems(response?.items||[])
    );
    if(data?.stale){if(!silent)effects.toast('Suche wurde während der Prüfung geändert');return data;}
    if(S.currentRoute(store.getState()).name==='saved-search-results'&&String(S.currentRoute(store.getState()).params?.id||'')===String(id))await loadResults(id);
    if(!silent)effects.toast(formatSavedResultDelta(data?.delta));
    return data;
  }

  async function openResults(id){
    const item=savedSearches(store.getState()).find(entry=>String(entry.id)===String(id));
    if(!item)return effects.toast('Gespeicherte Suche nicht gefunden');
    router.push('saved-search-results',{id:String(id)});
    if(!item.last_run_at){await runSaved(id,{silent:true});return;}
    await loadResults(id);
  }

  async function openResult(id,resultKey){
    const data=await api.openSavedSearchResult(id,resultKey);
    const row=data?.result||{};
    const advertId=String(row.advert_id||resultKey||'');
    const personId=Number(row.person_id||0)||null;
    if(!advertId)return effects.toast('Profil konnte nicht geöffnet werden');
    await effects.openProfile({personId,advertId});
  }

  async function saveEdit(form){
    const id=String(form.dataset.savedId||'');
    const item=savedSearches(store.getState()).find(entry=>String(entry.id)===id);
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
    const data=await mutate(
      ()=>api.updateSavedSearch(id,{name,filters}),
      response=>applyItems(response?.items||[])
    );
    if(data?.filters_changed)applyResults(id,[]);
    router.replace('saved-searches');
    effects.toast(data?.filters_changed?'Gespeicherte Suche aktualisiert · Vergleich wird neu aufgebaut':'Name aktualisiert');
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
      else if(action==='results')await openResults(el.dataset.savedId);
      else if(action==='open-result')await openResult(el.dataset.savedId,el.dataset.resultKey);
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

  void refresh();
  setInterval(()=>void refresh(),STATUS_POLL_MS);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void refresh();});
}
