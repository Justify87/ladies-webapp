const NOW_SLUG='jetzt-offen';
const TODAY_SLUG='heute-offen';
const LEGACY_NOW='jetzt geöffnet';
const LEGACY_TODAY='heute geöffnet';
const QUICK_TIME_VALUES=new Set([NOW_SLUG,TODAY_SLUG,LEGACY_NOW,LEGACY_TODAY]);

const clone=value=>structuredClone(value);

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

export function installSearchIntelligence({store,effects}) {
  if(document.documentElement.dataset.searchIntelligenceInstalled==='1')return;
  document.documentElement.dataset.searchIntelligenceInstalled='1';

  async function setAvailability(mode,{run=false}={}){
    const filters=filtersWithAvailabilityMode(store.getState().search.filters,mode);
    store.dispatch({type:'SEARCH_FILTERS',filters});
    effects.schedulePreview({immediate:true});
    if(run)await effects.performSearch();
  }

  document.addEventListener('click',async event=>{
    const el=event.target.closest('[data-search-intelligence-action]');
    if(!el)return;
    event.preventDefault();
    event.stopPropagation();
    try{
      const action=el.dataset.searchIntelligenceAction;
      if(action==='availability-select')await setAvailability(el.dataset.mode||'ALL',{run:false});
      else if(action==='availability-run')await setAvailability(el.dataset.mode||'ALL',{run:true});
    }catch(error){
      console.error(error);
      effects.toast(error?.message||String(error));
    }
  });
}
