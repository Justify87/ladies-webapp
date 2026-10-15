import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import {
  availabilityMode,
  filtersWithAvailabilityMode,
  normalizeAvailabilityFilters
} from '../ladies/static/explorer/js/search_intelligence.js';
import { savedSearchHasUnread } from '../ladies/static/explorer/js/saved_searches.js';
import { installActiveSearchSync } from '../ladies/static/explorer/js/search_runtime.js';
import { createMapEffects, mapRequestDescriptor } from '../ladies/static/explorer/js/effects/map.js';
import { filterMapFeatures } from '../ladies/static/explorer/js/ui/discover_map.js';
import { threadRows, unreadTotal } from '../ladies/static/explorer/js/app/selectors.js';
import { canonicalPriceRows, invalidatesProfilePricing } from '../ladies/static/explorer/js/requested_fixes.js';

// Canonical availability slugs must be the only values written back to state.
assert.equal(availabilityMode({selected:{zeiten:['jetzt-offen']}}),'NOW');
assert.equal(availabilityMode({selected:{zeiten:['heute-offen']}}),'TODAY');
assert.equal(availabilityMode({selected:{zeiten:['jetzt geöffnet']}}),'NOW');
assert.deepEqual(
  normalizeAvailabilityFilters({selected:{zeiten:['heute geöffnet','nach-22']}}).selected.zeiten,
  ['nach-22','heute-offen']
);
assert.deepEqual(
  filtersWithAvailabilityMode({selected:{zeiten:['jetzt-offen','nach-22']}},'TODAY').selected.zeiten,
  ['nach-22','heute-offen']
);
assert.deepEqual(
  filtersWithAvailabilityMode({selected:{zeiten:['heute-offen','nach-22']}},'ALL').selected.zeiten,
  ['nach-22']
);
assert.equal(savedSearchHasUnread({savedSearches:{items:[{unread_new_results:false},{unread_new_results:true}]}}),true);
assert.equal(savedSearchHasUnread({savedSearches:{items:[{unread_new_results:false}]}}),false);

// Active map identity comes only from current canonical results and carries the
// row-level city fallback for listings that have not been profile-ingested yet.
const descriptor=mapRequestDescriptor({search:{results:[
  {listing_id:7,city:'Leipzig'},
  {listing_id:8,city:'Markkleeberg'},
  {listing_id:7,city:'Leipzig'}
]}});
assert.deepEqual(descriptor.items,[
  {listing_id:7,city:'Leipzig'},
  {listing_id:8,city:'Markkleeberg'}
]);
assert.equal(descriptor.key,JSON.stringify([[7,'leipzig'],[8,'markkleeberg']]));

// A logical location remains one map point even when multiple profiles share it;
// local filters reduce the profile references without creating overlapping points.
const groupedPoints={type:'FeatureCollection',features:[{
  type:'Feature',geometry:{type:'Point',coordinates:[12.3,51.3]},properties:{
    location_key:'postal:DE:04109',profile_count:2,
    profiles_json:JSON.stringify([{person_id:1,listing_id:11,advert_id:'a'},{person_id:2,listing_id:22,advert_id:'b'}])
  }
}]};
const groupedVisible=filterMapFeatures(groupedPoints,[{personId:1,listing_id:11,advertId:'a'},{personId:2,listing_id:22,advertId:'b'}]);
assert.equal(groupedVisible.features.length,1);
assert.equal(groupedVisible.features[0].properties.profile_count,2);
const groupedFiltered=filterMapFeatures(groupedPoints,[{personId:2,listing_id:22,advertId:'b'}]);
assert.equal(groupedFiltered.features.length,1);
assert.equal(groupedFiltered.features[0].properties.profile_count,1);
assert.equal(JSON.parse(groupedFiltered.features[0].properties.profiles_json)[0].person_id,2);

