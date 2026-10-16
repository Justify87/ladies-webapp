import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createMapEffects, invalidateMapData } from '../ladies/static/explorer/js/effects/map.js';
import { filterMapFeatures } from '../ladies/static/explorer/js/ui/discover_map.js';

const mapSource=readFileSync(new URL('../ladies/static/explorer/js/ui/discover_map.js',import.meta.url),'utf8');
const mapEffectsSource=readFileSync(new URL('../ladies/static/explorer/js/effects/map.js',import.meta.url),'utf8');
const mapRepository=readFileSync(new URL('../ladies/core/repositories/map.py',import.meta.url),'utf8');
const mapProjection=readFileSync(new URL('../ladies/pricing/map_projection.py',import.meta.url),'utf8');
const mapService=readFileSync(new URL('../ladies/core/services/map.py',import.meta.url),'utf8');
const reducerSource=readFileSync(new URL('../ladies/static/explorer/js/app/reducer.js',import.meta.url),'utf8');
const coreEffectsSource=readFileSync(new URL('../ladies/static/explorer/js/effects/core.js',import.meta.url),'utf8');
const pricingSource=readFileSync(new URL('../ladies/static/explorer/js/effects/pricing.js',import.meta.url),'utf8');

// Camera state belongs to the mounted MapLibre instance. Data/filter updates may only
// replace source data after the initial fit.
assert.match(mapSource,/source\.setData\(latestData\)/);
assert.match(mapSource,/initialFitDone:false/);
assert.match(mapSource,/current\.initialFitDone\|\|/);
assert.doesNotMatch(mapSource,/fittedSignature/);

// Price labels aggregate only visible listing refs and remain visible over base-map
// text collisions.
assert.match(mapSource,/price_60_sum_cents:\['\+',\['get','price_60_sum_cents'\]\]/);
assert.match(mapSource,/price_60_count:\['\+',\['get','price_60_count'\]\]/);
assert.match(mapSource,/'text-allow-overlap':true/);
assert.match(mapSource,/'text-ignore-placement':true/);
assert.match(mapSource,/mapState\.stale/);

// The repository selects the current revision for each pricing scope before reading
// 60-minute offers, and only exact offers contribute to an average.
assert.match(mapRepository,/func\.row_number\(\)\.over/);
assert.match(mapRepository,/ranked_scopes\.c\.scope_rank == 1/);
assert.match(mapRepository,/PriceItem\.item_type == "BASE"/);
assert.match(mapRepository,/PriceItem\.duration_minutes == 60/);
assert.match(mapRepository,/PriceOfferMeta\.amount_type == "EXACT"/);
assert.match(mapRepository,/PriceOfferMeta\.price_item_id\.is_\(None\)/);
assert.match(mapRepository,/resolve_map_price_60_cents/);
assert.doesNotMatch(mapRepository,/price_rows\.sort\(/);
assert.match(mapProjection,/current_scopes\.get\(\(person_id, listing_id\)\)/);
assert.match(mapProjection,/current_scopes\.get\(\(person_id, None\)\)/);
assert.match(mapProjection,/upper\(\) != "EUR"/);
assert.match(mapService,/"price_60_cents": item\.get\("price_60_cents"\)/);

// Both accepted chat pricing and manual pricing invalidate the same map projection.
assert.match(mapEffectsSource,/DISCOVER_MAP_INVALIDATE/);
assert.match(mapEffectsSource,/latestRevision!==revision/);
assert.match(reducerSource,/case 'DISCOVER_MAP_INVALIDATE'/);
assert.match(reducerSource,/revision:Number\(state\.discover\.map\?\.revision\|\|0\)\+1/);
assert.match(coreEffectsSource,/invalidateMapData\(store\)/);
assert.match(pricingSource,/invalidateMapData\(store\)/);

const point={type:'FeatureCollection',features:[{
  type:'Feature',
  geometry:{type:'Point',coordinates:[12.3,51.3]},
  properties:{
    location_key:'city:DE:leipzig',
    listing_count:2,
    listing_refs_json:JSON.stringify([
      {person_id:7,listing_id:101,advert_id:'a',price_60_cents:10000},
      {person_id:7,listing_id:202,advert_id:'b',price_60_cents:14000}
    ])
  }
}]};
const allVisible=filterMapFeatures(point,[
  {personId:7,listing_id:101,advertId:'a'},
  {personId:7,listing_id:202,advertId:'b'}
]);
assert.equal(allVisible.features[0].properties.price_60_sum_cents,24000);
assert.equal(allVisible.features[0].properties.price_60_count,2);
const oneVisible=filterMapFeatures(point,[{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(oneVisible.features[0].properties.price_60_sum_cents,14000);
assert.equal(oneVisible.features[0].properties.price_60_count,1);

// An invalidation keeps current markers visible, rejects any response that started on
// the previous pricing revision, then allows a fresh response for the unchanged listing set.
const key=JSON.stringify([[7,'leipzig']]);
const state={
  search:{results:[{listing_id:7,city:'Leipzig'}]},
  discover:{map:{
    key,
    points:{type:'FeatureCollection',features:[],meta:{pending_count:0,version:'old'}},
    loading:false,error:null,stale:false,revision:0
  }}
};
const requests=[];
const store={
  getState:()=>state,
  dispatch(action){
    const current=state.discover.map;
    if(action.type==='DISCOVER_MAP_INVALIDATE'){
      state.discover.map={...current,stale:true,loading:false,error:null,revision:Number(current.revision||0)+1};
    }else if(action.type==='DISCOVER_MAP_LOADING'){
      state.discover.map={...current,key:action.key??current.key,loading:Boolean(action.value),error:action.error||null};
    }else if(action.type==='DISCOVER_MAP_POINTS'){
      state.discover.map={key:action.key,points:action.points,loading:false,error:null,stale:false,revision:Number(current.revision||0)};
    }else if(action.type==='DISCOVER_MAP_CLEAR'){
      state.discover.map={key:null,points:null,loading:false,error:null,stale:false,revision:0};
    }
  }
};
const effects=createMapEffects({
  store,
  mapPoints:payload=>new Promise(resolve=>requests.push({payload,resolve}))
});

const staleRequest=effects.loadMapPoints({force:true});
assert.equal(requests.length,1);
invalidateMapData(store);
assert.equal(state.discover.map.revision,1);
assert.equal(state.discover.map.stale,true);
assert.equal(state.discover.map.points.meta.version,'old');
requests[0].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,version:'stale-response'}});
await staleRequest;
assert.equal(state.discover.map.points.meta.version,'old','pre-invalidation responses must not overwrite retained map data');
assert.equal(state.discover.map.stale,true);

const freshRequest=effects.loadMapPoints();
assert.equal(requests.length,2);
requests[1].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,version:'new'}});
await freshRequest;
assert.equal(state.discover.map.points.meta.version,'new');
assert.equal(state.discover.map.stale,false);
assert.equal(state.discover.map.revision,1);

console.log('map price/persistence regression checks passed');
