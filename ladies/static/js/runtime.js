'use strict';

// Canonical browser boundary between the large core UI and feature scripts.
// New code uses LadiesApp instead of depending on script load side effects.
(() => {
  const config = window.__LADIES_CONFIG__ || {};
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

  // V9.6 transition contract: existing feature files are loaded explicitly by
  // index.html (never dynamically), but some still call the former bridge names.
  // Keep those aliases in one place until each feature file is migrated to the
  // LadiesApp namespace; state itself remains owned by app.js.
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
