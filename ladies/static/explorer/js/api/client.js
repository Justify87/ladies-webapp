const DEFAULT_HEADERS = Object.freeze({'Accept':'application/json'});

export class ApiError extends Error {
  constructor(message, {status=0, payload=null, url=''}={}) {
    super(message); this.name = 'ApiError'; this.status = status; this.payload = payload; this.url = url;
  }
}

async function logClient(level, scope, message, extra={}) {
  try {
    await fetch('/api/client-log', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({level,scope,message:String(message || ''),extra})});
  } catch {}
}

export async function request(url, options={}) {
  const opts = {...options, headers:{...DEFAULT_HEADERS, ...(options.headers || {})}};
  delete opts.expectedStatuses;
  if (options.body && !(options.body instanceof FormData) && typeof options.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch(url, opts); }
  catch (error) {
    if(error?.name!=='AbortError')await logClient('error','api-network',error?.message || String(error),{url});
    throw error;
  }
  let payload = null;
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) { try { payload = await response.json(); } catch {} }
  if (!response.ok) {
    const message = payload?.error || payload?.message || `HTTP ${response.status}`;
    const expected=Array.isArray(options.expectedStatuses)&&options.expectedStatuses.includes(response.status);
    if(!expected)await logClient('error','api',message,{url,status:response.status});
    throw new ApiError(message,{status:response.status,payload,url});
  }
  return payload ?? response;
}

