import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { filterMapFeatures } from '../ladies/static/explorer/js/ui/discover_map.js';

const points={type:'FeatureCollection',features:[{
  type:'Feature',
  geometry:{type:'Point',coordinates:[12.3,51.3]},
  properties:{
    location_key:'city:DE:leipzig',
    listing_count:3,
    listing_refs_json:JSON.stringify([
      {listing_id:1,advert_id:'a',price_60_cents:10000},
      {listing_id:2,advert_id:'b',price_60_cents:14000},
      {listing_id:3,advert_id:'c',price_60_cents:null},
    ])
  }
}]};

let data=filterMapFeatures(points,[
  {listing_id:1,advertId:'a'},
  {listing_id:2,advertId:'b'},
  {listing_id:3,advertId:'c'},
]);
assert.equal(data.features.length,1);
assert.equal(data.features[0].properties.listing_count,3);
assert.equal(data.features[0].properties.price_60_sum_cents,24000);
assert.equal(data.features[0].properties.price_60_count,2);

data=filterMapFeatures(points,[{listing_id:2,advertId:'b'}]);
assert.equal(data.features.length,1);
assert.equal(data.features[0].properties.listing_count,1);
assert.equal(data.features[0].properties.price_60_sum_cents,14000);
assert.equal(data.features[0].properties.price_60_count,1);
assert.deepEqual(JSON.parse(data.features[0].properties.listing_refs_json).map(ref=>ref.listing_id),[2]);

data=filterMapFeatures(points,[{listing_id:3,advertId:'c'}]);
assert.equal(data.features[0].properties.price_60_sum_cents,0);
assert.equal(data.features[0].properties.price_60_count,0);

const mapSource=readFileSync(new URL('../ladies/static/explorer/js/ui/discover_map.js',import.meta.url),'utf8');
const mapRepository=readFileSync(new URL('../ladies/core/repositories/map.py',import.meta.url),'utf8');
const mapService=readFileSync(new URL('../ladies/core/services/map.py',import.meta.url),'utf8');

assert.match(mapSource,/initialFitDone:false/,'each mounted map may auto-fit only its initial dataset');
assert.match(mapSource,/current\.initialFitDone\|\|/,'initial fit must become a no-op after the first fit');
assert.doesNotMatch(mapSource,/fittedSignature/,'data signatures must not trigger camera refits');
assert.match(mapSource,/source\.setData\(latestData\)/,'filter changes must update the existing GeoJSON source');
assert.match(mapSource,/price_60_sum_cents:\['\+',\['get','price_60_sum_cents'\]\]/,'cluster price sums must aggregate in MapLibre');
assert.match(mapSource,/price_60_count:\['\+',\['get','price_60_count'\]\]/,'cluster price counts must aggregate in MapLibre');
assert.match(mapSource,/CLUSTER_PRICE_LAYER/,'clusters must expose a price label layer');
assert.match(mapSource,/LOCATION_PRICE_LAYER/,'unclustered locations must expose a price label layer');
assert.match(mapSource,/\['>', \['get','price_60_count'\],0\]/,'price labels must disappear when no 60-minute price exists');
assert.match(mapRepository,/PriceItem\.duration_minutes == 60/,'map pricing must use the explicit 60-minute base offer');
assert.match(mapRepository,/PriceItem\.item_type == "BASE"/,'map pricing must use base offers only');
assert.match(mapRepository,/PriceList\.currency == "EUR"/,'map pricing must match the displayed euro currency');
assert.match(mapRepository,/desc\(PriceList\.observed_at\)/,'latest applicable price data must win');
assert.match(mapService,/"price_60_cents": item\.get\("price_60_cents"\)/,'map refs must carry the 60-minute price into the browser');

console.log('map persistence and 60-minute price regression checks passed');
