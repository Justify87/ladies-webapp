import { api } from './api/client.js';
import * as S from './app/selectors.js';

const QUICK_TIME_VALUES = new Set(['jetzt geöffnet','heute geöffnet']);
const MAX_SAVED_SEARCHES = 25;

const clone = value => structuredClone(value);
const toId = value => Number(value || 0) || null;
const savedSearches = state => Array.isArray(state.settings?.values?.saved_searches) ? state.settings.values.saved_searches : [];

export function availabilityMode(filters={}) {
  const values = new Set(filters?.selected?.zeiten || []);
  if (values.has('jetzt geöffnet')) return 'NOW';
  if (values.has('heute geöffnet')) return 'TODAY';
  return 'ALL';
}

export function filtersWithAvailabilityMode(filters, mode='ALL') {
  const next = clone(filters || {});
  next.selected = {...(next.selected || {})};
  const values = (next.selected.zeiten || []).filter(value => !QUICK_TIME_VALUES.has(String(value)));
  if (mode === 'NOW') values.push('jetzt geöffnet');
  if (mode === 'TODAY') values.push('heute geöffnet');
  if (values.length) next.selected.zeiten = values;
  else delete next.selected.zeiten;
  return next;
}

export function baselineFromResults(results, completedAt=new Date().toISOString()) {
  const people = [];
  const listings = [];
  const seenPeople = new Set();
  const seenListings = new Set();
  for (const row of results || []) {
    const personId = toId(row.person_id ?? row.personId);
    const listingId = toId(row.listing_id ?? row.listingId);
    if (personId && !seenPeople.has(personId)) {
      seenPeople.add(personId);
      people.push(personId);
    }
    if (listingId && !seenListings.has(listingId)) {
      seenListings.add(listingId);
      listings.push({listing_id:listingId,person_id:personId});
    }
  }
  return {completed_at:completedAt,people,listings};
}

function uniqueRowsByPerson(results) {
  const map = new Map();
  for (const row of results || []) {
    const personId = toId(row.person_id ?? row.personId);
    if (personId && !map.has(personId)) map.set(personId,row);
  }
  return map;
}

export function computeSavedSearchDelta(savedSearch, results, completedAt=new Date().toISOString()) {
  const current = baselineFromResults(results, completedAt);
  const previous = savedSearch?.baseline;
  if (!previous || !Array.isArray(previous.people)) {
    return {
      initial:true,
      current_people_count:current.people.length,
      current_listing_count:current.listings.length,
      new_people_count:0,
      returned_people_count:0,
      known_people_new_listing_count:0,
      new_listings_count:0,
      disappeared_people_count:0,
      changed_people_count:0,
      new_people_preview:[]
    };
  }

  const previousCompletedAt = Date.parse(previous.completed_at || '') || 0;
  const previousPeople = new Set((previous.people || []).map(toId).filter(Boolean));
  const previousListings = new Set((previous.listings || []).map(item => toId(item?.listing_id ?? item)).filter(Boolean));
  const currentPeople = new Set(current.people);
  const currentListings = new Set(current.listings.map(item => item.listing_id));
  const byPerson = uniqueRowsByPerson(results);
  const newPeople = new Set();
  const returnedPeople = new Set();
  const knownPeopleWithNewListing = new Set();
  const changedPeople = new Set();

  for (const [personId,row] of byPerson.entries()) {
    if (!previousPeople.has(personId)) {
      const firstSeen = Date.parse(row.person_first_seen_at || row.personFirstSeen || '') || 0;
      if (previousCompletedAt && firstSeen && firstSeen < previousCompletedAt) returnedPeople.add(personId);
      else newPeople.add(personId);
    }
    const changedAt = Date.parse(row.recent_change?.created_at || '') || 0;
    if (previousCompletedAt && changedAt > previousCompletedAt) changedPeople.add(personId);
  }
  for (const row of results || []) {
    const personId = toId(row.person_id ?? row.personId);
    const listingId = toId(row.listing_id ?? row.listingId);
    if (personId && previousPeople.has(personId) && listingId && !previousListings.has(listingId)) {
      knownPeopleWithNewListing.add(personId);
    }
  }

  const disappearedPeople = [...previousPeople].filter(personId => !currentPeople.has(personId));
  const newListingCount = [...currentListings].filter(listingId => !previousListings.has(listingId)).length;
  const newPeoplePreview = [...newPeople].slice(0,4).map(personId => {
    const row = byPerson.get(personId) || {};
    return String(row.name || row.display_name || `Person #${personId}`);
  });

  return {
    initial:false,
    current_people_count:current.people.length,
    current_listing_count:current.listings.length,
    new_people_count:newPeople.size,
    returned_people_count:returnedPeople.size,
    known_people_new_listing_count:knownPeopleWithNewListing.size,
    new_listings_count:newListingCount,
    disappeared_people_count:disappearedPeople.length,
    changed_people_count:changedPeople.size,
    new_people_preview:newPeoplePreview
  };
}

