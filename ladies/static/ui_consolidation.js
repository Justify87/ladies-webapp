'use strict';

// V9.2 follow-up: make the existing Search & Filter experience the actual home
// screen instead of maintaining two competing entry points.  This patch stays
// deliberately small and reuses the existing filter/search state and handlers.

const legacyFilterLayer = $('#filterLayer');
if (legacyFilterLayer) {
  legacyFilterLayer.innerHTML = '';
  legacyFilterLayer.classList.add('hidden');
  legacyFilterLayer.setAttribute('aria-hidden', 'true');
}

function renderUnifiedHome() {
  const host = $('#homeScreen .home-sheet');
  if (!host) return;
  const recent = Array.isArray(state.recentSearches) ? state.recentSearches.slice(0, 6) : [];
  host.classList.add('filter-home');
  host.innerHTML = `
    <div class="home-filter-head">
      <div>
        <h1>Suche &amp; Filter</h1>
        <p>Lege hier deine Suche fest und starte danach direkt in Swipe oder Grid.</p>
      </div>
      <button id="homeResetFilters" class="reset-link" type="button">${ico('refresh')}Zurücksetzen</button>
    </div>
    <section class="home-recent-searches" aria-label="Letzte Suchen">
      <div class="home-recent-head"><h2>Letzte Suchen</h2><small>werden automatisch gespeichert</small></div>
      ${recent.length ? `<div id="recentSearches" class="recent-chips">${recent.map((x, i) => `<button type="button" class="recent-chip ${i === 0 ? 'active' : ''}" data-recent-index="${i}">${ico('map-pin')}${esc(x.city || x.city_slug || 'Suche')}${x.radius_km ? ` · ${esc(x.radius_km)} km` : ''}</button>`).join('')}</div>` : '<p class="home-filter-empty">Nach deiner ersten Suche erscheint sie hier.</p>'}
    </section>
    <div id="filterContent" class="filter-content home-filter-content"></div>
    <button id="homeSearchBtn" class="filter-sticky-cta home-filter-cta" type="button">${ico('search')}<span id="filterResultCount">Treffer anzeigen</span></button>`;

  renderFilter();
  $('#homeResetFilters')?.addEventListener('click', resetFilters);
  $('#homeSearchBtn')?.addEventListener('click', async () => {
    state.filters.city = $('#filterCity')?.value || state.filters.city || 'Leipzig';
    await performSearch();
  });
  $('#recentSearches')?.addEventListener('click', event => {
    const button = event.target.closest('[data-recent-index]');
    if (button) applyRecentSearch(Number(button.dataset.recentIndex));
  });
}

function openUnifiedFilter() {
  if (!$('#profileLayer')?.classList.contains('hidden')) closeProfile();
  setScreen('home');
  requestAnimationFrame(() => {
    const screen = $('#homeScreen');
    if (screen) screen.scrollTop = 0;
  });
}

renderHome = renderUnifiedHome;
openFilter = openUnifiedFilter;

// "Meine Daten" is for the user's own assessment and notes.  Price lists are
// shown and edited only in the "Angebot" tab to avoid two competing sources.
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
      // Preserve an already recorded encounter price/duration, but do not edit it
      // from this tab.  Price list maintenance lives in "Angebot".
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

// bindGlobal() ran synchronously inside app.js before this deferred script.
// Replace only the controls that used to open the legacy filter layer.
$('#discoverFilterBtn') && ($('#discoverFilterBtn').onclick = openFilter);
$('#gridFilterBtn') && ($('#gridFilterBtn').onclick = openFilter);

if (state.screen === 'home') renderHome();
