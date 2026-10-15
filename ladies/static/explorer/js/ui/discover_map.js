import * as S from '../app/selectors.js';
import { logClient } from '../api/client.js';
import { escapeAttr as a, escapeHtml as e } from '../shared/html.js';
import { icon } from '../shared/icons.js';

const MAPLIBRE_JS='https://cdn.jsdelivr.net/npm/maplibre-gl@6.14.0/dist/maplibre-gl.mjs';
const MAP_STYLE='https://tiles.openfreemap.org/styles/liberty';
const SOURCE_ID='discover-locations';
const CLUSTER_LAYER='discover-clusters';
const CLUSTER_COUNT_LAYER='discover-cluster-count';
const CLUSTER_PRICE_LAYER='discover-cluster-price';
const LOCATION_LAYER='discover-locations';
const LOCATION_COUNT_LAYER='discover-location-count';
const LOCATION_PRICE_LAYER='discover-location-price';
const EMPTY=Object.freeze({type:'FeatureCollection',features:[]});
const MAP_LOAD_TIMEOUT_MS=15000;

let maplibrePromise=null;
let runtime=null;
let latestData=EMPTY;
let selectedLocationKey=null;
let closeListenerInstalled=false;
let wasActive=false;
const reportedMapErrors=new Set();

export function createMapLifecycle(){
  let generation=0;
  let current=null;

  const runCleanups=entry=>{
    const cleanups=entry?.cleanups?.splice(0).reverse()||[];
    for(const cleanup of cleanups){try{cleanup();}catch{}}
  };

  const destroy=()=>{
    generation+=1;
    const active=current;
    current=null;
    runCleanups(active);
  };

  const run=(target,task)=>{
    if(current?.target===target)return current.promise;
    if(current)destroy();

    const token=++generation;
    const entry={target,token,cleanups:[],promise:null};
    current=entry;
    const active=()=>current===entry&&generation===token&&target?.isConnected!==false;
    const cleanup=fn=>{
      if(typeof fn!=='function')return ()=>{};
      if(!active()){
        try{fn();}catch{}
        return ()=>{};
      }
      entry.cleanups.push(fn);
      let registered=true;
      return ()=>{
        if(!registered)return;
        registered=false;
        const index=entry.cleanups.indexOf(fn);
        if(index>=0)entry.cleanups.splice(index,1);
      };
    };

    entry.promise=(async()=>{
      try{
        const value=await task({active,cleanup});
        if(!active()){
          if(current===entry)destroy();
          return null;
        }
        return value;
      }catch(error){
        if(current===entry)destroy();
        throw error;
      }
    })();
    return entry.promise;
  };

  return Object.freeze({run,destroy,hasActive:()=>Boolean(current)});
}

const mapLifecycle=createMapLifecycle();

function listingRefs(feature){
  const raw=feature?.properties?.listing_refs_json;
  if(Array.isArray(raw))return raw;
  try{return JSON.parse(String(raw||'[]'));}catch{return [];}
}

function rowRefKey(row){
  const listingId=Number(row?.listing_id||row?.listingId||0);
  if(listingId)return `listing:${listingId}`;
  const advertId=String(row?.advertId||row?.advert_id||'');
  return advertId?`advert:${advertId}`:'';
}

function listingRefKey(ref){
  const listingId=Number(ref?.listing_id||0);
  if(listingId)return `listing:${listingId}`;
  const advertId=String(ref?.advert_id||'');
  return advertId?`advert:${advertId}`:'';
}

function rowMatchesRef(row,ref){
  const refKey=listingRefKey(ref);
  return Boolean(refKey&&rowRefKey(row)===refKey);
}

function priceStats(refs){
  let sumCents=0;
  let count=0;
  for(const ref of refs){
    const raw=ref?.price_60_cents;
    if(raw===null||raw===undefined||raw==='')continue;
    const cents=Number(raw);
    if(!Number.isFinite(cents)||cents<0)continue;
    sumCents+=cents;
    count+=1;
  }
  return {sumCents,count};
}

