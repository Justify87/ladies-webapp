from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old[:80]!r}; got {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Durable, at-least-once ingress for catch-up message/history events.
Path("ladies/messaging/provider_inbox.py").write_text(r'''from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

DURABLE_EVENT_TYPES = {"message", "history_batch"}


class DurableProviderInbox:
    """Tiny disk-backed at-least-once queue for provider events.

    Catch-up events are persisted before the canonical messaging service sees
    them. The file is removed only after the service returns successfully. A
    crash or transient database failure therefore turns into a replay on the
    next drain instead of a silently lost WhatsApp message. Canonical provider
    IDs make replay idempotent in the SQL store.
    """

    def __init__(self, root: str | Path, handler: Callable[[dict[str, Any]], None]):
        self.root = Path(root)
        self.handler = handler
        self._lock = threading.RLock()

    def _write(self, event: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        target = self.root / name
        temporary = self.root / f".{name}.{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target

    def _files(self) -> list[Path]:
        try:
            return sorted(path for path in self.root.glob("*.json") if path.is_file())
        except OSError:
            return []

    def pending_count(self) -> int:
        with self._lock:
            return len(self._files())

    def deliver(self, event: dict[str, Any]) -> None:
        with self._lock:
            pending = self._write(event)
            try:
                self.handler(event)
            except Exception:
                # Leave the durable copy in place for the next drain/restart.
                raise
            else:
                try:
                    pending.unlink()
                except FileNotFoundError:
                    pass

    def drain(self) -> int:
        delivered = 0
        with self._lock:
            for path in self._files():
                event = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(event, dict):
                    raise ValueError(f"invalid pending provider event: {path.name}")
                self.handler(event)
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                delivered += 1
        return delivered
''', encoding="utf-8")

# Gateway: provider catch-up is transport-open + pending-notification barrier +
# successfully drained durable ingress. Add bounded recovery when Baileys never
# emits receivedPendingNotifications.
replace_once(
    "ladies/messaging/gateway.py",
    "from typing import Any, Callable\n\nLOGGER = logging.getLogger(\"ladies_explorer.messaging.gateway\")",
    "from typing import Any, Callable\n\nfrom .provider_inbox import DURABLE_EVENT_TYPES, DurableProviderInbox\n\nLOGGER = logging.getLogger(\"ladies_explorer.messaging.gateway\")",
)
replace_once(
    "ladies/messaging/gateway.py",
    "APP_STATE_MISSING_KEY_RE = re.compile(r\"failed to find key .* to decode mutation\", re.I)\n",
    "APP_STATE_MISSING_KEY_RE = re.compile(r\"failed to find key .* to decode mutation\", re.I)\nCATCH_UP_TIMEOUT_SECONDS = 20.0\nCATCH_UP_MAX_RESTARTS = 2\n",
)
replace_once(
    "ladies/messaging/gateway.py",
    "        self._app_state_recovery_running = False\n        self.state = \"NOT_CONFIGURED\"",
    "        self._app_state_recovery_running = False\n        self._transport_open = False\n        self._pending_sync_ready = False\n        self._catch_up_active = False\n        self._catch_up_token = 0\n        self._catch_up_restarts = 0\n        self._provider_inbox = DurableProviderInbox(self.auth_dir.parent / \"pending-ingress\", event_handler)\n        self.state = \"NOT_CONFIGURED\"",
)
replace_once(
    "ladies/messaging/gateway.py",
    "            if not self._desired_running:\n                self._desired_running = True\n                self._generation += 1\n            self._stopping = False\n        self.start()",
    "            if not self._desired_running:\n                self._desired_running = True\n                self._generation += 1\n                self._catch_up_restarts = 0\n            self._stopping = False\n        self.start()",
)
replace_once(
    "ladies/messaging/gateway.py",
    "            self.auth_dir.mkdir(parents=True, exist_ok=True)\n            self._stopping = False\n            generation = self._generation\n            self._set_state(\"CONNECTING\")",
    "            self.auth_dir.mkdir(parents=True, exist_ok=True)\n            self._stopping = False\n            self._catch_up_active = True\n            self._transport_open = False\n            self._pending_sync_ready = False\n            self._catch_up_token += 1\n            generation = self._generation\n            try:\n                replayed = self._provider_inbox.drain()\n                if replayed:\n                    LOGGER.info(\"Replayed %s durable WhatsApp ingress event(s) before reconnect\", replayed)\n            except Exception:\n                self._set_state(\"ERROR\", \"Ausstehende WhatsApp-Nachrichten konnten nicht gespeichert werden\")\n                LOGGER.exception(\"Could not replay durable WhatsApp ingress\")\n                return\n            self._set_state(\"CONNECTING\")",
)
old_handle = '''    def _handle(self, event: dict[str, Any]) -> None:\n        event_type = event.get("type")\n        if event_type == "qr":\n            self.qr = event.get("value")\n            self._set_state("QR_REQUIRED")\n        elif event_type == "connection":\n            state = str(event.get("state") or "").upper()\n            if state in ALLOWED_STATES:\n                previous = self.state\n                self.state = state\n                self.detail = event.get("detail")\n                if state in {"ERROR", "LOGGED_OUT"}:\n                    LOGGER.warning("WhatsApp state %s -> %s detail=%s", previous, state, self.detail or "-")\n                elif state == "CONNECTED" and previous != state:\n                    LOGGER.info("WhatsApp connected")\n            if state == "CONNECTED":\n                self._restart_attempt = 0\n                self.qr = None\n                self.account = event.get("account")\n                self.last_connected_at = time.time()\n        request_id = event.get("id")\n        target = None\n        if request_id:\n            with self._lock:\n                target = self._pending.get(request_id)\n        if target is not None:\n            try:\n                target.put_nowait(event)\n            except queue.Full:\n                pass\n        self.event_handler(event)\n'''
new_handle = '''    def _begin_catch_up(self) -> int:\n        self._catch_up_active = True\n        self._transport_open = False\n        self._pending_sync_ready = False\n        self._catch_up_token += 1\n        return self._catch_up_token\n\n    def _finish_catch_up(self) -> dict[str, Any] | None:\n        if not self._catch_up_active or not self._transport_open or not self._pending_sync_ready:\n            return None\n        replayed = self._provider_inbox.drain()\n        if self._provider_inbox.pending_count():\n            return None\n        if replayed:\n            LOGGER.info("Persisted %s deferred WhatsApp catch-up event(s)", replayed)\n        previous = self.state\n        self._catch_up_active = False\n        self.state = "CONNECTED"\n        self.detail = None\n        self._restart_attempt = 0\n        self._catch_up_restarts = 0\n        self.qr = None\n        self.last_connected_at = time.time()\n        if previous != "CONNECTED":\n            LOGGER.info("WhatsApp offline catch-up complete; connection is ready")\n        return {\n            "type": "connection",\n            "state": "CONNECTED",\n            "detail": None,\n            "account": self.account,\n        }\n\n    def _arm_catch_up_watchdog(self, proc: subprocess.Popen[str] | None, token: int) -> None:\n        if proc is None:\n            return\n        threading.Thread(\n            target=self._watch_catch_up,\n            args=(proc, token),\n            daemon=True,\n            name="wa-catch-up",\n        ).start()\n\n    def _watch_catch_up(self, proc: subprocess.Popen[str], token: int) -> None:\n        time.sleep(CATCH_UP_TIMEOUT_SECONDS)\n        with self._lock:\n            if (\n                not self._catch_up_active\n                or token != self._catch_up_token\n                or proc is not self.proc\n                or proc.poll() is not None\n                or self._stopping\n            ):\n                return\n        try:\n            completed = self._finish_catch_up()\n        except Exception:\n            completed = None\n            LOGGER.exception("WhatsApp catch-up persistence retry failed")\n        if completed is not None:\n            self.event_handler(completed)\n            return\n\n        with self._lock:\n            if (\n                not self._catch_up_active\n                or token != self._catch_up_token\n                or proc is not self.proc\n                or proc.poll() is not None\n                or self._stopping\n            ):\n                return\n            if self._catch_up_restarts < CATCH_UP_MAX_RESTARTS:\n                self._catch_up_restarts += 1\n                attempt = self._catch_up_restarts\n                self._transport_open = False\n                self._pending_sync_ready = False\n                self._set_state(\n                    "RECONNECTING",\n                    f"WhatsApp-Synchronisierung wird erneut gestartet ({attempt}/{CATCH_UP_MAX_RESTARTS})",\n                )\n                restart = True\n            else:\n                restart = False\n\n        if restart:\n            try:\n                proc.terminate()\n            except Exception:\n                LOGGER.warning("Could not restart WhatsApp worker after catch-up timeout", exc_info=True)\n            return\n\n        # Never expose an unverified transport as CONNECTED. Stop automatic\n        # restarts after bounded recovery and require an explicit retry.\n        self.stop()\n        self._set_state(\n            "ERROR",\n            "WhatsApp-Synchronisierung konnte nicht bestätigt werden. Bitte erneut verbinden.",\n        )\n\n    def _handle(self, event: dict[str, Any]) -> None:\n        event_type = event.get("type")\n\n        # During reconnect, message/history delivery is at-least-once: write the\n        # provider event to disk first and remove it only after canonical SQL\n        # persistence succeeds. Service exceptions deliberately propagate here.\n        if self._catch_up_active and event_type in DURABLE_EVENT_TYPES:\n            try:\n                self._provider_inbox.deliver(event)\n            except Exception:\n                self.state = "CONNECTING"\n                self.detail = "Ausstehende WhatsApp-Nachrichten werden erneut gespeichert."\n                LOGGER.exception("Deferred WhatsApp provider event persistence failed")\n            return\n\n        forwarded_event = event\n        synthetic_events: list[dict[str, Any]] = []\n        if event_type == "qr":\n            self.qr = event.get("value")\n            self._set_state("QR_REQUIRED")\n        elif event_type == "connection":\n            state = str(event.get("state") or "").upper()\n            if state == "CONNECTED":\n                self._transport_open = True\n                self.qr = None\n                self.account = event.get("account")\n                try:\n                    completed = self._finish_catch_up()\n                except Exception:\n                    completed = None\n                    LOGGER.exception("WhatsApp catch-up persistence failed at transport-open")\n                if completed is not None:\n                    forwarded_event = completed\n                else:\n                    self.state = "CONNECTING"\n                    self.detail = "WhatsApp verbunden; ausstehende Nachrichten werden synchronisiert."\n                    forwarded_event = {**event, "state": "CONNECTING", "detail": self.detail}\n                    synthetic_events.append({\n                        "type": "sync",\n                        "state": "CATCHING_UP",\n                        "progress": None,\n                        "is_latest": False,\n                    })\n                    self._arm_catch_up_watchdog(self.proc, self._catch_up_token)\n            elif state in ALLOWED_STATES:\n                previous = self.state\n                self.state = state\n                self.detail = event.get("detail")\n                if state == "RECONNECTING":\n                    self._begin_catch_up()\n                elif state in {"DISCONNECTED", "LOGGED_OUT", "ERROR", "DEPENDENCY_MISSING"}:\n                    self._catch_up_active = False\n                    self._transport_open = False\n                    self._pending_sync_ready = False\n                    self._catch_up_token += 1\n                if state in {"ERROR", "LOGGED_OUT"}:\n                    LOGGER.warning("WhatsApp state %s -> %s detail=%s", previous, state, self.detail or "-")\n        elif event_type == "sync":\n            sync_state = str(event.get("state") or "").upper()\n            if sync_state == "READY" and event.get("is_latest") is not False:\n                self._pending_sync_ready = True\n                try:\n                    completed = self._finish_catch_up()\n                except Exception:\n                    completed = None\n                    self.state = "CONNECTING"\n                    self.detail = "Ausstehende WhatsApp-Nachrichten werden erneut gespeichert."\n                    forwarded_event = {\n                        "type": "sync",\n                        "state": "CATCHING_UP",\n                        "progress": None,\n                        "is_latest": False,\n                    }\n                    LOGGER.exception("WhatsApp catch-up persistence failed at READY barrier")\n                if completed is not None:\n                    synthetic_events.append(completed)\n\n        request_id = event.get("id")\n        target = None\n        if request_id:\n            with self._lock:\n                target = self._pending.get(request_id)\n        if target is not None:\n            try:\n                target.put_nowait(event)\n            except queue.Full:\n                pass\n\n        self.event_handler(forwarded_event)\n        for synthetic in synthetic_events:\n            self.event_handler(synthetic)\n'''
replace_once("ladies/messaging/gateway.py", old_handle, new_handle)
replace_once(
    "ladies/messaging/gateway.py",
    "            self._restart_attempt = 0\n            proc = self.proc",
    "            self._restart_attempt = 0\n            self._catch_up_active = False\n            self._transport_open = False\n            self._pending_sync_ready = False\n            self._catch_up_token += 1\n            proc = self.proc",
)

# Saved-session resume: establish desired-running synchronously and tolerate a
# few transient worker/IPC startup failures without blocking reset/disconnect.
replace_once(
    "ladies/messaging/connection_lifecycle.py",
    "LOGGER = logging.getLogger(\"ladies_explorer.messaging.connection\")\n",
    "LOGGER = logging.getLogger(\"ladies_explorer.messaging.connection\")\nAUTO_RESUME_DELAYS = (0.0, 1.0, 2.0)\n",
)
replace_once(
    "ladies/messaging/connection_lifecycle.py",
    '''        if self.has_saved_session():\n            generation = self._intent_generation\n            threading.Thread(\n                target=self._resume_saved_session,\n                args=(generation,),\n                daemon=True,\n                name="wa-resume-session",\n            ).start()\n''',
    '''        if self.has_saved_session():\n            generation = self._intent_generation\n            gateway = self.service.gateway\n            try:\n                gateway.activate()\n            except Exception:\n                gateway.stop()\n                gateway.state = "ERROR"\n                gateway.detail = "Gespeicherte WhatsApp-Verbindung konnte nicht gestartet werden."\n                LOGGER.warning("Could not activate registered WhatsApp session", exc_info=True)\n                return\n            threading.Thread(\n                target=self._resume_saved_session,\n                args=(generation,),\n                daemon=True,\n                name="wa-resume-session",\n            ).start()\n''',
)
replace_once(
    "ladies/messaging/connection_lifecycle.py",
    '''    def _resume_saved_session(self, generation: int) -> None:\n        # Serialize resume with explicit connect/disconnect/reset. If reset wins\n        # first, the generation changes and this stale task becomes a no-op. If\n        # resume wins first, reset waits and then deterministically wins last.\n        with self._lock:\n            if generation != self._intent_generation or not self.has_saved_session():\n                return\n            gateway = self.service.gateway\n            ok, detail = gateway.dependency_status()\n            if not ok:\n                gateway.state = "DEPENDENCY_MISSING"\n                gateway.detail = detail\n                return\n            try:\n                gateway.command("connect", timeout=15)\n            except Exception:\n                if generation == self._intent_generation:\n                    gateway.stop()\n                    gateway.state = "ERROR"\n                    gateway.detail = "Gespeicherte WhatsApp-Verbindung konnte nicht wiederhergestellt werden."\n                LOGGER.warning("Could not resume registered WhatsApp session", exc_info=True)\n''',
    '''    def _resume_saved_session(self, generation: int) -> None:\n        last_error: Exception | None = None\n        for delay in AUTO_RESUME_DELAYS:\n            if delay:\n                time.sleep(delay)\n            with self._lock:\n                if generation != self._intent_generation or not self.has_saved_session():\n                    return\n                gateway = self.service.gateway\n                ok, detail = gateway.dependency_status()\n                if not ok:\n                    gateway.stop()\n                    gateway.state = "DEPENDENCY_MISSING"\n                    gateway.detail = detail\n                    return\n                try:\n                    gateway.command("connect", timeout=15)\n                    return\n                except Exception as exc:\n                    last_error = exc\n                    LOGGER.info("Registered WhatsApp session resume attempt failed", exc_info=True)\n\n        with self._lock:\n            if generation != self._intent_generation or not self.has_saved_session():\n                return\n            gateway = self.service.gateway\n            gateway.stop()\n            gateway.state = "ERROR"\n            gateway.detail = "Gespeicherte WhatsApp-Verbindung konnte nicht wiederhergestellt werden."\n        LOGGER.warning("Could not resume registered WhatsApp session: %s", last_error)\n''',
)

# The canonical service must signal persistence failure to the gateway instead
# of logging-and-swallowing it; the durable inbox then retains the event.
replace_once(
    "ladies/messaging/service_v2.py",
    '''        if event_type == "history_batch":\n            payloads = [item for item in event.get("payloads") or [] if isinstance(item, dict)]\n            try:\n                ingested = self._ingest_provider_batch(payloads, rehydrated=True)\n            except Exception:\n                LOGGER.exception("Failed to ingest WhatsApp history batch")\n                return\n            if ingested:\n                self.events.publish({"type": "history_batch", "count": ingested})\n            return\n''',
    '''        if event_type == "history_batch":\n            payloads = [item for item in event.get("payloads") or [] if isinstance(item, dict)]\n            try:\n                ingested = self._ingest_provider_batch(payloads, rehydrated=True)\n            except Exception:\n                LOGGER.exception("Failed to ingest WhatsApp history batch")\n                raise\n            if ingested:\n                self.events.publish({"type": "history_batch", "count": ingested})\n            return\n        if event_type == "message":\n            try:\n                self._ingest_message(event.get("payload") or {})\n            except Exception:\n                LOGGER.exception("Failed to ingest WhatsApp message")\n                raise\n            return\n''',
)

# Worker: connection control-plane (QR/open/close/restart/logout) always runs
# first, while receivedPendingNotifications is emitted only after data-plane
# events in the same Baileys batch complete successfully.
replace_once(
    "ladies/messaging/whatsapp_worker/worker_v2.mjs",
    '''  if (update.receivedPendingNotifications === true) {\n    emit({ type: 'sync', state: 'READY', progress: 100, is_latest: true })\n  }\n  if (update.connection === 'open') {''',
    '''  if (update.connection === 'open') {''',
)
replace_once(
    "ladies/messaging/whatsapp_worker/worker_v2.mjs",
    '''  if (events['connection.update']) await handleConnectionUpdate(current, events['connection.update'])\n\n  if (events['messaging-history.set']) {''',
    '''  const connectionUpdate = events['connection.update']\n  if (connectionUpdate) await handleConnectionUpdate(current, connectionUpdate)\n\n  if (events['messaging-history.set']) {''',
)
replace_once(
    "ladies/messaging/whatsapp_worker/worker_v2.mjs",
    '''  const presence = events['presence.update']\n  if (presence) {\n    const jid = presence.id\n    for (const [participant, value] of Object.entries(presence.presences || {})) {\n      emit({\n        type: 'presence',\n        payload: {\n          jid,\n          participant,\n          state: value?.lastKnownPresence || null,\n          last_seen: value?.lastSeen || null,\n        },\n      })\n    }\n  }\n}\n\nasync function connect() {''',
    '''  const presence = events['presence.update']\n  if (presence) {\n    const jid = presence.id\n    for (const [participant, value] of Object.entries(presence.presences || {})) {\n      emit({\n        type: 'presence',\n        payload: {\n          jid,\n          participant,\n          state: value?.lastKnownPresence || null,\n          last_seen: value?.lastSeen || null,\n        },\n      })\n    }\n  }\n\n  if (connectionUpdate?.receivedPendingNotifications === true && current === sock) {\n    emit({ type: 'sync', state: 'READY', progress: 100, is_latest: true })\n  }\n}\n\nasync function connect() {''',
)

# Focused regression test: real durable replay semantics, both signal orders,
# non-final READY rejection, source-level worker control/data ordering, and
# saved-session retry behavior.
Path("tools/check_whatsapp_reconnect_sync.py").write_text(r'''from __future__ import annotations

import json
import runpy
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def gateway_namespace():
    package = types.ModuleType("ladies.messaging")
    package.__path__ = [str(ROOT / "ladies/messaging")]
    previous = sys.modules.get("ladies.messaging")
    sys.modules["ladies.messaging"] = package
    try:
        return runpy.run_path(str(ROOT / "ladies/messaging/gateway.py"), run_name="ladies.messaging.gateway")
    finally:
        if previous is None:
            sys.modules.pop("ladies.messaging", None)
        else:
            sys.modules["ladies.messaging"] = previous


def check_worker_batch_contract() -> None:
    worker = (ROOT / "ladies/messaging/whatsapp_worker/worker_v2.mjs").read_text(encoding="utf-8")
    batch = worker.split("async function processEvents(current, events, saveCreds) {", 1)[1].split("\n}\n\nasync function connect()", 1)[0]
    control = "if (connectionUpdate) await handleConnectionUpdate(current, connectionUpdate)"
    ready = "if (connectionUpdate?.receivedPendingNotifications === true && current === sock)"
    require(batch.index(control) < batch.index("events['messages.upsert']"), "connection control-plane runs before message data-plane")
    require(batch.index(control) < batch.index("events['messaging-history.set']"), "connection control-plane runs before history data-plane")
    require(batch.index(ready) > batch.index("events['messages.upsert']"), "READY barrier follows message data-plane")
    require(batch.index(ready) > batch.index("events['messaging-history.set']"), "READY barrier follows history data-plane")


def check_durable_catch_up() -> None:
    namespace = gateway_namespace()
    gateway_cls = namespace["WhatsAppGateway"]
    events: list[dict] = []
    fail = {"message": True}

    def handler(event):
        if event.get("type") == "message" and fail["message"]:
            raise RuntimeError("simulated database failure")
        events.append(dict(event))

    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", handler)
        gateway._catch_up_active = True
        gateway._catch_up_token = 1
        gateway._handle({"type": "message", "payload": {"provider_message_id": "m1", "text": "offline"}})
        require(gateway._provider_inbox.pending_count() == 1, "failed catch-up message stays durable")

        gateway._handle({"type": "connection", "state": "CONNECTED", "account": "companion"})
        require(gateway.state == "CONNECTING", "transport-open is not final connectivity")
        require(gateway.last_connected_at is None, "connected timestamp waits for persistence barrier")

        fail["message"] = False
        gateway._handle({"type": "sync", "state": "READY", "progress": 100, "is_latest": True})
        require(gateway._provider_inbox.pending_count() == 0, "READY drains durable ingress after database recovery")
        require(gateway.state == "CONNECTED", "CONNECTED requires transport, READY and successful persistence")
        require(any(event.get("type") == "message" for event in events), "deferred message is replayed to canonical service")

    # READY-before-open remains valid and non-final READY cannot release.
    events.clear()
    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", lambda event: events.append(dict(event)))
        gateway._catch_up_active = True
        gateway._handle({"type": "sync", "state": "READY", "progress": 100, "is_latest": True})
        require(gateway.state != "CONNECTED", "READY alone cannot complete reconnect")
        gateway._handle({"type": "connection", "state": "CONNECTED"})
        require(gateway.state == "CONNECTED", "READY-before-open completes once transport opens")

    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", lambda event: None)
        gateway._catch_up_active = True
        gateway._handle({"type": "connection", "state": "CONNECTED"})
        gateway._handle({"type": "sync", "state": "READY", "progress": 100, "is_latest": False})
        require(gateway.state == "CONNECTING", "non-final READY cannot complete reconnect")


def check_service_propagates_persistence_failures() -> None:
    service = (ROOT / "ladies/messaging/service_v2.py").read_text(encoding="utf-8")
    history = service.split('if event_type == "history_batch":', 1)[1].split('if event_type == "history_metadata":', 1)[0]
    require("raise" in history, "history persistence errors propagate to durable gateway")
    message = history.split('if event_type == "message":', 1)[1]
    require("self._ingest_message" in message and "raise" in message, "message persistence errors propagate to durable gateway")


def connection_namespace():
    previous = sys.modules.get("qrcode")
    fake_qrcode = types.ModuleType("qrcode")
    fake_qrcode.make = lambda value: None
    sys.modules["qrcode"] = fake_qrcode
    try:
        return runpy.run_path(str(ROOT / "ladies/messaging/connection_lifecycle.py"))
    finally:
        if previous is None:
            sys.modules.pop("qrcode", None)
        else:
            sys.modules["qrcode"] = previous


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for asynchronous reconnect test")


def check_saved_session_resume() -> None:
    namespace = connection_namespace()
    lifecycle_cls = namespace["WhatsAppConnectionLifecycle"]
    lifecycle_cls.__init__.__globals__["AUTO_RESUME_DELAYS"] = (0.0, 0.0, 0.0)

    class Events:
        def publish(self, event):
            return None

    class Gateway:
        def __init__(self):
            self.state = "NOT_CONFIGURED"
            self.detail = None
            self.qr = None
            self.account = None
            self.last_connected_at = None
            self.proc = None
            self.order: list[str] = []
            self.connect_attempts = 0
            self.connected = threading.Event()

        @staticmethod
        def dependency_status():
            return True, None

        def stop(self):
            self.order.append("stop")

        def activate(self):
            self.order.append("activate")

        def command(self, command_type, timeout=15, **payload):
            self.order.append(command_type)
            if command_type == "connect":
                self.connect_attempts += 1
                if self.connect_attempts < 3:
                    raise RuntimeError("transient startup failure")
                self.connected.set()
            return {"ok": True}

    class Service:
        def __init__(self, auth_dir: Path):
            self.auth_dir = auth_dir
            self.gateway = Gateway()
            self.events = Events()

        def status(self):
            return {"state": self.gateway.state, "detail": self.gateway.detail}

        def qr(self):
            return None

        def disconnect(self):
            return self.status()

    with tempfile.TemporaryDirectory() as tmp:
        auth_dir = Path(tmp) / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "creds.json").write_text(json.dumps({"registered": True}), encoding="utf-8")
        service = Service(auth_dir)
        lifecycle_cls(service)
        wait_for(service.gateway.connected.is_set)
        require(service.gateway.order[:2] == ["stop", "activate"], "saved session activates worker intent synchronously")
        require(service.gateway.connect_attempts == 3, "saved session retries bounded transient failures")


if __name__ == "__main__":
    check_worker_batch_contract()
    check_durable_catch_up()
    check_service_propagates_persistence_failures()
    check_saved_session_resume()
    print("WhatsApp reconnect/sync checks passed")
''', encoding="utf-8")

Path(".github/workflows/whatsapp-reconnect-sync.yml").write_text(r'''name: WhatsApp reconnect sync

on:
  pull_request:
    paths:
      - 'ladies/messaging/**'
      - 'tools/check_whatsapp_reconnect_sync.py'
      - '.github/workflows/whatsapp-reconnect-sync.yml'
  workflow_dispatch:

permissions:
  contents: read

jobs:
  reconnect-sync:
    name: Reconnect and catch-up regressions
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Python syntax
        run: python -m compileall -q ladies/messaging tools/check_whatsapp_reconnect_sync.py
      - name: Worker syntax
        run: node --check ladies/messaging/whatsapp_worker/worker_v2.mjs
      - name: Verify reconnect and durable catch-up
        run: python tools/check_whatsapp_reconnect_sync.py
''', encoding="utf-8")

print("WhatsApp reliability patch applied")
