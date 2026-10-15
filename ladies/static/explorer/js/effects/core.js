import { api } from '../api/client.js';
import * as S from '../app/selectors.js';
import { filterPayload, profileBundleVm, formEnumWireValue } from '../data/adapters.js';

const SEARCH_RADII=[5,10,20,30,50,70,100];
const normalizeRadius=value=>SEARCH_RADII.includes(Number(value))?Number(value):20;

export function createCoreEffects({store,router}) {
  let previewAbort=null, previewTimer=null, previewSeq=0, businessScanSeq=0;
  const toast=message=>store.dispatch({type:'TOAST',value:message});
  const state=()=>store.getState();
  const searchPayload=()=>{const payload=filterPayload(state().search.filters);payload.radius=normalizeRadius(payload.radius);return payload;};
  const businessFilterEnabled=()=>state().search.filters.local?.hideBusinessProfiles!==false;

  function rawSearchRowAt(index=state().discover.index) {
    return S.discoverQueueRows(state())[index] || null;
  }

  async function loadSettings() {
    const hydrateDefaults=!state().settings.loaded;
    try {
      const [settings,storage,pricingRuntime] = await Promise.all([api.settings(),api.storage(),api.pricingStatus().catch(()=>({}))]);
      store.dispatch({type:'SETTINGS',values:settings?.settings || {}});
      store.dispatch({type:'STORAGE',value:storage || {}});
      store.dispatch({type:'PRICING_RUNTIME',value:pricingRuntime || {}});
      const defaults=settings?.settings?.default_filters;
      if (hydrateDefaults && defaults && typeof defaults === 'object') {
        const current=state().search.filters;
        const merged={...current,...defaults,selected:{...(current.selected||{}),...(defaults.selected||{})},local:{...(current.local||{}),...(defaults.local||{})}};
        merged.radius=normalizeRadius(merged.radius);
        store.dispatch({type:'SEARCH_FILTERS',filters:merged});
      }
      document.documentElement.classList.toggle('reduced-motion',!!settings?.settings?.reduced_motion);
    } catch (error) { console.warn(error); }
  }

  async function loadDashboard(showToast=false) {
    try { store.dispatch({type:'DASHBOARD',value:await api.dashboard()}); if(showToast)toast('Aktualisiert'); }
    catch(error){ if(showToast)toast(error.message); }
  }

  async function loadRecentSearches() {
    try { const d=await api.recentSearches(); store.dispatch({type:'RECENT_SEARCHES',items:d?.items || []}); }
    catch {}
  }

  async function openHistoryDrill(kind) {
    try {
      const d = kind === 'relistings' ? await api.historyRelistings(100) : await api.historyChanges(100);
      store.dispatch({type:'SHEET',value:{kind:'history-drill',payload:{kind,items:d?.items || []}}});
    } catch(error) { toast(error.message); }
  }

  function schedulePreview({immediate=false}={}) {
    clearTimeout(previewTimer);
    const sequence=++previewSeq;
    store.dispatch({type:'SEARCH_PREVIEW',patch:{status:'loading',count:null,error:null}});
    previewTimer=setTimeout(async()=>{
      previewAbort?.abort(); previewAbort=new AbortController();
      const payload=searchPayload();
      try {
        const data=await api.searchPreview(payload,previewAbort.signal);
        if(sequence!==previewSeq)return;
        store.dispatch({type:'SEARCH_PREVIEW',patch:{
          status:'ready',count:Number(data?.count || 0),error:null,
          requestedRadius:Number(data?.radius_km || payload.radius),
          effectiveRadius:Number(data?.effective_radius_km || data?.radius_km || payload.radius),
          radiusAutoExpanded:Boolean(data?.radius_auto_expanded)
        }});
      } catch(error) {
        if(error?.name==='AbortError'||sequence!==previewSeq)return;
        store.dispatch({type:'SEARCH_PREVIEW',patch:{status:'error',count:null,error:error.message,radiusAutoExpanded:false}});
      }
    }, immediate?0:280);
  }

  async function performSearch() {
    businessScanSeq+=1;
    store.dispatch({type:'SEARCH_LOADING',value:true});
    try {
      const data=await api.search(searchPayload());
      const meta=data?.meta || null;
      store.dispatch({type:'SEARCH_RESULTS',results:data?.results || [],meta});
      store.dispatch({type:'SEARCH_LOADING',value:false});
      await loadRecentSearches();
      router.replaceRoot('discover');
      if(meta?.radius_auto_expanded)toast(`Ladies hat den Umkreis automatisch von ${meta.requested_radius_km} auf ${meta.effective_radius_km} km erweitert.`);
      if(state().discover.mode==='swipe')await prefetchCurrent();
      void classifyBusinessProfiles();
    } catch(error) {
      store.dispatch({type:'SEARCH_LOADING',value:false,error:error.message}); toast(error.message);
    }
  }

  async function loadBundle(row,{detail=true,quiet=false}={}) {
    if(!row?.advertId)return null;
    let bundle=state().profiles.bundles[row.advertId];
    if(!bundle) {
      try {
        store.dispatch({type:'PROFILE_LOADING',key:`advert:${row.advertId}`,value:true});
        bundle=await api.profile(row.advertId);
        store.dispatch({type:'PROFILE_BUNDLE',advertId:row.advertId,bundle});
      } catch(error) { if(!quiet)toast(error.message); return null; }
      finally { store.dispatch({type:'PROFILE_LOADING',key:`advert:${row.advertId}`,value:false}); }
    }
    const vm=profileBundleVm(bundle,row);
    if(detail && vm.personId) await loadPerson(vm.personId,{quiet:true});
    return vm;
  }

  async function loadPerson(personId,{force=false,quiet=false}={}) {
    const key=String(personId); if(!force && state().profiles.details[key]) return state().profiles.details[key];
    try {
      store.dispatch({type:'PROFILE_LOADING',key:`person:${personId}`,value:true});
      const d=await api.person(personId); store.dispatch({type:'PROFILE_DETAIL',personId,detail:d}); return d;
    } catch(error) { if(!quiet)toast(error.message); return null; }
    finally { store.dispatch({type:'PROFILE_LOADING',key:`person:${personId}`,value:false}); }
  }

  async function classifyBusinessProfiles() {
    const sequence=++businessScanSeq;
    if(!businessFilterEnabled())return;
    const advertIds=S.searchRows(state()).filter(row=>!row.businessProfileKnown).map(row=>row.advertId).filter(Boolean);
    for(const advertId of advertIds){
      if(sequence!==businessScanSeq||!businessFilterEnabled())return;
      const rows=S.searchRows(state());
      const row=rows.find(item=>item.advertId===advertId);
      if(!row||row.businessProfileKnown)continue;
      const queue=S.discoverQueueRows(state());
      const queueIndex=queue.findIndex(item=>item.advertId===advertId);
      if(queueIndex<0||queueIndex<=state().discover.index)continue;
      try {
        const result=await api.profileClassification(advertId);
        if(sequence!==businessScanSeq)return;
        store.dispatch({
          type:'SEARCH_RESULT_PATCH',
          advertId,
          patch:{
            business_profile_known:Boolean(result?.business_profile_known),
            is_business_profile:Boolean(result?.is_business_profile),
            business_website_url:result?.business_website_url || null,
            business_profile_checked_at:result?.business_profile_checked_at || null
          }
        });
      } catch {}
    }
  }

  async function prefetchCurrent() {
    const seen=new Set();
    while(true){
      const row=rawSearchRowAt();
      if(!row||seen.has(row.advertId))return;
      seen.add(row.advertId);
      await loadBundle(row,{detail:true,quiet:true});
      const current=rawSearchRowAt();
      if(!businessFilterEnabled()||current?.advertId===row.advertId)break;
    }
    [1,2].forEach((offset)=>{const next=rawSearchRowAt(state().discover.index+offset);if(next)setTimeout(()=>loadBundle(next,{detail:false,quiet:true}),offset*300);});
  }

  async function mutatePerson(personId,patch) {
    if(!personId)throw new Error('Person konnte nicht aufgelöst werden');
    const data=await api.personState(personId,patch); store.dispatch({type:'PERSON_STATE_SYNC',personId,userState:data?.user_state || patch}); return data?.user_state || patch;
  }

  async function discoverDecision(decision) {
    const row=rawSearchRowAt(); if(!row)return;
    const bundleVm=await loadBundle(row,{detail:false,quiet:false}); const personId=bundleVm?.personId || row.personId;
    if(!personId)return toast('Person konnte nicht aufgelöst werden');
    try {
      const before={preference:bundleVm?.preference || row.preference || 'NONE',favorite:bundleVm?.favorite ?? row.favorite ?? false,index:state().discover.index,personId};
      const remainsInQueue=decision==='LIKE' && state().settings.values.replay_likes !== false;
      if(decision==='LIKE') await mutatePerson(personId,{preference:'LIKE'});
      else if(decision==='DISLIKE') await mutatePerson(personId,{preference:'DISLIKE'});
      else if(decision==='FAVORITE') await mutatePerson(personId,{favorite:!(bundleVm?.favorite ?? row.favorite)});
      store.dispatch({type:'DISCOVER_HISTORY_PUSH',entry:before});
      if(decision!=='FAVORITE')store.dispatch({type:'DISCOVER_INDEX',index:remainsInQueue?before.index+1:before.index});
      loadDashboard(); prefetchCurrent();
    } catch(error){toast(error.message);}
  }

  async function undoDecision() {
    const history=state().discover.history; const entry=history.at(-1); if(!entry)return toast('Nichts zum Zurücknehmen');
    try { await mutatePerson(entry.personId,{preference:entry.preference,favorite:entry.favorite}); store.dispatch({type:'DISCOVER_HISTORY_POP'}); store.dispatch({type:'DISCOVER_INDEX',index:entry.index}); await Promise.all([prefetchCurrent(),loadDashboard()]); }
    catch(error){toast(error.message);}
  }

  async function openProfile({personId,advertId}) {
    let pid=Number(personId || 0) || null;
    if(!pid && advertId) { const row=S.searchRows(state()).find(x=>x.advertId===String(advertId)); const vm=await loadBundle(row,{detail:false}); pid=vm?.personId || null; }
    if(!pid)return toast('Person nicht gefunden');
    router.push('profile',{personId:pid,advertId:advertId || null});
    await loadPerson(pid,{force:false});
  }

  async function refreshProfile(personId) { await loadPerson(personId,{force:true}); }

  async function loadList(tab=state().lists.tab) {
    store.dispatch({type:'LIST_LOADING',value:true});
    try { const d=await api.list(tab); store.dispatch({type:'LIST_ITEMS',items:d?.items || []}); }
    catch(error){store.dispatch({type:'LIST_LOADING',value:false});toast(error.message);}
  }

  async function runStatusCheck() {
    const current=state().lists.statusCheck;if(current.running)return;
    try {
      const d=await api.statusTargets(); const items=d?.items || [];
      store.dispatch({type:'STATUS_CHECK',patch:{running:true,done:0,total:items.length,checkedAt:null}});
      for(let i=0;i<items.length;i+=1){try{await api.profileStatus(items[i].advert_id);}catch{}store.dispatch({type:'STATUS_CHECK',patch:{done:i+1}});}
      store.dispatch({type:'STATUS_CHECK',patch:{running:false,checkedAt:new Date().toISOString()}});toast(`${items.length} Anzeigen geprüft`);await Promise.all([loadList(),loadDashboard()]);
    } catch(error){store.dispatch({type:'STATUS_CHECK',patch:{running:false}});toast(error.message);}
  }

  async function statusSingle(advertId) { if(!advertId)return;try{await api.profileStatus(advertId);toast('Status aktualisiert');await Promise.all([loadList(),loadDashboard()]);}catch(error){toast(error.message);} }

  async function saveProfileData(form) {
    const personId=Number(form.dataset.personId);const encounterId=Number(form.dataset.encounterId || 0);const fd=new FormData(form);
    const choice=name=>form.querySelector(`[data-choice-name="${name}"] .selected`)?.dataset.value || 'UNKNOWN';
    const rating=name=>form.querySelector(`[data-rating-name="${name}"] .selected`)?.dataset.value || null;
    try {
      const user=await api.personState(personId,{repeat_interest:formEnumWireValue('repeat_interest',choice('repeat_interest')),private_note:String(fd.get('private_note')||'')});
      store.dispatch({type:'PERSON_STATE_SYNC',personId,userState:user?.user_state || {}});
      const payload={duration_minutes:fd.get('duration_minutes')||null,total_price_eur:fd.get('total_price_eur')||null,overall_rating:rating('overall_rating'),sympathy_rating:rating('sympathy_rating'),photo_status:formEnumWireValue('photo_status',choice('photo_status')),service_accuracy:formEnumWireValue('service_accuracy',choice('service_accuracy')),hygiene:formEnumWireValue('hygiene',choice('hygiene')),kissing:formEnumWireValue('kissing',choice('kissing')),negotiable:formEnumWireValue('negotiable',choice('negotiable'))};
      if(encounterId) await api.updateEncounter(personId,encounterId,payload); else if(Object.values(payload).some(v=>v&&v!=='UNKNOWN')) await api.addEncounter(personId,payload);
      await Promise.all([loadPerson(personId,{force:true}),loadDashboard()]); toast('Meine Daten gespeichert');
    } catch(error){toast(error.message);}
  }

  async function addPrice(form) {
    const personId=Number(form.dataset.personId),fd=new FormData(form);
    try { await api.addPriceList(personId,{source:'USER_ENTERED',items:[{item_type:'BASE',duration_minutes:fd.get('minutes')||null,amount_eur:fd.get('amount'),label:fd.get('label')||null}]});store.dispatch({type:'SHEET',value:null});await Promise.all([loadPerson(personId,{force:true}),loadDashboard()]);toast('Preisliste gespeichert'); }
    catch(error){toast(error.message);}
  }

  async function merge(form) { const source=Number(form.dataset.sourcePersonId),target=Number(new FormData(form).get('target'));if(!target||target===source)return toast('Andere gültige Person-ID eingeben');try{await api.merge(source,target);store.dispatch({type:'SHEET',value:null});router.back();await openProfile({personId:target});loadDashboard();toast('Personen zusammengeführt');}catch(error){toast(error.message);} }

  async function split(listingId) { try{const d=await api.split(listingId);store.dispatch({type:'SHEET',value:null});toast(`Neue Person #${d.new_person_id}`);const route=S.currentRoute(state());await Promise.all([route.params?.personId?loadPerson(route.params.personId,{force:true}):Promise.resolve(),loadDashboard()]);}catch(error){toast(error.message);} }

  async function snapshotCompare(a,b) { try{const [x,y]=await Promise.all([api.snapshot(a),api.snapshot(b)]);store.dispatch({type:'SHEET',value:{kind:'snapshot',payload:{before:x?.snapshot,after:y?.snapshot}}});}catch(error){toast(error.message);} }

  return Object.freeze({loadSettings,loadDashboard,loadRecentSearches,openHistoryDrill,schedulePreview,performSearch,loadBundle,loadPerson,classifyBusinessProfiles,prefetchCurrent,mutatePerson,discoverDecision,undoDecision,openProfile,refreshProfile,loadList,runStatusCheck,statusSingle,saveProfileData,addPrice,merge,split,snapshotCompare,rawSearchRowAt,toast});
}
