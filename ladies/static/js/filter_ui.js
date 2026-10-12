'use strict';

(() => {
  const App = window.LadiesApp;
  if (!App) throw new Error('LadiesApp runtime fehlt');

  const {state, config, esc, ico, $, $$} = App;
  const GROUP_LABELS = {
    figur:'Figur', typ:'Typ', geschlecht:'Geschlecht', busen:'Busen',
    intimbereich:'Intimbereich', hautton:'Hautton', haarfarbe:'Haarfarbe',
    haarlaenge:'Haarlänge', augenfarbe:'Augenfarbe', koerperschmuck:'Körperschmuck',
    sonstiges:'Sonstiges', service:'Services', treff:'Treffpunkte',
    location:'Location', zeiten:'Zeiten',
  };
  const GROUP_ICONS = {
    geschlecht:'users', busen:'heart', service:'heart', treff:'location', location:'home',
    zeiten:'clock', sonstiges:'sparkles',
  };

  function selected(group, value) {
    return (state.filters.selected?.[group] || []).includes(value);
  }

  function chip(group, value, label = value) {
    return `<button type="button" class="mini-chip ${selected(group, value) ? 'active' : ''}" data-filter-group="${esc(group)}" data-filter-value="${esc(value)}">${esc(label)}</button>`;
  }

  function categoryChip(value, label) {
    return `<button type="button" class="mini-chip ${state.filters.category === value ? 'active' : ''}" data-category="${esc(value)}">${esc(label)}</button>`;
  }

  function localToggle(key, label, iconName) {
    const on = Boolean(state.filters.local?.[key]);
    return `<button type="button" class="small-toggle-label" data-local-toggle="${esc(key)}">${iconName ? ico(iconName) : ''}<span class="small-switch ${on ? 'on' : ''}"></span>${esc(label)}</button>`;
  }

  function dualRange(prefix, label, min, max, low, high, suffix = '') {
    const lo = Number.isFinite(Number(low)) ? Number(low) : min;
    const hi = Number.isFinite(Number(high)) ? Number(high) : max;
    const left = ((lo - min) / (max - min)) * 100;
    const right = ((hi - min) / (max - min)) * 100;
    return `<div class="expanded-range-block">
      <div class="expanded-range-head"><b>${esc(label)}</b><strong id="${prefix}RangeLabel">${lo}${suffix} – ${hi}${suffix}</strong></div>
      <div class="expanded-dual-range">
        <span class="expanded-dual-track"></span><span id="${prefix}RangeFill" class="expanded-dual-fill" style="left:${left}%;width:${Math.max(0, right-left)}%"></span>
        <input id="${prefix}MinRange" aria-label="${esc(label)} von" type="range" min="${min}" max="${max}" value="${lo}">
        <input id="${prefix}MaxRange" aria-label="${esc(label)} bis" type="range" min="${min}" max="${max}" value="${hi}">
      </div>
    </div>`;
  }

  function filterGroupMarkup(group) {
    const key = String(group?.key || '');
    const options = Array.isArray(group?.options) ? group.options : [];
    if (!key || !options.length) return '';
    const label = GROUP_LABELS[key] || key;
    const icon = GROUP_ICONS[key] || 'user';
    return `<div class="filter-card full" data-filter-search="${esc(`${label} ${options.map(item => item.label).join(' ')}`)}">
      <div class="filter-card-head"><b>${ico(icon)}${esc(label)}</b><small>${options.length} Optionen</small></div>
      <div class="mini-chips">${options.map(option => chip(key, String(option.label || option.value || ''), String(option.label || option.value || ''))).join('')}</div>
    </div>`;
  }

  function syncHeightRange(source) {
    const minEl = $('#heightMinRange');
    const maxEl = $('#heightMaxRange');
    const fill = $('#heightRangeFill');
    const label = $('#heightRangeLabel');
    if (!minEl || !maxEl || !fill || !label) return;
    let low = Number(minEl.value);
    let high = Number(maxEl.value);
    if (low > high) {
      if (source === 'min') low = high; else high = low;
    }
    minEl.value = String(low);
    maxEl.value = String(high);
    state.filters.heightMin = low;
    state.filters.heightMax = high;
    const left = ((low - 140) / 60) * 100;
    const right = ((high - 140) / 60) * 100;
    fill.style.left = `${left}%`;
    fill.style.width = `${Math.max(0, right-left)}%`;
    label.textContent = `${low} cm – ${high} cm`;
  }

  function syncAgeFill() {
    const low = Number($('#ageMinRange')?.value || 18);
    const high = Number($('#ageMaxRange')?.value || 60);
    const fill = $('#ageRangeFill');
    if (!fill) return;
    const left = ((low - 18) / 42) * 100;
    const right = ((high - 18) / 42) * 100;
    fill.style.left = `${left}%`;
    fill.style.width = `${Math.max(0, right-left)}%`;
  }

  function renderExpandedFilter() {
    const content = $('#filterContent');
    if (!content || !state.filters) return;
    const filters = state.filters;
    filters.selected ||= {};
    filters.local ||= {photosOnly:true,videoOnly:false,likesOnly:false,imageMode:'ALL'};
    const categories = Array.isArray(config.categories) ? config.categories : [];
    const groups = Array.isArray(config.filter_groups) ? config.filter_groups : [];

    content.innerHTML = `
      <div class="filter-search-box">${ico('search')}<input id="filterSearchText" placeholder="Filter durchsuchen (z. B. Haarfarbe, Massage, Escort …)"></div>
      <div class="filter-grid expanded-filter-grid">
        <div class="expanded-top-grid">
          <div class="filter-card"><div class="filter-card-head"><b>${ico('map-pin')}Standort</b></div><input id="filterCity" class="compact-input" value="${esc(filters.city || '')}"></div>
          <div class="filter-card"><div class="filter-card-head"><b>${ico('location')}Umkreis</b><strong id="filterRadiusLabel">${Number(filters.radius || 20)} km</strong></div><input id="filterRadius" class="filter-range" type="range" min="1" max="100" value="${Number(filters.radius || 20)}"><div class="range-labels" style="margin-left:0"><span>1 km</span><span>100 km</span></div></div>
        </div>

        <div class="filter-card full">
          <div class="filter-card-head"><b>${ico('calendar')}Alter &amp; Größe</b></div>
          <div class="expanded-range-stack">
            ${dualRange('age','Alter',18,60,filters.ageMin ?? 18,filters.ageMax ?? 60)}
            ${dualRange('height','Größe',140,200,filters.heightMin ?? 140,filters.heightMax ?? 200,' cm')}
          </div>
          <p class="expanded-filter-note">Von–Bis-Werte werden gemeinsam an den Server übergeben.</p>
        </div>

        <div class="filter-card full"><div class="filter-card-head"><b>${ico('tag')}Kategorie</b></div><div class="mini-chips">${categories.map(item => categoryChip(String(item.value || ''), String(item.label || item.value || 'Alle'))).join('')}</div></div>

        <div class="filter-card full"><div class="filter-card-head"><b>${ico('sparkles')}KI-Bildfilter</b></div><div class="expanded-image-modes">${[['ALL','Alle'],['NATURAL','Natürlich'],['STUDIO','Studio'],['FACEAPP','FaceApp'],['AI','KI-optimiert']].map(([value,label]) => `<button type="button" class="expanded-image-mode ${filters.local.imageMode === value ? 'active' : ''}" data-image-mode="${value}">${label}</button>`).join('')}</div></div>

        ${groups.map(filterGroupMarkup).join('')}

        <div class="filter-card full"><div class="filter-card-head"><b>${ico('sliders')}Lokale Ergebnisfilter</b></div><div class="expanded-local-toggles">${localToggle('photosOnly','Nur mit Fotos','camera')}${localToggle('videoOnly','Nur mit Video','video')}${localToggle('likesOnly','Nur Likes','heart')}</div></div>

        <div class="filter-default-actions"><button id="saveDefaultFilters" type="button">${ico('save')}Als Standard speichern</button><button id="deleteDefaultFilters" type="button" ${state.settings?.default_filters ? '' : 'disabled'}>${ico('trash')}Standard löschen</button></div>
      </div>`;

    // The currently registered search hook augments the original binder with
    // preview scheduling. Markup intentionally uses the canonical data-* names.
    App.bindFilterControls();
    $('#heightMinRange')?.addEventListener('input', () => syncHeightRange('min'));
    $('#heightMaxRange')?.addEventListener('input', () => syncHeightRange('max'));
    $('#ageMinRange')?.addEventListener('input', syncAgeFill);
    $('#ageMaxRange')?.addEventListener('input', syncAgeFill);
    $('#filterSearchText')?.addEventListener('input', event => {
      const query = event.target.value.trim().toLocaleLowerCase('de-DE');
      $$('.filter-card', content).forEach(card => {
        const haystack = `${card.textContent || ''} ${card.dataset.filterSearch || ''}`.toLocaleLowerCase('de-DE');
        card.classList.toggle('hidden', Boolean(query) && !haystack.includes(query));
      });
    });
    App.updateFilterCount();
  }

  App.registerHooks({renderFilter: renderExpandedFilter}, 'filter_ui');
  if (state.screen === 'home' && $('#filterContent')) App.renderHome();
})();
