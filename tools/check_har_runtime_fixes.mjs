import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createMapLifecycle, filterMapFeatures, waitForMapReady } from '../ladies/static/explorer/js/ui/discover_map.js';
import { pricingExtractionAvailable, pricingManualAvailable } from '../ladies/static/explorer/js/app/selectors.js';

const mapSource=readFileSync(new URL('../ladies/static/explorer/js/ui/discover_map.js',import.meta.url),'utf8');
const mapRepository=readFileSync(new URL('../ladies/core/repositories/map.py',import.meta.url),'utf8');
const mapService=readFileSync(new URL('../ladies/core/services/map.py',import.meta.url),'utf8');
const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const messagingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/messaging.js',import.meta.url),'utf8');
const messagingService=readFileSync(new URL('../ladies/messaging/service.py',import.meta.url),'utf8');
const pricingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/pricing.js',import.meta.url),'utf8');
const chromeSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/chrome.js',import.meta.url),'utf8');
const configSource=readFileSync(new URL('../ladies/config.py',import.meta.url),'utf8');
const indexSource=readFileSync(new URL('../ladies/templates/index.html',import.meta.url),'utf8');

// MapLibre is loaded from its reviewed ESM distribution, not a CDN transform. Its
// required stylesheet is part of initial document loading, and the mounted map owns
// one lifecycle with explicit resize and cleanup behavior.
assert.match(mapSource,/maplibre-gl@6\.14\.0\/dist\/maplibre-gl\.mjs/,'MapLibre must use the native v6 ESM distribution');
assert.doesNotMatch(mapSource,/\+esm/,'MapLibre must not use a CDN ESM transform');
assert.match(indexSource,/maplibre-gl@6\.14\.0\/dist\/maplibre-gl\.css/,'MapLibre CSS must load with the app shell');
assert.match(mapSource,/new ResizeObserver/,'map must react when its mounted container changes size');
assert.match(mapSource,/instance\.resize\(\)/,'map must resize after mount/layout changes');
assert.match(mapSource,/current\.resizeObserver\?\.disconnect\(\)/,'map cleanup must disconnect its observer');
assert.match(mapSource,/current\.map\.remove\(\)/,'map cleanup must remove the MapLibre instance');
assert.match(mapSource,/source\.setData\(latestData\)/,'map data changes must update the existing GeoJSON source');
assert.match(mapSource,/initialFitDone:false/,'each mounted map may auto-fit only its first available dataset');
assert.match(mapSource,/current\.initialFitDone\|\|/,'filter updates must not re-fit a map whose initial camera has already been established');
assert.doesNotMatch(mapSource,/fittedSignature/,'data signatures must not drive repeated camera fits');
assert.match(mapSource,/price_60_sum_cents:\['\+',\['get','price_60_sum_cents'\]\]/,'cluster price sums must aggregate in MapLibre');
assert.match(mapSource,/price_60_count:\['\+',\['get','price_60_count'\]\]/,'cluster price counts must aggregate in MapLibre');
assert.match(mapSource,/CLUSTER_PRICE_LAYER/,'clusters must expose a 60-minute average price label');
assert.match(mapSource,/LOCATION_PRICE_LAYER/,'unclustered points must expose a 60-minute average price label');
assert.match(mapSource,/getClusterExpansionZoom\(clusterId\)/,'cluster taps must expand through the source API');
assert.match(mapSource,/instance\.on\('error',reportMapError\)/,'MapLibre runtime errors must remain observable');
assert.match(mapSource,/maplibrePromise=null/,'failed MapLibre imports must reset the shared import promise');
assert.match(mapRepository,/PriceItem\.duration_minutes == 60/,'map pricing must use the explicit 60-minute offer');
assert.match(mapRepository,/PriceItem\.item_type == "BASE"/,'map pricing must use base offers only');
assert.match(mapRepository,/PriceList\.currency == "EUR"/,'map pricing must match the displayed euro currency');
assert.match(mapRepository,/price_rows\.sort\(/,'latest applicable 60-minute price data must be prioritized without an ordered history query');
assert.match(mapRepository,/row\[0\]\.observed_at/,'price recency must drive the local price-row priority');
assert.match(mapService,/"price_60_cents": item\.get\("price_60_cents"\)/,'map refs must carry 60-minute prices into the browser');

// Lifecycle behavior is tested directly: same-container initialization is single-flight,
// failures at every initialization stage release ownership, and stale work cannot become
// active again after destruction.
const target={isConnected:true};
const lifecycle=createMapLifecycle();
let starts=0;
let releaseFirst;
const firstGate=new Promise(resolve=>{releaseFirst=resolve;});
const first=lifecycle.run(target,async({active})=>{starts+=1;await firstGate;return active()?'map':null;});
const duplicate=lifecycle.run(target,async()=>{starts+=1;return 'duplicate';});
assert.equal(first,duplicate,'same container must share one in-flight initialization');
assert.equal(starts,1,'shared initialization must only start once');
releaseFirst();
assert.equal(await first,'map');
lifecycle.destroy();

const importLifecycle=createMapLifecycle();
let importAttempts=0;
const importTarget={isConnected:true};
const loadLibrary=async()=>{
  importAttempts+=1;
  if(importAttempts===1)throw new Error('import failed');
  return 'library';
};
await assert.rejects(importLifecycle.run(importTarget,async()=>loadLibrary()),/import failed/);
assert.equal(importLifecycle.hasActive(),false,'failed imports must release runtime ownership');
assert.equal(await importLifecycle.run(importTarget,async()=>loadLibrary()),'library','a failed import must be retryable on the same container');
assert.equal(importAttempts,2);
importLifecycle.destroy();

const constructorLifecycle=createMapLifecycle();
let constructionAttempts=0;
const constructorTarget={isConnected:true};
const construct=()=>{
  constructionAttempts+=1;
  if(constructionAttempts===1)throw new Error('constructor failed');
  return 'instance';
};
await assert.rejects(constructorLifecycle.run(constructorTarget,async()=>construct()),/constructor failed/);
assert.equal(constructorLifecycle.hasActive(),false,'constructor failures must release runtime ownership');
assert.equal(await constructorLifecycle.run(constructorTarget,async()=>construct()),'instance','constructor failures must be retryable on the same container');
assert.equal(constructionAttempts,2);
constructorLifecycle.destroy();

const staleLifecycle=createMapLifecycle();
const staleTarget={isConnected:true};
let staleCleanups=0;
let releaseStale;
const staleGate=new Promise(resolve=>{releaseStale=resolve;});
const stale=staleLifecycle.run(staleTarget,async({active,cleanup})=>{
  cleanup(()=>{staleCleanups+=1;});
  await staleGate;
  return active()?'map':'stale';
});
staleTarget.isConnected=false;
staleLifecycle.destroy();
releaseStale();
assert.equal(await stale,null,'destroyed or disconnected initialization must never become active later');
assert.equal(staleCleanups,1,'destroy must execute registered resource cleanup exactly once');
assert.equal(staleLifecycle.hasActive(),false);

function fakeLoadInstance(){
  let handler=null;
  return {
    once(event,next){assert.equal(event,'load');handler=next;},
    off(event,next){assert.equal(event,'load');if(handler===next)handler=null;},
    fireLoad(){handler?.();},
    hasLoadListener(){return Boolean(handler);},
  };
}

// Pending load waits are cancellable. Cancellation removes the listener and clears the
// timeout instead of retaining the map/container until the full timeout elapses.
const cancelInstance=fakeLoadInstance();
let pendingCleanup=null;
const cancelled=waitForMapReady({
  instance:cancelInstance,
  isActive:()=>true,
  onReady:()=>cancelInstance,
  registerCleanup:cleanup=>{
    pendingCleanup=cleanup;
    return ()=>{if(pendingCleanup===cleanup)pendingCleanup=null;};
  },
  timeoutMs:1000,
});
assert.equal(cancelInstance.hasLoadListener(),true);
pendingCleanup();
assert.equal(await cancelled,null);
assert.equal(cancelInstance.hasLoadListener(),false,'cancelled load wait must remove its load listener');
assert.equal(pendingCleanup,null,'cancelled load wait must unregister its lifecycle cleanup');

const timeoutInstance=fakeLoadInstance();
let timeoutCleanup=null;
await assert.rejects(waitForMapReady({
  instance:timeoutInstance,
  isActive:()=>true,
  onReady:()=>timeoutInstance,
  registerCleanup:cleanup=>{
    timeoutCleanup=cleanup;
    return ()=>{if(timeoutCleanup===cleanup)timeoutCleanup=null;};
  },
  timeoutMs:5,
}),/nicht rechtzeitig geladen/);
assert.equal(timeoutInstance.hasLoadListener(),false,'timed-out load wait must remove its load listener');
assert.equal(timeoutCleanup,null,'timed-out load wait must unregister its lifecycle cleanup');

const readyInstance=fakeLoadInstance();
let readyCleanup=null;
const ready=waitForMapReady({
  instance:readyInstance,
  isActive:()=>true,
  onReady:()=>readyInstance,
  registerCleanup:cleanup=>{
    readyCleanup=cleanup;
    return ()=>{if(readyCleanup===cleanup)readyCleanup=null;};
  },
  timeoutMs:1000,
});
readyInstance.fireLoad();
assert.equal(await ready,readyInstance);
assert.equal(readyInstance.hasLoadListener(),false,'successful load must remove its one-shot listener');
assert.equal(readyCleanup,null,'successful load must unregister its pending cleanup');

// Map identity is listing-based end-to-end. Two listings belonging to one person
// must never bind through person_id alone. Price aggregation follows the same filtered
// listing refs, so locally hidden results cannot influence the displayed average.
const samePersonPoint={type:'FeatureCollection',features:[{
  type:'Feature',geometry:{type:'Point',coordinates:[12.3,51.3]},properties:{
    location_key:'city:DE:leipzig',listing_count:2,
    listing_refs_json:JSON.stringify([
      {person_id:7,listing_id:101,advert_id:'a',address_label:'04109 Leipzig',price_60_cents:10000},
      {person_id:7,listing_id:202,advert_id:'b',address_label:'04107 Leipzig',price_60_cents:14000}
    ])
  }
}]};
const allVisible=filterMapFeatures(samePersonPoint,[{personId:7,listing_id:101,advertId:'a'},{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(allVisible.features[0].properties.price_60_sum_cents,24000);
assert.equal(allVisible.features[0].properties.price_60_count,2);
const filtered=filterMapFeatures(samePersonPoint,[{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(filtered.features.length,1);
assert.equal(filtered.features[0].properties.listing_count,1);
assert.equal(filtered.features[0].properties.price_60_sum_cents,14000);
assert.equal(filtered.features[0].properties.price_60_count,1);
const refs=JSON.parse(filtered.features[0].properties.listing_refs_json);
assert.deepEqual(refs.map(item=>item.listing_id),[202]);
assert.equal(refs[0].address_label,'04107 Leipzig','map filtering must preserve the selected listing address label');
const personOnly={...samePersonPoint,features:samePersonPoint.features.map(feature=>({...feature,properties:{...feature.properties,listing_refs_json:JSON.stringify([{person_id:7}])}}))};
assert.equal(filterMapFeatures(personOnly,[{personId:7,listing_id:202,advertId:'b'}]).features.length,0,'person identity alone must never bind a map listing');
assert.doesNotMatch(mapRepository,/seen_people/,'map repository must keep every active listing');
assert.match(mapService,/"listing_count": len\(listings\)/);
assert.match(mapService,/"listing_refs_json":/);
assert.match(mapService,/"address_label": item\["address_label"\]/,'map refs must expose the locally known listing address label');
assert.doesNotMatch(mapService,/"profile_count"|"profiles_json"/,'map API must not expose person-labelled listing counters');
assert.doesNotMatch(mapService,/params=\{[^}]*hidden_location/s,'hidden address text must never be sent to the geocoding provider');

// The backend is the sole source of truth for thread order. The browser preserves
// the canonical newest-first order instead of applying a second business rule.
assert.match(messagingService,/order_by\(desc\(ChatThread\.last_message_at\), desc\(ChatThread\.updated_at\)\)/);
assert.doesNotMatch(messagingService,/out\.sort\(key=lambda item:/,'favorite/like ordering must not override message recency');
assert.match(messagingSource,/type:'THREADS',items:d\?\.items\|\|\[\]/);
assert.doesNotMatch(messagingSource,/newestThreads|threadTime/,'frontend must not duplicate canonical thread ordering');

// One shared pricing capability definition drives both actions and UI.
const pricingState=(enabled,magic,ready)=>({settings:{pricing:{price_intelligence_enabled:enabled,price_magic_button_enabled:magic},pricingRuntime:{extractor:{ready}}}});
assert.equal(pricingExtractionAvailable(pricingState(true,true,true)),true);
assert.equal(pricingExtractionAvailable(pricingState(false,true,true)),false);
assert.equal(pricingExtractionAvailable(pricingState(true,true,false)),false);
assert.equal(pricingManualAvailable(pricingState(true,true,true)),true);
assert.equal(pricingManualAvailable(pricingState(true,false,true)),false);
assert.match(pricingSource,/ensureExtractionAvailable/);
assert.match(pricingSource,/S\.pricingExtractionAvailable\(state\(\)\)/,'automatic signal loading must use the shared capability');
assert.match(chromeSource,/S\.pricingManualAvailable\(state\)/,'manual pricing UI must use the shared capability');
assert.doesNotMatch(pricingSource,/Promise\.all\(\[api\.pricingSettings\(\),api\.pricingStatus/,'pricing boot must not fetch runtime status twice');

assert.match(discoverSource,/loading="lazy" decoding="async"/,'Grid thumbnails must not all load eagerly');
assert.match(configSource,/FILTER_UI_LABELS/,'ambiguous parser aliases need explicit UI labels');
assert.match(configSource,/"ab18": "Mit 18\+ Inhalten"/);
assert.match(configSource,/"girlfriendsex": "Girlfriend-Sex"/);
assert.doesNotMatch(configSource,/len\(text\.strip\(\)\)/,'UI labels must not be selected by string-length heuristics');
assert.match(indexSource,/rel="icon" href="\/static\/explorer\/favicon\.svg"/);

console.log('HAR runtime regression checks passed');
