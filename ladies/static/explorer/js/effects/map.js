import { api } from '../api/client.js';

export function createMapEffects({store}) {
  const state=()=>store.getState();

  async function currentSearchRunId() {
    const explicit=Number(state().search.runId||0);
    if(explicit)return explicit;
    const recent=state().search.recent||[];
    const cached=Number(recent[0]?.search_run_id||0);
    if(cached)return cached;
    const response=await api.recentSearches(1);
    return Number(response?.items?.[0]?.search_run_id||0)||null;
  }

  async function loadMapPoints({force=false,quiet=false}={}) {
    let runId;
    try{runId=await currentSearchRunId();}
    catch(error){
      store.dispatch({type:'DISCOVER_MAP_LOADING',value:false,error:error.message});
      if(!quiet)store.dispatch({type:'TOAST',value:error.message});
      return null;
    }
    if(!runId)return null;
    const current=state().discover.map||{};
    if(!force&&current.runId===runId&&current.points)return current.points;
    if(current.loading)return current.points||null;
    store.dispatch({type:'DISCOVER_MAP_LOADING',value:true});
    try{
      const points=await api.mapPoints(runId);
      store.dispatch({type:'DISCOVER_MAP_POINTS',points,runId});
      return points;
    }catch(error){
      store.dispatch({type:'DISCOVER_MAP_LOADING',value:false,error:error.message});
      if(!quiet)store.dispatch({type:'TOAST',value:error.message});
      return null;
    }
  }

  return Object.freeze({loadMapPoints});
}