// A late response from an older active result set must never replace newer map data.
const mapState={
  search:{results:[{listing_id:1,city:'Leipzig'}]},
  discover:{map:{key:null,points:null,loading:false,error:null}}
};
const mapStore={
  getState:()=>mapState,
  dispatch(action){
    if(action.type==='DISCOVER_MAP_LOADING')mapState.discover.map={...mapState.discover.map,key:action.key,loading:Boolean(action.value),error:action.error||null};
    else if(action.type==='DISCOVER_MAP_POINTS')mapState.discover.map={key:action.key,points:action.points,loading:false,error:null};
    else if(action.type==='DISCOVER_MAP_CLEAR')mapState.discover.map={key:null,points:null,loading:false,error:null};
  }
};
const mapRequests=[];
const mapEffects=createMapEffects({
  store:mapStore,
  mapPoints:payload=>new Promise(resolve=>mapRequests.push({payload,resolve}))
});
const firstMapLoad=mapEffects.loadMapPoints();
assert.deepEqual(mapRequests[0].payload.items,[{listing_id:1,city:'Leipzig'}]);
mapState.search.results=[{listing_id:2,city:'Grimma'}];
const secondMapLoad=mapEffects.loadMapPoints();
assert.deepEqual(mapRequests[1].payload.items,[{listing_id:2,city:'Grimma'}]);
mapRequests[1].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'new'}});
await secondMapLoad;
mapRequests[0].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'old'}});
await firstMapLoad;
assert.equal(mapState.discover.map.points.meta.marker,'new','late map responses must be ignored');

// Manual A must suppress only the concurrent automatic fetch, not future A hydration after B.
const baseFilters=city=>({city,radius:20,category:'',ageMin:18,ageMax:45,heightMin:140,heightMax:200,selected:{},local:{}});
const runtimeState={
  search:{filters:baseFilters('Leipzig'),preview:{status:'idle'},loading:false,error:null,results:[],meta:null},
  navigation:{stack:[{name:'search',params:{}}]},
  discover:{mode:'swipe'}
};
const runtimeListeners=new Set();
const runtimeStore={
  getState:()=>runtimeState,
  subscribe(fn){runtimeListeners.add(fn);return()=>runtimeListeners.delete(fn);},
  dispatch(action){
    if(action.type==='SEARCH_FILTERS')runtimeState.search.filters=action.filters;
    else if(action.type==='SEARCH_PREVIEW')runtimeState.search.preview={...runtimeState.search.preview,...action.patch};
    else if(action.type==='SEARCH_LOADING'){runtimeState.search.loading=Boolean(action.value);runtimeState.search.error=action.error||null;}
    else if(action.type==='SEARCH_RESULTS'){runtimeState.search.results=action.results||[];runtimeState.search.meta=action.meta||null;}
    for(const fn of runtimeListeners)fn(runtimeState,action);
  }
};
const runtimeCalls=[];
installActiveSearchSync({
  store:runtimeStore,
  effects:{prefetchCurrent:async()=>{}},
  search:async payload=>{runtimeCalls.push(payload.city);return {results:[{advert_id:String(runtimeCalls.length),person_id:runtimeCalls.length}],meta:{}};}
});
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
await tick();
runtimeStore.dispatch({type:'SEARCH_LOADING',value:true});
runtimeStore.dispatch({type:'SEARCH_RESULTS',results:[{advert_id:'manual-a',person_id:10}],meta:{}});
runtimeStore.dispatch({type:'SEARCH_LOADING',value:false});
runtimeStore.dispatch({type:'SEARCH_PREVIEW',patch:{status:'ready'}});
await tick();
assert.deepEqual(runtimeCalls,[],'successful manual A must be treated as hydrated');

runtimeStore.dispatch({type:'SEARCH_FILTERS',filters:baseFilters('Berlin')});
runtimeStore.dispatch({type:'SEARCH_PREVIEW',patch:{status:'loading'}});
runtimeStore.dispatch({type:'SEARCH_PREVIEW',patch:{status:'ready'}});
await tick();
assert.deepEqual(runtimeCalls,['Berlin'],'B must hydrate automatically');

runtimeStore.dispatch({type:'SEARCH_FILTERS',filters:baseFilters('Leipzig')});
runtimeStore.dispatch({type:'SEARCH_PREVIEW',patch:{status:'loading'}});
runtimeStore.dispatch({type:'SEARCH_PREVIEW',patch:{status:'ready'}});
await tick();
assert.deepEqual(runtimeCalls,['Berlin','Leipzig'],'returning to A must hydrate automatically again');

