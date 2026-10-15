import { api, logClient } from '../api/client.js';

const MAX_REFRESHES=8;
const REFRESH_DELAY_MS=1400;

function activeMapItems(results=[]){
  const items=[];
  const indexByListing=new Map();
  for(const row of results||[]){
    const listingId=Number(row?.listing_id??row?.listingId??0);
    if(listingId<=0)continue;
    const city=String(row?.city||'').trim();
    const existing=indexByListing.get(listingId);
    if(existing===undefined){
      indexByListing.set(listingId,items.length);
      items.push({listing_id:listingId,city});
    }else if(city&&!items[existing].city){
      items[existing].city=city;
    }
  }
  return items;
}

function validCoordinatePair(value){
  if(!Array.isArray(value)||value.length<2)return false;
  const lng=Number(value[0]),lat=Number(value[1]);
  return Number.isFinite(lng)&&Number.isFinite(lat)&&lng>=-180&&lng<=180&&lat>=-90&&lat<=90;
}

async function sanitizeMapPoints(points){
  const features=Array.isArray(points?.features)?points.features:[];
  const valid=features.filter(feature=>feature?.geometry?.type==='Point'&&validCoordinatePair(feature.geometry.coordinates));
  if(valid.length===features.length)return points;
  const invalid=features.filter(feature=>!valid.includes(feature));
  await logClient('error','map-data','Ungültige Kartenkoordinaten vom Backend verworfen',{
    invalid_count:invalid.length,
    location_keys:invalid.map(feature=>feature?.properties?.location_key).filter(Boolean).slice(0,12)
  });
  const center=valid.length?[
    valid.reduce((sum,feature)=>sum+Number(feature.geometry.coordinates[0]),0)/valid.length,
    valid.reduce((sum,feature)=>sum+Number(feature.geometry.coordinates[1]),0)/valid.length
  ]:null;
  const invalidProfiles=invalid.reduce((sum,feature)=>sum+Number(feature?.properties?.profile_count||0),0);
  return {
    ...(points||{}),
    type:'FeatureCollection',
    features:valid,
    meta:{
      ...(points?.meta||{}),
      center,
      location_count:valid.length,
      mapped_profile_count:Math.max(0,Number(points?.meta?.mapped_profile_count||0)-invalidProfiles),
      unresolved_profile_count:Number(points?.meta?.unresolved_profile_count||0)+invalidProfiles,
      invalid_location_count:invalid.length
    }
  };
}

export function mapRequestDescriptor(state){
  const items=activeMapItems(state?.search?.results);
  const identity=items.map(item=>[item.listing_id,item.city.toLocaleLowerCase('de-DE')]);
  return {items,key:JSON.stringify(identity)};
}

export function createMapEffects({store,mapPoints=api.mapPoints}) {
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
    const current=store.getState().discover.map||{};
    if(!requested.items.length){
      cancel();
      if(current.key||current.points||current.loading||current.error)store.dispatch({type:'DISCOVER_MAP_CLEAR'});
      return null;
    }

    if(!force&&current.key===requested.key&&current.points&&!current.points?.meta?.pending_count)return current.points;
    if(!force&&current.key===requested.key&&current.loading)return current.points||null;

    const sequence=++generation;
    clearTimeout(timer);
    timer=null;
    store.dispatch({type:'DISCOVER_MAP_LOADING',key:requested.key,value:true});

    try{
      const points=await sanitizeMapPoints(await mapPoints({items:requested.items}));
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
      }else refreshes=0;
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