export function formatSavedSearchDelta(delta, {compact=false}={}) {
  if (!delta) return 'Noch nicht ausgeführt';
  if (delta.initial) return compact ? `Basis ${delta.current_people_count || 0}` : `Vergleichsbasis gespeichert · ${delta.current_people_count || 0} Personen`;
  const parts = [];
  if (delta.new_people_count) parts.push(`+${delta.new_people_count} neu`);
  if (delta.returned_people_count) parts.push(`${delta.returned_people_count} wieder da`);
  if (delta.known_people_new_listing_count) parts.push(`${delta.known_people_new_listing_count} neue Anzeige${delta.known_people_new_listing_count===1?'':'n'}`);
  if (delta.changed_people_count) parts.push(`${delta.changed_people_count} geändert`);
  if (delta.disappeared_people_count) parts.push(`${delta.disappeared_people_count} weg`);
  if (!parts.length) parts.push('Keine Änderungen');
  return parts.join(' · ');
}

function savedSearchId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `saved-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
}

async function persistSavedSearches(store, items) {
  const limited = items.slice(0,MAX_SAVED_SEARCHES);
  const response = await api.setSetting('saved_searches', limited);
  const current = store.getState().settings.values || {};
  store.dispatch({type:'SETTINGS',values:response?.settings || {...current,saved_searches:limited}});
  return limited;
}

function defaultSavedName(filters) {
  const city = String(filters?.city || 'Suche').trim() || 'Suche';
  const radius = Number(filters?.radius || 20);
  const mode = availabilityMode(filters);
  const suffix = mode === 'NOW' ? ' · Jetzt' : mode === 'TODAY' ? ' · Heute' : '';
  return `${city} · ${radius} km${suffix}`;
}

export function installSearchIntelligence({store,effects,router}) {
  if (document.documentElement.dataset.searchIntelligenceInstalled === '1') return;
  document.documentElement.dataset.searchIntelligenceInstalled = '1';

  async function saveCurrentSearch() {
    const filters = clone(store.getState().search.filters);
    const proposed = defaultSavedName(filters);
    const answer = prompt('Name für die gespeicherte Suche', proposed);
    if (answer === null) return;
    const name = String(answer).trim();
    if (!name) return effects.toast('Bitte einen Namen eingeben');
    const now = new Date().toISOString();
    const item = {
      id:savedSearchId(),
      name,
      filters,
      created_at:now,
      updated_at:now,
      last_run_at:null,
      baseline:null,
      last_delta:null
    };
    await persistSavedSearches(store,[item,...savedSearches(store.getState())]);
    effects.toast('Suche gespeichert');
  }

  async function deleteSavedSearch(id) {
    const items = savedSearches(store.getState());
    const item = items.find(entry => String(entry.id) === String(id));
    if (!item) return;
    if (!confirm(`Gespeicherte Suche „${item.name || 'Suche'}“ löschen?`)) return;
    await persistSavedSearches(store,items.filter(entry => String(entry.id) !== String(id)));
    effects.toast('Gespeicherte Suche gelöscht');
  }

  async function runSavedSearch(id) {
    const items = savedSearches(store.getState());
    const item = items.find(entry => String(entry.id) === String(id));
    if (!item?.filters) return effects.toast('Gespeicherte Suche nicht gefunden');
    store.dispatch({type:'SEARCH_FILTERS',filters:clone(item.filters)});
    effects.schedulePreview({immediate:true});
    await effects.performSearch();
    const after = store.getState();
    if (after.search.error) return;
    const completedAt = new Date().toISOString();
    const comparisonResults = S.visibleSearchRows(after);
    const delta = computeSavedSearchDelta(item, comparisonResults, completedAt);
    const baseline = baselineFromResults(comparisonResults, completedAt);
    const next = items.map(entry => String(entry.id) === String(id) ? {
      ...entry,
      filters:clone(item.filters),
      updated_at:completedAt,
      last_run_at:completedAt,
      baseline,
      last_delta:delta
    } : entry);
    await persistSavedSearches(store,next);
    const latest = store.getState();
    store.dispatch({type:'SEARCH_RESULTS',results:latest.search.results || [],meta:{
      ...(latest.search.meta || {}),
      saved_search_id:item.id,
      saved_search_name:item.name,
      saved_search_delta:delta
    }});
    effects.toast(formatSavedSearchDelta(delta));
  }

  async function setAvailability(mode,{run=false}={}) {
    const beforeRoute = S.currentRoute(store.getState()).name;
    const filters = filtersWithAvailabilityMode(store.getState().search.filters,mode);
    store.dispatch({type:'SEARCH_FILTERS',filters});
    effects.schedulePreview({immediate:true});
    if (!run) return;
    await effects.performSearch();
    if (beforeRoute === 'grid') router.replaceRoot('grid');
  }

  document.addEventListener('click', async event => {
    const el = event.target.closest('[data-search-intelligence-action]');
    if (!el) return;
    event.preventDefault();
    event.stopPropagation();
    try {
      const action = el.dataset.searchIntelligenceAction;
      if (action === 'save-current') await saveCurrentSearch();
      else if (action === 'run-saved') await runSavedSearch(el.dataset.savedId);
      else if (action === 'delete-saved') await deleteSavedSearch(el.dataset.savedId);
      else if (action === 'availability-select') await setAvailability(el.dataset.mode || 'ALL',{run:false});
      else if (action === 'availability-run') await setAvailability(el.dataset.mode || 'ALL',{run:true});
    } catch (error) {
      console.error(error);
      effects.toast(error?.message || String(error));
    }
  });
}