// Saved searches must execute through the read-only probe boundary. An edit during
// an in-flight probe invalidates that result, while explicit result opening is the
// only bridge back into canonical profile materialization.
execFileSync('python', ['-c', String.raw`
import copy
import runpy
import threading

module = runpy.run_path('ladies/core/services/saved_searches.py')
filter_results = module['filter_saved_search_results']
SavedSearchService = module['SavedSearchService']

rows = [
    {'advert_id':'1','thumbnail_url':'x','has_video':True,'preference':'NONE','is_business_profile':False,'has_ai_content':False,'markers':[]},
    {'advert_id':'2','thumbnail_url':'x','has_video':False,'preference':'NONE','is_business_profile':False,'has_ai_content':False,'markers':[]},
    {'advert_id':'3','thumbnail_url':'x','has_video':True,'preference':'DISLIKE','is_business_profile':False,'has_ai_content':False,'markers':[]},
    {'advert_id':'4','thumbnail_url':'x','has_video':True,'preference':'NONE','is_business_profile':True,'has_ai_content':False,'markers':[]},
    {'advert_id':'5','thumbnail_url':'x','has_video':True,'preference':'NONE','is_business_profile':False,'has_ai_content':True,'markers':[]},
]
filters = {'local':{'photosOnly':True,'videoOnly':True,'hideDislikes':True,'hideBusinessProfiles':True,'imageMode':'NATURAL'}}
assert [row['advert_id'] for row in filter_results(rows, filters)] == ['1']

class Repository:
    def __init__(self): self.items={}; self.results_by_id={}
    def list(self): return [copy.deepcopy(value) for value in self.items.values()]
    def get(self, saved_id):
        value=self.items.get(str(saved_id)); return copy.deepcopy(value) if value else None
    def create(self, saved_id, *, name, filters):
        self.items[str(saved_id)]={'id':str(saved_id),'name':name,'filters':copy.deepcopy(filters),'definition_version':1,'last_run_at':None,'last_attempt_at':None,'last_error':None,'last_delta':None,'unread_new_results':False}
        return self.list()
    def update_definition(self, saved_id, *, name, filters):
        item=self.items[str(saved_id)]; changed=item['filters']!=filters; item['name']=name; item['filters']=copy.deepcopy(filters)
        if changed: item['definition_version']+=1; item['last_run_at']=None; self.results_by_id[str(saved_id)]=[]
        return self.list(), changed
    def delete(self, saved_id): del self.items[str(saved_id)]; return self.list()
    def acknowledge_all(self):
        for item in self.items.values(): item['unread_new_results']=False
        return self.list()
    def record_success(self, saved_id, *, definition_version, results, visible_keys, mark_seen):
        item=self.items.get(str(saved_id))
        if not item or item['definition_version']!=definition_version: return {'stale':True,'delta':None}
        visible=[copy.deepcopy(row) for row in results if row['advert_id'] in visible_keys]
        self.results_by_id[str(saved_id)]=visible
        item['last_run_at']='done'; item['last_attempt_at']='done'; item['last_error']=None
        delta={'initial':True,'current_result_count':len(visible),'new_results_count':0,'removed_results_count':0,'new_result_keys':[]}
        item['last_delta']=delta
        return {'stale':False,'delta':delta}
    def record_failure(self, saved_id, *, definition_version, error):
        item=self.items.get(str(saved_id))
        if item and item['definition_version']==definition_version: item['last_attempt_at']='failed'; item['last_error']=error; return True
        return False
    def results(self, saved_id): return copy.deepcopy(self.results_by_id.get(str(saved_id),[]))
    def result(self, saved_id, result_key):
        return next((copy.deepcopy(row) for row in self.results(saved_id) if row['advert_id']==result_key),None)

class Search:
    def __init__(self): self.started=threading.Event(); self.release=threading.Event(); self.materialized=[]
    def execute_probe(self, payload):
        self.started.set(); self.release.wait(2)
        return {'results':[{'advert_id':'10','profile_url':'https://example/10','thumbnail_url':'x','has_video':True,'preference':'NONE','is_business_profile':False,'has_ai_content':False,'markers':[]}], 'meta':{}}
    def materialize_probe_result(self, payload): self.materialized.append(payload['advert_id']); return {'advert_id':payload['advert_id'],'person_id':99}

repo=Repository(); search=Search(); service=SavedSearchService(repo, search)
items=service.create(name='Alt', filters={'city':'Leipzig','radius':20,'category':'','ageMin':18,'ageMax':45,'heightMin':140,'heightMax':200,'selected':{},'local':{}})
saved_id=items[0]['id']
result={}
thread=threading.Thread(target=lambda: result.update(service.run(saved_id, mark_seen=False)))
thread.start(); assert search.started.wait(1)
service.update(saved_id, name='Neu', filters={'city':'Berlin','radius':20,'category':'','ageMin':18,'ageMax':45,'heightMin':140,'heightMax':200,'selected':{},'local':{}})
search.release.set(); thread.join(2)
assert result.get('stale') is True
assert service.list()[0]['name']=='Neu' and service.list()[0]['filters']['city']=='Berlin'

search.started.clear(); search.release.set(); service.run(saved_id, mark_seen=True)
assert [row['advert_id'] for row in service.results(saved_id)]==['10']
opened=service.materialize_result(saved_id,'10')
assert opened['person_id']==99 and search.materialized==['10']
`], {stdio:'inherit'});

