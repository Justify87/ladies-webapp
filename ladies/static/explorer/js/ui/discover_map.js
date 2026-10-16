import * as S from '../app/selectors.js';
import { logClient } from '../api/client.js';
import { buildMapProjection, indexMapRows, mapRefKey, visibleListingSignature } from '../data/map_model.js';
import { escapeAttr as a, escapeHtml as e } from '../shared/html.js';
import { icon } from '../shared/icons.js';
import { createDiscoverMapRuntime } from './map_runtime.js';

const EMPTY_PROJECTION=Object.freeze({geojson:Object.freeze({type:'FeatureCollection',features:[]}),locations:[],byKey:new Map(),signature:'empty'});
const UNSET=Symbol('unset');

let runtime=null;
let runtimeTarget=null;
let currentShell=null;
let latestProjection=EMPTY_PROJECTION;
let latestRowsByKey=new Map();
let selectedLocationKey=null;
let lastDataRef=UNSET;
let lastVisibleSignature='';
let closeListenerInstalled=false;
let ensureQueued=false;
let wasActive=false;
const reportedMapErrors=new Set();

function setHtml(host,html){
  if(host&&host.innerHTML!==html)host.innerHTML=html;
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

function renderStatus(host,mapState,visibleCount,featureCount){
  if(!host)return;
  const pending=Number(mapState?.points?.meta?.pending_count||0);
  if(!visibleCount){
    setHtml(host,`<div class="discover-map-state">${icon('users')}<span>Keine Treffer für die aktuellen Filter.</span></div>`);
    return;
  }
  if(mapState?.error&&!featureCount){
    setHtml(host,`<div class="discover-map-state">${icon('info')}<span>${e(mapState.error||'Kartendaten konnten nicht geladen werden.')}</span></div>`);
    return;
  }
  if((mapState?.loading||pending>0)&&!featureCount){
    setHtml(host,'<div class="discover-map-state"><span class="spinner"></span><span>Kartendaten werden vorbereitet …</span></div>');
    return;
  }
  if(mapState?.points&&!mapState?.loading&&!mapState?.error&&!pending&&!featureCount){
    setHtml(host,`<div class="discover-map-state">${icon('pin')}<span>Für diese Treffer sind keine Kartendaten verfügbar.</span></div>`);
    return;
  }
  if(mapState?.error&&featureCount){
    setHtml(host,`<div class="discover-map-state discover-map-state-compact">${icon('info')}<span>Aktualisierung fehlgeschlagen · erneuter Versuch läuft.</span></div>`);
    return;
  }
  if(mapState?.stale||mapState?.loading){
    setHtml(host,'<div class="discover-map-state discover-map-state-compact"><span class="spinner"></span><span>Karte wird aktualisiert …</span></div>');
    return;
  }
  if(pending>0){
    setHtml(host,'<div class="discover-map-state discover-map-state-compact"><span class="spinner"></span><span>Karte wird ergänzt …</span></div>');
    return;
  }
  setHtml(host,'');
}

function precisionLabel(value){
  if(value==='POSTAL')return 'PLZ-Gebiet';
  if(value==='ADDRESS')return 'Adresse';
  return 'Stadtgebiet';
}

function renderPreview(host){
  if(!host)return;
  if(!selectedLocationKey){setHtml(host,'');return;}
  const location=latestProjection.byKey.get(selectedLocationKey);
  if(!location){selectedLocationKey=null;setHtml(host,'');return;}
  const matched=(location.listings||[])
    .map(ref=>({ref,row:latestRowsByKey.get(mapRefKey(ref))||null}))
    .filter(item=>item.row);
  if(!matched.length){selectedLocationKey=null;setHtml(host,'');return;}
  const precision=precisionLabel(String(location.precision||''));
  const close='<button type="button" class="discover-map-preview-close" data-map-preview-close aria-label="Vorschau schließen">×</button>';

  if(matched.length===1){
    const {row,ref}=matched[0];
    const pid=Number(row.personId||ref.person_id||0)||'';
    const advert=String(row.advertId||ref.advert_id||'');
    const image=row.thumbnail||'';
    const address=String(ref.address_label||location.location_label||row.city||'');
    setHtml(host,`<article class="discover-map-preview">${close}<button type="button" class="discover-map-preview-main" data-action="open-profile" data-person-id="${a(pid)}" data-advert-id="${a(advert)}">${image?`<img src="${a(image)}" alt="${a(row.name)}">`:`<span class="discover-map-preview-image">${icon('user')}</span>`}<span class="discover-map-preview-copy"><b>${e(row.name)}${row.age?` <small>${e(row.age)}</small>`:''}</b><span>${icon('pin')}${e(address)}</span><small>${e(precision)} · Profil öffnen</small></span></button>${pid?`<button type="button" class="discover-map-preview-like ${row.preference==='LIKE'?'selected':''}" data-action="grid-like" data-person-id="${a(pid)}" data-advert-id="${a(advert)}" aria-label="Like">${icon('heart')}</button>`:''}</article>`);
    return;
  }

  const rows=matched.map(({row,ref})=>{
    const pid=Number(row.personId||ref.person_id||0)||'';
    const advert=String(row.advertId||ref.advert_id||'');
    const address=String(ref.address_label||location.location_label||row.city||'');
    return `<button type="button" class="discover-map-location-row" data-action="open-profile" data-person-id="${a(pid)}" data-advert-id="${a(advert)}">${row.thumbnail?`<img class="discover-map-location-thumb" src="${a(row.thumbnail)}" alt="${a(row.name)}">`:`<span class="discover-map-location-thumb">${icon('user')}</span>`}<span class="discover-map-location-row-copy"><b>${e(row.name)}${row.age?` <small>${e(row.age)}</small>`:''}</b><small>${icon('pin')}${e(address)}</small></span>${icon('chevron-right')}</button>`;
  }).join('');
  setHtml(host,`<section class="discover-map-location-sheet">${close}<header><b>${e(location.location_label||'Standort')}</b><small>${matched.length} Inserate · ${e(precision)}</small></header><div class="discover-map-location-list">${rows}</div></section>`);
}

function clearSelection(){
  if(!selectedLocationKey)return;
  selectedLocationKey=null;
  renderPreview(currentShell?.querySelector('.discover-map-preview-host'));
}

function destroyMap(){
  runtime?.destroy();
  runtime=null;
  runtimeTarget=null;
  currentShell=null;
  latestProjection=EMPTY_PROJECTION;
  latestRowsByKey=new Map();
  selectedLocationKey=null;
  lastDataRef=UNSET;
  lastVisibleSignature='';
  reportedMapErrors.clear();
}

function ensureRuntime(target){
  if(runtime&&runtimeTarget===target)return runtime;
  if(runtime)destroyMap();
  runtimeTarget=target;
  runtime=createDiscoverMapRuntime({
    target,
    onSelectLocation:key=>{
      selectedLocationKey=String(key||'');
      renderPreview(currentShell?.querySelector('.discover-map-preview-host'));
    },
    onClearSelection:clearSelection,
    onRuntimeError:reportMapError,
    onClusterError:(error,clusterId)=>{
      void logClient('warning','map-cluster',error?.message||String(error),{cluster_id:clusterId});
    },
  });
  return runtime;
}

function installCloseListener(){
  if(closeListenerInstalled)return;
  closeListenerInstalled=true;
  document.addEventListener('click',event=>{
    const close=event.target.closest('[data-map-preview-close]');
    if(!close)return;
    event.preventDefault();
    selectedLocationKey=null;
    setHtml(close.closest('.discover-map-preview-host'),'');
  });
}

function queueEnsureMapData(effects){
  if(ensureQueued)return;
  ensureQueued=true;
  queueMicrotask(()=>{
    ensureQueued=false;
    if(!wasActive)return;
    void effects.ensureMapData({quiet:true});
  });
}

export function syncDiscoverMap({host,state,effects}){
  installCloseListener();
  const route=S.currentRoute(state);
  const active=route.name==='discover'&&state.discover.mode==='map';
  if(!active){
    const shouldCancel=wasActive;
    wasActive=false;
    if(shouldCancel)effects.cancelMapRequest();
    if(runtime)destroyMap();
    return;
  }
  wasActive=true;

  const target=host.querySelector('#discoverMap');
  if(!target)return;
  const shell=target.closest('.discover-map-shell');
  currentShell=shell;

  const mapState=state.discover.map||{};
  const visibleRows=S.visibleSearchRows(state);
  const visibleSignature=visibleListingSignature(visibleRows);
  const backendChanged=mapState.points!==lastDataRef;
  const projectionChanged=backendChanged||visibleSignature!==lastVisibleSignature;
  latestRowsByKey=indexMapRows(visibleRows);
  if(projectionChanged){
    latestProjection=buildMapProjection(mapState.points,visibleRows);
    lastDataRef=mapState.points;
    lastVisibleSignature=visibleSignature;
  }

  renderStatus(shell?.querySelector('.discover-map-state-host'),mapState,visibleRows.length,latestProjection.geojson.features.length);
  renderPreview(shell?.querySelector('.discover-map-preview-host'));
  queueEnsureMapData(effects);

  const activeRuntime=ensureRuntime(target);
  currentShell=shell;
  void activeRuntime.setProjection(latestProjection,{fitCamera:backendChanged}).catch(error=>{
    const status=shell?.querySelector('.discover-map-state-host');
    setHtml(status,`<div class="discover-map-state">${icon('info')}<span>${e(error?.message||'Karte konnte nicht geladen werden.')}</span></div>`);
    void logClient('error','map-init',error?.message||String(error));
  });
}
