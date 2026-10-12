'use strict';

// Search & Filter is the one and only search surface. The former full-screen
// filter layer remains unreachable; every filter affordance routes to Home.
const legacyFilterLayer = $('#filterLayer');
if (legacyFilterLayer) {
  legacyFilterLayer.innerHTML = '';
  legacyFilterLayer.classList.add('hidden');
  legacyFilterLayer.setAttribute('aria-hidden', 'true');
}

const legacyRenderFilter = renderFilter;
const legacyBindFilterControls = bindFilterControls;
const legacyUpdateFilterCount = updateFilterCount;
const legacyPerformSearch = performSearch;

const COMPLETE_SERVICE_FILTERS = [
  'AV','ZK','GV','GF6','Girlfriend-Sex','Franz.','nur Massage','D**p Thr**t','Franz. bei ihr','69','AV bei ihm',
  'FA aktiv','FA passiv','ZA aktiv','ZA passiv','NS aktiv','NS passiv','KV aktiv','KV passiv','dominant','devot',
  'Damen','Paare','Menschen mit Behinderung','Flotter Dreier (MFF)','Zu dritt (MMF)','Herrenrunde','GS (Gruppen-S*x)',
  'SW (S*ndw*ch)','Body-to-Body M.','erotische Massage','Ganzkörperm.','HE (Handentsp.)','Intim-Massage','Öl-Massage',
  'Pärchen-Massage','Po-Massage','Pr*stata-Massage','Synchron-/Duo-M.','Tantra-Massage','Thai-Massage','Bondage/Fess**ung',
  'Fetisch-Spielchen','Fesselspiele aktiv','Fesselspiele passiv','FS (Facesitting)','Lack, Leder u. Latex','Verbalerotik','WS',
  'Dusch-/Badeservice','Fotos/Videos möglich','Fuß-/Schuherotik','Intimrasur','Strapserotik','gekonnter Striptease',
  'LS/Duo (Lesboshow)','Nylonerotik','Outdoor-Erotik','Rollenspiele','Squ*rting','Begleitservice','Frivoles Ausgehen',
  'Urlaubsbegleitung','Pärchenbesuche','Swingerclub-Begl.','Übernacht. b. Gast','Wochenende','Gastrobegleitung','Messebegleitung'
];
SERVER_FILTERS.service = COMPLETE_SERVICE_FILTERS;

let previewTimer = null;
let previewAbort = null;
let previewSequence = 0;
let previewSignature = '';
let previewCount = null;
let previewStatus = 'idle';
let previewPaused = false;

function serverFilterPayload() {
  return {
    city: state.filters.city || 'Leipzig',
    radius: Number(state.filters.radius || 20),
    category: state.filters.category || '',
    age_min: Number(state.filters.ageMin || 18),
    age_max: Number(state.filters.ageMax || 60),
    height_min: Number(state.filters.heightMin || 140),
    height_max: Number(state.filters.heightMax || 200),
    selected: Object.fromEntries(
      Object.entries(state.filters.selected || {})
        .filter(([, values]) => Array.isArray(values) && values.length)
        .map(([group, values]) => [group, [...values].sort()])
    ),
  };
}

function currentPreviewSignature() {
  return JSON.stringify(serverFilterPayload());
}

function renderPreviewButton() {
  const button = $('#homeSearchBtn');
  const label = $('#filterResultCount');
  if (!button || !label) return;
  button.classList.toggle('zero-results', previewStatus === 'ready' && previewCount === 0);
  button.classList.toggle('preview-loading', previewStatus === 'loading');
  if (previewStatus === 'loading') {
    label.textContent = 'Suche starten · Treffer werden geprüft …';
  } else if (previewStatus === 'ready' && Number.isFinite(previewCount)) {
    label.textContent = `Suche starten · ${previewCount} Treffer`;
  } else if (previewStatus === 'error') {
    label.textContent = 'Suche starten · Trefferzahl nicht verfügbar';
  } else {
    label.textContent = 'Suche starten';
  }
}

function scheduleSearchPreview({immediate = false, force = false} = {}) {
  if (previewPaused || state.screen !== 'home' || !$('#homeSearchBtn')) return;
  clearTimeout(previewTimer);
  const signature = currentPreviewSignature();
  if (!force && signature === previewSignature && previewStatus === 'ready') {
    renderPreviewButton();
    return;
  }
  previewTimer = setTimeout(() => runSearchPreview(signature), immediate ? 0 : 650);
}

async function runSearchPreview(signature = currentPreviewSignature()) {
  if (previewPaused || state.screen !== 'home') return;
  previewAbort?.abort();
  previewAbort = new AbortController();
  const sequence = ++previewSequence;
  previewStatus = 'loading';
  renderPreviewButton();
  try {
    const data = await api('/api/search-preview', {
      method: 'POST',
      body: serverFilterPayload(),
      signal: previewAbort.signal,
    });
    if (sequence !== previewSequence) return;
    previewSignature = signature;
    previewCount = Number(data.count ?? 0);
    previewStatus = 'ready';
    renderPreviewButton();
  } catch (error) {
    if (error?.name === 'AbortError' || sequence !== previewSequence) return;
    previewSignature = signature;
    previewCount = null;
    previewStatus = 'error';
    renderPreviewButton();
  }
}