export function filterMapFeatures(points,rows){
  if(!points?.features?.length||!rows?.length)return EMPTY;
  const visibleKeys=new Set(rows.map(rowRefKey).filter(Boolean));
  const features=[];
  for(const feature of points.features){
    const refs=listingRefs(feature).filter(ref=>visibleKeys.has(listingRefKey(ref)));
    if(!refs.length)continue;
    const prices=priceStats(refs);
    features.push({
      ...feature,
      properties:{
        ...(feature.properties||{}),
        listing_count:refs.length,
        price_60_sum_cents:prices.sumCents,
        price_60_count:prices.count,
        listing_refs_json:JSON.stringify(refs),
      }
    });
  }
  return {type:'FeatureCollection',features};
}

function rowForRef(rows,ref){return rows.find(row=>rowMatchesRef(row,ref))||null;}

function loadMapLibre(){
  if(!maplibrePromise){
    maplibrePromise=import(MAPLIBRE_JS).catch(error=>{
      maplibrePromise=null;
      throw error;
    });
  }
  return maplibrePromise;
}

function queueResize(instance,target){
  if(!instance||!target?.isConnected)return;
  requestAnimationFrame(()=>{
    if(runtime?.map===instance&&runtime.container===target&&target.isConnected)instance.resize();
  });
}

function destroyMap(){
  mapLifecycle.destroy();
  runtime=null;
  latestData=EMPTY;
  selectedLocationKey=null;
  reportedMapErrors.clear();
}

function fitInitialData(instance,data){
  const current=runtime;
  if(!instance||current?.map!==instance||current.initialFitDone||!current.lib?.LngLatBounds||!data.features.length)return;
  current.initialFitDone=true;
  if(data.features.length===1){
    instance.easeTo({center:data.features[0].geometry.coordinates,zoom:12.8,duration:280});
    return;
  }
  const bounds=new current.lib.LngLatBounds();
  data.features.forEach(feature=>bounds.extend(feature.geometry.coordinates));
  instance.fitBounds(bounds,{padding:{top:42,right:42,bottom:42,left:42},maxZoom:13,duration:300});
}

function renderStatus(host,state,data){
  if(!host)return;
  const visible=S.visibleSearchRows(state);
  const mapState=state.discover.map||{};
  const pending=Number(mapState.points?.meta?.pending_count||0);
  if(!visible.length){host.innerHTML=`<div class="discover-map-state">${icon('users')}<span>Keine Treffer für die aktuellen Filter.</span></div>`;return;}
  if(mapState.error&&!mapState.points){host.innerHTML=`<div class="discover-map-state">${icon('info')}<span>${e(mapState.error)}</span></div>`;return;}
  if((mapState.loading||pending>0)&&!data.features.length){host.innerHTML='<div class="discover-map-state"><span class="spinner"></span><span>Kartendaten werden vorbereitet …</span></div>';return;}
  if(mapState.points&&!data.features.length){host.innerHTML=`<div class="discover-map-state">${icon('pin')}<span>Für diese Treffer sind noch keine Kartendaten verfügbar.</span></div>`;return;}
  if(pending>0){host.innerHTML='<div class="discover-map-state discover-map-state-compact"><span class="spinner"></span><span>Karte wird ergänzt …</span></div>';return;}
  host.innerHTML='';
}

function precisionLabel(value){
  if(value==='POSTAL')return 'PLZ-Gebiet';
  if(value==='ADDRESS')return 'Adresse';
  return 'Stadtgebiet';
}

