'use strict';

import { openPerson, toast } from '../app.js';

(() => {
  let currentChatThreadId = null;
  let profileOpenedFromChat = false;

  function rememberThreadFromClick(event) {
    const row = event.target.closest?.('#threadList [data-thread-id]');
    if (row?.dataset.threadId) currentChatThreadId = Number(row.dataset.threadId) || null;
    const profileContact = event.target.closest?.('#profileContent [data-contact]');
    if (profileContact?.dataset.messagingThread) {
      currentChatThreadId = Number(profileContact.dataset.messagingThread) || currentChatThreadId;
    }
  }

  async function resolveCurrentThread() {
    if (currentChatThreadId) {
      try {
        return await fetch(`/api/messaging/threads/${encodeURIComponent(currentChatThreadId)}?limit=1`)
          .then(async response => response.ok ? response.json() : null);
      } catch {
        return null;
      }
    }
    const displayName = document.querySelector('#chatName')?.textContent?.trim();
    if (!displayName) return null;
    try {
      const response = await fetch('/api/messaging/threads?filter=ALL');
      if (!response.ok) return null;
      const data = await response.json();
      const matches = (data.items || []).filter(
        item => String(item.display_name || '').trim() === displayName,
      );
      if (matches.length !== 1) return null;
      currentChatThreadId = Number(matches[0].thread_id) || null;
      if (!currentChatThreadId) return null;
      const detail = await fetch(`/api/messaging/threads/${encodeURIComponent(currentChatThreadId)}?limit=1`);
      return detail.ok ? detail.json() : null;
    } catch {
      return null;
    }
  }

  async function openProfileFromChat() {
    const layer = document.querySelector('#messagingChatLayer');
    if (!layer || layer.classList.contains('hidden')) return;
    const thread = await resolveCurrentThread();
    if (!thread?.person_id) {
      toast('Für diesen Chat ist noch kein Personenprofil zugeordnet.');
      return;
    }
    profileOpenedFromChat = true;
    document.body.classList.add('chat-profile-open');
    try {
      await openPerson(Number(thread.person_id));
      const profileLayer = document.querySelector('#profileLayer');
      const showTop = () => {
        if (profileOpenedFromChat && profileLayer && !profileLayer.classList.contains('hidden')) {
          profileLayer.scrollTop = 0;
        }
      };
      requestAnimationFrame(showTop);
      setTimeout(showTop, 80);
      setTimeout(showTop, 220);
      if (!profileLayer || profileLayer.classList.contains('hidden')) {
        throw new Error('Profil konnte nicht geöffnet werden');
      }
    } catch (error) {
      profileOpenedFromChat = false;
      document.body.classList.remove('chat-profile-open');
      toast(error?.message || 'Profil konnte nicht geöffnet werden');
    }
  }

  function decorateChatIdentity() {
    const avatar = document.querySelector('#chatAvatar');
    const identity = document.querySelector('#chatIdentity');
    if (avatar) {
      avatar.setAttribute('role', 'button');
      avatar.setAttribute('tabindex', '0');
      avatar.setAttribute('aria-label', 'Profil öffnen');
      avatar.setAttribute('title', 'Profil öffnen');
    }
    if (identity) {
      identity.setAttribute('aria-label', 'Profil öffnen');
      identity.setAttribute('title', 'Profil öffnen');
    }
  }

  document.addEventListener('click', event => {
    rememberThreadFromClick(event);
    const target = event.target.closest?.('#chatAvatar,#chatIdentity');
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    openProfileFromChat();
  }, true);

  document.addEventListener('keydown', event => {
    if (!event.target?.matches?.('#chatAvatar')) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    openProfileFromChat();
  }, true);

  document.addEventListener('click', event => {
    if (!profileOpenedFromChat || !event.target.closest?.('[data-profile-close]')) return;
    setTimeout(() => {
      profileOpenedFromChat = false;
      document.body.classList.remove('chat-profile-open');
      document.querySelector('#chatIdentity')?.focus({preventScroll:true});
    }, 0);
  }, true);

  const observer = new MutationObserver(decorateChatIdentity);
  observer.observe(document.body, {childList:true, subtree:true});
  decorateChatIdentity();
})();