import assert from 'node:assert/strict';

import { api } from '../ladies/static/explorer/js/api/client.js';
import { createMapEffects, invalidateMapProjection, mapRequestDescriptor } from '../ladies/static/explorer/js/effects/map.js';
import { buildMapProjection, sanitizeMapPoints } from '../ladies/static/explorer/js/data/map_model.js';
import { notifyMapProjectionChanged } from '../ladies/static/explorer/js/shared/domain_events.js';
import { createMapLifecycle, waitForMapReady } from '../ladies/static/explorer/js/ui/map_runtime.js';

function mapState(results=[{listing_id:1,city:'Leipzig'}]){
  return {
    search:{results},
    discover:{map:{key:null,points:null,loading:false,error:null,stale:false,revision:0}},
  };
}

function createStore(state){
  return {
    getState:()=>state,
    dispatch(action){
      const current=state.discover.map;
      if(action.type==='DISCOVER_MAP_LOADING'){
        state.discover.map={...current,key:action.key??current.key,loading:Boolean(action.value),error:action.error||null};
      }else if(action.type==='DISCOVER_MAP_POINTS'){
        state.discover.map={key:action.key||null,points:action.points||null,loading:false,error:null,stale:false,revision:Number(current.revision||0)};
      }else if(action.type==='DISCOVER_MAP_INVALIDATE'){
        state.discover.map={...current,stale:true,loading:false,error:null,revision:Number(current.revision||0)+1};
      }else if(action.type==='DISCOVER_MAP_CLEAR'){
        state.discover.map={key:null,points:null,loading:false,error:null,stale:false,revision:0};
      }
    },
  };
}

function fakeTimers(){
  const timers=[];
  return {
    timers,
    setTimer(fn,delay){
      const timer={fn,delay,cancelled:false};
      timers.push(timer);
      return timer;
    },
    clearTimer(timer){if(timer)timer.cancelled=true;},
    async runNext(){
      const timer=timers.find(item=>!item.cancelled&&!item.ran);
      assert.ok(timer,'expected a scheduled retry');
      timer.ran=true;
      timer.fn();
      await new Promise(resolve=>setImmediate(resolve));
      await new Promise(resolve=>setImmediate(resolve));
      return timer;
    },
  };
}

const descriptor=mapRequestDescriptor({search:{results:[
  {listing_id:7,city:'Leipzig'},
  {listing_id:8,city:'Markkleeberg'},
  {listing_id:7,city:'Leipzig'},
]}});
assert.deepEqual(descriptor.items,[{listing_id:7,city:'Leipzig'},{listing_id:8,city:'Markkleeberg'}]);
assert.equal(descriptor.key,JSON.stringify([[7,'leipzig'],[8,'markkleeberg']]));

const groupedPoints={type:'FeatureCollection',features:[{
  type:'Feature',
  geometry:{type:'Point',coordinates:[12.3,51.3]},
  properties:{
    location_key:'postal:DE:04109',
    location_label:'04109 · Leipzig',
    precision:'POSTAL',
    listing_count:2,
    listing_refs:[
      {person_id:7,listing_id:101,advert_id:'a',address_label:'04109 Leipzig',price_60_cents:10000},
      {person_id:7,listing_id:202,advert_id:'b',address_label:'04107 Leipzig',price_60_cents:14000},
    ],
  },
}],meta:{mapped_listing_count:2,unresolved_listing_count:0,pending_count:0}};

const projectionAll=buildMapProjection(groupedPoints,[
  {personId:7,listing_id:101,advertId:'a'},
  {personId:7,listing_id:202,advertId:'b'},
]);
assert.equal(projectionAll.geojson.features.length,1);
assert.equal(projectionAll.geojson.features[0].properties.listing_count,2);
assert.equal(projectionAll.geojson.features[0].properties.price_60_sum_cents,24000);
assert.equal(projectionAll.geojson.features[0].properties.price_60_count,2);
assert.equal('listing_refs' in projectionAll.geojson.features[0].properties,false,'MapLibre projection must only contain render properties');

