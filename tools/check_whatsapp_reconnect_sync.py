from __future__ import annotations

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


def provider_inbox_namespace():
    return runpy.run_path(str(ROOT / "ladies/messaging/provider_inbox.py"))


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
        time.sleep(0.005)
    raise AssertionError("timed out waiting for asynchronous reconnect test")


def check_worker_sync_contract() -> None:
    worker = (ROOT / "ladies/messaging/whatsapp_worker/worker_v2.mjs").read_text(encoding="utf-8")
    batch = worker.split("async function processEvents(current, events, saveCreds) {", 1)[1].split("\n}\n\nasync function connect()", 1)[0]
    pending = batch.split("const pendingReceivedNow", 1)[1].split("const historyStatus", 1)[0]
    connect = worker.split("async function connect() {", 1)[1].split("\n}\nasync function handle(command)", 1)[0]

    control = "if (connectionUpdate) await handleConnectionUpdate(current, connectionUpdate)"
    require(batch.index(control) < batch.index("events['messages.upsert']"), "connection control-plane precedes message data-plane")
    require(batch.index(control) < batch.index("events['messaging-history.set']"), "connection control-plane precedes history data-plane")
    require("state: 'PENDING'" in pending, "receivedPendingNotifications is only a pending/catch-up signal")
    require("state: 'READY'" not in pending, "receivedPendingNotifications cannot directly release READY")
    require("events['messaging-history.status']" in batch, "Baileys history completion/status is consumed")
    require("historySet?.isLatest" in batch, "latest history batch can close first-sync catch-up")
    require("scheduleReadyAfterBaileysFlush(current)" in batch, "existing sessions wait behind Baileys buffered flush")
    require("context.eventTail = task.catch" in connect, "worker event batches are serialized through one tail")
    require("context.catchUpFailed = true" in connect, "data-plane failure permanently blocks READY on that socket")


def check_provider_inbox_recovery() -> None:
    namespace = provider_inbox_namespace()
    inbox_cls = namespace["DurableProviderInbox"]
    source = (ROOT / "ladies/messaging/provider_inbox.py").read_text(encoding="utf-8")
    require("os.fsync(handle.fileno())" in source and "os.replace(temporary, target)" in source, "ingress write is flushed before atomic publish")

    delivered: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pending"
        root.mkdir(parents=True)
        inbox = inbox_cls(root, lambda event: delivered.append(dict(event)))

        orphan = root / ".orphan.json.123.tmp"
        orphan.write_text(json.dumps({"type": "message", "payload": {"provider_message_id": "orphan"}}), encoding="utf-8")
        count, quarantined = inbox.drain()
        require((count, quarantined) == (1, 0), "valid crash-temp is recovered and delivered")
        require(delivered[-1]["payload"]["provider_message_id"] == "orphan", "recovered temp keeps event payload")

        corrupt = root / "00000000000000000001-corrupt.json"
        corrupt.write_text("{not-json", encoding="utf-8")
        count, quarantined = inbox.drain()
        require(count == 0 and quarantined == 1, "corrupt ingress is quarantined instead of blocking startup")
        require(inbox.pending_count() == 0 and inbox.quarantined_count() >= 1, "poison entry leaves active queue")

        pending = root / "00000000000000000002-pending.json"
        pending.write_text(json.dumps({"type": "message", "payload": {"provider_message_id": "old-account"}}), encoding="utf-8")
        moved = inbox.abandon("reset")
        require(moved == 1 and inbox.pending_count() == 0, "reset isolates session-bound pending ingress")


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
        gateway._catch_up_started_at = gateway._catch_up_last_activity = time.monotonic()
        gateway._handle({"type": "message", "payload": {"provider_message_id": "m1", "text": "offline"}})
        require(gateway._provider_inbox.pending_count() == 1, "failed catch-up message stays durable")

        gateway._handle({"type": "connection", "state": "CONNECTED", "account": "companion"})
        gateway._handle({"type": "sync", "state": "PENDING", "is_latest": False, "expected_history": False})
        require(gateway.state == "CONNECTING", "transport and pending-notification signal are not final connectivity")
        require(gateway.last_connected_at is None, "connected timestamp waits for final provider sync")

        fail["message"] = False
        gateway._handle({"type": "message", "payload": {"provider_message_id": "m2", "text": "buffered-later"}})
        require(gateway.state == "CONNECTING", "delayed Baileys flush remains inside durable catch-up")
        require(any(event.get("payload", {}).get("provider_message_id") == "m2" for event in events), "late buffered message reaches canonical persistence")

        gateway._handle({"type": "sync", "state": "READY", "progress": 100, "is_latest": True})
        require(gateway._provider_inbox.pending_count() == 0, "final READY drains earlier failed durable ingress")
        require(gateway.state == "CONNECTED", "CONNECTED requires transport, final READY and successful persistence")
        require(any(event.get("payload", {}).get("provider_message_id") == "m1" for event in events), "deferred failed message is replayed")

    events.clear()
    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", lambda event: events.append(dict(event)))
        gateway._catch_up_active = True
        gateway._catch_up_started_at = gateway._catch_up_last_activity = time.monotonic()
        gateway._handle({"type": "sync", "state": "READY", "progress": 100, "is_latest": True})
        require(gateway.state != "CONNECTED", "READY alone cannot complete reconnect")
        gateway._handle({"type": "connection", "state": "CONNECTED"})
        require(gateway.state == "CONNECTED", "READY-before-open completes once transport opens")

    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", lambda event: None)
        gateway._catch_up_active = True
        gateway._catch_up_started_at = gateway._catch_up_last_activity = time.monotonic()
        gateway._handle({"type": "connection", "state": "CONNECTED"})
        gateway._handle({"type": "sync", "state": "READY", "progress": 100, "is_latest": False})
        require(gateway.state == "CONNECTING", "non-final READY cannot complete reconnect")