execFileSync('python', ['tools/check_map_view.py'], {stdio:'inherit'});

// Message filters are local views over one canonical all-thread collection.
const messageState={messaging:{query:'',viewFilter:'UNASSIGNED',threads:[
  {thread_id:1,person_id:11,display_name:'A',unread_count:2},
  {thread_id:2,person_id:22,display_name:'B',unread_count:3},
  {thread_id:3,person_id:null,display_name:'C',unread_count:1}
]}};
assert.deepEqual(threadRows(messageState).map(row=>row.threadId),[3],'UNASSIGNED must only filter the visible list locally');
assert.equal(unreadTotal(messageState),6,'global unread count must still include threads hidden by the active view filter');

// Profile pricing must render every monetary component from the canonical proposal.
const canonicalRows=canonicalPriceRows({
  offers:[{label:'30 Minuten',amount_cents:10000}],
  service_rules:[{raw_label:'Massage',status:'SURCHARGE',amount_cents:2000},{raw_label:'Küssen',status:'INCLUDED',amount_cents:null}],
  conditions:[{label:'Hausbesuch',amount_cents:5000},{label:'Nur nach Absprache',amount_cents:null}]
});
assert.deepEqual(canonicalRows.map(row=>row.amount_cents),[10000,2000,5000],'offers, service surcharges and monetary conditions must all be visible');
assert.match(canonicalRows[1].label,/Massage/,'service price rows keep their service label');
assert.match(canonicalRows[2].label,/Hausbesuch/,'condition price rows keep their condition label');
assert.equal(invalidatesProfilePricing('PRICING_THREAD'),true);
assert.equal(invalidatesProfilePricing('PRICING_PROPOSAL'),true);
assert.equal(invalidatesProfilePricing('PROFILE_TAB'),false);

// Focused guards for Explorer architecture and follow-up fixes.
const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const discoverMapSource=readFileSync(new URL('../ladies/static/explorer/js/ui/discover_map.js',import.meta.url),'utf8');
const mapEffectsSource=readFileSync(new URL('../ladies/static/explorer/js/effects/map.js',import.meta.url),'utf8');
const mapServiceSource=readFileSync(new URL('../ladies/core/services/map.py',import.meta.url),'utf8');
const mapRepositorySource=readFileSync(new URL('../ladies/core/repositories/map.py',import.meta.url),'utf8');
const mapViewSource=readFileSync(new URL('../ladies/map_view.py',import.meta.url),'utf8');
const appSource=readFileSync(new URL('../ladies/app.py',import.meta.url),'utf8');
const profileSource=readFileSync(new URL('../ladies/static/explorer/js/ui/profile.js',import.meta.url),'utf8');
const prototypeSource=readFileSync(new URL('../ladies/static/explorer/css/prototype.css',import.meta.url),'utf8');
const chromeSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/chrome.js',import.meta.url),'utf8');
const searchScreenSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/search.js',import.meta.url),'utf8');
const savedSearchScreenSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/saved_searches.js',import.meta.url),'utf8');
const savedSearchSource=readFileSync(new URL('../ladies/static/explorer/js/saved_searches.js',import.meta.url),'utf8');
const savedSearchBackendSource=readFileSync(new URL('../ladies/core/services/saved_searches.py',import.meta.url),'utf8');
const savedSearchRepositorySource=readFileSync(new URL('../ladies/core/repositories/saved_searches.py',import.meta.url),'utf8');
const settingsRepositorySource=readFileSync(new URL('../ladies/core/repositories/settings.py',import.meta.url),'utf8');
const savedSearchWebSource=readFileSync(new URL('../ladies/saved_searches.py',import.meta.url),'utf8');
const activeSearchSource=readFileSync(new URL('../ladies/static/explorer/js/search_runtime.js',import.meta.url),'utf8');
const searchServiceSource=readFileSync(new URL('../ladies/core/services/search.py',import.meta.url),'utf8');
const searchRepositorySource=readFileSync(new URL('../ladies/core/repositories/search.py',import.meta.url),'utf8');
const searchPreviewSource=readFileSync(new URL('../ladies/search_preview.py',import.meta.url),'utf8');
const clientSource=readFileSync(new URL('../ladies/static/explorer/js/api/client.js',import.meta.url),'utf8');
const encounterSource=readFileSync(new URL('../ladies/static/explorer/js/effects/encounters.js',import.meta.url),'utf8');
const requestedSource=readFileSync(new URL('../ladies/static/explorer/js/requested_fixes.js',import.meta.url),'utf8');
const canonicalPricingSource=readFileSync(new URL('../ladies/pricing/canonical.py',import.meta.url),'utf8');
const pricingWebSource=readFileSync(new URL('../ladies/pricing/web.py',import.meta.url),'utf8');

