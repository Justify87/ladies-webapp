import { initialState } from './state.js';

const updateSlice = (state, key, patch) => ({
  ...state,
  [key]: {...state[key], ...patch},
});

const updateMappedSlice = (state, key, mapKey, mapValue) => ({
  ...state,
  [key]: {
    ...state[key],
    [mapKey]: {...state[key][mapKey], ...mapValue},
  },
});

export function reducer(state=initialState, action={}) {
  switch (action.type) {
    case 'NAV_SET':
      return {...state, navigation: action.navigation};

    case 'SEARCH_FILTERS':
      return updateSlice(state, 'search', {filters: action.filters});
    case 'SEARCH_FILTER_QUERY':
      return updateSlice(state, 'search', {filterQuery: String(action.value || '')});
    case 'SEARCH_PREVIEW':
      return updateSlice(state, 'search', {preview: {...state.search.preview, ...action.patch}});
    case 'SEARCH_LOADING':
      return updateSlice(state, 'search', {loading: Boolean(action.value), error: action.error || null});
    case 'SEARCH_RESULTS':
      return {
        ...state,
        search: {...state.search, results: action.results || [], meta: action.meta || null},
        discover: {
          ...state.discover,
          index: 0,
          history: [],
          map: {key:null,points:null,loading:false,error:null},
        },
      };
    case 'SEARCH_RESULT_PATCH': {
      const advertId=String(action.advertId||'');
      const patch=action.patch||{};
      return updateSlice(state, 'search', {
        results: state.search.results.map(row => (
          String(row.advert_id ?? row.external_listing_id ?? row.id ?? '')===advertId
            ? {...row, ...patch}
            : row
        )),
      });
    }
    case 'RECENT_SEARCHES':
      return updateSlice(state, 'search', {recent: action.items || []});

    case 'SAVED_SEARCH_ITEMS':
      return updateSlice(state, 'savedSearches', {items: action.items || []});
    case 'SAVED_SEARCH_RESULTS':
      return updateMappedSlice(state, 'savedSearches', 'resultsById', {
        [String(action.savedId)]: action.items || [],
      });
    case 'SAVED_SEARCH_LOADING':
      return updateMappedSlice(state, 'savedSearches', 'loadingById', {
        [String(action.savedId)]: Boolean(action.value),
      });

    case 'DISCOVER_INDEX':
      return updateSlice(state, 'discover', {index: Math.max(0, action.index)});
    case 'DISCOVER_MODE': {
      const mode=['swipe','grid','map'].includes(action.mode)?action.mode:'swipe';
      return updateSlice(state, 'discover', {mode});
    }
    case 'DISCOVER_MEDIA':
      return updateMappedSlice(state, 'discover', 'mediaIndex', {
        [String(action.advertId)]: Number(action.index || 0),
      });
    case 'DISCOVER_HISTORY_PUSH':
      return updateSlice(state, 'discover', {history: [...state.discover.history, action.entry]});
    case 'DISCOVER_HISTORY_POP':
      return updateSlice(state, 'discover', {history: state.discover.history.slice(0, -1)});
    case 'DISCOVER_MAP_LOADING':
      return updateSlice(state, 'discover', {
        map:{...state.discover.map,key:action.key??state.discover.map.key,loading:Boolean(action.value),error:action.error||null}
      });
    case 'DISCOVER_MAP_POINTS':
      return updateSlice(state, 'discover', {
        map:{key:action.key||null,points:action.points||null,loading:false,error:null}
      });
    case 'DISCOVER_MAP_CLEAR':
      return updateSlice(state, 'discover', {map:{key:null,points:null,loading:false,error:null}});

    case 'PROFILE_BUNDLE':
      return updateMappedSlice(state, 'profiles', 'bundles', {[String(action.advertId)]: action.bundle});
    case 'PROFILE_DETAIL':
      return updateMappedSlice(state, 'profiles', 'details', {[String(action.personId)]: action.detail});
    case 'PROFILE_LOADING':
      return updateMappedSlice(state, 'profiles', 'loading', {[String(action.key)]: Boolean(action.value)});
    case 'PROFILE_TAB':
      return updateMappedSlice(state, 'profileUi', 'tabByPerson', {[String(action.personId)]: action.tab});
    case 'PROFILE_MEDIA':
      return updateMappedSlice(state, 'profileUi', 'mediaByPerson', {[String(action.personId)]: Number(action.index || 0)});
    case 'PROFILE_EXPANDED':
      return updateMappedSlice(state, 'profileUi', 'expandedByPerson', {[String(action.personId)]: Boolean(action.value)});
    case 'PERSON_STATE_SYNC': {
      const pid=Number(action.personId);
      const patch=action.userState || {};
      const searchResults=state.search.results.map(row => {
        if(Number(row.person_id)!==pid)return row;
        const next={...row};
        if('preference' in patch)next.preference=patch.preference;
        if('favorite' in patch)next.favorite=patch.favorite;
        return next;
      });
      const detailKey=String(pid);
      const currentDetail=state.profiles.details[detailKey];
      const details=currentDetail
        ? {...state.profiles.details, [detailKey]: {...currentDetail, user_state: {...(currentDetail.user_state || {}), ...patch}}}
        : state.profiles.details;
      const bundles=Object.fromEntries(Object.entries(state.profiles.bundles).map(([key,bundle]) => [
        key,
        Number(bundle?.identity?.person_id)===pid
          ? {...bundle, identity: {...bundle.identity, ...patch}}
          : bundle,
      ]));
      const listItems=state.lists.items
        .map(row => {
          if(Number(row.person_id)!==pid)return row;
          const next={...row};
          if('preference' in patch)next.preference=patch.preference;
          if('favorite' in patch)next.favorite=patch.favorite;
          return next;
        })
        .filter(row => {
          if(Number(row.person_id)!==pid)return true;
          if(state.lists.tab==='FAVORITE')return Boolean(row.favorite);
          return row.preference===state.lists.tab;
        });
      return {
        ...state,
        search: {...state.search, results: searchResults},
        profiles: {...state.profiles, details, bundles},
        lists: {...state.lists, items: listItems},
      };
    }

    case 'LIST_TAB':
      return updateSlice(state, 'lists', {tab: action.tab, items: [], loading: true});
    case 'LIST_SORT':
      return updateSlice(state, 'lists', {sort: action.value || 'DATE'});
    case 'LIST_ITEMS':
      return updateSlice(state, 'lists', {items: action.items || [], loading: false});
    case 'LIST_LOADING':
      return updateSlice(state, 'lists', {loading: Boolean(action.value)});
    case 'STATUS_CHECK':
      return updateSlice(state, 'lists', {statusCheck: {...state.lists.statusCheck, ...action.patch}});

    case 'DASHBOARD':
      return {...state, dashboard: action.value || {counts:{},usage:{},recent_changes:[]}};
    case 'HISTORY_ITEMS':
      return {...state, history: {kind: action.kind, items: action.items || []}};

    case 'SETTINGS':
      return updateSlice(state, 'settings', {values: action.values || {}, loaded: true});
    case 'STORAGE':
      return updateSlice(state, 'settings', {storage: action.value || {}});
    case 'PRICING_SETTINGS':
      return updateSlice(state, 'settings', {pricing: action.value || {}});
    case 'PRICING_RUNTIME':
      return updateSlice(state, 'settings', {pricingRuntime: action.value || {}});

    case 'MESSAGE_STATUS': {
      const status=action.value || {};
      const patch={status};
      if(String(status.state || '').toUpperCase()!=='CONNECTED')patch.presenceByThread={};
      return updateSlice(state, 'messaging', patch);
    }
    case 'MESSAGE_FILTER':
      return updateSlice(state, 'messaging', {filter: 'ALL', viewFilter: action.value || 'ALL'});
    case 'MESSAGE_VIEW_FILTER':
      return updateSlice(state, 'messaging', {viewFilter: action.value || 'ALL', filter: 'ALL'});
    case 'MESSAGE_QUERY':
      return updateSlice(state, 'messaging', {query: action.value || ''});
    case 'THREADS':
      return updateSlice(state, 'messaging', {threads: action.items || [], loading: false});
    case 'THREADS_LOADING':
      return updateSlice(state, 'messaging', {loading: Boolean(action.value)});
    case 'CURRENT_THREAD':
      return updateSlice(state, 'messaging', {currentThread: action.value, threadLoading: false});
    case 'THREAD_ENDPOINT': {
      const thread=state.messaging.currentThread;
      if(!thread)return state;
      const endpointId=Number(action.endpointId||0);
      const endpoints=Array.isArray(thread.endpoints)?thread.endpoints:[];
      const endpoint=endpoints.find(item=>Number(item.endpoint_id)===endpointId)
        || (Number(thread.endpoint?.endpoint_id)===endpointId?thread.endpoint:null);
      if(!endpoint)return state;
      return updateSlice(state, 'messaging', {currentThread: {...thread, selected_endpoint: endpoint}});
    }
    case 'THREAD_LOADING':
      return updateSlice(state, 'messaging', {threadLoading: Boolean(action.value)});
    case 'REPLY_TO':
      return updateSlice(state, 'messaging', {replyTo: action.value || null});
    case 'EDITING':
      return updateSlice(state, 'messaging', {editing: action.value || null});
    case 'MESSAGE_EVENTS':
      return updateSlice(state, 'messaging', {eventsConnected: Boolean(action.value)});
    case 'MESSAGE_PRESENCE': {
      const key=String(Number(action.threadId || 0));
      if(key==='0')return state;
      const presenceByThread={...state.messaging.presenceByThread};
      if(action.value)presenceByThread[key]=action.value;
      else delete presenceByThread[key];
      return updateSlice(state, 'messaging', {presenceByThread});
    }
    case 'MESSAGE_CONTACT_INFO': {
      const key=String(Number(action.endpointId||0));
      if(key==='0')return state;
      return updateMappedSlice(state, 'messaging', 'contactByEndpoint', {
        [key]: {...(state.messaging.contactByEndpoint[key]||{}), ...(action.value||{})},
      });
    }
    case 'PROFILE_REQUEST_STATE': {
      const key=String(Number(action.personId||0));
      if(key==='0')return state;
      return updateMappedSlice(state, 'messaging', 'profileRequests', {
        [key]: {...(state.messaging.profileRequests?.[key]||{}), ...(action.value||{})},
      });
    }
    case 'ASSIGNMENT':
      return updateSlice(state, 'messaging', {assignment: action.value || null});
    case 'MESSAGE_RECORDING':
      return updateSlice(state, 'messaging', {recording: Boolean(action.value)});

    case 'PRICING_THREAD': {
      const key=String(action.threadId);
      return updateMappedSlice(state, 'pricing', 'byThread', {
        [key]: {...(state.pricing.byThread[key] || {}), ...action.value},
      });
    }
    case 'PRICING_PROPOSAL':
      return updateSlice(state, 'pricing', {proposal: action.value || null});
    case 'PRICING_DRAFT':
      return {
        ...state,
        pricing: {
          ...state.pricing,
          proposal: state.pricing.proposal
            ? {...state.pricing.proposal, proposal: action.value || null}
            : state.pricing.proposal,
        },
      };

    case 'SHEET':
      return updateSlice(state, 'ui', {sheet: action.value || null});
    case 'TOAST':
      return updateSlice(state, 'ui', {toast: action.value ? {message:String(action.value),nonce:Date.now()} : null});
    case 'BUSY':
      return updateSlice(state, 'ui', {busy: Boolean(action.value)});
    case 'VISUAL_DONE':
      return updateMappedSlice(state, 'visual', 'done', {[String(action.mediaId)]: action.value ?? true});

    default:
      return state;
  }
}
