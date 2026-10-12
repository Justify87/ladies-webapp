'use strict';

(() => {
  const APP_VERSION = '9.2.1';
  document.title = `Ladies Explorer V${APP_VERSION}`;
  document.querySelectorAll('.version-pill').forEach(el => { el.textContent = `V${APP_VERSION}`; });

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = name => `<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;

  const style = document.createElement('style');
  style.textContent = `
    .profile-chat-section{display:none!important}
    .profile-big-action[data-contact]{gap:.35rem}
    .profile-big-action[data-contact] .profile-contact-copy{display:flex;min-width:0;flex-direction:column;align-items:center;line-height:1.15}
    .profile-big-action[data-contact] .profile-contact-copy b{font:inherit;font-weight:700}
    .profile-big-action[data-contact] .profile-contact-copy small{display:block;max-width:100%;margin-top:.18rem;font-size:.68rem;font-weight:600;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  `;
  document.head.appendChild(style);

  async function summaryForCurrentProfile() {
    const detail = window.state?.detail;
    const personId = detail?.person?.person_id;
    if (!personId) return null;
    const listing = detail.listings?.find(x => String(x.advert_id) === String(window.state.detailAdvertId)) || detail.listings?.[0];
    const qs = listing?.listing_id ? `?listing_id=${encodeURIComponent(listing.listing_id)}` : '';
    const response = await fetch(`/api/messaging/person/${personId}${qs}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function openInternalChat() {
    const trigger = document.querySelector('#profileContent .profile-chat-section #profileOpenChat');
    if (trigger) {
      trigger.click();
      return true;
    }
    return false;
  }

  function renderContactButton(button, phone) {
    const html = `<span>${icon('message')}</span><span class="profile-contact-copy"><b>Chat öffnen</b><small>${esc(phone || 'Keine WhatsApp-Nummer')}</small></span>`;
    if (button.innerHTML !== html) button.innerHTML = html;
    button.setAttribute('aria-label', phone ? `Chat öffnen, ${phone}` : 'Chat öffnen');
  }

  async function syncProfileContact() {
    const root = document.querySelector('#profileContent');
    const detail = window.state?.detail;
    const personId = detail?.person?.person_id;
    const button = root?.querySelector('[data-contact]');
    if (!root || !button || !personId) return;

    const legacy = root.querySelector('.profile-chat-section');
    if (legacy) legacy.style.display = 'none';

    if (button.dataset.messagingPerson === String(personId)) return;
    button.dataset.messagingPerson = String(personId);
    renderContactButton(button, 'Nummer wird geladen …');

    try {
      const summary = await summaryForCurrentProfile();
      if (button.dataset.messagingPerson !== String(personId)) return;
      const endpoint = summary?.selected_endpoint;
      renderContactButton(button, endpoint?.display_phone || endpoint?.normalized_phone || 'Keine WhatsApp-Nummer');
    } catch {
      if (button.dataset.messagingPerson === String(personId)) renderContactButton(button, 'Chat derzeit nicht verfügbar');
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
    if (!openInternalChat()) {
      setTimeout(() => {
        if (!openInternalChat()) window.toast?.('Chat wird noch vorbereitet.');
      }, 60);
    }
  }, true);

  let queued = false;
  const resync = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      syncProfileContact();
      cleanMessagesUi();
    });
  };
  new MutationObserver(resync).observe(document.body, {childList:true, subtree:true});
  resync();
})();