assert.match(discoverSource,/\['swipe','cards','Swipe'\].*\['grid','grid','Grid'\].*\['map','pin','Karte'\]/s,'Entdecken must expose explicit Swipe, Grid and Karte modes');
assert.match(discoverSource,/data-action="discover-view"/,'view mode switching must use the normal command boundary');
assert.match(discoverSource,/grid-card-heart[^>]+data-action="grid-like"/s,'visible Grid heart must trigger Like');
assert.doesNotMatch(discoverSource,/discover-mode-pills/,'availability controls must not be duplicated inside discovery content');
assert.match(chromeSource,/header-discovery-modes/,'availability controls must stay in the shared header');
assert.match(chromeSource,/\['discover','cards','Entdecken'\]/,'bottom navigation must expose Entdecken instead of Swipe');
assert.match(chromeSource,/route\.name==='discover'/,'shared header must recognize one discovery route');
assert.doesNotMatch(chromeSource,/gridGlyph|cardsGlyph|view-switch/,'retired two-state header toggles must not return');

assert.match(discoverMapSource,/maplibre-gl@6\.14\.0/,'MapLibre must remain pinned to the reviewed version');
assert.match(discoverMapSource,/tiles\.openfreemap\.org\/styles\/liberty/,'map must use OpenFreeMap Liberty');
assert.match(discoverMapSource,/cluster:true/,'map locations must use MapLibre clustering');
assert.match(discoverMapSource,/clusterProperties:\{profile_total/,'cluster labels must aggregate profiles, not location buckets');
assert.match(discoverMapSource,/discover-map-location-sheet/,'same-location profiles must open an explicit list instead of overlapping');
assert.match(mapEffectsSource,/mapRequestDescriptor\(store\.getState\(\)\)/,'map requests must derive from the active result set');
assert.match(mapEffectsSource,/sequence!==generation\|\|latest\.key!==requested\.key/,'stale map responses must be rejected');
assert.match(mapEffectsSource,/validCoordinatePair/,'frontend must reject invalid map coordinates before MapLibre sees them');
assert.doesNotMatch(mapEffectsSource,/recentSearches|search_run_id|runId/,'map must not fall back to historical search runs');
assert.match(clientSource,/mapPoints:\s*payload\s*=>\s*request\('\/api\/map-points'/,'frontend must use the local map API');
assert.match(mapViewSource,/@app\.post\("\/api\/map-points"\)/,'map API must be POST-only');
assert.match(mapViewSource,/payload\.get\("items"\)/,'map API must accept explicit active result identities plus row-level cities');
assert.doesNotMatch(mapViewSource,/fallback_city|listing_ids/,'retired global-city/listing-id-only map payload must not return');
assert.doesNotMatch(mapViewSource,/search_run_id/,'map API must not depend on SearchRun history');
assert.match(mapRepositorySource,/ListingSnapshot\.snapshot_id == Listing\.current_snapshot_id/,'map repository must join only the canonical current snapshot');
assert.doesNotMatch(mapRepositorySource,/order_by\(|captured_at/,'map repository must not load full snapshot history');
assert.match(mapServiceSource,/active_city_by_listing/,'map service must use each active row city only when canonical snapshot location is absent');
assert.match(mapServiceSource,/_valid_coordinates/,'map service must validate coordinates before caching or emitting GeoJSON');
assert.match(mapServiceSource,/0 if spec\["kind"\] == "postal" else 1/,'postal geocoding must be prioritized over city fallback');
assert.match(mapServiceSource,/postal_record\.get\("status"\) == "NOT_FOUND"/,'city fallback must wait for a definitively unresolved postcode');
assert.match(mapServiceSource,/background\.submit/,'missing geocodes must run in a background executor');
assert.match(appSource,/map_background = BackgroundTasks\(max_workers=1, max_pending=4\)/,'map geocoding must have an isolated bounded executor');
assert.match(appSource,/install_map_view\(app, background=map_background/,'map geocoding must not consume the shared domain background executor');
assert.match(mapServiceSource,/map-location-cache\.json/,'resolved locations must be cached locally');
assert.match(mapServiceSource,/with self\._lock:\s*payload = orjson\.dumps[\s\S]*tmp\.replace\(self\.cache_path\)/,'cache serialization and atomic replace must share one lock');
assert.match(mapServiceSource,/POSTAL_RE/,'postcodes must be derived locally from stored data');
assert.match(mapServiceSource,/groups\.setdefault\(location_key/,'profiles sharing a PLZ/search area must be grouped into one logical location');
const featureCollectionBlock=mapServiceSource.split('def feature_collection',2)[1]||'';
assert.doesNotMatch(featureCollectionBlock,/_http\.get\(/,'normal map requests must never perform synchronous external geocoding');
assert.doesNotMatch(mapServiceSource,/params=\{[^}]*hidden_location/s,'hidden locations must never be sent to external geocoders');

assert.match(chromeSource,/data-saved-search-action="open"/,'search header action must open the dedicated saved-search screen');
assert.match(chromeSource,/saved-search-notification/,'header must expose a dedicated new-result indicator');
assert.match(chromeSource,/saved-search-results/,'saved result browsing must have its own header route');
assert.doesNotMatch(searchScreenSource,/savedSearchesCard|saved-searches-card/,'saved searches must no longer render inside the filter screen');
assert.match(savedSearchScreenSource,/data-saved-search-action="results"/,'saved searches must expose their cached result list');
assert.match(savedSearchScreenSource,/data-saved-search-action="open-result"/,'saved result cards must open the actual profile explicitly');
assert.match(savedSearchSource,/api\.runSavedSearch/,'saved searches must execute through their dedicated backend service');
assert.match(savedSearchSource,/stateEpoch/,'poll responses must be invalidated across saved-search mutations');
assert.match(savedSearchSource,/mutationActive/,'polls must not apply while a saved-search mutation is active');
assert.match(savedSearchSource,/api\.openSavedSearchResult/,'profile materialization must happen only on explicit result opening');
assert.doesNotMatch(savedSearchSource,/searchTransient|BACKGROUND_REFRESH_MS|refreshDue/,'the browser must not execute saved-search background searches');
assert.doesNotMatch(savedSearchSource,/type:'SEARCH_FILTERS'|type:'SEARCH_RESULTS'|type:'SETTINGS'/,'saved-search UI must never mutate active search or generic settings state');
assert.match(savedSearchBackendSource,/self\.search\.execute_probe/,'saved-search execution must use the read-only probe path');
assert.doesNotMatch(savedSearchBackendSource,/execute_transient|sync_search_profiles|business_classifier/,'saved-search background execution must not canonicalize or ingest profile state');
assert.match(savedSearchBackendSource,/BACKGROUND_REFRESH_SECONDS = 15 \* 60/,'saved searches must retain the 15-minute background interval');
assert.match(savedSearchBackendSource,/BACKGROUND_RETRY_SECONDS = 5 \* 60/,'failed saved searches must use a bounded retry interval');
assert.match(savedSearchRepositorySource,/class SavedSearchResultRecord/,'saved-search result history must be relational instead of growing settings JSON');
assert.match(savedSearchRepositorySource,/definition_version/,'saved-search result writes must be revision guarded');
assert.doesNotMatch(settingsRepositorySource,/saved_searches/,'generic app settings must not own saved-search state');
assert.match(savedSearchWebSource,/\/results\/<result_key>\/open/,'saved results need an explicit profile-open bridge');
assert.doesNotMatch(savedSearchWebSource,/ProfileService|classify_business_profile/,'saved-search background wiring must not invoke profile ingestion/classification');
assert.match(activeSearchSource,/type:'SEARCH_RESULTS'.*automatic:true,transient:true/s,'automatic full-result hydration must populate the active result set without becoming a manual search');
assert.doesNotMatch(activeSearchSource,/suppressedKey/,'manual searches must not permanently blacklist a filter key');
assert.match(searchServiceSource,/def execute_transient/,'canonical SearchService must own active transient execution');
assert.match(searchServiceSource,/def execute_probe/,'SearchService must expose a read-only saved-search probe boundary');
assert.match(searchServiceSource,/probe_search_profiles\(profiles\)/,'saved-search probes must only read canonical local state');
assert.match(searchServiceSource,/def materialize_probe_result/,'explicit saved-result opening must have a narrow canonicalization bridge');
assert.match(searchRepositorySource,/def probe_search_profiles/,'repository must expose read-only search enrichment');
assert.match(searchRepositorySource,/self\._canonical_write_lock/,'canonical listing writes must be serialized across concurrent searches');
assert.match(searchPreviewSource,/search_service\.execute_transient/,'active transient API must still delegate to canonical SearchService');
assert.match(clientSource,/savedSearchResults:/,'frontend must read cached saved-search results through the dedicated API');
assert.match(clientSource,/threads:\s*\(\)\s*=>\s*request\('\/api\/messaging\/threads\?filter=ALL'\)/,'thread API must always load the canonical unfiltered collection');
assert.match(encounterSource,/querySelector\('\[name="base_price_eur"\]'\)\)payload\.base_price_eur=/,'hidden base price must only be sent when present');
assert.match(encounterSource,/querySelector\('\[name="note"\]'\)\)payload\.note=/,'hidden note must only be sent when present');
assert.match(encounterSource,/querySelector\('\[data-encounter-service-row\]'\)\)payload\.services=/,'hidden services must only be sent when present');
assert.match(profileSource,/function renderProfileBody\(/,'standalone and swipe profiles must share one profile body renderer');
assert.match(profileSource,/screen-view standalone-profile swipe-profile/,'Swipe must use the canonical standalone profile surface');
assert.doesNotMatch(profileSource,/swipe-details/,'retired Swipe-only detail markup must not return');
assert.match(profileSource,/data-action="profile-request"/,'profile action rail must expose the request action');
assert.doesNotMatch(profileSource,/profile-share/,'retired share action must not return');
assert.doesNotMatch(requestedSource,/cleanSwipe|requested-swipe-identity|requested-profile-availability|availabilityMode/,'legacy Swipe DOM rewriting must stay removed');
assert.match(prototypeSource,/\.swipe-profile \.screen-scroll/,'Swipe keeps its media-sized scroll surface through the canonical Swipe profile class');
assert.doesNotMatch(prototypeSource,/swipe-details|profile-media::after/,'legacy Swipe detail styling and dark media overlay must stay removed');
assert.match(requestedSource,/requested-new-encounter"><summary/,'new encounter capture must start collapsed');
assert.doesNotMatch(requestedSource,/requested-new-encounter"\s+open/,'new encounter capture must never render open by default');
assert.match(requestedSource,/dataset\.requestedPriceHistory='1'/,'profile must provide a dedicated price history tab');
assert.match(requestedSource,/api\.currentPricing\(personId\).*api\.pricingHistory\(personId,100\)/s,'profile pricing must read exclusively through canonical pricing APIs');
assert.doesNotMatch(requestedSource,/15000/,'profile pricing must not use a time-based stale window');
assert.match(requestedSource,/if\(invalidatesProfilePricing\(action\?\.type\)\)pricingCache\.clear\(\)/,'pricing mutations must invalidate profile pricing immediately');
assert.match(requestedSource,/removeCardsByHeading\(panel,\['Merkmale','Öffnungszeiten','Preislisten'\]\)/,'service tab must no longer own traits, opening hours or price lists');
assert.match(requestedSource,/removeCardsByHeading\(panel,\['Aktuelle Filter'\]\)/,'analysis must not duplicate current filters');
assert.match(canonicalPricingSource,/PriceList\.source == CANONICAL_PRICE_SOURCE/,'canonical pricing must filter legacy/manual price rows');
assert.match(canonicalPricingSource,/CANONICAL_PRICE_SOURCE = "WHATSAPP_EXTRACTED"/,'chat-extracted prices are the source of truth');
assert.match(pricingWebSource,/CanonicalPriceIntelligenceService\(database, data_dir\)/,'pricing routes must use the canonical chat pricing service');

console.log('search intelligence regression checks passed');
