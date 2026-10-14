import { initialState } from './state.js';

const shallowEqual = (a,b) => {
  if (a === b) return true;
  if (!a || !b) return false;
  const ak=Object.keys(a), bk=Object.keys(b);
  return ak.length===bk.length && ak.every(key=>a[key]===b[key]);
};

const activeChatThreadId = state => {
  const route=state.navigation.stack.at(-1);
  return route?.name==='chat' ? Number(route.params?.threadId||0) : 0;
};

export function reducer(state=initialState, action={}) {
  switch (action.type) {
    case 'NAV_SET':
      if(state.navigation===action.navigation)return state;
      return {...state,navigation:action.navigation};
    case 'SEARCH_FILTERS':
      if(state.search.filters===action.filters)return state;
      return {...state,search:{...state.search,filters:action.filters}};
    case 'SEARCH_FILTER_QUERY': {
      const value=String(action.value||'');
      if(state.search.filterQuery===value)return state;
      return {...state,search:{...state.search,filterQuery:value}};
    }
    case 'SEARCH_PREVIEW': {
      const preview={...state.search.preview,...action.patch};
      if(shallowEqual(preview,state.search.preview))return state;
      return {...state,search:{...state.search,preview}};
    }
    case 'SEARCH_LOADING': {
      const loading=Boolean(action.value), error=action.error||null;
      if(state.search.loading===loading&&state.search.error===error)return state;
      return {...state,search:{...state.search,loading,error}};
    }
    case 'SEARCH_RESULTS':
      return {...state,search:{...state.search,results:action.results||[],meta:action.meta||null},discover:{...state.discover,index:0,history:[]}};
    case 'RECENT_SEARCHES':
      return {...state,search:{...state.search,recent:action.items||[]}};
    case 'DISCOVER_INDEX': {
      const index=Math.max(0,Number(action.index)||0);
      if(state.discover.index===index)return state;
      return {...state,discover:{...state.discover,index}};
    }
    case 'DISCOVER_MODE':
      if(state.discover.mode===action.mode)return state;
      return {...state,discover:{...state.discover,mode:action.mode}};
    case 'DISCOVER_MEDIA': {
      const key=String(action.advertId), index=Number(action.index||0);
      if(state.discover.mediaIndex[key]===index)return state;
      return {...state,discover:{...state.discover,mediaIndex:{...state.discover.mediaIndex,[key]:index}}};
    }
    case 'DISCOVER_HISTORY_PUSH':
      return {...state,discover:{...state.discover,history:[...state.discover.history,action.entry]}};
    case 'DISCOVER_HISTORY_POP':
      if(!state.discover.history.length)return state;
      return {...state,discover:{...state.discover,history:state.discover.history.slice(0,-1)}};
    case 'PROFILE_BUNDLE': {
      const key=String(action.advertId);
      if(state.profiles.bundles[key]===action.bundle)return state;
      return {...state,profiles:{...state.profiles,bundles:{...state.profiles.bundles,[key]:action.bundle}}};
    }
    case 'PROFILE_DETAIL': {
      const key=String(action.personId);
      if(state.profiles.details[key]===action.detail)return state;
      return {...state,profiles:{...state.profiles,details:{...state.profiles.details,[key]:action.detail}}};
    }
    case 'PROFILE_LOADING': {
      const key=String(action.key), value=Boolean(action.value), current=Boolean(state.profiles.loading[key]);
      if(current===value)return state;
      const loading={...state.profiles.loading};
      if(value)loading[key]=true;else delete loading[key];
      return {...state,profiles:{...state.profiles,loading}};
    }
    case 'PROFILE_TAB': {
      const key=String(action.personId), value=action.tab;
      if(state.profileUi.tabByPerson[key]===value)return state;
      return {...state,profileUi:{...state.profileUi,tabByPerson:{...state.profileUi.tabByPerson,[key]:value}}};
    }
    case 'PROFILE_MEDIA': {
      const key=String(action.personId), value=Number(action.index||0);
      if(Number(state.profileUi.mediaByPerson[key]||0)===value)return state;
      return {...state,profileUi:{...state.profileUi,mediaByPerson:{...state.profileUi.mediaByPerson,[key]:value}}};
    }
    case 'PROFILE_EXPANDED': {
      const key=String(action.personId), value=Boolean(action.value);
      if(Boolean(state.profileUi.expandedByPerson[key])===value)return state;
      return {...state,profileUi:{...state.profileUi,expandedByPerson:{...state.profileUi.expandedByPerson,[key]:value}}};
    }
    case 'PERSON_STATE_SYNC': {
      const pid=Number(action.personId), patch=action.userState||{};
      const updateRow=row=>{
        if(Number(row.person_id)!==pid)return row;
        let next=row;
        if('preference' in patch&&row.preference!==patch.preference)next={...next,preference:patch.preference};
        if('favorite' in patch&&Boolean(row.favorite)!==Boolean(patch.favorite))next={...next,favorite:Boolean(patch.favorite)};
        return next;
      };
      const results=state.search.results.map(updateRow);
      const detailKey=String(pid), detail=state.profiles.details[detailKey];
      const details=detail?{...state.profiles.details,[detailKey]:{...detail,user_state:{...(detail.user_state||{}),...patch}}}:state.profiles.details;
      let bundles=state.profiles.bundles;
      for(const [key,bundle] of Object.entries(state.profiles.bundles)){
        if(Number(bundle?.identity?.person_id)!==pid)continue;
        if(bundles===state.profiles.bundles)bundles={...bundles};
        bundles[key]={...bundle,identity:{...(bundle.identity||{}),...patch}};
      }
      const updatedList=state.lists.items.map(updateRow).filter(row=>{
        if(Number(row.person_id)!==pid)return true;
        if(state.lists.tab==='FAVORITE')return Boolean(row.favorite);
        return row.preference===state.lists.tab;
      });
      return {...state,search:results.some((row,i)=>row!==state.search.results[i])?{...state.search,results}:state.search,profiles:(details!==state.profiles.details||bundles!==state.profiles.bundles)?{...state.profiles,details,bundles}:state.profiles,lists:updatedList.some((row,i)=>row!==state.lists.items[i])||updatedList.length!==state.lists.items.length?{...state.lists,items:updatedList}:state.lists};
    }
    case 'LIST_TAB':
      if(state.lists.tab===action.tab)return state;
      return {...state,lists:{...state.lists,tab:action.tab}};
    case 'LIST_ITEMS':
      if(action.tab&&action.tab!==state.lists.tab)return state;
      return {...state,lists:{...state.lists,items:action.items||[],loading:false}};
    case 'LIST_LOADING': {
      const value=Boolean(action.value);
      if(state.lists.loading===value)return state;
      return {...state,lists:{...state.lists,loading:value}};
    }
    case 'STATUS_CHECK': {
      const statusCheck={...state.lists.statusCheck,...action.patch};
      if(shallowEqual(statusCheck,state.lists.statusCheck))return state;
      return {...state,lists:{...state.lists,statusCheck}};
    }
    case 'DASHBOARD':
      if(state.dashboard===action.value)return state;
      return {...state,dashboard:action.value||{counts:{},usage:{},recent_changes:[]}};
    case 'HISTORY_ITEMS':
      return {...state,history:{kind:action.kind,items:action.items||[]}};
    case 'SETTINGS': {
      const values=action.values||{};
      if(state.settings.values===values&&state.settings.loaded)return state;
      return {...state,settings:{...state.settings,values,loaded:true}};
    }
    case 'STORAGE':
      if(state.settings.storage===action.value)return state;
      return {...state,settings:{...state.settings,storage:action.value||{}}};
    case 'PRICING_SETTINGS':
      if(state.settings.pricing===action.value)return state;
      return {...state,settings:{...state.settings,pricing:action.value||{}}};
    case 'PRICING_RUNTIME':
      if(state.settings.pricingRuntime===action.value)return state;
      return {...state,settings:{...state.settings,pricingRuntime:action.value||{}}};
    case 'MESSAGE_STATUS': {
      const status=action.value||{};
      const connected=String(status.state||'').toUpperCase()==='CONNECTED';
      const presenceByThread=connected||!Object.keys(state.messaging.presenceByThread).length?state.messaging.presenceByThread:{};
      if(state.messaging.status===status&&presenceByThread===state.messaging.presenceByThread)return state;
      return {...state,messaging:{...state.messaging,status,presenceByThread}};
    }
    case 'MESSAGE_FILTER': {
      const value=action.value||'ALL';
      if(state.messaging.filter===value)return state;
      return {...state,messaging:{...state.messaging,filter:value}};
    }
    case 'MESSAGE_QUERY': {
      const value=action.value||'';
      if(state.messaging.query===value)return state;
      return {...state,messaging:{...state.messaging,query:value}};
    }
    case 'THREADS':
      if(action.filter&&action.filter!==state.messaging.filter)return state;
      return {...state,messaging:{...state.messaging,threads:action.items||[],loading:false}};
    case 'THREADS_LOADING': {
      const value=Boolean(action.value);
      if(state.messaging.loading===value)return state;
      return {...state,messaging:{...state.messaging,loading:value}};
    }
    case 'CURRENT_THREAD': {
      const threadId=Number(action.threadId||action.value?.thread_id||0);
      const active=activeChatThreadId(state);
      if(active&&threadId&&active!==threadId)return state;
      return {...state,messaging:{...state.messaging,currentThread:action.value,threadLoading:false}};
    }
    case 'THREAD_ENDPOINT': {
      const thread=state.messaging.currentThread;
      if(!thread)return state;
      const endpointId=Number(action.endpointId||0);
      const endpoints=Array.isArray(thread.endpoints)?thread.endpoints:[];
      const endpoint=endpoints.find(item=>Number(item.endpoint_id)===endpointId)||(Number(thread.endpoint?.endpoint_id)===endpointId?thread.endpoint:null);
      if(!endpoint||Number(thread.selected_endpoint?.endpoint_id||0)===endpointId)return state;
      return {...state,messaging:{...state.messaging,currentThread:{...thread,selected_endpoint:endpoint}}};
    }
    case 'THREAD_LOADING': {
      const threadId=Number(action.threadId||0), active=activeChatThreadId(state);
      if(active&&threadId&&active!==threadId)return state;
      const value=Boolean(action.value);
      if(state.messaging.threadLoading===value)return state;
      return {...state,messaging:{...state.messaging,threadLoading:value}};
    }
    case 'REPLY_TO': {
      const value=action.value||null;
      if(state.messaging.replyTo===value)return state;
      return {...state,messaging:{...state.messaging,replyTo:value}};
    }
    case 'EDITING': {
      const value=action.value||null;
      if(state.messaging.editing===value)return state;
      return {...state,messaging:{...state.messaging,editing:value}};
    }
    case 'MESSAGE_EVENTS': {
      const value=Boolean(action.value);
      if(state.messaging.eventsConnected===value)return state;
      return {...state,messaging:{...state.messaging,eventsConnected:value}};
    }
    case 'MESSAGE_PRESENCE': {
      const key=String(Number(action.threadId||0));
      if(key==='0')return state;
      const current=state.messaging.presenceByThread[key];
      if(action.value===null||action.value===undefined){
        if(current===undefined)return state;
        const presenceByThread={...state.messaging.presenceByThread};delete presenceByThread[key];
        return {...state,messaging:{...state.messaging,presenceByThread}};
      }
      if(current===action.value)return state;
      return {...state,messaging:{...state.messaging,presenceByThread:{...state.messaging.presenceByThread,[key]:action.value}}};
    }
    case 'ASSIGNMENT': {
      const value=action.value||null;
      if(state.messaging.assignment===value)return state;
      return {...state,messaging:{...state.messaging,assignment:value}};
    }
    case 'MESSAGE_RECORDING': {
      const value=Boolean(action.value);
      if(state.messaging.recording===value)return state;
      return {...state,messaging:{...state.messaging,recording:value}};
    }
    case 'PRICING_THREAD': {
      const key=String(action.threadId);
      const value={...(state.pricing.byThread[key]||{}),...action.value};
      return {...state,pricing:{...state.pricing,byThread:{...state.pricing.byThread,[key]:value}}};
    }
    case 'PRICING_PROPOSAL': {
      const value=action.value||null;
      if(state.pricing.proposal===value)return state;
      return {...state,pricing:{...state.pricing,proposal:value}};
    }
    case 'SHEET': {
      const value=action.value||null;
      if(state.ui.sheet===value)return state;
      return {...state,ui:{...state.ui,sheet:value}};
    }
    case 'TOAST': {
      if(!action.value){
        if(!state.ui.toast)return state;
        return {...state,ui:{...state.ui,toast:null}};
      }
      return {...state,ui:{...state.ui,toast:{message:String(action.value),nonce:Date.now()}}};
    }
    case 'BUSY': {
      const value=Boolean(action.value);
      if(state.ui.busy===value)return state;
      return {...state,ui:{...state.ui,busy:value}};
    }
    case 'VISUAL_DONE': {
      const key=String(action.mediaId), value=action.value??true;
      if(state.visual.done[key]===value)return state;
      return {...state,visual:{...state.visual,done:{...state.visual.done,[key]:value}}};
    }
    default:
      return state;
  }
}
