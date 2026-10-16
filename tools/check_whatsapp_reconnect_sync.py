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
