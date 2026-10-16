const MAPLIBRE_JS='https://cdn.jsdelivr.net/npm/maplibre-gl@6.14.0/dist/maplibre-gl.mjs';
const MAP_STYLE='https://tiles.openfreemap.org/styles/liberty';
const SOURCE_ID='discover-locations';
const CLUSTER_LAYER='discover-clusters';
const CLUSTER_COUNT_LAYER='discover-cluster-count';
const CLUSTER_PRICE_LAYER='discover-cluster-price';
const LOCATION_LAYER='discover-locations';
const LOCATION_COUNT_LAYER='discover-location-count';
const LOCATION_PRICE_LAYER='discover-location-price';
const MAP_LOAD_TIMEOUT_MS=15000;
const EMPTY_GEOJSON=Object.freeze({type:'FeatureCollection',features:[]});

let maplibrePromise=null;

function loadMapLibre(){
  if(!maplibrePromise){
    maplibrePromise=import(MAPLIBRE_JS).catch(error=>{
      maplibrePromise=null;
      throw error;
    });
  }
  return maplibrePromise;
}

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

function priceLabelLayout(textFont,offset){
  return {
    'text-field':priceLabelExpression(),
    'text-size':11,
    'text-font':textFont,
    'text-offset':offset,
    'text-anchor':'top',
    'text-allow-overlap':true,
    'text-ignore-placement':true,
  };
}

