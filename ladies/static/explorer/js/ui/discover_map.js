import * as S from '../app/selectors.js';
import { logClient } from '../api/client.js';
import { escapeAttr as a, escapeHtml as e } from '../shared/html.js';
import { icon } from '../shared/icons.js';

const MAPLIBRE_JS='https://cdn.jsdelivr.net/npm/maplibre-gl@6.14.0/+esm';
const MAPLIBRE_CSS='https://cdn.jsdelivr.net/npm/maplibre-gl@6.14.0/dist/maplibre-gl.css';
const MAP_STYLE='https://tiles.openfreemap.org/styles/liberty';
const SOURCE_ID='discover-locations';
const EMPTY=Object.freeze({type:'FeatureCollection',features:[]});

export function createMapInitCoordinator(){
  let generation=0;
  let pending=null;
  const invalidate=()=>{generation+=1;pending=null;};
  const run=(target,task)=>{
    if(pending?.target===target)return pending.promise;
    const token=++generation;
    const active=()=>generation===token&&target?.isConnected!==false;
    const promise=(async()=>task(active))();
    pending={target,token,promise};
    const clear=()=>{if(pending?.token===token)pending=null;};
    void promise.then(clear,clear);
    return promise;
  };
  return Object.freeze({run,invalidate,pendingTarget:()=>pending?.target||null});
}

let maplibrePromise=null;
let maplibre=null;
let map=null;
let container=null;
let latestData=EMPTY;
let fittedSignature='';
let selectedLocationKey=null;
let closeListenerInstalled=false;
let wasActive=false;
const reportedMapErrors=new Set();
const mapInit=createMapInitCoordinator();

function listingRefs(feature){
  const raw=feature?.properties?.listing_refs_json;
  if(Array.isArray(raw))return raw;
  try{return JSON.parse(String(raw||'[]'));}catch{return [];}
}

function rowMatchesRef(row,ref){
  const listingId=Number(ref?.listing_id||0);
  if(listingId)return Number(row?.listing_id||row?.listingId||0)===listingId;
  const advertId=String(ref?.advert_id||'');
  return Boolean(advertId&&String(row?.advertId||row?.advert_id||'')===advertId);
}