function renderPreview(host,state){
  if(!host)return;
  if(!selectedLocationKey){host.innerHTML='';return;}
  const feature=latestData.features.find(item=>String(item.properties?.location_key||'')===selectedLocationKey);
  if(!feature){selectedLocationKey=null;host.innerHTML='';return;}
  const rows=S.visibleSearchRows(state);
  const matched=listingRefs(feature).map(ref=>({ref,row:rowForRef(rows,ref)})).filter(item=>item.row);
  if(!matched.length){selectedLocationKey=null;host.innerHTML='';return;}
  const props=feature.properties||{};
  const precision=precisionLabel(String(props.precision||''));
  const close='<button type="button" class="discover-map-preview-close" data-map-preview-close aria-label="Vorschau schließen">×</button>';

  if(matched.length===1){
    const {row,ref}=matched[0];
    const pid=Number(row.personId||ref.person_id||0)||'';
    const advert=String(row.advertId||ref.advert_id||'');
    const image=row.thumbnail||'';
    const address=String(ref.address_label||props.location_label||row.city||'');
    host.innerHTML=`<article class="discover-map-preview">${close}<button type="button" class="discover-map-preview-main" data-action="open-profile" data-person-id="${a(pid)}" data-advert-id="${a(advert)}">${image?`<img src="${a(image)}" alt="${a(row.name)}">`:`<span class="discover-map-preview-image">${icon('user')}</span>`}<span class="discover-map-preview-copy"><b>${e(row.name)}${row.age?` <small>${e(row.age)}</small>`:''}</b><span>${icon('pin')}${e(address)}</span><small>${e(precision)} · Profil öffnen</small></span></button>${pid?`<button type="button" class="discover-map-preview-like ${row.preference==='LIKE'?'selected':''}" data-action="grid-like" data-person-id="${a(pid)}" data-advert-id="${a(advert)}" aria-label="Like">${icon('heart')}</button>`:''}</article>`;
    return;
  }

  host.innerHTML=`<section class="discover-map-location-sheet">${close}<header><b>${e(props.location_label||'Standort')}</b><small>${matched.length} Inserate · ${e(precision)}</small></header><div class="discover-map-location-list">${matched.map(({row,ref})=>{const pid=Number(row.personId||ref.person_id||0)||'';const advert=String(row.advertId||ref.advert_id||'');const address=String(ref.address_label||props.location_label||row.city||'');return `<button type="button" class="discover-map-location-row" data-action="open-profile" data-person-id="${a(pid)}" data-advert-id="${a(advert)}">${row.thumbnail?`<img class="discover-map-location-thumb" src="${a(row.thumbnail)}" alt="${a(row.name)}">`:`<span class="discover-map-location-thumb">${icon('user')}</span>`}<span class="discover-map-location-row-copy"><b>${e(row.name)}${row.age?` <small>${e(row.age)}</small>`:''}</b><small>${icon('pin')}${e(address)}</small></span>${icon('chevron-right')}</button>`;}).join('')}</div></section>`;
}

function styleTextFont(instance){
  for(const layer of instance.getStyle()?.layers||[]){
    const value=layer?.layout?.['text-font'];
    if(Array.isArray(value)&&value.length&&value.every(item=>typeof item==='string'))return value;
  }
  return null;
}

function priceLabelExpression(){
  return ['concat','Ø ',['to-string',['round',['/',['/',['get','price_60_sum_cents'],['max',1,['get','price_60_count']]],100]]],' €'];
}

function reportMapError(event){
  const error=event?.error;
  const message=String(error?.message||event?.message||'MapLibre-Laufzeitfehler');
  const sourceId=String(event?.sourceId||event?.source?.id||'');
  const key=`${sourceId}|${message}`;
  if(reportedMapErrors.has(key))return;
  if(reportedMapErrors.size>=20)reportedMapErrors.clear();
  reportedMapErrors.add(key);
  void logClient('error','map-runtime',message,{source_id:sourceId||null});
}

function clearSelection(target,getState){
  if(!selectedLocationKey)return;
  selectedLocationKey=null;
  renderPreview(target.closest('.discover-map-shell')?.querySelector('.discover-map-preview-host'),getState());
}

