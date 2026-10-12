'use strict';

// Small bridge between the framework-free core UI and the messaging module.
window.ladiesMessagingBridge = {
  get state() { return state; },
  setScreen,
  openPerson,
  openSheet,
  closeSheet,
  openContactSheet,
  toast,
};
window.state = state;
window.setScreen = setScreen;
window.openPerson = openPerson;
window.openSheet = openSheet;
window.closeSheet = closeSheet;
window.openContactSheet = openContactSheet;
window.toast = toast;
window.saveDefaultFilters = saveDefaultFilters;
window.deleteDefaultFilters = deleteDefaultFilters;
window.updateFilterCount = updateFilterCount;

// V9 kept height unrestricted as null; the restored dual-thumb UI needs the
// equivalent explicit full range while preserving the same search semantics.
if (state.filters.heightMin == null) state.filters.heightMin = 140;
if (state.filters.heightMax == null) state.filters.heightMax = 200;

// Load messaging UI policy first, then the compact cross-feature follow-up layer.
document.addEventListener('DOMContentLoaded', () => {
  const loadPricing = () => {
    if (!document.querySelector('link[data-ladies-pricing]')) {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = '/static/pricing.css';
      css.dataset.ladiesPricing = '1';
      document.head.appendChild(css);
    }
    if (!document.querySelector('script[data-ladies-pricing]')) {
      const pricing = document.createElement('script');
      pricing.src = '/static/pricing.js';
      pricing.dataset.ladiesPricing = '1';
      document.head.appendChild(pricing);
    }
  };

  const loadFollowup = () => {
    if (document.querySelector('script[data-ladies-ui-followup]')) {
      loadPricing();
      return;
    }
    const followup = document.createElement('script');
    followup.src = '/static/ui_followup.js';
    followup.dataset.ladiesUiFollowup = '1';
    followup.addEventListener('load', loadPricing, {once:true});
    followup.addEventListener('error', loadPricing, {once:true});
    document.head.appendChild(followup);
  };

  const profileUi = document.createElement('script');
  profileUi.src = '/static/profile_messaging_ui.js';
  profileUi.addEventListener('load', loadFollowup, {once:true});
  profileUi.addEventListener('error', loadFollowup, {once:true});
  document.head.appendChild(profileUi);
});
