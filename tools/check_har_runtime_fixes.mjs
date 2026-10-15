import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { filterMapFeatures } from '../ladies/static/explorer/js/ui/discover_map.js';

const mapSource=readFileSync(new URL('../ladies/static/explorer/js/ui/discover_map.js',import.meta.url),'utf8');
const mapRepository=readFileSync(new URL('../ladies/core/repositories/map.py',import.meta.url),'utf8');
const discoverSource=readFileSync(new URL('../ladies/static/explorer/js/ui/screens/discover.js',import.meta.url),'utf8');
const messagingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/messaging.js',import.meta.url),'utf8');
const pricingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/pricing.js',import.meta.url),'utf8');
const configSource=readFileSync(new URL('../ladies/config.py',import.meta.url),'utf8');
const indexSource=readFileSync(new URL('../ladies/templates/index.html',import.meta.url),'utf8');

// One in-flight MapLibre initialization must be shared for the same live container.
assert.match(mapSource,/if\(mapInit\?\.target===target\)return mapInit\.promise/);
assert.match(mapSource,/lifecycleGeneration/);
assert.match(mapSource,/instance\.on\('error',reportMapError\)/);
assert.match(mapSource,/'text-font':textFont/);
assert.doesNotMatch(mapSource,/promise\.finally\(/,'map init cleanup must not create an unhandled rejection branch');

// Map identity must follow the listing/advert first. Two listings belonging to one
// person must never cause the visible row to bind to its sibling listing.
const samePersonPoint={type:'FeatureCollection',features:[{
  type:'Feature',geometry:{type:'Point',coordinates:[12.3,51.3]},properties:{
    location_key:'city:DE:leipzig',profile_count:2,
    profiles_json:JSON.stringify([
      {person_id:7,listing_id:101,advert_id:'a'},
      {person_id:7,listing_id:202,advert_id:'b'}
    ])
  }
}]};
const filtered=filterMapFeatures(samePersonPoint,[{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(filtered.features.length,1);
const refs=JSON.parse(filtered.features[0].properties.profiles_json);
assert.deepEqual(refs.map(item=>item.listing_id),[202]);
assert.doesNotMatch(mapRepository,/seen_people/,'map repository must keep every active listing');

assert.match(discoverSource,/loading="lazy" decoding="async"/,'Grid thumbnails must not all load eagerly');
assert.match(messagingSource,/items:newestThreads\(d\?\.items\)/,'thread ingestion must be ordered by latest activity');
assert.match(messagingSource,/last_message_at/);
assert.match(pricingSource,/ensureRuntimeReady/);
assert.match(pricingSource,/signal:runtimeReady\(\)\?/,'new pricing actions must be capability-gated');
assert.doesNotMatch(pricingSource,/Promise\.all\(\[api\.pricingSettings\(\),api\.pricingStatus/,'pricing boot must not fetch runtime status twice');
assert.match(configSource,/_ui_filter_options/,'frontend filter aliases must collapse to one canonical option');
assert.match(indexSource,/rel="icon" href="\/static\/explorer\/favicon\.svg"/);
