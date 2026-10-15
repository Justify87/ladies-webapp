import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { filterMapFeatures } from '../ladies/static/explorer/js/ui/discover_map.js';
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
// one simple lifecycle with explicit resize and cleanup behavior.
assert.match(mapSource,/maplibre-gl@6\.14\.0\/dist\/maplibre-gl\.mjs/,'MapLibre must use the native v6 ESM distribution');
assert.doesNotMatch(mapSource,/\+esm/,'MapLibre must not use a CDN ESM transform');
assert.match(indexSource,/maplibre-gl@6\.14\.0\/dist\/maplibre-gl\.css/,'MapLibre CSS must load with the app shell');
assert.match(mapSource,/new ResizeObserver/,'map must react when its mounted container changes size');
assert.match(mapSource,/instance\.resize\(\)/,'map must resize after mount/layout changes');
assert.match(mapSource,/active\?\.resizeObserver\?\.disconnect\(\)/,'map cleanup must disconnect its observer');
assert.match(mapSource,/active\.map\.remove\(\)/,'map cleanup must remove the MapLibre instance');
assert.match(mapSource,/source\.setData\(latestData\)/,'map data changes must update the existing GeoJSON source');
assert.match(mapSource,/getClusterExpansionZoom\(clusterId\)/,'cluster taps must expand through the source API');
assert.match(mapSource,/instance\.on\('error',reportMapError\)/,'MapLibre runtime errors must remain observable');
assert.match(mapSource,/maplibrePromise=null/,'failed MapLibre imports must remain retryable');

// Map identity is listing-based end-to-end. Two listings belonging to one person
// must never bind through person_id alone.
const samePersonPoint={type:'FeatureCollection',features:[{
  type:'Feature',geometry:{type:'Point',coordinates:[12.3,51.3]},properties:{
    location_key:'city:DE:leipzig',listing_count:2,
    listing_refs_json:JSON.stringify([
      {person_id:7,listing_id:101,advert_id:'a',address_label:'04109 Leipzig'},
      {person_id:7,listing_id:202,advert_id:'b',address_label:'04107 Leipzig'}
    ])
  }
}]};
const filtered=filterMapFeatures(samePersonPoint,[{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(filtered.features.length,1);
assert.equal(filtered.features[0].properties.listing_count,1);
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