function installLayers(instance,{onSelectLocation,onClearSelection,onClusterError,onCameraInteraction}){
  if(instance.getSource(SOURCE_ID))return;
  instance.addSource(SOURCE_ID,{
    type:'geojson',
    data:EMPTY_GEOJSON,
    cluster:true,
    clusterMaxZoom:13,
    clusterRadius:48,
    clusterProperties:{
      listing_total:['+',['get','listing_count']],
      price_60_sum_cents:['+',['get','price_60_sum_cents']],
      price_60_count:['+',['get','price_60_count']],
    },
  });
  instance.addLayer({id:CLUSTER_LAYER,type:'circle',source:SOURCE_ID,filter:['has','point_count'],paint:{'circle-color':'#cc2d7c','circle-radius':['step',['get','listing_total'],18,10,22,30,27,75,32],'circle-stroke-color':'#ffffff','circle-stroke-width':2,'circle-opacity':.92}});
  instance.addLayer({id:LOCATION_LAYER,type:'circle',source:SOURCE_ID,filter:['!', ['has','point_count']],paint:{'circle-color':'#cc2d7c','circle-radius':['case',['>', ['get','listing_count'],1],13,8],'circle-stroke-color':'#ffffff','circle-stroke-width':3}});

  const textFont=styleTextFont(instance);
  if(textFont){
    instance.addLayer({id:CLUSTER_COUNT_LAYER,type:'symbol',source:SOURCE_ID,filter:['has','point_count'],layout:{'text-field':['to-string',['get','listing_total']],'text-size':12,'text-font':textFont},paint:{'text-color':'#ffffff'}});
    instance.addLayer({id:CLUSTER_PRICE_LAYER,type:'symbol',source:SOURCE_ID,filter:['all',['has','point_count'],['>', ['get','price_60_count'],0]],layout:priceLabelLayout(textFont,[0,3]),paint:{'text-color':'#8f1f5a','text-halo-color':'#ffffff','text-halo-width':2}});
    instance.addLayer({id:LOCATION_COUNT_LAYER,type:'symbol',source:SOURCE_ID,filter:['all',['!', ['has','point_count']],['>', ['get','listing_count'],1]],layout:{'text-field':['to-string',['get','listing_count']],'text-size':10,'text-font':textFont},paint:{'text-color':'#ffffff'}});
    instance.addLayer({id:LOCATION_PRICE_LAYER,type:'symbol',source:SOURCE_ID,minzoom:10,filter:['all',['!', ['has','point_count']],['>', ['get','price_60_count'],0]],layout:priceLabelLayout(textFont,[0,1.7]),paint:{'text-color':'#8f1f5a','text-halo-color':'#ffffff','text-halo-width':2}});
  }

  const expandCluster=async event=>{
    const feature=event.features?.[0]||instance.queryRenderedFeatures(event.point,{layers:[CLUSTER_LAYER]})[0];
    const clusterId=Number(feature?.properties?.cluster_id);
    if(!Number.isFinite(clusterId))return;
    try{
      const source=instance.getSource(SOURCE_ID);
      const zoom=await source.getClusterExpansionZoom(clusterId);
      onCameraInteraction?.();
      instance.easeTo({center:feature.geometry.coordinates,zoom,duration:280});
      onClearSelection?.();
    }catch(error){
      onClusterError?.(error,clusterId);
    }
  };
  const selectLocation=event=>{
    const feature=event.features?.[0]||instance.queryRenderedFeatures(event.point,{layers:[LOCATION_LAYER]})[0];
    const key=String(feature?.properties?.location_key||'');
    if(key)onSelectLocation?.(key);
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
}

function fitData(instance,lib,data){
  if(!data?.features?.length||!lib?.LngLatBounds)return;
  if(data.features.length===1){
    instance.easeTo({center:data.features[0].geometry.coordinates,zoom:12.8,duration:280});
    return;
  }
  const bounds=new lib.LngLatBounds();
  data.features.forEach(feature=>bounds.extend(feature.geometry.coordinates));
  instance.fitBounds(bounds,{padding:{top:42,right:42,bottom:42,left:42},maxZoom:13,duration:300});
}

export function createDiscoverMapRuntime({target,onSelectLocation,onClearSelection,onRuntimeError,onClusterError}){
  const lifecycle=createMapLifecycle();
  let current=null;
  let mountPromise=null;

  const mount=()=>{
    if(mountPromise)return mountPromise;
    const attempt=lifecycle.run(target,async scope=>{
      const runtime={map:null,lib:null,resizeObserver:null,lastSignature:null,userOwnsCamera:false};
      current=runtime;
      scope.cleanup(()=>{
        if(current===runtime)current=null;
        runtime.resizeObserver?.disconnect();
        if(runtime.map){try{runtime.map.remove();}catch{}}
      });

      const lib=await loadMapLibre();
      if(!scope.active())return null;
      runtime.lib=lib;
      const instance=new lib.Map({
        container:target,
        style:MAP_STYLE,
        center:[12.3731,51.3397],
        zoom:10.5,
        attributionControl:true,
        fadeDuration:0,
      });
      runtime.map=instance;
      instance.on('error',event=>onRuntimeError?.(event));
      instance.on('dragstart',()=>{runtime.userOwnsCamera=true;});
      for(const eventName of ['zoomstart','rotatestart','pitchstart']){
        instance.on(eventName,event=>{if(event?.originalEvent)runtime.userOwnsCamera=true;});
      }
      instance.addControl(new lib.NavigationControl({showCompass:false}),'bottom-right');
      if(typeof ResizeObserver==='function'){
        runtime.resizeObserver=new ResizeObserver(()=>{
          if(current===runtime&&target.isConnected)instance.resize();
        });
        runtime.resizeObserver.observe(target);
      }
      requestAnimationFrame(()=>{if(current===runtime&&target.isConnected)instance.resize();});

      return await waitForMapReady({
        instance,
        isActive:()=>scope.active()&&current===runtime&&target.isConnected,
        registerCleanup:scope.cleanup,
        onReady:()=>{
          installLayers(instance,{
            onSelectLocation,
            onClearSelection,
            onClusterError,
            onCameraInteraction:()=>{runtime.userOwnsCamera=true;},
          });
          requestAnimationFrame(()=>{if(current===runtime&&target.isConnected)instance.resize();});
          return runtime;
        },
      });
    });
    mountPromise=attempt.catch(error=>{
      mountPromise=null;
      throw error;
    });
    return mountPromise;
  };

  const setProjection=async(projection,{fitCamera=false}={})=>{
    const runtime=await mount();
    if(!runtime||current!==runtime)return false;
    if(runtime.lastSignature===projection.signature)return false;
    const source=runtime.map.getSource(SOURCE_ID);
    if(!source)return false;
    source.setData(projection.geojson);
    runtime.lastSignature=projection.signature;
    if(fitCamera&&!runtime.userOwnsCamera)fitData(runtime.map,runtime.lib,projection.geojson);
    requestAnimationFrame(()=>{if(current===runtime&&target.isConnected)runtime.map.resize();});
    return true;
  };

  const destroy=()=>{
    lifecycle.destroy();
    current=null;
    mountPromise=null;
  };

  return Object.freeze({mount,setProjection,destroy,userOwnsCamera:()=>Boolean(current?.userOwnsCamera)});
}
