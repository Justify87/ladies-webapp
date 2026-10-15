import * as S from '../app/selectors.js';
import { escapeHtml as e, escapeAttr as a } from '../shared/html.js';
import { icon } from '../shared/icons.js';

const MAPLIBRE_JS='https://unpkg.com/maplibre-gl@6.14.0/dist/maplibre-gl.mjs';
const MAPLIBRE_CSS='https://unpkg.com/maplibre-gl@6.14.0/dist/maplibre-gl.css';
const MAP_STYLE='https://tiles.openfreemap.org/styles/liberty';
const SOURCE_ID='discover-profiles';
const EMPTY_GEOJSON=Object.freeze({type:'FeatureCollection',features:[]});

let maplibrePromise=null;
let maplibreModule=null;
let map=null;
let mapContainer=null;
let currentData=EMPTY_GEOJSON;
let lastFitRunId=null;
let selectedFeature=null;
let bindings=null;
let navigationInstalled=false;

function loadMapLibre(){
  if(!document.querySelector('link[data-maplibre-css]')){
    const link=document.createElement('link');
    link.rel='stylesheet';
    link.href=MAPLIBRE_CSS;
    link.dataset.maplibreCss='1';
    document.head.appendChild(link);
  }
  maplibrePromise ||= import(MAPLIBRE_JS);
  return maplibrePromise;
}

function cleanupMap(){
  if(map){try{map.remove();}catch{}}
  map=null;
  mapContainer=null;
  currentData=EMPTY_GEOJSON;
  lastFitRunId=null;
  selectedFeature=null;
}

function visibleGeoJson(state){
  const raw=state.discover.map?.points;
  if(!raw?.features)return EMPTY_GEOJSON;
  const rows=S.visibleSearchRows(state);
  const people=new Set(rows.map(row=>Number(row.personId||0)).filter(Boolean));
  const adverts=new Set(rows.map(row=>String(row.advertId||'')).filter(Boolean));
  return {
    type:'FeatureCollection',
    features:raw.features.filter(feature=>{
      const props=feature?.properties||{};
      return people.has(Number(props.person_id||0))||adverts.has(String(props.advert_id||''));
    })
  };
}

function centerFor(state,data){
  const configured=state.discover.map?.points?.meta?.center;
  if(Array.isArray(configured)&&configured.length===2&&configured.every(Number.isFinite))return configured;
  if(data.features.length){
    return [
      data.features.reduce((sum,item)=>sum+Number(item.geometry?.coordinates?.[0]||0),0)/data.features.length,
      data.features.reduce((sum,item)=>sum+Number(item.geometry?.coordinates?.[1]||0),0)/data.features.length
    ];
  }
  return [12.3731,51.3397];
}

function renderStatus(host,state,data){
  if(!host)return;
  const mapState=state.discover.map||{};
  const rows=S.visibleSearchRows(state);
  if(!rows.length){host.innerHTML=`<div class="discover-map-state">${icon('users')}<span>Keine Treffer für die aktuellen Filter.</span></div>`;return;}
  if(mapState.loading&&!mapState.points){host.innerHTML='<div class="discover-map-state"><span class="spinner"></span><span>Karte wird vorbereitet …</span></div>';return;}
  if(mapState.error&&!mapState.points){host.innerHTML=`<div class="discover-map-state">${icon('info')}<span>${e(mapState.error)}</span></div>`;return;}
  if(mapState.points&&!data.features.length){host.innerHTML=`<div class="discover-map-state">${icon('pin')}<span>Für diese Treffer sind noch keine Kartendaten verfügbar.</span></div>`;return;}
  host.innerHTML='';
}

function renderPreview(host,state){
  if(!host)return;
  if(!selectedFeature){host.innerHTML='';return;}
  const selected=selectedFeature.properties||{};
  const rows=S.visibleSearchRows(state);
  const row=rows.find(item=>Number(item.personId||0)===Number(selected.person_id||0)||String(item.advertId||'')===String(selected.advert_id||''));
  if(!row){selectedFeature=null;host.innerHTML='';return;}
  const personId=Number(selected.person_id||row.personId||0)||'';
  const advertId=String(selected.advert_id||row.advertId||'');
  const name=row.name||selected.name||'Unbekannt';
  const city=row.city||selected.city||'';
  const postal=selected.postal_code||'';
  const image=row.thumbnail||selected.thumbnail_url||'';
  const precision=selected.precision==='POSTAL'?'PLZ-Gebiet':'Suchgebiet';
  host.innerHTML=`<article class="discover-map-preview"><button type="button" class="discover-map-preview-close" data-map-preview-close aria-label="Vorschau schließen">×</button><button type="button" class="discover-map-preview-main" data-action="open-profile" data-person-id="${a(personId)}" data-advert-id="${a(advertId)}">${image?`<img src="${a(image)}" alt="${a(name)}">`:`<span class="discover-map-preview-image">${icon('user')}</span>`}<span class="discover-map-preview-copy"><b>${e(name)}${row.age?` <small>${e(row.age)}</small>`:''}</b><span>${icon('pin')}${e([city,postal].filter(Boolean).join(' '))}</span><small>${e(precision)} · Profil öffnen</small></span></button>${personId?`<button type="button" class="discover-map-preview-like ${row.preference==='LIKE'?'selected':''}" data-action="grid-like" data-person-id="${a(personId)}" data-advert-id="${a(advertId)}" aria-label="Like">${icon('heart')}</button>`:''}</article>`;
}