export const api = Object.freeze({
  config: () => request('/api/config'),
  dashboard: () => request('/api/dashboard'),
  historyRelistings: (limit=100) => request(`/api/history/relistings?limit=${encodeURIComponent(limit)}`),
  historyChanges: (limit=100) => request(`/api/history/changes?limit=${encodeURIComponent(limit)}`),
  savedSearches: () => request('/api/saved-searches'),
  createSavedSearch: payload => request('/api/saved-searches',{method:'POST',body:payload}),
  updateSavedSearch: (id,payload) => request(`/api/saved-searches/${encodeURIComponent(id)}`,{method:'PUT',body:payload}),
  deleteSavedSearch: id => request(`/api/saved-searches/${encodeURIComponent(id)}`,{method:'DELETE'}),
  runSavedSearch: id => request(`/api/saved-searches/${encodeURIComponent(id)}/run`,{method:'POST'}),
  savedSearchResults: id => request(`/api/saved-searches/${encodeURIComponent(id)}/results`),
  openSavedSearchResult: (id,resultKey) => request(`/api/saved-searches/${encodeURIComponent(id)}/results/${encodeURIComponent(resultKey)}/open`,{method:'POST'}),
  acknowledgeSavedSearches: () => request('/api/saved-searches/acknowledge',{method:'POST'}),
  storage: () => request('/api/storage'),
  settings: () => request('/api/settings'),
  setSetting: (key,value) => request('/api/settings',{method:'POST',body:{key,value}}),
  searchPreview: (payload, signal) => request('/api/search-preview',{method:'POST',body:payload,signal}),
  search: payload => request('/api/search',{method:'POST',body:payload}),
  mapPoints: payload => request('/api/map-points',{method:'POST',body:payload}),
  profile: advertId => request(`/api/profile/${encodeURIComponent(advertId)}`),
  profileClassification: advertId => request(`/api/profile-classification/${encodeURIComponent(advertId)}`),
  listing: advertId => request(`/api/listing/${encodeURIComponent(advertId)}`),
  person: personId => request(`/api/person/${encodeURIComponent(personId)}`),
  personState: (personId, patch) => request(`/api/person/${personId}/state`,{method:'POST',body:patch}),
  addPriceList: (personId, payload) => request(`/api/person/${personId}/prices`,{method:'POST',body:payload}),
  addEncounter: (personId, payload) => request(`/api/person/${personId}/encounters`,{method:'POST',body:payload}),
  updateEncounter: (personId, encounterId, payload) => request(`/api/person/${personId}/encounters/${encounterId}`,{method:'PUT',body:payload}),
  list: tab => request(`/api/lists/${encodeURIComponent(tab)}`),
  statusTargets: () => request('/api/status-targets'),
  profileStatus: advertId => request(`/api/profile-status/${encodeURIComponent(advertId)}`,{method:'POST'}),
  merge: (sourcePersonId,targetPersonId) => request('/api/identity/merge',{method:'POST',body:{source_person_id:sourcePersonId,target_person_id:targetPersonId}}),
  split: listingId => request(`/api/listing-id/${listingId}/split`,{method:'POST'}),
  snapshot: id => request(`/api/snapshot/${id}`),
  archiveMedia: (kind='',limit=100) => request(`/api/archive/media?kind=${encodeURIComponent(kind)}&limit=${encodeURIComponent(limit)}`),
  clearVisual: () => request('/api/visual-analysis',{method:'DELETE'}),
  saveVisual: (mediaId,payload) => request(`/api/media/${mediaId}/visual-analysis`,{method:'POST',body:payload}),

  messagingStatus: () => request('/api/messaging/status'),
  messagingConnect: () => request('/api/messaging/connect',{method:'POST'}),
  messagingDisconnect: () => request('/api/messaging/disconnect',{method:'POST'}),
  messagingReset: () => request('/api/messaging/connection',{method:'DELETE'}),
  threads: () => request('/api/messaging/threads?filter=ALL'),
  messagingInbox: (filter='ALL') => request(`/api/messaging/inbox?filter=${encodeURIComponent(filter)}`),
  messagingReview: () => request('/api/messaging/review'),
  messagingClassification: () => request('/api/messaging/classification'),
  classifyThread: (threadId,payload) => request(`/api/messaging/threads/${threadId}/classification`,{method:'POST',body:payload}),
  thread: (threadId,{limit=100,before=null}={}) => request(`/api/messaging/threads/${threadId}?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ''}`),
  ensurePersonThread: (personId,listingId=null) => request(`/api/messaging/person/${personId}/thread`,{method:'POST',body:{listing_id:listingId}}),
  personMessaging: (personId,listingId=null) => request(`/api/messaging/person/${personId}${listingId ? `?listing_id=${listingId}` : ''}`),
  endpointProbe: endpointId => request(`/api/messaging/endpoints/${endpointId}/probe`),
  sendText: (threadId,payload) => request(`/api/messaging/threads/${threadId}/messages`,{method:'POST',body:payload}),
  sendMedia: (threadId,formData) => request(`/api/messaging/threads/${threadId}/media`,{method:'POST',body:formData}),
  retryMessage: id => request(`/api/messaging/messages/${id}/retry`,{method:'POST'}),
  reactMessage: (id,emoji) => request(`/api/messaging/messages/${id}/reaction`,{method:'POST',body:{emoji}}),
  editMessage: (id,text) => request(`/api/messaging/messages/${id}`,{method:'PATCH',body:{text}}),
  deleteMessage: id => request(`/api/messaging/messages/${id}`,{method:'DELETE'}),
  presence: (threadId,state,endpointId=null) => request(`/api/messaging/threads/${threadId}/presence`,{method:'POST',body:{state,endpoint_id:endpointId}}),
  markRead: (threadId,messageIds=null) => request(`/api/messaging/threads/${threadId}/read`,{method:'POST',body:{message_ids:messageIds}}),
  unassigned: () => request('/api/messaging/unassigned'),
  assignmentCandidates: endpointId => request(`/api/messaging/endpoints/${endpointId}/candidates`),
  assignEndpoint: (endpointId,payload) => request(`/api/messaging/endpoints/${endpointId}/assign`,{method:'POST',body:payload}),
  messagingSettings: payload => request('/api/messaging/settings',{method:'POST',body:payload}),

  pricingStatus: () => request('/api/pricing/runtime/status'),
  pricingSettings: () => request('/api/pricing/settings'),
  setPricingSettings: payload => request('/api/pricing/settings',{method:'POST',body:payload}),
  pricingSignal: threadId => request(`/api/pricing/threads/${threadId}/signal`),
  pricingTimeline: (threadId,limit=100) => request(`/api/pricing/threads/${threadId}/timeline?limit=${limit}`),
  createPriceProposal: (threadId,payload) => request(`/api/pricing/threads/${threadId}/proposals`,{method:'POST',body:payload}),
  priceProposal: id => request(`/api/pricing/proposals/${id}`),
  acceptPriceProposal: (id,payload) => request(`/api/pricing/proposals/${id}/accept`,{method:'POST',body:payload}),
  revisePriceProposal: id => request(`/api/pricing/proposals/${id}/revise`,{method:'POST'}),
  rejectPriceProposal: id => request(`/api/pricing/proposals/${id}/reject`,{method:'POST'}),
  currentPricing: personId => request(`/api/pricing/person/${personId}/current`),
  pricingHistory: (personId,limit=30) => request(`/api/pricing/person/${personId}/history?limit=${limit}`)
});

export { logClient };
