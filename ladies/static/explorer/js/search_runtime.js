import { filterPayload } from './data/adapters.js';
import * as S from './app/selectors.js';
import { searchTransient } from './api/search_runtime.js';

const payloadKey=filters=>JSON.stringify(filterPayload(filters||{}));

export function installActiveSearchSync({store,effects}) {
  let abort=null;
  let generation=0;
  let inFlightKey=null;
  let hydratedKey=null;
  let suppressedKey=null;
  let lastLoading=false;
  let queued=false;

  async function sync(){
    queued=false;
    const state=store.getState();
    const key=payloadKey(state.search.filters);

    if(inFlightKey&&inFlightKey!==key){
      generation+=1;
      abort?.abort();
      abort=null;
      inFlightKey=null;
    }

    if(state.search.loading){
      if(!lastLoading){
        generation+=1;
        abort?.abort();
        abort=null;
        inFlightKey=null;
        suppressedKey=key;
      }
      lastLoading=true;
      return;
    }

    if(lastLoading){
      lastLoading=false;
      if(state.search.error)suppressedKey=null;
    }

    if(state.search.preview?.status!=='ready')return;
    if(key===hydratedKey||key===suppressedKey||key===inFlightKey)return;

    const sequence=++generation;
    inFlightKey=key;
    abort?.abort();
    abort=new AbortController();
    try{
      const data=await searchTransient(filterPayload(state.search.filters),{signal:abort.signal});
      const latest=store.getState();
      if(sequence!==generation||payloadKey(latest.search.filters)!==key||latest.search.loading)return;
      hydratedKey=key;
      inFlightKey=null;
      store.dispatch({type:'SEARCH_RESULTS',results:data?.results||[],meta:{...(data?.meta||{}),automatic:true,transient:true}});
      const route=S.currentRoute(store.getState());
      if(route.name==='swipe'||route.name==='grid')void effects.prefetchCurrent();
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

  store.subscribe(schedule);
  schedule();
}