def check_activity_aware_watchdog() -> None:
    namespace = gateway_namespace()
    gateway_cls = namespace["WhatsAppGateway"]
    globals_ = gateway_cls._watch_catch_up.__globals__
    names = (
        "CATCH_UP_IDLE_TIMEOUT_SECONDS",
        "CATCH_UP_HISTORY_IDLE_TIMEOUT_SECONDS",
        "CATCH_UP_HARD_TIMEOUT_SECONDS",
        "CATCH_UP_POLL_SECONDS",
        "CATCH_UP_MAX_RESTARTS",
    )
    previous = {name: globals_[name] for name in names}
    globals_.update({
        "CATCH_UP_IDLE_TIMEOUT_SECONDS": 0.02,
        "CATCH_UP_HISTORY_IDLE_TIMEOUT_SECONDS": 0.08,
        "CATCH_UP_HARD_TIMEOUT_SECONDS": 0.20,
        "CATCH_UP_POLL_SECONDS": 0.005,
        "CATCH_UP_MAX_RESTARTS": 2,
    })

    class Proc:
        def __init__(self):
            self.terminated = threading.Event()

        def poll(self):
            return None

        def terminate(self):
            self.terminated.set()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            gateway = gateway_cls(Path(tmp), Path(tmp) / "whatsapp" / "auth", lambda event: None)
            proc = Proc()
            gateway.proc = proc
            gateway._desired_running = True
            gateway._stopping = False
            gateway._catch_up_active = True
            gateway._transport_open = True
            gateway._pending_sync_ready = False
            gateway._catch_up_token = 7
            gateway._catch_up_started_at = gateway._catch_up_last_activity = time.monotonic()
            gateway._handle({"type": "sync", "state": "HISTORY_WAIT", "is_latest": False})
            gateway._arm_catch_up_watchdog(proc, 7)
            time.sleep(0.04)
            require(not proc.terminated.is_set(), "active history phase is not killed by normal idle timeout")
            wait_for(proc.terminated.is_set, timeout=0.25)
            require(gateway._catch_up_restarts == 1, "stalled history eventually causes one bounded reconnect")
            require(gateway.state == "RECONNECTING", "stalled catch-up never exposes CONNECTED")
    finally:
        globals_.update(previous)


def check_catch_up_barrier_atomicity() -> None:
    source = (ROOT / "ladies/messaging/gateway.py").read_text(encoding="utf-8")
    finish = source.split("def _finish_catch_up", 1)[1].split("def _arm_catch_up_watchdog", 1)[0]
    durable = source.split("if event_type in DURABLE_EVENT_TYPES:", 1)[1].split("forwarded_event = event", 1)[0]
    require("with self._lock:" in finish, "catch-up completion is serialized with gateway state")
    require("with self._lock:" in durable, "durable catch-up delivery shares the completion lock")
    require("CATCH_UP_HARD_TIMEOUT_SECONDS" in source and "_catch_up_last_activity" in source, "watchdog has activity timeout plus hard cap")


def check_service_propagates_persistence_failures() -> None:
    service = (ROOT / "ladies/messaging/service_v2.py").read_text(encoding="utf-8")
    history = service.split('if event_type == "history_batch":', 1)[1].split('if event_type == "history_metadata":', 1)[0]
    require("raise" in history, "history persistence errors propagate to durable gateway")
    message = history.split('if event_type == "message":', 1)[1]
    require("self._ingest_message" in message and "raise" in message, "message persistence errors propagate to durable gateway")


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


def check_resume_does_not_hold_lifecycle_lock() -> None:
    namespace = connection_namespace()
    lifecycle_cls = namespace["WhatsAppConnectionLifecycle"]
    lifecycle_cls.__init__.__globals__["AUTO_RESUME_DELAYS"] = (0.0,)

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
            self.started = threading.Event()
            self.release = threading.Event()
            self.discarded: list[str] = []

        @staticmethod
        def dependency_status():
            return True, None

        def stop(self):
            return None

        def activate(self):
            return None

        def command(self, command_type, timeout=15, **payload):
            if command_type == "connect":
                self.started.set()
                self.release.wait(1)
            return {"ok": True}

        def discard_pending_ingress(self, reason="reset"):
            self.discarded.append(reason)
            return 0

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
        lifecycle = lifecycle_cls(service)
        wait_for(service.gateway.started.is_set)
        completed = threading.Event()

        def read_status():
            lifecycle.status()
            completed.set()

        threading.Thread(target=read_status, daemon=True).start()
        wait_for(completed.is_set, timeout=0.2)
        service.gateway.release.set()

    reset_source = (ROOT / "ladies/messaging/connection_lifecycle.py").read_text(encoding="utf-8").split("def reset", 1)[1]
    require("discard_pending_ingress" in reset_source, "reset isolates pending ingress before auth deletion")


if __name__ == "__main__":
    check_worker_sync_contract()
    check_provider_inbox_recovery()
    check_durable_catch_up()
    check_activity_aware_watchdog()
    check_catch_up_barrier_atomicity()
    check_service_propagates_persistence_failures()
    check_saved_session_resume()
    check_resume_does_not_hold_lifecycle_lock()
    print("WhatsApp reconnect/sync checks passed")
