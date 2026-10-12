'use strict';

(() => {
  const version = String(window.LadiesApp?.config?.version || window.__LADIES_CONFIG__?.version || '').trim();
  if (!version) return;

  function syncVersion() {
    document.title = `Ladies Explorer V${version}`;
    document.querySelectorAll('.version-pill,#versionPill').forEach(element => {
      element.textContent = `V${version}`;
    });
  }

  syncVersion();
  const observer = new MutationObserver(syncVersion);
  observer.observe(document.head, {childList:true, subtree:true, characterData:true});
  observer.observe(document.body, {childList:true, subtree:true});
})();
