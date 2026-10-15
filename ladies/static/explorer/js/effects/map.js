import { api } from '../api/client.js';

const MAX_REFRESHES=8;
const REFRESH_DELAY_MS=1400;

function uniqueListingIds(results=[]){
  const ids=[];
  const seen=new Set();
  for(const row of results||[]){
    const value=Number(row?.listing_id??row?.listingId??0);
    if(value>0&&!seen.has(value)){seen.add(value);ids.push(value);}
  }
  return ids;
}

export function mapRequestDescriptor(state){
  const listingIds=uniqueListingIds(state?.search?.results);
  const city=String(state?.search?.filters?.city||'').trim();
  return {
    listingIds,
    city,
    key:`${city.toLocaleLowerCase('de-DE')}|${listingIds.join(',')}`
  };
}

export function createMapEffects({store}) {
  let generation=0;
  let timer=null;
  let refreshes=0;

  const cancel=()=>{
    generation+=1;
    clearTimeout(timer);
    timer=null;
    refreshes=0;
  };

  async function loadMapPoints({force=false,quiet=true}={}) {
    const requested=mapRequestDescriptor(store.getState());
    if(!requested.listingIds.length){
      cancel();
      store.dispatch({type:'DISCOVER_MAP_CLEAR'});
      return null;
    }

    const current=store.getState().discover.map||{};
    if(!force&&current.key===requested.key&&current.points&&!current.points?.meta?.pending_count)return current.points;
    if(!force&&current.key===requested.key&&current.loading)return current.points||null;

    const sequence=++generation;
    clearTimeout(timer);
    timer=null;
    store.dispatch({type:'DISCOVER_MAP_LOADING',key:requested.key,value:true});

    try{
      const points=await api.mapPoints({
        listing_ids:requested.listingIds,
        fallback_city:requested.city
      });
      const latest=mapRequestDescriptor(store.getState());
      if(sequence!==generation||latest.key!==requested.key)return null;

      store.dispatch({type:'DISCOVER_MAP_POINTS',key:requested.key,points});
      if(Number(points?.meta?.pending_count||0)>0&&refreshes<MAX_REFRESHES){
        refreshes+=1;
        timer=setTimeout(()=>{
          timer=null;
          const now=mapRequestDescriptor(store.getState());
          if(now.key===requested.key)void loadMapPoints({force:true,quiet:true});
        },REFRESH_DELAY_MS);
      }else{
        refreshes=0;
      }
      return points;
    }catch(error){
      if(sequence!==generation)return null;
      const latest=mapRequestDescriptor(store.getState());
      if(latest.key!==requested.key)return null;
      store.dispatch({type:'DISCOVER_MAP_LOADING',key:requested.key,value:false,error:error?.message||String(error)});
      if(!quiet)store.dispatch({type:'TOAST',value:error?.message||String(error)});
      return null;
    }
  }

  return Object.freeze({loadMapPoints,cancelMapRequest:cancel});
}