function fitData(state,data){
  if(!map||!maplibreModule||!data.features.length)return;
  const runId=Number(state.discover.map?.runId||0)||'local';
  if(lastFitRunId===runId)return;
  lastFitRunId=runId;
  if(data.features.length===1){
    map.easeTo({center:data.features[0].geometry.coordinates,zoom:12.5,duration:350});
    return;
  }
  const bounds=data.features.reduce((acc,feature)=>acc.extend(feature.geometry.coordinates),new maplibreModule.LngLatBounds());
  map.fitBounds(bounds,{padding:42,maxZoom:13,duration:350});
}

function installLayers(state){
  if(!map||map.getSource(SOURCE_ID))return;
  map.addSource(SOURCE_ID,{type:'geojson',data:currentData,cluster:true,clusterMaxZoom:14,clusterRadius:46});
  map.addLayer({
    id:'discover-clusters',type:'circle',source:SOURCE_ID,filter:['has','point_count'],
    paint:{'circle-color':'#cc2d7c','circle-radius':['step',['get','point_count'],18,10,22,30,27,75,32],'circle-stroke-color':'#ffffff','circle-stroke-width':2,'circle-opacity':0.92}
  });
  map.addLayer({
    id:'discover-cluster-count',type:'symbol',source:SOURCE_ID,filter:['has','point_count'],
    layout:{'text-field':['get','point_count_abbreviated'],'text-size':12},paint:{'text-color':'#ffffff'}
  });
  map.addLayer({
    id:'discover-points',type:'circle',source:SOURCE_ID,filter:['!', ['has','point_count']],
    paint:{'circle-color':'#cc2d7c','circle-radius':8,'circle-stroke-color':'#ffffff','circle-stroke-width':3}
  });

  map.on('click','discover-clusters',async event=>{
    const feature=map.queryRenderedFeatures(event.point,{layers:['discover-clusters']})[0];
    const clusterId=feature?.properties?.cluster_id;
    if(clusterId==null)return;
    const source=map.getSource(SOURCE_ID);
    try{
      const zoom=await source.getClusterExpansionZoom(clusterId);
      map.easeTo({center:feature.geometry.coordinates,zoom,duration:300});
    }catch{}
  });
  map.on('click','discover-points',event=>{
    const feature=map.queryRenderedFeatures(event.point,{layers:['discover-points']})[0];
    if(!feature)return;
    selectedFeature=feature;
    renderPreview(mapContainer?.closest('.discover-map-shell')?.querySelector('.discover-map-preview-host'),bindings?.store.getState());
  });
  for(const layer of ['discover-clusters','discover-points']){
    map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor='';});
  }
  fitData(state,currentData);
}

async function ensureMap(container,state){
  if(map&&mapContainer===container)return map;
  if(mapContainer!==container){
    cleanupMap();
    mapContainer=container;
  }
  const lib=await loadMapLibre();
  if(!container.isConnected||mapContainer!==container)return null;
  if(map)return map;
  maplibreModule=lib;
  currentData=visibleGeoJson(state);
  map=new lib.Map({container,style:MAP_STYLE,center:centerFor(state,currentData),zoom:10.5,attributionControl:true});
  map.addControl(new lib.NavigationControl({showCompass:false}),'bottom-right');
  map.once('load',()=>installLayers(bindings?.store.getState()||state));
  return map;
}

function installNavigation(){
  if(navigationInstalled)return;
  navigationInstalled=true;
  document.addEventListener('click',async event=>{
    const modeButton=event.target.closest('[data-discover-mode]');
    if(modeButton&&bindings){
      event.preventDefault();
      event.stopPropagation();
      const mode=String(modeButton.dataset.discoverMode||'swipe');
      if(!['swipe','grid','map'].includes(mode))return;
      bindings.store.dispatch({type:'DISCOVER_MODE',mode});
      bindings.router.replaceRoot(mode);
      if(mode==='swipe')await bindings.effects.prefetchCurrent();
      else if(mode==='map')await bindings.effects.loadMapPoints({quiet:true});
      return;
    }
    const close=event.target.closest('[data-map-preview-close]');
    if(close){
      event.preventDefault();
      selectedFeature=null;
      const host=close.closest('.discover-map-preview-host');
      if(host)host.innerHTML='';
    }
  });
}

export function syncDiscoverMap({host,state,store,effects,router}){
  bindings={store,effects,router};
  installNavigation();
  const route=S.currentRoute(state);
  if(route.name!=='map'){
    if(map||mapContainer)cleanupMap();
    return;
  }

  const container=host.querySelector('#discoverMap');
  if(!container)return;
  const data=visibleGeoJson(state);
  const shell=container.closest('.discover-map-shell');
  renderStatus(shell?.querySelector('.discover-map-state-host'),state,data);
  renderPreview(shell?.querySelector('.discover-map-preview-host'),state);

  if(!state.discover.map?.points&&!state.discover.map?.loading){
    void effects.loadMapPoints({quiet:true});
  }

  void ensureMap(container,state).then(instance=>{
    if(!instance||mapContainer!==container)return;
    currentData=visibleGeoJson(store.getState());
    const source=instance.getSource(SOURCE_ID);
    if(source){source.setData(currentData);fitData(store.getState(),currentData);}
  }).catch(error=>{
    const status=shell?.querySelector('.discover-map-state-host');
    if(status)status.innerHTML=`<div class="discover-map-state">${icon('info')}<span>${e(error?.message||'Karte konnte nicht geladen werden.')}</span></div>`;
  });
}
