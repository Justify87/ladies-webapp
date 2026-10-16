from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, got {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    target.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


GATEWAY = "ladies/messaging/gateway.py"
replace_once(
    GATEWAY,
    "CATCH_UP_TIMEOUT_SECONDS = 20.0\nCATCH_UP_MAX_RESTARTS = 2\n",
    "CATCH_UP_IDLE_TIMEOUT_SECONDS = 30.0\n"
    "CATCH_UP_HISTORY_IDLE_TIMEOUT_SECONDS = 135.0\n"
    "CATCH_UP_HARD_TIMEOUT_SECONDS = 240.0\n"
    "CATCH_UP_POLL_SECONDS = 1.0\n"
    "CATCH_UP_MAX_RESTARTS = 2\n",
)
replace_once(
    GATEWAY,
    "        self._catch_up_token = 0\n        self._catch_up_restarts = 0\n        self._provider_inbox = DurableProviderInbox(self.auth_dir.parent / \"pending-ingress\", event_handler)\n",
    "        self._catch_up_token = 0\n"
    "        self._catch_up_restarts = 0\n"
    "        self._catch_up_started_at = 0.0\n"
    "        self._catch_up_last_activity = 0.0\n"
    "        self._catch_up_history_waiting = False\n"
    "        self._catch_up_watchdog_token: int | None = None\n"
    "        self._provider_inbox = DurableProviderInbox(self.auth_dir.parent / \"pending-ingress\", event_handler)\n",
)
replace_once(
    GATEWAY,
    '''    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": bool(self.qr),
            "account": self.account,
            "last_connected_at": self.last_connected_at,
        }
''',
    '''    def status(self) -> dict[str, Any]:
        quarantined = self._provider_inbox.quarantined_count()
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": bool(self.qr),
            "account": self.account,
            "last_connected_at": self.last_connected_at,
            "ingress_quarantined": quarantined,
            "recovery_warning": (
                "Beschädigte oder veraltete WhatsApp-Eingangsdaten wurden isoliert."
                if quarantined else None
            ),
        }
''',
)
replace_once(
    GATEWAY,
    '''            self._catch_up_active = True
            self._transport_open = False
            self._pending_sync_ready = False
            self._catch_up_token += 1
            generation = self._generation
            try:
                replayed = self._provider_inbox.drain()
                if replayed:
                    LOGGER.info("Replayed %s durable WhatsApp ingress event(s) before reconnect", replayed)
            except Exception:
''',
    '''            self._catch_up_active = True
            self._transport_open = False
            self._pending_sync_ready = False
            self._catch_up_token += 1
            now = time.monotonic()
            self._catch_up_started_at = now
            self._catch_up_last_activity = now
            self._catch_up_history_waiting = False
            self._catch_up_watchdog_token = None
            generation = self._generation
            try:
                replayed, quarantined = self._provider_inbox.drain()
                if replayed:
                    LOGGER.info("Replayed %s durable WhatsApp ingress event(s) before reconnect", replayed)
                if quarantined:
                    LOGGER.error("Quarantined %s corrupt WhatsApp ingress event(s) during recovery", quarantined)
            except Exception:
''',
)

replace_between(
    GATEWAY,
    "    def _begin_catch_up(self) -> int:\n",
    "    def _handle(self, event: dict[str, Any]) -> None:\n",
    '''    def _touch_catch_up(self, *, history_waiting: bool | None = None) -> None:
        with self._lock:
            if not self._catch_up_active:
                return
            self._catch_up_last_activity = time.monotonic()
            if history_waiting is not None:
                self._catch_up_history_waiting = history_waiting

    def _begin_catch_up(self) -> int:
        with self._lock:
            self._catch_up_active = True
            self._transport_open = False
            self._pending_sync_ready = False
            self._catch_up_token += 1
            now = time.monotonic()
            self._catch_up_started_at = now
            self._catch_up_last_activity = now
            self._catch_up_history_waiting = False
            self._catch_up_watchdog_token = None
            return self._catch_up_token

    def _finish_catch_up(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._catch_up_active or not self._transport_open or not self._pending_sync_ready:
                return None
            replayed, quarantined = self._provider_inbox.drain()
            if self._provider_inbox.pending_count():
                return None
            if replayed:
                LOGGER.info("Persisted %s deferred WhatsApp catch-up event(s)", replayed)
            if quarantined:
                LOGGER.error("Quarantined %s corrupt WhatsApp ingress event(s) at catch-up barrier", quarantined)
            previous = self.state
            self._catch_up_active = False
            self._catch_up_watchdog_token = None
            self.state = "CONNECTED"
            self.detail = None
            self._restart_attempt = 0
            self._catch_up_restarts = 0
            self.qr = None
            self.last_connected_at = time.time()
            if previous != "CONNECTED":
                LOGGER.info("WhatsApp offline catch-up complete; connection is ready")
            return {
                "type": "connection",
                "state": "CONNECTED",
                "detail": None,
                "account": self.account,
            }

    def _arm_catch_up_watchdog(self, proc: subprocess.Popen[str] | None, token: int) -> None:
        if proc is None:
            return
        with self._lock:
            if not self._catch_up_active or token != self._catch_up_token:
                return
            if self._catch_up_watchdog_token == token:
                return
            self._catch_up_watchdog_token = token
        threading.Thread(
            target=self._watch_catch_up,
            args=(proc, token),
            daemon=True,
            name="wa-catch-up",
        ).start()

    def _watch_catch_up(self, proc: subprocess.Popen[str], token: int) -> None:
        reason = "idle"
        while True:
            time.sleep(CATCH_UP_POLL_SECONDS)
            with self._lock:
                if (
                    not self._catch_up_active
                    or token != self._catch_up_token
                    or proc is not self.proc
                    or proc.poll() is not None
                    or self._stopping
                ):
                    return
                now = time.monotonic()
                idle_limit = (
                    CATCH_UP_HISTORY_IDLE_TIMEOUT_SECONDS
                    if self._catch_up_history_waiting
                    else CATCH_UP_IDLE_TIMEOUT_SECONDS
                )
                idle_expired = now - self._catch_up_last_activity >= idle_limit
                hard_expired = now - self._catch_up_started_at >= CATCH_UP_HARD_TIMEOUT_SECONDS
                if not idle_expired and not hard_expired:
                    continue
                reason = "hard_limit" if hard_expired else "idle"
                self._catch_up_watchdog_token = None
                break

        try:
            completed = self._finish_catch_up()
        except Exception:
            completed = None
            LOGGER.exception("WhatsApp catch-up persistence retry failed")
        if completed is not None:
            self.event_handler(completed)
            return

        with self._lock:
            if (
                not self._catch_up_active
                or token != self._catch_up_token
                or proc is not self.proc
                or proc.poll() is not None
                or self._stopping
            ):
                return
            if self._catch_up_restarts < CATCH_UP_MAX_RESTARTS:
                self._catch_up_restarts += 1
                attempt = self._catch_up_restarts
                self._transport_open = False
                self._pending_sync_ready = False
                self._set_state(
                    "RECONNECTING",
                    f"WhatsApp-Synchronisierung wird erneut gestartet ({attempt}/{CATCH_UP_MAX_RESTARTS})",
                )
                restart = True
            else:
                restart = False

        if restart:
            LOGGER.warning("WhatsApp catch-up stalled reason=%s; restarting worker", reason)
            try:
                proc.terminate()
            except Exception:
                LOGGER.warning("Could not restart WhatsApp worker after catch-up stall", exc_info=True)
            return

        self.stop()
        self._set_state(
            "ERROR",
            "WhatsApp-Synchronisierung konnte nicht bestätigt werden. Bitte erneut verbinden.",
        )

''',
)
replace_once(
    GATEWAY,
    '''        if event_type in DURABLE_EVENT_TYPES:
            with self._lock:
                if self._catch_up_active:
                    try:
                        self._provider_inbox.deliver(event)
                    except Exception:
                        self.state = "CONNECTING"
                        self.detail = "Ausstehende WhatsApp-Nachrichten werden erneut gespeichert."
                        LOGGER.exception("Deferred WhatsApp provider event persistence failed")
                    return
''',
    '''        if event_type in DURABLE_EVENT_TYPES:
            with self._lock:
                if self._catch_up_active:
                    self._catch_up_last_activity = time.monotonic()
                    try:
                        self._provider_inbox.deliver(event)
                    except Exception:
                        self.state = "CONNECTING"
                        self.detail = "Ausstehende WhatsApp-Nachrichten werden erneut gespeichert."
                        LOGGER.exception("Deferred WhatsApp provider event persistence failed")
                    else:
                        self._catch_up_last_activity = time.monotonic()
                    return
''',
)
replace_once(
    GATEWAY,
    '''            if state == "CONNECTED":
                self._transport_open = True
                self.qr = None
                self.account = event.get("account")
''',
    '''            if state == "CONNECTED":
                with self._lock:
                    self._transport_open = True
                    self.qr = None
                    self.account = event.get("account")
                    if self._catch_up_active:
                        self._catch_up_last_activity = time.monotonic()
''',
)
replace_once(
    GATEWAY,
    '''        elif event_type == "sync":
            sync_state = str(event.get("state") or "").upper()
            if sync_state == "READY" and event.get("is_latest") is not False:
                self._pending_sync_ready = True
''',
    '''        elif event_type == "sync":
            sync_state = str(event.get("state") or "").upper()
            with self._lock:
                if self._catch_up_active:
                    self._catch_up_last_activity = time.monotonic()
                    if sync_state in {"HISTORY", "HISTORY_WAIT"}:
                        self._catch_up_history_waiting = True
                    elif sync_state == "PENDING":
                        self._catch_up_history_waiting = bool(event.get("expected_history"))
                    elif sync_state == "READY":
                        self._catch_up_history_waiting = False
            if sync_state == "READY" and event.get("is_latest") is not False:
                self._pending_sync_ready = True
''',
)
replace_once(
    GATEWAY,
    '''            self._catch_up_active = False
            self._transport_open = False
            self._pending_sync_ready = False
            self._catch_up_token += 1
            proc = self.proc
''',
    '''            self._catch_up_active = False
            self._transport_open = False
            self._pending_sync_ready = False
            self._catch_up_history_waiting = False
            self._catch_up_watchdog_token = None
            self._catch_up_token += 1
            proc = self.proc
''',
)
# Add explicit reset/account-switch isolation without coupling lifecycle to inbox internals.
gateway = Path(GATEWAY)
text = gateway.read_text(encoding="utf-8")
if "    def discard_pending_ingress(" not in text:
    text = text.rstrip() + '''\n\n    def discard_pending_ingress(self, reason: str = "reset") -> int:\n        with self._lock:\n            return self._provider_inbox.abandon(reason)\n'''
    gateway.write_text(text + "\n", encoding="utf-8")


WORKER = "ladies/messaging/whatsapp_worker/worker_v2.mjs"
replace_once(
    WORKER,
    "const HISTORY_BATCH_SIZE = 100\n",
    "const HISTORY_BATCH_SIZE = 100\nconst INITIAL_SYNC_EMPTY_FALLBACK_MS = 22_000\n",
)
replace_once(
    WORKER,
    '''  if (update.connection !== 'close') return

  const code = statusCode(update.lastDisconnect?.error)
''',
    '''  if (update.connection !== 'close') return
  if (context.noHistoryTimer) {
    clearTimeout(context.noHistoryTimer)
    context.noHistoryTimer = null
  }

  const code = statusCode(update.lastDisconnect?.error)
''',
)
insert_marker = "async function handleConnectionUpdate(current, update) {\n"
worker = Path(WORKER)
text = worker.read_text(encoding="utf-8")
helpers = '''function emitSyncReady(current) {
  const context = socketContexts.get(current)
  if (!context || current !== sock || !context.pendingReceived || context.readyEmitted || context.catchUpFailed) return
  context.readyEmitted = true
  if (context.noHistoryTimer) {
    clearTimeout(context.noHistoryTimer)
    context.noHistoryTimer = null
  }
  emit({ type: 'sync', state: 'READY', progress: 100, is_latest: true })
}

function scheduleReadyAfterBaileysFlush(current) {
  const context = socketContexts.get(current)
  if (!context || context.readyScheduled || context.readyEmitted || context.catchUpFailed) return
  context.readyScheduled = true
  setTimeout(() => {
    context.readyScheduled = false
    if (current !== sock || context.readyEmitted || context.catchUpFailed) return
    const tail = context.eventTail
    context.eventTail = tail.then(() => emitSyncReady(current))
  }, 0)
}

'''
if helpers not in text:
    text = text.replace(insert_marker, helpers + insert_marker, 1)
    worker.write_text(text, encoding="utf-8")

replace_between(
    WORKER,
    "async function processEvents(current, events, saveCreds) {\n",
    "async function connect() {\n",
    '''async function processEvents(current, events, saveCreds) {
  const context = socketContexts.get(current)
  if (events['creds.update']) await saveCreds()
  const connectionUpdate = events['connection.update']
  if (connectionUpdate) await handleConnectionUpdate(current, connectionUpdate)

  const pendingReceivedNow = connectionUpdate?.receivedPendingNotifications === true && current === sock && context
  if (pendingReceivedNow) {
    context.pendingReceived = true
    const expectedHistory = context.accountSyncCounterAtStart <= 0
    emit({ type: 'sync', state: 'PENDING', progress: null, is_latest: false, expected_history: expectedHistory })
  }

  const historyStatus = events['messaging-history.status']
  if (historyStatus && context) {
    context.historyStarted = true
    if (context.noHistoryTimer) {
      clearTimeout(context.noHistoryTimer)
      context.noHistoryTimer = null
    }
    emit({
      type: 'sync',
      state: 'HISTORY_WAIT',
      progress: null,
      is_latest: false,
      history_status: historyStatus.status || null,
      explicit: Boolean(historyStatus.explicit),
      sync_type: historyStatus.syncType ?? null,
    })
  }

  const historySet = events['messaging-history.set']
  if (historySet) {
    if (context) {
      context.historyStarted = true
      if (context.noHistoryTimer) {
        clearTimeout(context.noHistoryTimer)
        context.noHistoryTimer = null
      }
    }
    await emitHistory(current, historySet)
  }

  const upsert = events['messages.upsert']
  if (upsert) {
    const rehydrated = upsert.type !== 'notify'
    for (const msg of upsert.messages || []) {
      await rememberRawMessage(msg)
      const providerId = messageIdentity(msg.key)
      if (msg.key?.fromMe && providerId && locallySentIds.has(providerId)) continue
      if (!rehydrated && msg.key?.fromMe && localSendsInFlight > 0 && providerId) {
        pendingOwnMessages.set(providerId, { current, msg })
        continue
      }
      await emitMessage(current, msg, { rehydrated, downloadMedia: !rehydrated })
    }
  }

  for (const item of events['messages.update'] || []) {
    if (localSendsInFlight > 0) pendingMessageUpdates.push(item)
    else emitMessageUpdate(item)
  }

  const deletedMessages = events['messages.delete']
  for (const key of deletedMessages?.keys || []) {
    const providerId = messageIdentity(key)
    if (!providerId) continue
    emit({
      type: 'message_content_update',
      payload: {
        provider_message_id: providerId,
        provider_key: providerKey(key),
        action: 'DELETE',
      },
    })
  }

  for (const item of events['messages.reaction'] || []) {
    emit({
      type: 'reaction',
      payload: {
        target_provider_message_id: messageIdentity(item.key),
        target_key: providerKey(item.key),
        reaction_key: providerKey(item.reaction?.key),
        emoji: item.reaction?.text || '',
        from_me: Boolean(item.reaction?.key?.fromMe),
        timestamp_ms: Number(item.reaction?.senderTimestampMs || Date.now()),
      },
    })
  }

  const contactRows = [...(events['contacts.upsert'] || []), ...(events['contacts.update'] || [])]
  if (contactRows.length) {
    const items = await compactPeers(current, contactRows)
    if (items.length) emit({ type: 'provider_contacts', items })
  }

  const chatRows = [...(events['chats.upsert'] || []), ...(events['chats.update'] || [])]
  if (chatRows.length) {
    const items = await compactPeers(current, chatRows, { chat: true })
    if (items.length) emit({ type: 'provider_chats', items })
  }

  const chatDeletes = (events['chats.delete'] || []).filter(isDirectJid)
  if (chatDeletes.length) {
    emit({ type: 'provider_chat_deleted', addresses: chatDeletes })
  }

  const presence = events['presence.update']
  if (presence) {
    const jid = presence.id
    for (const [participant, value] of Object.entries(presence.presences || {})) {
      emit({
        type: 'presence',
        payload: {
          jid,
          participant,
          state: value?.lastKnownPresence || null,
          last_seen: value?.lastSeen || null,
        },
      })
    }
  }

  if (pendingReceivedNow) {
    if (context.accountSyncCounterAtStart > 0) {
      scheduleReadyAfterBaileysFlush(current)
    } else if (!context.historyStarted && !context.noHistoryTimer) {
      context.noHistoryTimer = setTimeout(() => {
        context.noHistoryTimer = null
        if (context.historyStarted || current !== sock || context.readyEmitted || context.catchUpFailed) return
        scheduleReadyAfterBaileysFlush(current)
      }, INITIAL_SYNC_EMPTY_FALLBACK_MS)
      context.noHistoryTimer.unref?.()
    }
  }

  if (context?.pendingReceived && historySet?.isLatest) emitSyncReady(current)
  if (context?.pendingReceived && historyStatus?.status === 'paused') emitSyncReady(current)
}

''',
)
replace_once(
    WORKER,
    '''    sock = current
    socketContexts.set(current, { registeredAtStart: Boolean(state.creds?.registered), qrSeen: false })
    current.ev.process(async events => {
      try {
        await processEvents(current, events, saveCreds)
      } catch (error) {
        logger.error({ err: errorText(error) }, 'Baileys event batch failed')
        emit({ type: 'error', code: 'EVENT_BATCH_FAILED', message: errorText(error) })
      }
    })
''',
    '''    sock = current
    const context = {
      registeredAtStart: Boolean(state.creds?.registered),
      qrSeen: false,
      accountSyncCounterAtStart: Number(state.creds?.accountSyncCounter || 0),
      pendingReceived: false,
      historyStarted: false,
      readyScheduled: false,
      readyEmitted: false,
      catchUpFailed: false,
      noHistoryTimer: null,
      eventTail: Promise.resolve(),
    }
    socketContexts.set(current, context)
    current.ev.process(events => {
      const task = context.eventTail.then(() => processEvents(current, events, saveCreds))
      context.eventTail = task.catch(error => {
        if (context.pendingReceived || context.historyStarted) context.catchUpFailed = true
        logger.error({ err: errorText(error) }, 'Baileys event batch failed')
        emit({ type: 'error', code: 'EVENT_BATCH_FAILED', message: errorText(error) })
      })
      return context.eventTail
    })
''',
)
