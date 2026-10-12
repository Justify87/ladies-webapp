'use strict';

(() => {
  const App = window.LadiesApp;
  if (!App) throw new Error('LadiesApp runtime fehlt');
  const {state, toast, esc} = App;
  const icon = name => `<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  const contactCache = new Map();
  let activeThreadId = null;
  let activeContact = null;
  let activeEndpoint = null;
  let chatHydrationToken = 0;
  let presenceExpiryTimer = null;
  let presenceExpiredAt = 0;
  let presenceObserverMuted = false;

  function currentProfileContext() {
    const detail = state?.detail;
    const personId = detail?.person?.person_id;
    if (!personId) return null;
    const listing = detail.listings?.find(x => String(x.advert_id) === String(state.detailAdvertId)) || detail.listings?.[0];
    return {personId, listingId: listing?.listing_id || null};
  }

  async function getJson(url, options) {
    const response = await fetch(url, options);
    let data = {};
    try { data = await response.json(); } catch {}
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function summaryForCurrentProfile() {
    const context = currentProfileContext();
    if (!context) return null;
    const qs = context.listingId ? `?listing_id=${encodeURIComponent(context.listingId)}` : '';
    return getJson(`/api/messaging/person/${context.personId}${qs}`);
  }

  async function probeEndpoint(endpoint) {
    if (!endpoint?.endpoint_id) return null;
    const key = Number(endpoint.endpoint_id);
    const cached = contactCache.get(key);
    if (cached && Date.now() - cached.at < 5 * 60 * 1000) return cached.value;
    const value = await getJson(`/api/messaging/endpoints/${key}/probe`);
    contactCache.set(key, {at: Date.now(), value});
    return value;
  }

  function openInternalChat() {
    const trigger = document.querySelector('#profileContent .profile-chat-section #profileOpenChat');
    if (trigger) {
      trigger.click();
      return true;
    }
    return false;
  }

  function displayPhone(endpoint, fallback='Keine WhatsApp-Nummer') {
    return endpoint?.display_phone || endpoint?.normalized_phone || fallback;
  }

  function renderContactButton(button, phone, availability='unknown') {
    const missing = availability === 'missing';
    const label = missing ? 'Kein WhatsApp' : 'Chat öffnen';
    const html = `<span class="profile-contact-cta">${icon('message')}<b>${label}</b></span><small class="profile-contact-number">${esc(phone || 'Keine WhatsApp-Nummer')}</small>`;
    if (button.innerHTML !== html) button.innerHTML = html;
    button.classList.toggle('wa-missing', missing);
    button.disabled = missing;
    button.setAttribute('aria-label', missing ? `${phone || 'Diese Nummer'} ist nicht bei WhatsApp registriert` : (phone ? `Chat öffnen, ${phone}` : 'Chat öffnen'));
  }

  function normalPresenceText() {
    const endpoint = activeContact || activeEndpoint;
    const phone = displayPhone(endpoint, 'WhatsApp');
    return activeContact?.exists === false ? `${phone} · kein WhatsApp` : phone;
  }

  function restorePresenceLine() {
    const presence = document.querySelector('#chatPresence');
    if (!presence) return;
    presenceObserverMuted = true;
    presence.textContent = normalPresenceText();
    presence.classList.toggle('wa-unavailable', activeContact?.exists === false);
    setTimeout(() => { presenceObserverMuted = false; }, 0);
  }

  function schedulePresenceExpiry() {
    clearTimeout(presenceExpiryTimer);
    presenceExpiryTimer = setTimeout(() => {
      presenceExpiredAt = Date.now();
      restorePresenceLine();
    }, 6500);
  }

  function handlePresenceMutation() {
    if (presenceObserverMuted) return;
    const presence = document.querySelector('#chatPresence');
    const text = presence?.textContent || '';
    const transient = /tippt|Sprachnachricht auf/i.test(text);
    if (transient) {
      if (presenceExpiredAt && Date.now() - presenceExpiredAt < 30000) {
        restorePresenceLine();
      } else {
        schedulePresenceExpiry();
      }
      return;
    }
    clearTimeout(presenceExpiryTimer);
    presenceExpiryTimer = null;
    presenceExpiredAt = 0;
  }

  function syncChatViewport() {
    const layer = document.querySelector('#messagingChatLayer');
    if (!layer || layer.classList.contains('hidden')) return;
    const viewport = window.visualViewport;
    if (!viewport) {
      layer.style.top = '0px';
      layer.style.bottom = '0px';
      layer.style.height = 'auto';
      return;
    }
    const frame = layer.offsetParent || document.documentElement;
    const rect = frame.getBoundingClientRect();
    const top = Math.max(0, Math.round((viewport.offsetTop || 0) - rect.top));
    const available = Math.max(260, Math.min(Math.round(viewport.height), Math.round(rect.height - top)));
    layer.style.top = `${top}px`;
    layer.style.bottom = 'auto';
    layer.style.height = `${available}px`;
  }

  function autoSizeComposer() {
    const input = document.querySelector('#chatInput');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 48), 112)}px`;
  }

  function polishChatChrome() {
    const send = document.querySelector('#chatSend');
    if (send && !send.dataset.polished) {
      send.dataset.polished = '1';
      send.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.2 20 12 4 18.8l2.35-6.8L4 5.2Z" fill="none" stroke="currentColor" stroke-linejoin="round"/><path d="M6.6 12H20" fill="none" stroke="currentColor" stroke-linecap="round"/></svg>';
      send.title = 'Senden';
    }
    autoSizeComposer();
    syncChatViewport();
  }

  function applyChatContact(contact, fallbackEndpoint=null) {
    if (!contact && !fallbackEndpoint) return;
    if (fallbackEndpoint) activeEndpoint = fallbackEndpoint;
    if (contact) activeContact = contact;
    restorePresenceLine();
    const avatar = document.querySelector('#chatAvatar');
    if (avatar && contact?.profile_picture_url) {
      avatar.classList.add('wa-avatar');
      avatar.innerHTML = `<img src="${esc(contact.profile_picture_url)}" alt="WhatsApp-Profilbild" referrerpolicy="no-referrer">`;
    }
    const input = document.querySelector('#chatInput');
    const send = document.querySelector('#chatSend');
    if (contact?.exists === false) {
      if (input) input.disabled = true;
      if (send) send.disabled = true;
      const banner = document.querySelector('#chatConnectionBanner');
      if (banner) {
        banner.classList.remove('hidden');
        banner.textContent = 'Diese Telefonnummer ist nicht bei WhatsApp registriert.';
      }
    }
    polishChatChrome();
  }

  async function hydrateChat(threadId, preferredEndpointId=null) {
    if (!threadId) return;
    const token = ++chatHydrationToken;
    activeThreadId = Number(threadId);
    try {
      const thread = await getJson(`/api/messaging/threads/${encodeURIComponent(threadId)}?limit=1`);
      if (token !== chatHydrationToken) return;
      const endpoint = (thread.endpoints || []).find(x => Number(x.endpoint_id) === Number(preferredEndpointId)) || thread.endpoint || thread.endpoints?.[0];
      if (!endpoint) return;
      activeEndpoint = endpoint;
      applyChatContact(null, endpoint);
      const contact = await probeEndpoint(endpoint);
      if (token !== chatHydrationToken || Number(activeThreadId) !== Number(threadId)) return;
      applyChatContact(contact, endpoint);
    } catch (error) {
      console.debug?.('Chat contact hydration skipped', error);
    }
  }

  async function syncProfileContact() {
    const root = document.querySelector('#profileContent');
    const context = currentProfileContext();
    const button = root?.querySelector('[data-contact]');
    if (!root || !button || !context) return;

    const legacy = root.querySelector('.profile-chat-section');
    if (legacy) legacy.style.display = 'none';

    if (button.dataset.messagingPerson === String(context.personId)) return;
    button.dataset.messagingPerson = String(context.personId);
    renderContactButton(button, 'Nummer wird geladen …');

    try {
      const summary = await summaryForCurrentProfile();
      if (button.dataset.messagingPerson !== String(context.personId)) return;
      const endpoint = summary?.selected_endpoint;
      const phone = displayPhone(endpoint);
      button.dataset.messagingThread = summary?.thread_id || '';
      button.dataset.messagingEndpoint = endpoint?.endpoint_id || '';
      renderContactButton(button, phone);
      if (!endpoint) return;
      const contact = await probeEndpoint(endpoint);
      if (button.dataset.messagingPerson !== String(context.personId)) return;
      renderContactButton(button, phone, contact?.exists === false ? 'missing' : 'available');
    } catch {
      if (button.dataset.messagingPerson === String(context.personId)) renderContactButton(button, 'Chat derzeit nicht verfügbar');
    }
  }

  function cleanMessagesUi() {
    document.querySelector('[data-message-filter="UNASSIGNED"]')?.remove();
    document.querySelectorAll('#threadList .message-empty span').forEach(span => {
      if (span.textContent?.includes('Neue WhatsApp-Nachrichten') || span.textContent?.includes('Favoriten und Likes')) {
        span.textContent = 'Hier erscheinen alle Unterhaltungen, sobald Nachrichten vorhanden sind.';
      }
    });
  }

  document.addEventListener('click', event => {
    const button = event.target.closest?.('#profileContent [data-contact]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    if (button.disabled || button.classList.contains('wa-missing')) {
      toast('Für diese Nummer wurde kein WhatsApp-Konto gefunden.');
      return;
    }
    const threadId = Number(button.dataset.messagingThread || 0) || null;
    const endpointId = Number(button.dataset.messagingEndpoint || 0) || null;
    if (threadId) {
      activeThreadId = threadId;
      hydrateChat(threadId, endpointId);
    }
    if (!openInternalChat()) {
      setTimeout(() => {
        if (!openInternalChat()) toast('Chat wird noch vorbereitet.');
      }, 60);
    }
  }, true);

  document.addEventListener('click', event => {
    const row = event.target.closest?.('#threadList [data-thread-id]');
    if (row) {
      activeThreadId = Number(row.dataset.threadId);
      activeContact = null;
      activeEndpoint = null;
      presenceExpiredAt = 0;
      setTimeout(() => hydrateChat(activeThreadId), 0);
      return;
    }
    const endpointChoice = event.target.closest?.('[data-use-endpoint]');
    if (endpointChoice && activeThreadId) {
      contactCache.delete(Number(endpointChoice.dataset.useEndpoint));
      activeContact = null;
      setTimeout(() => hydrateChat(activeThreadId, Number(endpointChoice.dataset.useEndpoint)), 0);
    }
  }, true);

  document.addEventListener('input', event => {
    if (event.target?.id === 'chatInput') {
      autoSizeComposer();
      syncChatViewport();
    }
  }, true);

  document.addEventListener('focusin', event => {
    if (event.target?.id === 'chatInput') {
      syncChatViewport();
      setTimeout(syncChatViewport, 80);
      setTimeout(syncChatViewport, 240);
    }
  });

  document.addEventListener('submit', event => {
    if (event.target?.id === 'chatComposer') setTimeout(autoSizeComposer, 0);
  }, true);

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearTimeout(presenceExpiryTimer);
      presenceExpiredAt = Date.now();
      restorePresenceLine();
    }
  });

  window.visualViewport?.addEventListener('resize', syncChatViewport);
  window.visualViewport?.addEventListener('scroll', syncChatViewport);
  window.addEventListener('orientationchange', () => setTimeout(syncChatViewport, 80));

  const chatLayerObserver = new MutationObserver(() => {
    const layer = document.querySelector('#messagingChatLayer');
    if (!layer) return;
    if (layer.classList.contains('hidden')) {
      clearTimeout(presenceExpiryTimer);
      activeContact = null;
      activeEndpoint = null;
      presenceExpiredAt = 0;
      return;
    }
    polishChatChrome();
    if (activeThreadId) hydrateChat(activeThreadId);
  });

  const presenceObserver = new MutationObserver(handlePresenceMutation);

  let queued = false;
  const resync = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      syncProfileContact();
      cleanMessagesUi();
      const layer = document.querySelector('#messagingChatLayer');
      if (layer && !layer.dataset.profileMessagingObserved) {
        layer.dataset.profileMessagingObserved = '1';
        chatLayerObserver.observe(layer, {attributes:true, attributeFilter:['class']});
      }
      const presence = document.querySelector('#chatPresence');
      if (presence && !presence.dataset.presenceExpiryObserved) {
        presence.dataset.presenceExpiryObserved = '1';
        presenceObserver.observe(presence, {childList:true, characterData:true, subtree:true});
      }
      if (layer && !layer.classList.contains('hidden')) polishChatChrome();
    });
  };
  new MutationObserver(resync).observe(document.body, {childList:true, subtree:true});
  resync();
})();
