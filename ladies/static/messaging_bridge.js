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

// Load UI policy overrides after all deferred core/messaging scripts have run.
document.addEventListener('DOMContentLoaded', () => {
  const script = document.createElement('script');
  script.src = '/static/profile_messaging_ui.js';
  document.head.appendChild(script);
});