export function filterMapFeatures(points,rows){
  if(!points?.features?.length||!rows?.length)return EMPTY;
  const features=[];
  for(const feature of points.features){
    const refs=listingRefs(feature).filter(ref=>rows.some(row=>rowMatchesRef(row,ref)));
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

function ensureMapLibre(){
  if(!document.querySelector('link[data-maplibre-css]')){
    const link=document.createElement('link');
    link.rel='stylesheet';
    link.href=MAPLIBRE_CSS;
    link.dataset.maplibreCss='1';
    document.head.appendChild(link);
  }
  if(maplibre)return Promise.resolve(maplibre);
  if(!maplibrePromise){
    maplibrePromise=import(MAPLIBRE_JS)
      .then(module=>{maplibre=module;return module;})
      .catch(error=>{
        maplibrePromise=null;
        maplibre=null;
        document.querySelector('link[data-maplibre-css]')?.remove();
        throw error;
      });
  }
  return maplibrePromise;
}

function cleanup(){
  mapInit.invalidate();
  const active=map;
  map=null;
  container=null;
  latestData=EMPTY;
  fittedSignature='';
  selectedLocationKey=null;
  reportedMapErrors.clear();
  if(active){try{active.remove();}catch{}}
}

function fitData(instance,state,data){
  if(!instance||!maplibre?.LngLatBounds||!data.features.length)return;
  const key=String(state.discover.map?.key||'');
  const signature=`${key}|${data.features.map(item=>item.properties?.location_key||'').sort().join(',')}`;
  if(signature===fittedSignature)return;
  fittedSignature=signature;
  if(data.features.length===1){
    instance.easeTo({center:data.features[0].geometry.coordinates,zoom:12.5,duration:300});
    return;
  }
  const bounds=new maplibre.LngLatBounds();
  data.features.forEach(feature=>bounds.extend(feature.geometry.coordinates));
  instance.fitBounds(bounds,{padding:42,maxZoom:13,duration:300});
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

function renderPreview(host,state){
  if(!host)return;
  if(!selectedLocationKey){host.innerHTML='';return;}
  const feature=latestData.features.find(item=>String(item.properties?.location_key||'')===selectedLocationKey);
  if(!feature){selectedLocationKey=null;host.innerHTML='';return;}
  const rows=S.visibleSearchRows(state);
  const refs=listingRefs(feature);
  const matched=refs.map(ref=>({ref,row:rowForRef(rows,ref)})).filter(item=>item.row);
  if(!matched.length){selectedLocationKey=null;host.innerHTML='';return;}
  const props=feature.properties||{};
  const precision=props.precision==='POSTAL'?'PLZ-Gebiet':'Suchgebiet';
  const close='<button type="button" class="discover-map-preview-close" data-map-preview-close aria-label="Vorschau schließen">×</button>';

  if(matched.length===1){
    const {row,ref}=matched[0];
    const pid=Number(row.personId||ref.person_id||0)||'';
    const advert=String(row.advertId||ref.advert_id||'');
    const image=row.thumbnail||'';
    host.innerHTML=`<article class="discover-map-preview">${close}<button type="button" class="discover-map-preview-main" data-action="open-profile" data-person-id="${a(pid)}" data-advert-id="${a(advert)}">${image?`<img src="${a(image)}" alt="${a(row.name)}">`:`<span class="discover-map-preview-image">${icon('user')}</span>`}<span class="discover-map-preview-copy"><b>${e(row.name)}${row.age?` <small>${e(row.age)}</small>`:''}</b><span>${icon('pin')}${e(props.location_label||row.city||'')}</span><small>${e(precision)} · Profil öffnen</small></span></button>${pid?`<button type="button" class="discover-map-preview-like ${row.preference==='LIKE'?'selected':''}" data-action="grid-like" data-person-id="${a(pid)}" data-advert-id="${a(advert)}" aria-label="Like">${icon('heart')}</button>`:''}</article>`;
    return;
  }

  host.innerHTML=`<section class="discover-map-location-sheet">${close}<header><b>${e(props.location_label||'Standort')}</b><small>${matched.length} Inserate · ${e(precision)}</small></header><div class="discover-map-location-list">${matched.map(({row,ref})=>{const pid=Number(row.personId||ref.person_id||0)||'';const advert=String(row.advertId||ref.advert_id||'');return `<button type="button" class="discover-map-location-row" data-action="open-profile" data-person-id="${a(pid)}" data-advert-id="${a(advert)}">${row.thumbnail?`<img src="${a(row.thumbnail)}" alt="${a(row.name)}">`:`<span>${icon('user')}</span>`}<b>${e(row.name)}${row.age?` <small>${e(row.age)}</small>`:''}</b>${icon('chevron-right')}</button>`;}).join('')}</div></section>`;
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

function installLayers(instance,target,getState){
  if(!instance||instance.getSource(SOURCE_ID))return;
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
  instance.addLayer({id:'discover-clusters',type:'circle',source:SOURCE_ID,filter:['has','point_count'],paint:{'circle-color':'#cc2d7c','circle-radius':['step',['get','listing_total'],18,10,22,30,27,75,32],'circle-stroke-color':'#ffffff','circle-stroke-width':2,'circle-opacity':.92}});
  instance.addLayer({id:'discover-locations',type:'circle',source:SOURCE_ID,filter:['!', ['has','point_count']],paint:{'circle-color':'#cc2d7c','circle-radius':['case',['>', ['get','listing_count'],1],13,8],'circle-stroke-color':'#ffffff','circle-stroke-width':3}});
  const textFont=styleTextFont(instance);
  if(textFont){
    instance.addLayer({id:'discover-cluster-count',type:'symbol',source:SOURCE_ID,filter:['has','point_count'],layout:{'text-field':['to-string',['get','listing_total']],'text-size':12,'text-font':textFont},paint:{'text-color':'#ffffff'}});
    instance.addLayer({id:'discover-location-count',type:'symbol',source:SOURCE_ID,filter:['all',['!', ['has','point_count']],['>', ['get','listing_count'],1]],layout:{'text-field':['to-string',['get','listing_count']],'text-size':10,'text-font':textFont},paint:{'text-color':'#ffffff'}});
  }

  instance.on('click','discover-clusters',async event=>{
    const feature=instance.queryRenderedFeatures(event.point,{layers:['discover-clusters']})[0];
    const clusterId=feature?.properties?.cluster_id;
    if(clusterId==null)return;
    try{
      const zoom=await instance.getSource(SOURCE_ID).getClusterExpansionZoom(clusterId);
      instance.easeTo({center:feature.geometry.coordinates,zoom,duration:280});
    }catch{}
  });
  instance.on('click','discover-locations',event=>{
    const feature=instance.queryRenderedFeatures(event.point,{layers:['discover-locations']})[0];
    if(!feature)return;
    selectedLocationKey=String(feature.properties?.location_key||'');
    const host=target.closest('.discover-map-shell')?.querySelector('.discover-map-preview-host');
    renderPreview(host,getState());
  });
  for(const layer of ['discover-clusters','discover-locations']){
    instance.on('mouseenter',layer,()=>{instance.getCanvas().style.cursor='pointer';});
    instance.on('mouseleave',layer,()=>{instance.getCanvas().style.cursor='';});
  }
  fitData(instance,current,latestData);
}

function ensureMap(target,state,getState){
  if(map&&container===target)return Promise.resolve(map);
  if(mapInit.pendingTarget()===target)return mapInit.run(target,()=>null);
  if(map||container||mapInit.pendingTarget())cleanup();
  return mapInit.run(target,async active=>{
    const lib=await ensureMapLibre();
    if(!active())return null;
    latestData=filterMapFeatures(state.discover.map?.points,S.visibleSearchRows(state));
    const instance=new lib.Map({container:target,style:MAP_STYLE,center:[12.3731,51.3397],zoom:10.5,attributionControl:true});
    instance.on('error',reportMapError);
    instance.addControl(new lib.NavigationControl({showCompass:false}),'bottom-right');
    if(!active()){try{instance.remove();}catch{}return null;}
    container=target;
    map=instance;
    instance.once('load',()=>{
      if(map!==instance||container!==target||!target.isConnected)return;
      installLayers(instance,target,getState);
    });
    return instance;
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
    if(map||mapInit.pendingTarget())cleanup();
    return;
  }
  wasActive=true;

  const target=host.querySelector('#discoverMap');
  if(!target)return;
  const shell=target.closest('.discover-map-shell');
  latestData=filterMapFeatures(state.discover.map?.points,S.visibleSearchRows(state));
  renderStatus(shell?.querySelector('.discover-map-state-host'),state,latestData);
  renderPreview(shell?.querySelector('.discover-map-preview-host'),state);

  if(!state.discover.map?.points&&!state.discover.map?.loading)queueMicrotask(()=>effects.loadMapPoints({quiet:true}));

  void ensureMap(target,state,()=>store.getState()).then(instance=>{
    if(!instance||map!==instance||container!==target)return;
    const current=store.getState();
    latestData=filterMapFeatures(current.discover.map?.points,S.visibleSearchRows(current));
    const source=instance.getSource(SOURCE_ID);
    if(source){source.setData(latestData);fitData(instance,current,latestData);}
  }).catch(error=>{
    const status=shell?.querySelector('.discover-map-state-host');
    if(status)status.innerHTML=`<div class="discover-map-state">${icon('info')}<span>${e(error?.message||'Karte konnte nicht geladen werden.')}</span></div>`;
  });
}
