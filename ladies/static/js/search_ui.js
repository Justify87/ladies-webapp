'use strict';

(() => {
  const App = window.LadiesApp;
  if (!App) throw new Error('LadiesApp runtime fehlt');

  const {
    state, base, api, $, $$, ico, esc, toast, setScreen, closeProfile,
    resetFilters, applyRecentSearch, ratingButtons, choice, readChoice, readRating,
    fetchPersonDetail, renderProfile, loadDashboard,
  } = App;

  const legacyFilterLayer = $('#filterLayer');
  if (legacyFilterLayer) {
    legacyFilterLayer.innerHTML = '';
    legacyFilterLayer.classList.add('hidden');
    legacyFilterLayer.setAttribute('aria-hidden', 'true');
  }

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
          .map(([group, values]) => [group, [...values].sort()]),
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

  function bindUnifiedFilterControls() {
    base.bindFilterControls();
    ['#filterCity', '#filterRadius', '#ageMinRange', '#ageMaxRange', '#heightMinRange', '#heightMaxRange']
      .forEach(selector => $(selector)?.addEventListener('input', () => scheduleSearchPreview()));
  }

  function renderUnifiedFilter() {
    base.renderFilter();
    renderPreviewButton();
    scheduleSearchPreview();
  }

  function updateUnifiedFilterCount() {
    if (state.screen === 'home' && $('#homeSearchBtn')) {
      renderPreviewButton();
      return;
    }
    base.updateFilterCount();
  }

  async function performUnifiedSearch(options = {}) {
    previewPaused = true;
    clearTimeout(previewTimer);
    previewAbort?.abort();
    try {
      await base.performSearch(options);
      if (state.searchMeta) {
        previewSignature = currentPreviewSignature();
        previewCount = Number(state.searchMeta.server_advert_count ?? state.results.length ?? 0);
        previewStatus = 'ready';
      }
    } finally {
      previewPaused = false;
      if (state.screen === 'home') renderPreviewButton();
    }
  }

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

    App.renderFilter();
    $('#homeResetFilters')?.addEventListener('click', () => {
      resetFilters();
      scheduleSearchPreview({immediate: true, force: true});
    });
    $('#homeSearchBtn')?.addEventListener('click', async () => {
      state.filters.city = $('#filterCity')?.value || state.filters.city || 'Leipzig';
      await App.performSearch();
    });
    $('#recentSearches')?.addEventListener('click', event => {
      const button = event.target.closest('[data-recent-index]');
      if (button) applyRecentSearch(Number(button.dataset.recentIndex));
    });
    scheduleSearchPreview({immediate: true});
  }

  function openUnifiedFilter() {
    if (!$('#profileLayer')?.classList.contains('hidden')) closeProfile();
    legacyFilterLayer?.classList.add('hidden');
    setScreen('home');
    requestAnimationFrame(() => {
      const screen = $('#homeScreen');
      if (screen) screen.scrollTop = 0;
      scheduleSearchPreview();
    });
  }

  const toLocalInput = value => {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const pad = number => String(number).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  function renderMyDataWithoutPriceDuplication() {
    const detail = state.detail;
    const encounter = (detail.encounters || [])[0] || null;
    const userState = detail.user_state || {};
    return `<form id="myDataForm">
      <section class="profile-card data-form-section"><div class="profile-card-head"><h2>${ico('star')}Bewertungen</h2></div><div class="ratings-grid"><div class="rating-box"><small>Gesamtbewertung</small>${ratingButtons('overall_rating', encounter?.overall_rating)}</div><div class="rating-box"><small>Freundlichkeit &amp; Umgang</small>${ratingButtons('sympathy_rating', encounter?.sympathy_rating)}</div></div></section>
      <section class="profile-card data-form-section"><h3>${ico('image')} Fotos</h3>${choice('photo_status', encounter?.photo_status || 'UNKNOWN', [['AS_AD','Wie auf Anzeige'],['BETTER','Besser'],['WORSE','Schlechter']], true)}</section>
      <section class="profile-card data-form-section"><h3>${ico('refresh')} Wiederholungsinteresse</h3>${choice('repeat_interest', userState.repeat_interest || 'UNKNOWN', [['YES','Ja'],['MAYBE','Vielleicht'],['NO','Nein']])}</section>
      <section class="profile-card data-form-section"><h3>${ico('list')} Erfahrung &amp; Service</h3><div class="form-two"><div><small>Service wie besprochen</small>${choice('service_accuracy', encounter?.service_accuracy || 'UNKNOWN', [['GOOD','Ja'],['PARTLY','Teilweise'],['BAD','Nein']])}</div><div><small>Hygiene</small>${choice('hygiene', encounter?.hygiene || 'UNKNOWN', [['GOOD','Sehr gut'],['OK','Okay'],['BAD','Schlecht']])}</div><div><small>Küssen</small>${choice('kissing', encounter?.kissing || 'UNKNOWN', [['YES','Ja'],['ON_REQUEST','Auf Wunsch'],['NO','Nein']])}</div><div><small>Verhandlungsbereit</small>${choice('negotiable', encounter?.negotiable || 'UNKNOWN', [['YES','Ja'],['PARTLY','Teilweise'],['NO','Nein']], true)}</div></div></section>
      <section class="profile-card data-form-section"><h3>${ico('note')} Privatnotiz</h3><div class="data-input"><textarea id="dataPrivateNote" maxlength="10000">${esc(userState.private_note || '')}</textarea><small class="encounter-note">Die Privatnotiz hängt an der Person. Besuchsdaten bleiben als einzelne Treffen erhalten.</small></div></section>
      <input type="hidden" id="currentEncounterId" value="${encounter?.encounter_id || ''}"><input type="hidden" id="currentEncounterDate" value="${esc(toLocalInput(encounter?.occurred_at))}">
      <div class="save-bar"><button class="save-cta" type="submit">${ico('save')}Speichern</button></div></form>`;
  }

  async function saveMyDataWithoutPriceDuplication(event) {
    event.preventDefault();
    const personId = state.detail.person.person_id;
    const encounter = (state.detail.encounters || [])[0] || null;
    const note = $('#dataPrivateNote')?.value || '';
    const repeat = readChoice('repeat_interest', 'UNKNOWN');
    try {
      const user = await api(`/api/person/${personId}/state`, {
        method: 'POST',
        body: {repeat_interest: repeat, private_note: note},
      });
      state.detail.user_state = user.user_state;
      const encounterPayload = {
        duration_minutes: encounter?.duration_minutes ?? null,
        total_price_eur: encounter?.total_price_eur ?? null,
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
    } catch (error) {
      toast(error.message);
    }
  }

  App.registerHooks({
    bindFilterControls: bindUnifiedFilterControls,
    renderFilter: renderUnifiedFilter,
    updateFilterCount: updateUnifiedFilterCount,
    performSearch: performUnifiedSearch,
    renderHome: renderUnifiedHome,
    openFilter: openUnifiedFilter,
    renderMyData: renderMyDataWithoutPriceDuplication,
    saveMyData: saveMyDataWithoutPriceDuplication,
  }, 'search_ui');

  // bindGlobal() executed in app.js before deferred feature scripts. Controls
  // that captured the old openFilter callback are rebound to the registered hook.
  $('#discoverFilterBtn') && ($('#discoverFilterBtn').onclick = () => App.openFilter());
  $('#gridFilterBtn') && ($('#gridFilterBtn').onclick = () => App.openFilter());

  if (state.screen === 'home') App.renderHome();
})();
