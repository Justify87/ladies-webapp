'use strict';

(() => {
  const APP_VERSION = '9.5.0';
  document.title = `Ladies Explorer V${APP_VERSION}`;
  document.querySelectorAll('.version-pill').forEach(el => { el.textContent = `V${APP_VERSION}`; });

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = name => `<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  const contactCache = new Map();
  let activeThreadId = null;
  let activeContact = null;
  let activeEndpoint = null;
  let chatHydrationToken = 0;
  let presenceExpiryTimer = null;
  let presenceExpiredAt = 0;
  let presenceObserverMuted = false;

  const style = document.createElement('style');
  style.textContent = `
    .profile-chat-section{display:none!important}
    .profile-big-action[data-contact]{gap:.38rem;min-width:0}
    .profile-big-action[data-contact]>.profile-contact-cta{
      width:100%;max-width:116px;height:50px;border-radius:14px;background:#e8f2ff;color:#2273df;
      display:flex;flex-direction:row;align-items:center;justify-content:center;gap:7px;padding:0 9px;
      box-shadow:inset 0 0 0 1px #d9e8fa
    }
    .profile-big-action[data-contact]>.profile-contact-cta svg{width:21px;height:21px;flex:0 0 auto}
    .profile-big-action[data-contact]>.profile-contact-cta b{font-size:11px;line-height:1;font-weight:800;white-space:nowrap}
    .profile-big-action[data-contact]>.profile-contact-number{
      display:block;width:100%;max-width:120px;min-height:1.15em;margin:0;text-align:center;
      color:#71849a;font-size:9.5px;font-weight:650;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis
    }
    .profile-big-action[data-contact].wa-missing>.profile-contact-cta{background:#f2f4f7;color:#8b98a8;box-shadow:inset 0 0 0 1px #e2e7ed}
    .profile-big-action[data-contact].wa-missing>.profile-contact-number{color:#b04459}
    .profile-big-action[data-contact]:disabled{opacity:1;cursor:default}

    .messaging-chat-layer{
      overflow:hidden!important;padding-bottom:0!important;
      background:
        radial-gradient(circle at 85% -10%,rgba(92,139,196,.18),transparent 34%),
        linear-gradient(180deg,#f7faff 0%,#f2f6fb 100%)!important
    }
    .chat-header{
      min-height:64px!important;
      background:rgba(7,28,49,.88)!important;
      border-bottom:1px solid rgba(255,255,255,.09)!important;
      box-shadow:0 8px 24px rgba(5,25,45,.12)!important;
      -webkit-backdrop-filter:blur(22px) saturate(165%);backdrop-filter:blur(22px) saturate(165%)
    }
    .chat-icon-btn{background:rgba(255,255,255,.07)!important;border:1px solid rgba(255,255,255,.06)!important}
    .chat-avatar{border-radius:15px!important;box-shadow:0 4px 14px rgba(0,0,0,.2)}
    .chat-avatar.wa-avatar img{width:100%;height:100%;object-fit:cover}
    .chat-identity small.wa-unavailable{color:#ffb7c6}
    .chat-messages{padding:15px 11px 10px!important;scroll-padding-bottom:86px!important}
    .message-bubble{
      border:1px solid rgba(218,228,239,.72)!important;
      background:rgba(255,255,255,.82)!important;
      box-shadow:0 5px 18px rgba(18,43,70,.065)!important;
      -webkit-backdrop-filter:blur(14px) saturate(135%);backdrop-filter:blur(14px) saturate(135%)
    }
    .message-line.out .message-bubble{
      background:linear-gradient(145deg,rgba(255,239,245,.96),rgba(255,225,236,.92))!important;
      border-color:rgba(247,47,100,.10)!important
    }
    .chat-composer{
      grid-template-columns:minmax(0,1fr) 50px!important;align-items:end!important;gap:9px!important;
      padding:9px 11px max(10px,env(safe-area-inset-bottom))!important;
      background:rgba(248,251,255,.82)!important;
      border-top:1px solid rgba(204,217,231,.72)!important;
      box-shadow:0 -9px 28px rgba(26,57,89,.08)!important;
      -webkit-backdrop-filter:blur(24px) saturate(175%);backdrop-filter:blur(24px) saturate(175%)
    }
    .composer-plus{display:none!important}
    .chat-composer textarea{
      width:100%!important;min-height:48px!important;max-height:112px!important;margin:0!important;
      border:1px solid rgba(193,208,224,.9)!important;border-radius:20px!important;
      padding:13px 15px!important;background:rgba(255,255,255,.86)!important;
      box-shadow:inset 0 1px 2px rgba(20,44,69,.03),0 3px 12px rgba(31,64,99,.04)!important;
      overflow-y:auto!important;line-height:1.32!important
    }
    .chat-composer textarea:focus{
      background:#fff!important;border-color:rgba(46,124,246,.48)!important;
      box-shadow:0 0 0 3px rgba(46,124,246,.09),0 5px 16px rgba(31,64,99,.06)!important
    }
    .composer-send{
      width:50px!important;height:50px!important;border-radius:17px!important;
      background:linear-gradient(145deg,#ff4d7d,#ec225b)!important;color:#fff!important;
      box-shadow:0 8px 20px rgba(238,31,91,.28),inset 0 1px 0 rgba(255,255,255,.28)!important;
      transition:transform .12s ease,filter .12s ease,opacity .12s ease
    }
    .composer-send:not(:disabled):active{transform:scale(.94);filter:brightness(.96)}
    .composer-send svg{width:23px!important;height:23px!important;stroke-width:2.2}
    .composer-send:disabled{box-shadow:none!important;filter:saturate(.45)}
    body.chat-open{overscroll-behavior:none}
  `;
  document.head.appendChild(style);

  function currentProfileContext() {
    const detail = window.state?.detail;
    const personId = detail?.person?.person_id;
    if (!personId) return null;
    const listing = detail.listings?.find(x => String(x.advert_id) === String(window.state.detailAdvertId)) || detail.listings?.[0];
    return { personId, listingId: listing?.listing_id || null };
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
    contactCache.set(key, { at: Date.now(), value });
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
      window.toast?.('Für diese Nummer wurde kein WhatsApp-Konto gefunden.');
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
        if (!openInternalChat()) window.toast?.('Chat wird noch vorbereitet.');
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
