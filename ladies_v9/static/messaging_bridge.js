'use strict';

// Explicit, tiny compatibility bridge between the existing framework-free V9 UI
// and the additive messaging module. Keeps messaging.js from reaching into
// implementation details while preserving the existing app.js architecture.
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
