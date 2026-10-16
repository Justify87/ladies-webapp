import { api, logClient } from '../api/client.js';
import { mapRequestDescriptor, sanitizeMapPoints } from '../data/map_model.js';
import { subscribeMapProjectionChanges } from '../shared/domain_events.js';

const PARTIAL_RETRY_MIN_MS=1200;
const PARTIAL_RETRY_MAX_MS=5000;
const ERROR_RETRY_MIN_MS=2000;
const ERROR_RETRY_MAX_MS=15000;

export { mapRequestDescriptor } from '../data/map_model.js';

export function invalidateMapProjection(store){
  store.dispatch({type:'DISCOVER_MAP_INVALIDATE'});
}

export const invalidateMapData=invalidateMapProjection;

function linearBackoff(base,max,attempt){
  return Math.min(max,base*Math.max(1,attempt));
}

function errorBackoff(attempt){
  return Math.min(ERROR_RETRY_MAX_MS,ERROR_RETRY_MIN_MS*(2**Math.min(3,Math.max(0,attempt-1))));
}

export function createMapEffects({
  store,
  mapPoints=api.mapPoints,
  setTimer=setTimeout,
  clearTimer=clearTimeout,
}={}) {
  let generation=0;
  let timer=null;
  let timerKey=null;
  let partialAttempts=0;
  let errorAttempts=0;
  const unsubscribeProjection=subscribeMapProjectionChanges(()=>invalidateMapProjection(store));

  const clearScheduledRetry=()=>{
    if(timer!==null)clearTimer(timer);
    timer=null;
    timerKey=null;
  };

  const cancel=()=>{
    generation+=1;
    clearScheduledRetry();
    partialAttempts=0;
    errorAttempts=0;
  };

  const scheduleRetry=(requested,revision,delay)=>{
    clearScheduledRetry();
    timerKey=requested.key;
    timer=setTimer(()=>{
      timer=null;
      timerKey=null;
      const state=store.getState();
      const latest=mapRequestDescriptor(state);
      const latestRevision=Number(state.discover.map?.revision||0);
      if(latest.key===requested.key&&latestRevision===revision){
        void loadMapPoints({force:true,quiet:true});
      }
    },delay);
  };

  async function loadMapPoints({force=false,quiet=true}={}) {
    const requested=mapRequestDescriptor(store.getState());
    const current=store.getState().discover.map||{};
    const revision=Number(current.revision||0);
    const pending=Number(current.points?.meta?.pending_count||0);
    if(!requested.items.length){
      cancel();
      if(current.key||current.points||current.loading||current.error||current.stale){
        store.dispatch({type:'DISCOVER_MAP_CLEAR'});
      }
      return null;
    }

    const sameKey=current.key===requested.key;
    if(!force&&sameKey&&current.loading)return current.points||null;
    if(!force&&sameKey&&!current.stale&&current.points&&!pending&&!current.error)return current.points;
    if(!force&&sameKey&&!current.stale&&timer!==null&&timerKey===requested.key&&(pending>0||current.error))return current.points||null;

    const sequence=++generation;
    clearScheduledRetry();
    store.dispatch({type:'DISCOVER_MAP_LOADING',key:requested.key,value:true});

    try{
      const sanitized=sanitizeMapPoints(await mapPoints({items:requested.items}));
      if(sanitized.invalidFeatures.length){
        await logClient('error','map-data','Ungültige Kartenkoordinaten vom Backend verworfen',{
          invalid_count:sanitized.invalidFeatures.length,
          location_keys:sanitized.invalidFeatures.map(feature=>feature?.properties?.location_key).filter(Boolean).slice(0,12),
        });
      }
      const points=sanitized.points;
      const latestState=store.getState();
      const latest=mapRequestDescriptor(latestState);
      const latestRevision=Number(latestState.discover.map?.revision||0);
      if(sequence!==generation||latest.key!==requested.key||latestRevision!==revision)return null;

      store.dispatch({type:'DISCOVER_MAP_POINTS',key:requested.key,points});
      const nextPending=Number(points?.meta?.pending_count||0);
      if(nextPending>0){
        errorAttempts=0;
        partialAttempts+=1;
        scheduleRetry(requested,revision,linearBackoff(PARTIAL_RETRY_MIN_MS,PARTIAL_RETRY_MAX_MS,partialAttempts));
      }else{
        partialAttempts=0;
        errorAttempts=0;
      }
      return points;
    }catch(error){
      if(sequence!==generation)return null;
      const latestState=store.getState();
      const latest=mapRequestDescriptor(latestState);
      const latestRevision=Number(latestState.discover.map?.revision||0);
      if(latest.key!==requested.key||latestRevision!==revision)return null;
      errorAttempts+=1;
      store.dispatch({type:'DISCOVER_MAP_LOADING',key:requested.key,value:false,error:error?.message||String(error)});
      scheduleRetry(requested,revision,errorBackoff(errorAttempts));
      if(!quiet)store.dispatch({type:'TOAST',value:error?.message||String(error)});
      return null;
    }
  }

  function ensureMapData(options={}){
    return loadMapPoints({...options,force:false});
  }

  const dispose=()=>{
    cancel();
    unsubscribeProjection();
  };

  return Object.freeze({ensureMapData,loadMapPoints,cancelMapRequest:cancel,dispose});
}
