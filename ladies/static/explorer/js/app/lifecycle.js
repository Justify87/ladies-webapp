const currentRoute=state=>state.navigation.stack.at(-1)||{name:'search',params:{}};

function hydrationKey(state,effects) {
  const route=currentRoute(state);
  if(route.name==='swipe') {
    const row=effects.rawSearchRowAt?.(state.discover.index)||null;
    return `swipe:${state.discover.index}:${row?.advertId||row?.advert_id||''}`;
  }
  if(route.name==='favorites')return `favorites:${state.lists.tab}`;
  if(route.name==='messages')return `messages:${state.messaging.filter}`;
  if(route.name==='profile')return `profile:${Number(route.params?.personId||0)}`;
  if(route.name==='chat')return `chat:${Number(route.params?.threadId||0)}:${Number(route.params?.endpointId||0)}`;
  return route.name;
}

export function createRouteLifecycle({store,effects}) {
  let disposed=false,scheduled=false,settledKey=null;
  const activeKeys=new Set();

  const schedule=()=>{
    if(disposed||scheduled)return;
    scheduled=true;
    queueMicrotask(()=>void hydrate());
  };

  const hydrate=async()=>{
    scheduled=false;
    if(disposed)return;
    const snapshot=store.getState(),route=currentRoute(snapshot),key=hydrationKey(snapshot,effects);
    if(key===settledKey||activeKeys.has(key))return;
    activeKeys.add(key);
    try {
      if(route.name==='favorites') {
        await effects.loadList(snapshot.lists.tab);
      } else if(route.name==='messages') {
        await Promise.allSettled([effects.loadMessagingStatus(),effects.loadThreads(snapshot.messaging.filter)]);
      } else if(route.name==='swipe') {
        await effects.prefetchCurrent();
        if(hydrationKey(store.getState(),effects)===key)await effects.autoAnalyzeCurrent();
      } else if(route.name==='settings') {
        await Promise.allSettled([effects.loadSettings(),effects.loadDashboard(),effects.loadPricingSettings()]);
      } else if(route.name==='profile') {
        const personId=Number(route.params?.personId||0);
        if(personId)await effects.loadPerson(personId,{force:false});
      } else if(route.name==='chat') {
        const threadId=Number(route.params?.threadId||0);
        if(threadId){
          await effects.loadThread(threadId);
          const endpointId=Number(route.params?.endpointId||0);
          if(endpointId&&hydrationKey(store.getState(),effects)===key)store.dispatch({type:'THREAD_ENDPOINT',endpointId});
        }
      } else if(route.name==='history') {
        await effects.loadDashboard();
      }
    } finally {
      activeKeys.delete(key);
      if(!disposed&&hydrationKey(store.getState(),effects)===key)settledKey=key;
      schedule();
    }
  };

  const unsubscribe=store.subscribe(schedule);
  schedule();
  return Object.freeze({dispose(){disposed=true;unsubscribe();}});
}