const projectionOne=buildMapProjection(groupedPoints,[{personId:7,listing_id:202,advertId:'b'}]);
assert.equal(projectionOne.geojson.features.length,1);
assert.equal(projectionOne.geojson.features[0].properties.listing_count,1);
assert.equal(projectionOne.geojson.features[0].properties.price_60_sum_cents,14000);
assert.deepEqual(projectionOne.byKey.get('postal:DE:04109').listings.map(item=>item.listing_id),[202]);
assert.equal(projectionOne.byKey.get('postal:DE:04109').listings[0].address_label,'04107 Leipzig');

const personOnly={...groupedPoints,features:groupedPoints.features.map(feature=>({
  ...feature,
  properties:{...feature.properties,listing_refs:[{person_id:7}]},
}))};
assert.equal(buildMapProjection(personOnly,[{personId:7,listing_id:202,advertId:'b'}]).geojson.features.length,0,'person identity alone must never bind a listing');

const sanitized=sanitizeMapPoints({
  type:'FeatureCollection',
  features:[
    groupedPoints.features[0],
    {type:'Feature',geometry:{type:'Point',coordinates:[999,51]},properties:{location_key:'bad',listing_count:2,listing_refs:[{listing_id:1},{listing_id:2}]}},
  ],
  meta:{mapped_listing_count:4,unresolved_listing_count:1,pending_count:0},
});
assert.equal(sanitized.invalidFeatures.length,1);
assert.equal(sanitized.points.features.length,1);
assert.equal(sanitized.points.meta.mapped_listing_count,2);
assert.equal(sanitized.points.meta.unresolved_listing_count,3);
assert.equal(sanitized.points.meta.invalid_location_count,1);
assert.equal('mapped_profile_count' in sanitized.points.meta,false);

// Partial geocoding keeps polling until all pending locations are resolved.
{
  const state=mapState();
  const store=createStore(state);
  const clock=fakeTimers();
  let requests=0;
  const effects=createMapEffects({
    store,
    setTimer:clock.setTimer,
    clearTimer:clock.clearTimer,
    mapPoints:async()=>{
      requests+=1;
      return {type:'FeatureCollection',features:[],meta:{pending_count:requests<=10?1:0}};
    },
  });
  await effects.loadMapPoints();
  for(let i=0;i<10;i+=1)await clock.runNext();
  assert.equal(requests,11,'partial map loading must continue past eight refreshes');
  assert.equal(state.discover.map.points.meta.pending_count,0);
  effects.dispose();
}

// A transient request error must schedule recovery without requiring another user action.
{
  const state=mapState();
  const store=createStore(state);
  const clock=fakeTimers();
  let requests=0;
  const effects=createMapEffects({
    store,
    setTimer:clock.setTimer,
    clearTimer:clock.clearTimer,
    mapPoints:async()=>{
      requests+=1;
      if(requests===1)throw new Error('temporary');
      return {type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'recovered'}};
    },
  });
  await effects.loadMapPoints();
  assert.equal(state.discover.map.error,'temporary');
  await clock.runNext();
  assert.equal(requests,2);
  assert.equal(state.discover.map.error,null);
  assert.equal(state.discover.map.points.meta.marker,'recovered');
  effects.dispose();
}

// Leaving during an in-flight request clears loading so re-entering can fetch again.
{
  const state=mapState();
  const store=createStore(state);
  const requests=[];
  const effects=createMapEffects({store,mapPoints:payload=>new Promise(resolve=>requests.push({payload,resolve}))});
  const first=effects.loadMapPoints();
  assert.equal(state.discover.map.loading,true);
  effects.cancelMapRequest();
  assert.equal(state.discover.map.loading,false);
  const second=effects.ensureMapData();
  assert.equal(requests.length,2);
  requests[1].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'reentered'}});
  await second;
  requests[0].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'cancelled'}});
  await first;
  assert.equal(state.discover.map.points.meta.marker,'reentered');
  effects.dispose();
}

