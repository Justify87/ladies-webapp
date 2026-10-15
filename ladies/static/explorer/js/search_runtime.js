import { filterPayload } from './data/adapters.js';
import * as S from './app/selectors.js';
import { searchTransient } from './api/search_runtime.js';

const payloadKey=filters=>JSON.stringify(filterPayload(filters||{}));

export function installActiveSearchSync({store,effects,search=searchTransient}) {
  let abort=null;
  let generation=0;
  let inFlightKey=null;
  let hydratedKey=null;
  let manualKey=null;
  let queued=false;

  function cancelAutomatic(){
    generation+=1;
    abort?.abort();
    abort=null;
    inFlightKey=null;
  }

  async function sync(){
    queued=false;
    const state=store.getState();
    const key=payloadKey(state.search.filters);

    if(inFlightKey&&inFlightKey!==key)cancelAutomatic();
    if(state.search.loading)return;
    if(state.search.preview?.status!=='ready')return;
    if(key===hydratedKey||key===inFlightKey)return;

    const sequence=++generation;
    inFlightKey=key;
    abort?.abort();
    abort=new AbortController();
    try{
      const data=await search(filterPayload(state.search.filters),{signal:abort.signal});
      const latest=store.getState();
      if(sequence!==generation||payloadKey(latest.search.filters)!==key||latest.search.loading)return;
      hydratedKey=key;
      inFlightKey=null;
      store.dispatch({type:'SEARCH_RESULTS',results:data?.results||[],meta:{...(data?.meta||{}),automatic:true,transient:true}});
      if(S.isSwipeDiscover(store.getState()))void effects.prefetchCurrent();
    }catch(error){
      if(error?.name!=='AbortError')console.warn('Automatic search result sync failed',error);
      if(sequence===generation)inFlightKey=null;
    }
  }

  function schedule(){
    if(queued)return;
    queued=true;
    queueMicrotask(sync);
  }

  store.subscribe((state,action)=>{
    if(action?.type==='SEARCH_LOADING'&&action.value===true){
      manualKey=payloadKey(state.search.filters);
      cancelAutomatic();
    }else if(action?.type==='SEARCH_RESULTS'&&!action.meta?.automatic){
      const key=payloadKey(state.search.filters);
      if(!manualKey||manualKey===key)hydratedKey=key;
    }else if(action?.type==='SEARCH_LOADING'&&action.value===false){
      manualKey=null;
    }
    schedule();
  });
  schedule();
}
