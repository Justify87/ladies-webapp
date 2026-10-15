import * as S from '../app/selectors.js';
import { logClient } from '../api/client.js';
import { escapeAttr as a, escapeHtml as e } from '../shared/html.js';
import { icon } from '../shared/icons.js';

const MAPLIBRE_JS='https://cdn.jsdelivr.net/npm/maplibre-gl@6.14.0/dist/maplibre-gl.mjs';
const MAP_STYLE='https://tiles.openfreemap.org/styles/liberty';
const SOURCE_ID='discover-locations';
const CLUSTER_LAYER='discover-clusters';
const CLUSTER_COUNT_LAYER='discover-cluster-count';
const LOCATION_LAYER='discover-locations';
const LOCATION_COUNT_LAYER='discover-location-count';
const EMPTY=Object.freeze({type:'FeatureCollection',features:[]});
const MAP_LOAD_TIMEOUT_MS=15000;

let maplibrePromise=null;
let runtime=null;
let runtimeGeneration=0;
let latestData=EMPTY;
let fittedSignature='';
let selectedLocationKey=null;
let closeListenerInstalled=false;
let wasActive=false;
const reportedMapErrors=new Set();

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

export function filterMapFeatures(points,rows){
  if(!points?.features?.length||!rows?.length)return EMPTY;
  const visibleKeys=new Set(rows.map(rowRefKey).filter(Boolean));
  const features=[];
  for(const feature of points.features){
    const refs=listingRefs(feature).filter(ref=>visibleKeys.has(listingRefKey(ref)));
    if(!refs.length)continue;
    features.push({
      ...feature,
      properties:{
        ...(feature.properties||{}),
        listing_count:refs.length,
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
  runtimeGeneration+=1;
  const active=runtime;
  runtime=null;
  latestData=EMPTY;
  fittedSignature='';
  selectedLocationKey=null;
  reportedMapErrors.clear();
  active?.resizeObserver?.disconnect();
  if(active?.map){try{active.map.remove();}catch{}}
}

function fitData(instance,state,data){
  if(!instance||!runtime?.lib?.LngLatBounds||!data.features.length)return;
  const key=String(state.discover.map?.key||'');
  const signature=`${key}|${data.features.map(item=>item.properties?.location_key||'').sort().join(',')}`;
  if(signature===fittedSignature)return;
  fittedSignature=signature;
  if(data.features.length===1){
    instance.easeTo({center:data.features[0].geometry.coordinates,zoom:12.8,duration:280});
    return;
  }
  const bounds=new runtime.lib.LngLatBounds();
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
    clusterProperties:{listing_total:['+',['get','listing_count']]}
  });
  instance.addLayer({id:CLUSTER_LAYER,type:'circle',source:SOURCE_ID,filter:['has','point_count'],paint:{'circle-color':'#cc2d7c','circle-radius':['step',['get','listing_total'],18,10,22,30,27,75,32],'circle-stroke-color':'#ffffff','circle-stroke-width':2,'circle-opacity':.92}});
  instance.addLayer({id:LOCATION_LAYER,type:'circle',source:SOURCE_ID,filter:['!', ['has','point_count']],paint:{'circle-color':'#cc2d7c','circle-radius':['case',['>', ['get','listing_count'],1],13,8],'circle-stroke-color':'#ffffff','circle-stroke-width':3}});

  const textFont=styleTextFont(instance);
  if(textFont){
    instance.addLayer({id:CLUSTER_COUNT_LAYER,type:'symbol',source:SOURCE_ID,filter:['has','point_count'],layout:{'text-field':['to-string',['get','listing_total']],'text-size':12,'text-font':textFont},paint:{'text-color':'#ffffff'}});
    instance.addLayer({id:LOCATION_COUNT_LAYER,type:'symbol',source:SOURCE_ID,filter:['all',['!', ['has','point_count']],['>', ['get','listing_count'],1]],layout:{'text-field':['to-string',['get','listing_count']],'text-size':10,'text-font':textFont},paint:{'text-color':'#ffffff'}});
  }

  instance.on('click',CLUSTER_LAYER,async event=>{
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
  });
  instance.on('click',LOCATION_LAYER,event=>{
    const feature=event.features?.[0]||instance.queryRenderedFeatures(event.point,{layers:[LOCATION_LAYER]})[0];
    if(!feature)return;
    selectedLocationKey=String(feature.properties?.location_key||'');
    renderPreview(target.closest('.discover-map-shell')?.querySelector('.discover-map-preview-host'),getState());
  });
  for(const layer of [CLUSTER_LAYER,LOCATION_LAYER]){
    instance.on('mouseenter',layer,()=>{instance.getCanvas().style.cursor='pointer';});
    instance.on('mouseleave',layer,()=>{instance.getCanvas().style.cursor='';});
  }
  fitData(instance,current,latestData);
}

function waitUntilLoaded(instance,token,target,getState){
  return new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Die Kartenbasis konnte nicht rechtzeitig geladen werden.')),MAP_LOAD_TIMEOUT_MS);
    instance.once('load',()=>{
      clearTimeout(timeout);
      if(token!==runtimeGeneration||runtime?.map!==instance||runtime.container!==target||!target.isConnected){resolve(null);return;}
      installLayers(instance,target,getState);
      runtime.loaded=true;
      queueResize(instance,target);
      resolve(instance);
    });
  });
}

async function ensureMap(target,getState){
  if(runtime?.container===target)return runtime.promise;
  if(runtime)destroyMap();

  const token=++runtimeGeneration;
  const current={container:target,map:null,lib:null,loaded:false,resizeObserver:null,promise:null};
  runtime=current;
  current.promise=(async()=>{
    const lib=await loadMapLibre();
    if(token!==runtimeGeneration||runtime!==current||!target.isConnected)return null;
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
    try{
      return await waitUntilLoaded(instance,token,target,getState);
    }catch(error){
      if(runtime===current)destroyMap();
      throw error;
    }
  })();
  return current.promise;
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
      fitData(instance,current,latestData);
    }
    renderPreview(shell?.querySelector('.discover-map-preview-host'),current);
    queueResize(instance,target);
  }).catch(error=>{
    const status=shell?.querySelector('.discover-map-state-host');
    if(status)status.innerHTML=`<div class="discover-map-state">${icon('info')}<span>${e(error?.message||'Karte konnte nicht geladen werden.')}</span></div>`;
    void logClient('error','map-init',error?.message||String(error));
  });
}
