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

// Load messaging UI policy first, then the compact cross-feature follow-up layer.
document.addEventListener('DOMContentLoaded', () => {
  const loadFollowup = () => {
    if (document.querySelector('script[data-ladies-ui-followup]')) return;
    const followup = document.createElement('script');
    followup.src = '/static/ui_followup.js';
    followup.dataset.ladiesUiFollowup = '1';
    document.head.appendChild(followup);
  };

  const profileUi = document.createElement('script');
  profileUi.src = '/static/profile_messaging_ui.js';
  profileUi.addEventListener('load', loadFollowup, {once:true});
  profileUi.addEventListener('error', loadFollowup, {once:true});
  document.head.appendChild(profileUi);
});
