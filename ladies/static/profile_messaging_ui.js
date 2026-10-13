'use strict';

import { state, toast, esc } from './app.js';

const icon = name => `<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
const contactCache = new Map();
let activeThreadId = null;
let activeContact = null;
let activeEndpoint = null;
let hydrationToken = 0;
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

async function getJson(url) {
  const response = await fetch(url);
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
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

function displayPhone(endpoint, fallback='Keine WhatsApp-Nummer') {
  return endpoint?.display_phone || endpoint?.normalized_phone || fallback;
}

function renderContactButton(button, phone, availability='unknown') {
  const missing = availability === 'missing';
  const label = missing ? 'Kein WhatsApp' : 'Chat öffnen';
  button.innerHTML = `<span class="profile-contact-cta">${icon('message')}<b>${label}</b></span><small class="profile-contact-number">${esc(phone || 'Keine WhatsApp-Nummer')}</small>`;
  button.classList.toggle('wa-missing', missing);
  button.disabled = missing;
  button.setAttribute('aria-label', missing ? `${phone || 'Diese Nummer'} ist nicht bei WhatsApp registriert` : (phone ? `Chat öffnen, ${phone}` : 'Chat öffnen'));
}

function claimProfileContactButton(button, context) {
  if (button.dataset.messagingOwner === '1') return button;
  const owned = button.cloneNode(true);
  owned.dataset.messagingOwner = '1';
  button.replaceWith(owned);
  owned.addEventListener('click', event => {
    event.preventDefault();
    if (owned.disabled || owned.classList.contains('wa-missing')) {
      toast('Für diese Nummer wurde kein WhatsApp-Konto gefunden.');
      return;
    }
    document.dispatchEvent(new CustomEvent('ladies:open-person-chat', {
      detail: {personId: context.personId, listingId: context.listingId},
    }));
  });
  return owned;
}

async function syncProfileContact() {
  const root = document.querySelector('#profileContent');
  const context = currentProfileContext();
  let button = root?.querySelector('[data-contact]');
  if (!root || !button || !context) return;
  button = claimProfileContactButton(button, context);
  if (button.dataset.messagingPerson === String(context.personId)) return;
  button.dataset.messagingPerson = String(context.personId);
  renderContactButton(button, 'Nummer wird geladen …');

  try {
    const qs = context.listingId ? `?listing_id=${encodeURIComponent(context.listingId)}` : '';
    const summary = await getJson(`/api/messaging/person/${context.personId}${qs}`);
    if (button.dataset.messagingPerson !== String(context.personId)) return;
    const endpoint = summary?.selected_endpoint;
    const phone = displayPhone(endpoint);
    renderContactButton(button, phone);
    if (!endpoint) return;
    const contact = await probeEndpoint(endpoint);
    if (button.dataset.messagingPerson !== String(context.personId)) return;
    renderContactButton(button, phone, contact?.exists === false ? 'missing' : 'available');
  } catch {
    if (button.dataset.messagingPerson === String(context.personId)) {
      renderContactButton(button, 'Chat derzeit nicht verfügbar');
    }
  }
}

function normalPresenceText() {
  const endpoint = activeContact || activeEndpoint;
  const phone = displayPhone(endpoint, 'WhatsApp');
  return activeContact?.exists === false ? `${phone} · kein WhatsApp` : phone;
}

function restorePresenceLine() {
  const presence = document.querySelector('#chatPresence');
  if (!presence || !activeThreadId) return;
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
  if (presenceObserverMuted || !activeThreadId) return;
  const text = document.querySelector('#chatPresence')?.textContent || '';
  if (/tippt|Sprachnachricht auf/i.test(text)) {
    if (presenceExpiredAt && Date.now() - presenceExpiredAt < 30000) restorePresenceLine();
    else schedulePresenceExpiry();
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

function applyChatContact(contact, endpoint=null) {
  if (endpoint) activeEndpoint = endpoint;
  if (contact) activeContact = contact;
  restorePresenceLine();
  const avatar = document.querySelector('#chatAvatar');
  if (avatar && contact?.profile_picture_url) {
    avatar.classList.add('wa-avatar');
    avatar.innerHTML = `<img src="${esc(contact.profile_picture_url)}" alt="WhatsApp-Profilbild" referrerpolicy="no-referrer">`;
  }
  const unavailable = contact?.exists === false;
  for (const selector of ['#chatInput', '#chatSend', '#chatMic', '#chatPlus']) {
    const element = document.querySelector(selector);
    if (element && unavailable) element.disabled = true;
  }
  if (unavailable) {
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
  const token = ++hydrationToken;
  activeThreadId = Number(threadId);
  try {
    const thread = await getJson(`/api/messaging/threads/${encodeURIComponent(threadId)}?limit=1`);
    if (token !== hydrationToken || activeThreadId !== Number(threadId)) return;
    const endpoint = (thread.endpoints || []).find(x => Number(x.endpoint_id) === Number(preferredEndpointId)) || thread.endpoint || thread.endpoints?.[0];
    if (!endpoint) return;
    activeEndpoint = endpoint;
    applyChatContact(null, endpoint);
    const contact = await probeEndpoint(endpoint);
    if (token !== hydrationToken || activeThreadId !== Number(threadId)) return;
    applyChatContact(contact, endpoint);
  } catch (error) {
    console.debug?.('Chat contact hydration skipped', error);
  }
}

function resetChatContact() {
  hydrationToken += 1;
  activeThreadId = null;
  activeContact = null;
  activeEndpoint = null;
  presenceExpiredAt = 0;
  clearTimeout(presenceExpiryTimer);
  presenceExpiryTimer = null;
}

const profileRoot = document.querySelector('#profileContent');
if (profileRoot) {
  new MutationObserver(() => queueMicrotask(syncProfileContact)).observe(profileRoot, {childList:true});
}
const presence = document.querySelector('#chatPresence');
if (presence) {
  new MutationObserver(handlePresenceMutation).observe(presence, {childList:true, characterData:true, subtree:true});
}

document.addEventListener('ladies:messaging-thread-opened', event => hydrateChat(event.detail?.threadId));
document.addEventListener('ladies:messaging-thread-updated', event => {
  if (Number(event.detail?.threadId) === activeThreadId) hydrateChat(activeThreadId, activeEndpoint?.endpoint_id);
});
document.addEventListener('ladies:messaging-thread-closed', resetChatContact);
document.addEventListener('ladies:messaging-endpoint-selected', event => {
  if (!activeThreadId) return;
  const endpointId = Number(event.detail?.endpointId || 0) || null;
  if (endpointId) contactCache.delete(endpointId);
  activeContact = null;
  hydrateChat(activeThreadId, endpointId);
});
document.addEventListener('input', event => {
  if (event.target?.id === 'chatInput') {
    autoSizeComposer();
    syncChatViewport();
  }
});
document.addEventListener('focusin', event => {
  if (event.target?.id === 'chatInput') {
    syncChatViewport();
    setTimeout(syncChatViewport, 80);
    setTimeout(syncChatViewport, 240);
  }
});
document.addEventListener('submit', event => {
  if (event.target?.id === 'chatComposer') setTimeout(autoSizeComposer, 0);
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden && activeThreadId) {
    presenceExpiredAt = Date.now();
    restorePresenceLine();
  }
});
window.visualViewport?.addEventListener('resize', syncChatViewport);
window.visualViewport?.addEventListener('scroll', syncChatViewport);
window.addEventListener('orientationchange', () => setTimeout(syncChatViewport, 80));

syncProfileContact();
polishChatChrome();