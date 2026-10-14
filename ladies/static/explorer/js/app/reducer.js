import { initialState } from './state.js';
const copy = value => structuredClone(value);

export function reducer(state=initialState, action={}) {
  const next = copy(state);
  switch (action.type) {
    case 'NAV_SET': next.navigation = action.navigation; return next;
    case 'SEARCH_FILTERS': next.search.filters = action.filters; return next;
    case 'SEARCH_FILTER_QUERY': next.search.filterQuery = String(action.value || ''); return next;
    case 'SEARCH_PREVIEW': next.search.preview = {...next.search.preview,...action.patch}; return next;
    case 'SEARCH_LOADING': next.search.loading = Boolean(action.value); next.search.error = action.error || null; return next;
    case 'SEARCH_RESULTS': next.search.results = action.results || []; next.search.meta = action.meta || null; next.discover.index = 0; next.discover.history = []; return next;
    case 'RECENT_SEARCHES': next.search.recent = action.items || []; return next;
    case 'DISCOVER_INDEX': next.discover.index = Math.max(0, action.index); return next;
    case 'DISCOVER_MODE': next.discover.mode = action.mode; return next;
    case 'DISCOVER_MEDIA': next.discover.mediaIndex[String(action.advertId)] = Number(action.index || 0); return next;
    case 'DISCOVER_HISTORY_PUSH': next.discover.history.push(action.entry); return next;
    case 'DISCOVER_HISTORY_POP': next.discover.history.pop(); return next;
    case 'PROFILE_BUNDLE': next.profiles.bundles[String(action.advertId)] = action.bundle; return next;
    case 'PROFILE_DETAIL': next.profiles.details[String(action.personId)] = action.detail; return next;
    case 'PROFILE_LOADING': next.profiles.loading[String(action.key)] = Boolean(action.value); return next;
    case 'PROFILE_TAB': next.profileUi.tabByPerson[String(action.personId)] = action.tab; return next;
    case 'PROFILE_MEDIA': next.profileUi.mediaByPerson[String(action.personId)] = Number(action.index || 0); return next;
    case 'PROFILE_EXPANDED': next.profileUi.expandedByPerson[String(action.personId)] = Boolean(action.value); return next;
    case 'PERSON_STATE_SYNC': {
      const pid = Number(action.personId); const patch = action.userState || {};
      for (const row of next.search.results) if (Number(row.person_id) === pid) { if ('preference' in patch) row.preference = patch.preference; if ('favorite' in patch) row.favorite = patch.favorite; }
      const detail = next.profiles.details[String(pid)]; if (detail) detail.user_state = {...(detail.user_state || {}),...patch};
      for (const bundle of Object.values(next.profiles.bundles)) if (Number(bundle?.identity?.person_id) === pid) bundle.identity = {...bundle.identity,...patch};
      for (const row of next.lists.items) if (Number(row.person_id) === pid) { if ('preference' in patch) row.preference = patch.preference; if ('favorite' in patch) row.favorite = patch.favorite; }
      next.lists.items = next.lists.items.filter(row => {
        if (Number(row.person_id) !== pid) return true;
        if (next.lists.tab === 'FAVORITE') return Boolean(row.favorite);
        return row.preference === next.lists.tab;
      });
      return next;
    }
    case 'LIST_TAB': next.lists.tab = action.tab; return next;
    case 'LIST_SORT': next.lists.sort = action.value || 'DATE'; return next;
    case 'LIST_ITEMS': next.lists.items = action.items || []; next.lists.loading = false; return next;
    case 'LIST_LOADING': next.lists.loading = Boolean(action.value); return next;
    case 'STATUS_CHECK': next.lists.statusCheck = {...next.lists.statusCheck,...action.patch}; return next;
    case 'DASHBOARD': next.dashboard = action.value || {counts:{},usage:{},recent_changes:[]}; return next;
    case 'HISTORY_ITEMS': next.history = {kind:action.kind,items:action.items || []}; return next;
    case 'SETTINGS': next.settings.values = action.values || {}; next.settings.loaded = true; return next;
    case 'STORAGE': next.settings.storage = action.value || {}; return next;
    case 'PRICING_SETTINGS': next.settings.pricing = action.value || {}; return next;
    case 'PRICING_RUNTIME': next.settings.pricingRuntime = action.value || {}; return next;
    case 'MESSAGE_STATUS': {
      next.messaging.status = action.value || {};
      if (String(next.messaging.status.state || '').toUpperCase() !== 'CONNECTED') next.messaging.presenceByThread = {};
      return next;
    }
    case 'MESSAGE_FILTER': next.messaging.filter = action.value || 'ALL'; next.messaging.viewFilter = next.messaging.filter; return next;
    case 'MESSAGE_VIEW_FILTER': next.messaging.viewFilter = action.value || 'ALL'; next.messaging.filter = 'ALL'; return next;
    case 'MESSAGE_QUERY': next.messaging.query = action.value || ''; return next;
    case 'THREADS': next.messaging.threads = action.items || []; next.messaging.loading = false; return next;
    case 'THREADS_LOADING': next.messaging.loading = Boolean(action.value); return next;
    case 'CURRENT_THREAD': next.messaging.currentThread = action.value; next.messaging.threadLoading = false; return next;
    case 'THREAD_ENDPOINT': {
      const thread=next.messaging.currentThread;
      if(!thread)return state;
      const endpointId=Number(action.endpointId||0);
      const endpoints=Array.isArray(thread.endpoints)?thread.endpoints:[];
      const endpoint=endpoints.find(item=>Number(item.endpoint_id)===endpointId) || (Number(thread.endpoint?.endpoint_id)===endpointId?thread.endpoint:null);
      if(!endpoint)return state;
      thread.selected_endpoint=endpoint;
      return next;
    }
    case 'THREAD_LOADING': next.messaging.threadLoading = Boolean(action.value); return next;
    case 'REPLY_TO': next.messaging.replyTo = action.value || null; return next;
    case 'EDITING': next.messaging.editing = action.value || null; return next;
    case 'MESSAGE_EVENTS': next.messaging.eventsConnected = Boolean(action.value); return next;
    case 'MESSAGE_PRESENCE': {
      const key = String(Number(action.threadId || 0)); if (key === '0') return state;
      if (action.value) next.messaging.presenceByThread[key] = action.value; else delete next.messaging.presenceByThread[key];
      return next;
    }
    case 'ASSIGNMENT': next.messaging.assignment = action.value || null; return next;
    case 'MESSAGE_RECORDING': next.messaging.recording = Boolean(action.value); return next;
    case 'PRICING_THREAD': next.pricing.byThread[String(action.threadId)] = {...(next.pricing.byThread[String(action.threadId)] || {}),...action.value}; return next;
    case 'PRICING_PROPOSAL': next.pricing.proposal = action.value || null; return next;
    case 'SHEET': next.ui.sheet = action.value || null; return next;
    case 'TOAST': next.ui.toast = action.value ? {message:String(action.value),nonce:Date.now()} : null; return next;
    case 'BUSY': next.ui.busy = Boolean(action.value); return next;
    case 'VISUAL_DONE': next.visual.done[String(action.mediaId)] = action.value ?? true; return next;
    default: return state;
  }
}
