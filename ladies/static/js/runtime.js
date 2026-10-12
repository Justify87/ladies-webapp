'use strict';

// Canonical browser boundary between the core UI and feature scripts.
(() => {
  const config = window.__LADIES_CONFIG__ || {};

  if (config.defaults) {
    if (config.defaults.city) BUILTIN_FILTERS.city = String(config.defaults.city);
    if (Number.isFinite(Number(config.defaults.radius_km))) BUILTIN_FILTERS.radius = Number(config.defaults.radius_km);
    if (!state.filters.city) state.filters.city = BUILTIN_FILTERS.city;
    if (!Number.isFinite(Number(state.filters.radius))) state.filters.radius = BUILTIN_FILTERS.radius;
  }
  if (Array.isArray(config.categories) && config.categories.length) {
    CATEGORIES.splice(
      0,
      CATEGORIES.length,
      ...config.categories.map(item => [String(item.value || ''), String(item.label || item.value || 'Alle')]),
    );
  }
  if (Array.isArray(config.filter_groups)) {
    for (const group of config.filter_groups) {
      const key = String(group?.key || '');
      if (!key || !Array.isArray(group.options)) continue;
      SERVER_FILTERS[key] = group.options
        .map(option => String(option.label || option.value || ''))
        .filter(Boolean);
    }
  }

  if (config.version) {
    openInfoSheet = () => openSheet(`<div class="sheet-title"><h2>Ladies Explorer V${esc(config.version)}</h2></div><p class="about-copy">Die Oberfläche ist personenzentriert. Anzeigen, Snapshots, Medien, Preise und Treffen bleiben historisiert; die mobile UI bildet diese Daten verständlich ab.</p>`);
  }

  const base = Object.freeze({
    bindFilterControls,
    renderFilter,
    updateFilterCount,
    performSearch,
    renderHome,
    openFilter,
    renderMyData,
    saveMyData,
  });

  const hookSetters = Object.freeze({
    bindFilterControls: handler => { bindFilterControls = handler; },
    renderFilter: handler => { renderFilter = handler; },
    updateFilterCount: handler => { updateFilterCount = handler; },
    performSearch: handler => { performSearch = handler; },
    renderHome: handler => { renderHome = handler; },
    openFilter: handler => { openFilter = handler; },
    renderMyData: handler => { renderMyData = handler; },
    saveMyData: handler => { saveMyData = handler; },
  });

  function registerHooks(hooks, source = 'feature') {
    if (!hooks || typeof hooks !== 'object') return;
    for (const [name, handler] of Object.entries(hooks)) {
      const setter = hookSetters[name];
      if (!setter) throw new Error(`Unbekannter LadiesApp-Hook: ${name}`);
      if (typeof handler !== 'function') throw new TypeError(`Hook ${name} aus ${source} ist keine Funktion`);
      setter(handler);
    }
  }

  const runtime = {
    config: Object.freeze(config),
    base,
    registerHooks,
    state,
    api,
    setScreen,
    openPerson,
    closeProfile,
    renderProfile,
    latestSnapshot,
    openContactSheet,
    fetchPersonDetail,
    loadDashboard,
    loadRecentSearches,
    loadStorage,
    resetFilters,
    applyRecentSearch,
    ratingButtons,
    choice,
    readChoice,
    readRating,
    toast,
    openSheet,
    closeSheet,
    esc,
    ico,
    $,
    $$,
    fmtDate,
    fmtShortDate,
    euro,
    SERVER_FILTERS,
    CATEGORIES,
    BUILTIN_FILTERS,
  };

  for (const name of Object.keys(hookSetters)) {
    Object.defineProperty(runtime, name, {
      enumerable: true,
      configurable: false,
      get: () => ({
        bindFilterControls,
        renderFilter,
        updateFilterCount,
        performSearch,
        renderHome,
        openFilter,
        renderMyData,
        saveMyData,
      })[name],
    });
  }

  Object.defineProperty(window, 'LadiesApp', {
    value: Object.freeze(runtime),
    configurable: false,
    enumerable: true,
    writable: false,
  });

  // Transitional read-only aliases for messaging/pricing scripts that are still
  // classic scripts. Search/filter features must use registerHooks and never
  // assign these globals themselves.
  const aliases = {
    state: () => state,
    setScreen: () => setScreen,
    api: () => api,
    openPerson: () => openPerson,
    closeProfile: () => closeProfile,
    renderProfile: () => renderProfile,
    latestSnapshot: () => latestSnapshot,
    fetchPersonDetail: () => fetchPersonDetail,
    loadDashboard: () => loadDashboard,
    loadRecentSearches: () => loadRecentSearches,
    loadStorage: () => loadStorage,
    openSheet: () => openSheet,
    closeSheet: () => closeSheet,
    toast: () => toast,
    esc: () => esc,
    ico: () => ico,
  };
  for (const [name, getter] of Object.entries(aliases)) {
    if (Object.prototype.hasOwnProperty.call(window, name)) continue;
    Object.defineProperty(window, name, {
      get: getter,
      configurable: false,
      enumerable: false,
    });
  }

  const syncVersion = () => {
    const version = String(config.version || '').trim();
    if (!version) return;
    const title = `Ladies Explorer V${version}`;
    if (document.title !== title) document.title = title;
    document.querySelectorAll('.version-pill,#versionPill').forEach(element => {
      const text = `V${version}`;
      if (element.textContent !== text) element.textContent = text;
    });
  };
  document.addEventListener('DOMContentLoaded', () => {
    syncVersion();
    const observer = new MutationObserver(syncVersion);
    observer.observe(document.body, {childList:true, subtree:true});
  }, {once:true});
})();
