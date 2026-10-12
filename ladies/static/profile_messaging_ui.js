'use strict';

(() => {
  const APP_VERSION = '9.2.2';
  document.title = `Ladies Explorer V${APP_VERSION}`;
  document.querySelectorAll('.version-pill').forEach(el => { el.textContent = `V${APP_VERSION}`; });

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = name => `<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  const contactCache = new Map();
  let activeThreadId = null;
  let activeContact = null;
  let chatHydrationToken = 0;

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
    .chat-avatar.wa-avatar img{width:100%;height:100%;object-fit:cover}
    .chat-identity small.wa-unavailable{color:#ffb7c6}
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

  function applyChatContact(contact, fallbackEndpoint=null) {
    if (!contact && !fallbackEndpoint) return;
    activeContact = contact || activeContact;
    const phone = displayPhone(contact || fallbackEndpoint, 'WhatsApp');
    const presence = document.querySelector('#chatPresence');
    if (presence) {
      presence.textContent = contact?.exists === false ? `${phone} · kein WhatsApp` : phone;
      presence.classList.toggle('wa-unavailable', contact?.exists === false);
    }
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
      if (span.textContent?.includes('Neue WhatsApp-Nachrichten')) {
        span.textContent = 'Hier erscheinen Favoriten und Likes, denen du bereits geschrieben hast.';
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
      setTimeout(() => hydrateChat(activeThreadId), 0);
      return;
    }
    const endpointChoice = event.target.closest?.('[data-use-endpoint]');
    if (endpointChoice && activeThreadId) {
      contactCache.delete(Number(endpointChoice.dataset.useEndpoint));
      setTimeout(() => hydrateChat(activeThreadId, Number(endpointChoice.dataset.useEndpoint)), 0);
    }
  }, true);

  const chatLayerObserver = new MutationObserver(() => {
    const layer = document.querySelector('#messagingChatLayer');
    if (!layer || layer.classList.contains('hidden')) return;
    if (activeThreadId) hydrateChat(activeThreadId);
  });

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
    });
  };
  new MutationObserver(resync).observe(document.body, {childList:true, subtree:true});
  resync();
})();