// New result sets and projection revisions must reject older in-flight responses.
{
  const state=mapState();
  const store=createStore(state);
  const requests=[];
  const effects=createMapEffects({store,mapPoints:payload=>new Promise(resolve=>requests.push({payload,resolve}))});
  const oldLoad=effects.loadMapPoints({force:true});
  state.search.results=[{listing_id:2,city:'Grimma'}];
  const newLoad=effects.loadMapPoints({force:true});
  requests[1].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'new'}});
  await newLoad;
  requests[0].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'old'}});
  await oldLoad;
  assert.equal(state.discover.map.points.meta.marker,'new');

  const revisionLoad=effects.loadMapPoints({force:true});
  invalidateMapProjection(store);
  requests[2].resolve({type:'FeatureCollection',features:[],meta:{pending_count:0,marker:'stale-revision'}});
  await revisionLoad;
  assert.notEqual(state.discover.map.points?.meta?.marker,'stale-revision');
  assert.equal(state.discover.map.stale,true);
  effects.dispose();
}

// Projection-affecting domain mutations always advance the revision, even while stale.
{
  const state=mapState();
  const store=createStore(state);
  const effects=createMapEffects({store,mapPoints:async()=>({type:'FeatureCollection',features:[],meta:{pending_count:0}})});
  notifyMapProjectionChanged('first-mutation');
  notifyMapProjectionChanged('second-mutation');
  assert.equal(state.discover.map.revision,2);
  assert.equal(state.discover.map.stale,true);
  effects.dispose();
}

// API mutation boundaries emit projection changes for every mutation that can affect map data.
{
  const state=mapState();
  const store=createStore(state);
  const effects=createMapEffects({store,mapPoints:async()=>({type:'FeatureCollection',features:[],meta:{pending_count:0}})});
  const originalFetch=globalThis.fetch;
  globalThis.fetch=async()=>({
    ok:true,
    status:200,
    headers:{get:()=> 'application/json'},
    json:async()=>({}),
  });
  try{
    await api.profile('123');
    await api.addPriceList(1,{items:[]});
    await api.profileStatus('123');
    await api.merge(1,2);
    await api.split(7);
    await api.acceptPriceProposal(9,{});
    assert.equal(state.discover.map.revision,6);
  }finally{
    globalThis.fetch=originalFetch;
    effects.dispose();
  }
}

// Lifecycle ownership remains single-flight and cancellable.
{
  const target={isConnected:true};
  const lifecycle=createMapLifecycle();
  let starts=0;
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const first=lifecycle.run(target,async({active})=>{starts+=1;await gate;return active()?'map':null;});
  const duplicate=lifecycle.run(target,async()=>{starts+=1;return 'duplicate';});
  assert.equal(first,duplicate);
  assert.equal(starts,1);
  release();
  assert.equal(await first,'map');
  lifecycle.destroy();
}

function fakeLoadInstance(){
  let handler=null;
  return {
    once(event,next){assert.equal(event,'load');handler=next;},
    off(event,next){assert.equal(event,'load');if(handler===next)handler=null;},
    fireLoad(){handler?.();},
    hasLoadListener(){return Boolean(handler);},
  };
}

{
  const instance=fakeLoadInstance();
  let pendingCleanup=null;
  const cancelled=waitForMapReady({
    instance,
    isActive:()=>true,
    onReady:()=>instance,
    registerCleanup:cleanup=>{
      pendingCleanup=cleanup;
      return ()=>{if(pendingCleanup===cleanup)pendingCleanup=null;};
    },
    timeoutMs:1000,
  });
  assert.equal(instance.hasLoadListener(),true);
  pendingCleanup();
  assert.equal(await cancelled,null);
  assert.equal(instance.hasLoadListener(),false);
  assert.equal(pendingCleanup,null);
}

console.log('map frontend behavior checks passed');