function ensureCompleteServiceFilters() {
  const cards = $$('.filter-card', $('#filterContent'));
  const card = cards.find(item => item.querySelector('.filter-card-head')?.textContent?.includes('Beliebte Services'));
  const wrap = card?.querySelector('.mini-chips');
  if (!wrap) return;
  card.querySelector('.filter-card-head b').lastChild.textContent = 'Services';
  const existing = new Set($$('[data-filter-group="service"]', wrap).map(button => button.dataset.filterValue));
  COMPLETE_SERVICE_FILTERS.forEach(value => {
    if (existing.has(value)) return;
    const holder = document.createElement('div');
    holder.innerHTML = filterChip('service', value);
    const button = holder.firstElementChild;
    if (!button) return;
    button.onclick = () => {
      state.filters.selected.service ??= [];
      const values = state.filters.selected.service;
      const index = values.indexOf(value);
      if (index >= 0) values.splice(index, 1); else values.push(value);
      renderFilter();
    };
    wrap.appendChild(button);
  });
}

bindFilterControls = function bindUnifiedFilterControls() {
  legacyBindFilterControls();
  ['#filterCity', '#filterRadius', '#ageMinRange', '#ageMaxRange'].forEach(selector => {
    $(selector)?.addEventListener('input', () => scheduleSearchPreview());
  });
};

renderFilter = function renderUnifiedFilter() {
  legacyRenderFilter();
  ensureCompleteServiceFilters();
  renderPreviewButton();
  scheduleSearchPreview();
};

updateFilterCount = function updateUnifiedFilterCount() {
  if (state.screen === 'home' && $('#homeSearchBtn')) {
    renderPreviewButton();
    return;
  }
  legacyUpdateFilterCount();
};

performSearch = async function performUnifiedSearch(options = {}) {
  previewPaused = true;
  clearTimeout(previewTimer);
  previewAbort?.abort();
  try {
    await legacyPerformSearch(options);
    if (state.searchMeta) {
      previewSignature = currentPreviewSignature();
      previewCount = Number(state.searchMeta.server_advert_count ?? state.results.length ?? 0);
      previewStatus = 'ready';
    }
  } finally {
    previewPaused = false;
    if (state.screen === 'home') renderPreviewButton();
  }
};

function renderUnifiedHome() {
  const host = $('#homeScreen .home-sheet');
  if (!host) return;
  const recent = Array.isArray(state.recentSearches) ? state.recentSearches.slice(0, 6) : [];
  host.classList.add('filter-home');
  host.innerHTML = `
    <div class="home-filter-actions">
      <button id="homeResetFilters" class="reset-link" type="button">${ico('refresh')}Zurücksetzen</button>
    </div>
    <section class="home-recent-searches" aria-label="Letzte Suchen">
      <div class="home-recent-head"><h2>Letzte Suchen</h2><small>werden automatisch gespeichert</small></div>
      ${recent.length ? `<div id="recentSearches" class="recent-chips">${recent.map((x, i) => `<button type="button" class="recent-chip ${i === 0 ? 'active' : ''}" data-recent-index="${i}">${ico('map-pin')}${esc(x.city || x.city_slug || 'Suche')}${x.radius_km ? ` · ${esc(x.radius_km)} km` : ''}</button>`).join('')}</div>` : '<p class="home-filter-empty">Nach deiner ersten Suche erscheint sie hier.</p>'}
    </section>
    <div id="filterContent" class="filter-content home-filter-content"></div>
    <button id="homeSearchBtn" class="filter-sticky-cta home-filter-cta" type="button">${ico('search')}<span id="filterResultCount">Suche starten</span></button>`;

  renderFilter();
  $('#homeResetFilters')?.addEventListener('click', () => {
    resetFilters();
    scheduleSearchPreview({immediate: true, force: true});
  });
  $('#homeSearchBtn')?.addEventListener('click', async () => {
    state.filters.city = $('#filterCity')?.value || state.filters.city || 'Leipzig';
    await performSearch();
  });
  $('#recentSearches')?.addEventListener('click', event => {
    const button = event.target.closest('[data-recent-index]');
    if (button) applyRecentSearch(Number(button.dataset.recentIndex));
  });
  scheduleSearchPreview({immediate: true});
}

function openUnifiedFilter() {
  if (!$('#profileLayer')?.classList.contains('hidden')) closeProfile();
  if (legacyFilterLayer) legacyFilterLayer.classList.add('hidden');
  setScreen('home');
  requestAnimationFrame(() => {
    const screen = $('#homeScreen');
    if (screen) screen.scrollTop = 0;
    scheduleSearchPreview();
  });
}

renderHome = renderUnifiedHome;
openFilter = openUnifiedFilter;

