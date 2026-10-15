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
assert.equal(savedSearchHasUnread({settings:{values:{saved_searches:[{unread_new_results:false},{unread_new_results:true}]}}}),true);
assert.equal(savedSearchHasUnread({settings:{values:{saved_searches:[{unread_new_results:false}]}}}),false);

// Manual A must suppress only the concurrent automatic fetch, not future A hydration after B.
const baseFilters=city=>({city,radius:20,category:'',ageMin:18,ageMax:45,heightMin:140,heightMax:200,selected:{},local:{}});
const runtimeState={
  search:{filters:baseFilters('Leipzig'),preview:{status:'idle'},loading:false,error:null,results:[],meta:null},
  navigation:{stack:[{name:'search',params:{}}]}
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

// Server-side saved-search behavior: local filters are part of the monitored result set,
// and an edit during an in-flight request must win over the stale result.
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

class Settings:
    def __init__(self): self.items=[]
    def get_saved_searches(self): return copy.deepcopy(self.items)
    def set_saved_searches(self, items): self.items=copy.deepcopy(items); return copy.deepcopy(self.items)

class Search:
    def __init__(self): self.started=threading.Event(); self.release=threading.Event()
    def execute_transient(self, payload):
        self.started.set(); self.release.wait(2)
        return {'results':[{'advert_id':'10','thumbnail_url':'x','has_video':True,'preference':'NONE','is_business_profile':False,'has_ai_content':False,'markers':[]}], 'meta':{}}

settings=Settings(); search=Search(); service=SavedSearchService(settings, search)
items=service.create(name='Alt', filters={'city':'Leipzig','radius':20,'category':'','ageMin':18,'ageMax':45,'heightMin':140,'heightMax':200,'selected':{},'local':{}})
saved_id=items[0]['id']
result={}
thread=threading.Thread(target=lambda: result.update(service.run(saved_id, mark_seen=False)))
thread.start(); assert search.started.wait(1)
service.update(saved_id, name='Neu', filters={'city':'Berlin','radius':20,'category':'','ageMin':18,'ageMax':45,'heightMin':140,'heightMax':200,'selected':{},'local':{}})
search.release.set(); thread.join(2)
assert result.get('stale') is True
latest=service.list()[0]
assert latest['name']=='Neu' and latest['filters']['city']=='Berlin'
`], {stdio:'inherit'});

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

// Focused guards for the Explorer follow-up fixes.
const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const profileSource=readFileSync(new URL('../ladies/static/explorer/js/ui/profile.js',import.meta.url),'utf8');
const prototypeSource=readFileSync(new URL('../ladies/static/explorer/css/prototype.css',import.meta.url),'utf8');
const chromeSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/chrome.js',import.meta.url),'utf8');
const searchScreenSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/search.js',import.meta.url),'utf8');
const savedSearchSource=readFileSync(new URL('../ladies/static/explorer/js/saved_searches.js',import.meta.url),'utf8');
const savedSearchBackendSource=readFileSync(new URL('../ladies/core/services/saved_searches.py',import.meta.url),'utf8');
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
assert.match(discoverSource,/grid-card-heart[^>]+data-action="grid-like"/s,'visible Grid heart must trigger Like');
assert.doesNotMatch(discoverSource,/discover-mode-pills/,'availability controls must not be duplicated inside Grid or Swipe content');
assert.match(chromeSource,/header-discovery-modes/,'Swipe and Grid availability controls must live in the shared header');
assert.match(chromeSource,/route\.name==='swipe'.*data-route="grid".*gridGlyph\(\).*route\.name==='grid'.*data-route="swipe"/s,'shared header must expose both view switches with a real Grid glyph');
assert.match(chromeSource,/data-saved-search-action="open"/,'search header action must open the dedicated saved-search screen');
assert.match(chromeSource,/saved-search-notification/,'header must expose a dedicated new-result indicator');
assert.doesNotMatch(searchScreenSource,/savedSearchesCard|saved-searches-card/,'saved searches must no longer render inside the filter screen');
assert.match(savedSearchSource,/api\.runSavedSearch/,'saved searches must execute through their dedicated backend service');
assert.doesNotMatch(savedSearchSource,/searchTransient|BACKGROUND_REFRESH_MS|refreshDue/,'the browser must not execute saved-search background searches');
assert.doesNotMatch(savedSearchSource,/type:'SEARCH_FILTERS'|type:'SEARCH_RESULTS'/,'saved-search UI must never mutate active search filters or results');
assert.match(savedSearchBackendSource,/filter_saved_search_results/,'server-side saved searches must apply local visibility filters before delta calculation');
assert.match(savedSearchBackendSource,/BACKGROUND_REFRESH_SECONDS = 15 \* 60/,'saved searches must retain the 15-minute background interval');
assert.match(savedSearchBackendSource,/current\.get\("updated_at"\) != revision/,'stale background results must not overwrite edited saved searches');
assert.match(savedSearchWebSource,/SavedSearchScheduler/,'saved searches must have a server-side scheduler');
assert.match(activeSearchSource,/type:'SEARCH_RESULTS'.*automatic:true,transient:true/s,'automatic full-result hydration must populate the active result set without becoming a manual search');
assert.doesNotMatch(activeSearchSource,/suppressedKey/,'manual searches must not permanently blacklist a filter key');
assert.match(searchServiceSource,/def execute_transient/,'canonical SearchService must own transient execution');
assert.match(searchServiceSource,/sync_search_profiles\(profiles\)/,'transient execution must canonicalize listings and identity state');
assert.match(searchRepositorySource,/def sync_search_profiles/,'repository must expose canonical transient search synchronization');
assert.match(searchPreviewSource,/search_service\.execute_transient/,'transient API must delegate to canonical SearchService');
assert.match(clientSource,/savedSearches:\s*\(\)\s*=>\s*request\('\/api\/saved-searches'\)/,'frontend must read saved searches through their dedicated API');
assert.match(clientSource,/threads:\s*\(\)\s*=>\s*request\('\/api\/messaging\/threads\?filter=ALL'\)/,'thread API must always load the canonical unfiltered collection');
assert.match(encounterSource,/querySelector\('\[name="base_price_eur"\]'\)\)payload\.base_price_eur=/,'hidden base price must only be sent when present');
assert.match(encounterSource,/querySelector\('\[name="note"\]'\)\)payload\.note=/,'hidden note must only be sent when present');
assert.match(encounterSource,/querySelector\('\[data-encounter-service-row\]'\)\)payload\.services=/,'hidden services must only be sent when present');
assert.match(profileSource,/function renderProfileBody\(/,'standalone and swipe profiles must share one profile body renderer');
assert.match(profileSource,/screen-view standalone-profile swipe-profile/,'Swipe must use the canonical standalone profile surface');
assert.doesNotMatch(profileSource,/swipe-details/,'retired Swipe-only detail markup must not return');
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
