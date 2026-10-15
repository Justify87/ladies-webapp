import { api } from './api/client.js';
import * as S from './app/selectors.js';
import { filtersWithAvailabilityMode, normalizeAvailabilityFilters } from './search_intelligence.js';

const STATUS_POLL_MS=60*1000;
const clone=value=>structuredClone(value);
const savedSearches=state=>Array.isArray(state.settings?.values?.saved_searches)?state.settings.values.saved_searches:[];

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
  let settingsReady=Boolean(store.getState().settings.loaded);
  let refreshPromise=null;

  function applyItems(items){
    const current=store.getState().settings.values||{};
    store.dispatch({type:'SETTINGS',values:{...current,saved_searches:Array.isArray(items)?items:[]}});
  }

  async function refresh({ackVisible=false}={}){
    if(!store.getState().settings.loaded)return;
    if(refreshPromise)return refreshPromise;
    refreshPromise=(async()=>{
      const data=await api.savedSearches();
      applyItems(data?.items||[]);
      if(ackVisible&&S.currentRoute(store.getState()).name==='saved-searches'&&savedSearchHasUnread(store.getState())){
        const acknowledged=await api.acknowledgeSavedSearches();
        applyItems(acknowledged?.items||[]);
      }
    })();
    try{await refreshPromise;}finally{refreshPromise=null;}
  }

  async function markAllSeen(){
    const data=await api.acknowledgeSavedSearches();
    applyItems(data?.items||[]);
  }

  async function saveCurrent(){
    const filters=normalizeAvailabilityFilters(store.getState().search.filters);
    const answer=prompt('Name für die gespeicherte Suche',defaultSavedName(filters));
    if(answer===null)return;
    const name=String(answer).trim();
    if(!name)return effects.toast('Bitte einen Namen eingeben');
    const data=await api.createSavedSearch({name,filters:clone(filters)});
    applyItems(data?.items||[]);
    effects.toast('Suche gespeichert');
  }

  async function deleteSaved(id){
    const item=savedSearches(store.getState()).find(entry=>String(entry.id)===String(id));
    if(!item)return;
    if(!confirm(`Gespeicherte Suche „${item.name||'Suche'}“ löschen?`))return;
    const data=await api.deleteSavedSearch(id);
    applyItems(data?.items||[]);
    effects.toast('Gespeicherte Suche gelöscht');
  }

  async function runSaved(id){
    const data=await api.runSavedSearch(id);
    applyItems(data?.items||[]);
    if(data?.stale)return effects.toast('Suche wurde während der Prüfung geändert');
    effects.toast(formatSavedResultDelta(data?.delta));
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
    const data=await api.updateSavedSearch(id,{name,filters});
    applyItems(data?.items||[]);
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
    if(!settingsReady&&store.getState().settings.loaded){settingsReady=true;void refresh();}
  });
  if(settingsReady)void refresh();
  setInterval(()=>{if(store.getState().settings.loaded)void refresh({ackVisible:true});},STATUS_POLL_MS);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void refresh({ackVisible:true});});
}