// "Meine Daten" is for the user's own assessment and notes. Price lists are
// shown and edited only in "Angebot" to avoid two competing sources.
renderMyData = function renderMyDataWithoutPriceDuplication() {
  const d = state.detail;
  const enc = (d.encounters || [])[0] || null;
  const us = d.user_state || {};
  return `<form id="myDataForm">
    <section class="profile-card data-form-section"><div class="profile-card-head"><h2>${ico('star')}Bewertungen</h2></div><div class="ratings-grid"><div class="rating-box"><small>Gesamtbewertung</small>${ratingButtons('overall_rating', enc?.overall_rating)}</div><div class="rating-box"><small>Freundlichkeit &amp; Umgang</small>${ratingButtons('sympathy_rating', enc?.sympathy_rating)}</div></div></section>
    <section class="profile-card data-form-section"><h3>${ico('image')} Fotos</h3>${choice('photo_status', enc?.photo_status || 'UNKNOWN', [['AS_AD','Wie auf Anzeige'],['BETTER','Besser'],['WORSE','Schlechter']], true)}</section>
    <section class="profile-card data-form-section"><h3>${ico('refresh')} Wiederholungsinteresse</h3>${choice('repeat_interest', us.repeat_interest || 'UNKNOWN', [['YES','Ja'],['MAYBE','Vielleicht'],['NO','Nein']])}</section>
    <section class="profile-card data-form-section"><h3>${ico('list')} Erfahrung &amp; Service</h3><div class="form-two"><div><small>Service wie besprochen</small>${choice('service_accuracy', enc?.service_accuracy || 'UNKNOWN', [['GOOD','Ja'],['PARTLY','Teilweise'],['BAD','Nein']])}</div><div><small>Hygiene</small>${choice('hygiene', enc?.hygiene || 'UNKNOWN', [['GOOD','Sehr gut'],['OK','Okay'],['BAD','Schlecht']])}</div><div><small>Küssen</small>${choice('kissing', enc?.kissing || 'UNKNOWN', [['YES','Ja'],['ON_REQUEST','Auf Wunsch'],['NO','Nein']])}</div><div><small>Verhandlungsbereit</small>${choice('negotiable', enc?.negotiable || 'UNKNOWN', [['YES','Ja'],['PARTLY','Teilweise'],['NO','Nein']], true)}</div></div></section>
    <section class="profile-card data-form-section"><h3>${ico('note')} Privatnotiz</h3><div class="data-input"><textarea id="dataPrivateNote" maxlength="10000">${esc(us.private_note || '')}</textarea><small class="encounter-note">Die Privatnotiz hängt an der Person. Besuchsdaten bleiben als einzelne Treffen erhalten.</small></div></section>
    <input type="hidden" id="currentEncounterId" value="${enc?.encounter_id || ''}"><input type="hidden" id="currentEncounterDate" value="${esc(toLocalInput(enc?.occurred_at))}">
    <div class="save-bar"><button class="save-cta" type="submit">${ico('save')}Speichern</button></div></form>`;
};

saveMyData = async function saveMyDataWithoutPriceDuplication(event) {
  event.preventDefault();
  const personId = state.detail.person.person_id;
  const enc = (state.detail.encounters || [])[0] || null;
  const note = $('#dataPrivateNote')?.value || '';
  const repeat = readChoice('repeat_interest', 'UNKNOWN');
  try {
    const st = await api(`/api/person/${personId}/state`, {method: 'POST', body: {repeat_interest: repeat, private_note: note}});
    state.detail.user_state = st.user_state;
    const encounterPayload = {
      duration_minutes: enc?.duration_minutes ?? null,
      total_price_eur: enc?.total_price_eur ?? null,
      overall_rating: readRating('overall_rating'),
      sympathy_rating: readRating('sympathy_rating'),
      photo_status: readChoice('photo_status'),
      service_accuracy: readChoice('service_accuracy'),
      hygiene: readChoice('hygiene'),
      kissing: readChoice('kissing'),
      negotiable: readChoice('negotiable'),
    };
    const encounterId = Number($('#currentEncounterId')?.value || 0);
    if (encounterId) {
      await api(`/api/person/${personId}/encounters/${encounterId}`, {method: 'PUT', body: encounterPayload});
    } else if (Object.values(encounterPayload).some(value => value && value !== 'UNKNOWN')) {
      await api(`/api/person/${personId}/encounters`, {method: 'POST', body: encounterPayload});
    }
    state.detail = await fetchPersonDetail(personId, state.detailAdvertId);
    renderProfile();
    loadDashboard();
    toast('Meine Daten gespeichert');
  } catch (err) {
    toast(err.message);
  }
};

// bindGlobal() ran synchronously inside app.js before this deferred patch. These
// two controls captured the former openFilter function directly, so replace them.
$('#discoverFilterBtn') && ($('#discoverFilterBtn').onclick = openFilter);
$('#gridFilterBtn') && ($('#gridFilterBtn').onclick = openFilter);

if (state.screen === 'home') renderHome();
