'use strict';

// Canonical browser boundary between the core UI and feature scripts.
(() => {
  const config = window.__LADIES_CONFIG__ || {};

  // Server-side Ladies mappings are the source of truth. The core arrays are
  // deliberately mutated before feature scripts initialize, so all consumers
  // see the same values without another hard-coded backend catalog.
  if (config.defaults) {
    if (config.defaults.city) {
      BUILTIN_FILTERS.city = String(config.defaults.city);
      if (!state.filters.city) state.filters.city = BUILTIN_FILTERS.city;
    }
    if (Number.isFinite(Number(config.defaults.radius_km))) {
      BUILTIN_FILTERS.radius = Number(config.defaults.radius_km);
      if (!Number.isFinite(Number(state.filters.radius))) state.filters.radius = BUILTIN_FILTERS.radius;
    }
  }
  if (Array.isArray(config.categories) && config.categories.length) {
    CATEGORIES.splice(0, CATEGORIES.length, ...config.categories.map(item => [String(item.value || ''), String(item.label || item.value || 'Alle')]));
  }
  if (Array.isArray(config.filter_groups)) {
    for (const group of config.filter_groups) {
      const key = String(group?.key || '');
      if (!key || !Array.isArray(group.options)) continue;
      SERVER_FILTERS[key] = group.options.map(option => String(option.label || option.value || '')).filter(Boolean);
    }
  }

  const runtime = {
    config,
    get state() { return state; },
    api,
    setScreen,
    switchMain,
    setLoading,
    toast,
    openSheet,
    closeSheet,
    openPerson,
    openSearchProfile,
    applyMainFilters,
    renderDeck,
    showMedia,
    openVisualAnalysis,
    localMediaUrl,
    profileName,
    dollars,
    datePretty,
    esc,
    ico,
    $, $$,
  };
  Object.defineProperty(window, 'LadiesApp', {
    value: Object.freeze(runtime),
    configurable: false,
    enumerable: true,
    writable: false,
  });

  // Temporary compatibility surface for existing feature scripts. All aliases
  // live here instead of being created by a dynamic bridge/loading chain.
  const aliases = {
    state: () => state,
    setScreen: () => setScreen,
    switchMain: () => switchMain,
    setLoading: () => setLoading,
    api: () => api,
    openSheet: () => openSheet,
    closeSheet: () => closeSheet,
    openPerson: () => openPerson,
    openSearchProfile: () => openSearchProfile,
    applyMainFilters: () => applyMainFilters,
    renderDeck: () => renderDeck,
    showMedia: () => showMedia,
    openVisualAnalysis: () => openVisualAnalysis,
    localMediaUrl: () => localMediaUrl,
    profileName: () => profileName,
    dollars: () => dollars,
    datePretty: () => datePretty,
    esc: () => esc,
  };
  for (const [name, getter] of Object.entries(aliases)) {
    if (Object.prototype.hasOwnProperty.call(window, name)) continue;
    Object.defineProperty(window, name, {
      get: getter,
      configurable: false,
      enumerable: false,
    });
  }
})();