function installLayers(instance,target,getState){
  if(instance.getSource(SOURCE_ID))return;
  const current=getState();
  latestData=filterMapFeatures(current.discover.map?.points,S.visibleSearchRows(current));
  instance.addSource(SOURCE_ID,{
    type:'geojson',
    data:latestData,
    cluster:true,
    clusterMaxZoom:13,
    clusterRadius:48,
    clusterProperties:{listing_total:['+',['get','listing_count']],
      price_60_sum_cents:['+',['get','price_60_sum_cents']],
      price_60_count:['+',['get','price_60_count']]
    }
  });
  instance.addLayer({id:CLUSTER_LAYER,type:'circle',source:SOURCE_ID,filter:['has','point_count'],paint:{'circle-color':'#cc2d7c','circle-radius':['step',['get','listing_total'],18,10,22,30,27,75,32],'circle-stroke-color':'#ffffff','circle-stroke-width':2,'circle-opacity':.92}});
  instance.addLayer({id:LOCATION_LAYER,type:'circle',source:SOURCE_ID,filter:['!', ['has','point_count']],paint:{'circle-color':'#cc2d7c','circle-radius':['case',['>', ['get','listing_count'],1],13,8],'circle-stroke-color':'#ffffff','circle-stroke-width':3}});

  const textFont=styleTextFont(instance);
  if(textFont){
    instance.addLayer({id:CLUSTER_COUNT_LAYER,type:'symbol',source:SOURCE_ID,filter:['has','point_count'],layout:{'text-field':['to-string',['get','listing_total']],'text-size':12,'text-font':textFont},paint:{'text-color':'#ffffff'}});
    instance.addLayer({id:CLUSTER_PRICE_LAYER,type:'symbol',source:SOURCE_ID,filter:['all',['has','point_count'],['>', ['get','price_60_count'],0]],layout:{'text-field':priceLabelExpression(),'text-size':11,'text-font':textFont,'text-offset':[0,3],'text-anchor':'top'},paint:{'text-color':'#8f1f5a','text-halo-color':'#ffffff','text-halo-width':2}});
    instance.addLayer({id:LOCATION_COUNT_LAYER,type:'symbol',source:SOURCE_ID,filter:['all',['!', ['has','point_count']],['>', ['get','listing_count'],1]],layout:{'text-field':['to-string',['get','listing_count']],'text-size':10,'text-font':textFont},paint:{'text-color':'#ffffff'}});
    instance.addLayer({id:LOCATION_PRICE_LAYER,type:'symbol',source:SOURCE_ID,minzoom:10,filter:['all',['!', ['has','point_count']],['>', ['get','price_60_count'],0]],layout:{'text-field':priceLabelExpression(),'text-size':11,'text-font':textFont,'text-offset':[0,1.7],'text-anchor':'top'},paint:{'text-color':'#8f1f5a','text-halo-color':'#ffffff','text-halo-width':2}});
  }

  const expandCluster=async event=>{
    const feature=event.features?.[0]||instance.queryRenderedFeatures(event.point,{layers:[CLUSTER_LAYER]})[0];
    const clusterId=Number(feature?.properties?.cluster_id);
    if(!Number.isFinite(clusterId))return;
    try{
      const source=instance.getSource(SOURCE_ID);
      const zoom=await source.getClusterExpansionZoom(clusterId);
      instance.easeTo({center:feature.geometry.coordinates,zoom,duration:280});
      clearSelection(target,getState);
    }catch(error){
      void logClient('warning','map-cluster',error?.message||String(error),{cluster_id:clusterId});
    }
  };
  const selectLocation=event=>{
    const feature=event.features?.[0]||instance.queryRenderedFeatures(event.point,{layers:[LOCATION_LAYER]})[0];
    if(!feature)return;
    selectedLocationKey=String(feature.properties?.location_key||'');
    renderPreview(target.closest('.discover-map-shell')?.querySelector('.discover-map-preview-host'),getState());
  };
  for(const layer of [CLUSTER_LAYER,CLUSTER_PRICE_LAYER]){
    if(instance.getLayer(layer))instance.on('click',layer,expandCluster);
  }
  for(const layer of [LOCATION_LAYER,LOCATION_PRICE_LAYER]){
    if(instance.getLayer(layer))instance.on('click',layer,selectLocation);
  }
  for(const layer of [CLUSTER_LAYER,CLUSTER_PRICE_LAYER,LOCATION_LAYER,LOCATION_PRICE_LAYER]){
    if(!instance.getLayer(layer))continue;
    instance.on('mouseenter',layer,()=>{instance.getCanvas().style.cursor='pointer';});
    instance.on('mouseleave',layer,()=>{instance.getCanvas().style.cursor='';});
  }
  fitInitialData(instance,latestData);
}

