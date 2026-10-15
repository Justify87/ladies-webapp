import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createMapInitCoordinator, filterMapFeatures } from '../ladies/static/explorer/js/ui/discover_map.js';
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

// Map initialization is genuinely single-flight. Invalidating a pending generation
// makes its late result stale instead of letting it take ownership of the container.
const coordinator=createMapInitCoordinator();
const target={isConnected:true};
let constructions=0;
let releaseFirst;
const firstGate=new Promise(resolve=>{releaseFirst=resolve;});
const first=coordinator.run(target,async active=>{constructions+=1;await firstGate;return active()?'map':'stale';});
const duplicate=coordinator.run(target,async()=>{constructions+=1;return 'duplicate';});
assert.equal(first,duplicate,'same container must share one in-flight initialization');
assert.equal(constructions,1,'shared initialization must construct only once');
releaseFirst();
assert.equal(await first,'map');

const staleTarget={isConnected:true};
let releaseStale;
const staleGate=new Promise(resolve=>{releaseStale=resolve;});
const stale=coordinator.run(staleTarget,async active=>{await staleGate;return active()?'active':'discarded';});
coordinator.invalidate();
releaseStale();
assert.equal(await stale,'discarded','invalidated initialization must not become active later');

assert.match(mapSource,/instance\.on\('error',reportMapError\)/);
assert.match(mapSource,/'text-font':textFont/);
assert.match(mapSource,/\.catch\(error=>\{\s*maplibrePromise=null;/s,'failed MapLibre imports must be retryable');
assert.doesNotMatch(mapSource,/promise\.finally\(/,'map init cleanup must not create an unhandled rejection branch');

// Map identity is listing-based end-to-end. Two listings belonging to one person
// must never bind through person_id alone.
const samePersonPoint={type:'FeatureCollection',features:[{
  type:'Feature',geometry:{type:'Point',coordinates:[12.3,51.3]},properties:{
    location_key:'city:DE:leipzig',listing_count:2,
    listing_refs_json:JSON.stringify([
      {person_id:7,listing_id:101,advert_id:'a'},
      {person_id:7,listing_id:202,advert_id:'b'}
    ])
  }
}]};
const filtered=filterMapFeatures(samePersonPoint,[{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(filtered.features.length,1);
assert.equal(filtered.features[0].properties.listing_count,1);
const refs=JSON.parse(filtered.features[0].properties.listing_refs_json);
assert.deepEqual(refs.map(item=>item.listing_id),[202]);
const personOnly={...samePersonPoint,features:samePersonPoint.features.map(feature=>({...feature,properties:{...feature.properties,listing_refs_json:JSON.stringify([{person_id:7}])}}))};
assert.equal(filterMapFeatures(personOnly,[{personId:7,listing_id:202,advertId:'b'}]).features.length,0,'person identity alone must never bind a map listing');
assert.doesNotMatch(mapRepository,/seen_people/,'map repository must keep every active listing');
assert.match(mapService,/"listing_count": len\(listings\)/);
assert.match(mapService,/"listing_refs_json":/);
assert.doesNotMatch(mapService,/"profile_count"|"profiles_json"/,'map API must not expose person-labelled listing counters');

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