export function waitForMapReady({instance,isActive,onReady,registerCleanup,timeoutMs=MAP_LOAD_TIMEOUT_MS}){
  return new Promise((resolve,reject)=>{
    let settled=false;
    let unregisterCleanup=()=>{};
    const finish=(error,value)=>{
      if(settled)return;
      settled=true;
      clearTimeout(timeout);
      if(typeof instance.off==='function')instance.off('load',handleLoad);
      unregisterCleanup();
      if(error)reject(error);
      else resolve(value);
    };
    const handleLoad=()=>{
      if(!isActive()){finish(null,null);return;}
      try{finish(null,onReady());}
      catch(error){finish(error,null);}
    };
    const timeout=setTimeout(()=>{
      if(!isActive()){finish(null,null);return;}
      finish(new Error('Die Kartenbasis konnte nicht rechtzeitig geladen werden.'),null);
    },timeoutMs);
    try{
      instance.once('load',handleLoad);
      unregisterCleanup=registerCleanup(()=>finish(null,null));
    }catch(error){
      finish(error,null);
    }
  });
}

function ensureMap(target,getState){
  return mapLifecycle.run(target,async lifecycle=>{
    const current={container:target,map:null,lib:null,loaded:false,initialFitDone:false,resizeObserver:null};
    runtime=current;
    lifecycle.cleanup(()=>{
      if(runtime===current)runtime=null;
      current.resizeObserver?.disconnect();
      if(current.map){try{current.map.remove();}catch{}}
    });

    const lib=await loadMapLibre();
    if(!lifecycle.active())return null;
    current.lib=lib;
    const instance=new lib.Map({
      container:target,
      style:MAP_STYLE,
      center:[12.3731,51.3397],
      zoom:10.5,
      attributionControl:true,
      fadeDuration:0
    });
    current.map=instance;
    instance.on('error',reportMapError);
    instance.addControl(new lib.NavigationControl({showCompass:false}),'bottom-right');
    if(typeof ResizeObserver==='function'){
      current.resizeObserver=new ResizeObserver(()=>{
        if(runtime===current&&current.map===instance&&target.isConnected)instance.resize();
      });
      current.resizeObserver.observe(target);
    }
    queueResize(instance,target);

    return await waitForMapReady({
      instance,
      isActive:()=>lifecycle.active()&&runtime===current&&current.map===instance&&target.isConnected,
      registerCleanup:lifecycle.cleanup,
      onReady:()=>{
        installLayers(instance,target,getState);
        current.loaded=true;
        queueResize(instance,target);
        return instance;
      }
    });
  });
}

function installCloseListener(){
  if(closeListenerInstalled)return;
  closeListenerInstalled=true;
  document.addEventListener('click',event=>{
    const close=event.target.closest('[data-map-preview-close]');
    if(!close)return;
    event.preventDefault();
    selectedLocationKey=null;
    const host=close.closest('.discover-map-preview-host');
    if(host)host.innerHTML='';
  });
}

export function syncDiscoverMap({host,state,store,effects}){
  installCloseListener();
  const route=S.currentRoute(state);
  const active=route.name==='discover'&&state.discover.mode==='map';
  if(!active){
    if(wasActive)effects.cancelMapRequest();
    wasActive=false;
    if(runtime)destroyMap();
    return;
  }
  wasActive=true;

  const target=host.querySelector('#discoverMap');
  if(!target)return;
  if(runtime&&runtime.container!==target)destroyMap();
  const shell=target.closest('.discover-map-shell');
  latestData=filterMapFeatures(state.discover.map?.points,S.visibleSearchRows(state));
  renderStatus(shell?.querySelector('.discover-map-state-host'),state,latestData);
  renderPreview(shell?.querySelector('.discover-map-preview-host'),state);

  if(!state.discover.map?.points&&!state.discover.map?.loading)queueMicrotask(()=>effects.loadMapPoints({quiet:true}));

  void ensureMap(target,()=>store.getState()).then(instance=>{
    if(!instance||runtime?.map!==instance||runtime.container!==target)return;
    const current=store.getState();
    latestData=filterMapFeatures(current.discover.map?.points,S.visibleSearchRows(current));
    const source=instance.getSource(SOURCE_ID);
    if(source){
      source.setData(latestData);
      fitInitialData(instance,latestData);
    }
    renderPreview(shell?.querySelector('.discover-map-preview-host'),current);
    queueResize(instance,target);
  }).catch(error=>{
    const status=shell?.querySelector('.discover-map-state-host');
    if(status)status.innerHTML=`<div class="discover-map-state">${icon('info')}<span>${e(error?.message||'Karte konnte nicht geladen werden.')}</span></div>`;
    void logClient('error','map-init',error?.message||String(error));
  });
}
