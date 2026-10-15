from __future__ import annotations

import runpy
import sys
import tempfile
import threading
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def check_identity() -> None:
    identity = runpy.run_path(str(ROOT / "ladies/messaging/provider_identity.py"))
    message_identity = identity["message_identity"]
    message_raw_id = identity["message_raw_id"]
    key = {"remoteJid": "491701234567@s.whatsapp.net", "id": "ABC123", "fromMe": False}
    require(message_identity(key) == "491701234567@s.whatsapp.net,ABC123,0", "identity remote/id/fromMe")
    require(message_identity({**key, "fromMe": True}) == "491701234567@s.whatsapp.net,ABC123,1", "identity fromMe")
    require(message_identity({**key, "remoteJid": "12345@lid"}) == "12345@lid,ABC123,0", "identity lid")
    require(message_raw_id(message_identity(key)) == "ABC123", "identity raw from canonical")
    require(message_raw_id({"id": "ABC123"}) == "ABC123", "identity raw from key")


def _connection_namespace():
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


def _check_connection_runtime() -> None:
    gateway_ns = runpy.run_path(str(ROOT / "ladies/messaging/gateway.py"))
    gateway_cls = gateway_ns["WhatsAppGateway"]

    # Legacy service startup must not be able to create a worker without an
    # explicit connection intent.
    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "auth", lambda event: None)
        gateway.dependency_status = lambda: (_ for _ in ()).throw(AssertionError("implicit start checked dependencies"))
        gateway.start()
        require(gateway.proc is None, "gateway start is inert without connection intent")

    # Reproduce the old reset race: the process already crashed, _watch() has
    # cleared proc and is sleeping. stop() must still invalidate that watcher.
    class CrashedProc:
        stdin = None

        @staticmethod
        def wait():
            return 7

        @staticmethod
        def poll():
            return 7

    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "auth", lambda event: None)
        gateway._desired_running = True
        gateway._stopping = False
        gateway._generation = 4
        gateway.state = "CONNECTED"
        proc = CrashedProc()
        gateway.proc = proc
        starts: list[str] = []
        gateway.start = lambda: starts.append("start")
        globals_ = gateway_cls._watch.__globals__
        old_sleep = globals_["time"].sleep
        old_delays = globals_["RESTART_DELAYS"]
        globals_["RESTART_DELAYS"] = (0,)
        globals_["time"].sleep = lambda delay: gateway.stop()
        try:
            gateway._watch(proc, 4)
        finally:
            globals_["time"].sleep = old_sleep
            globals_["RESTART_DELAYS"] = old_delays
        require(not starts, "stale crash watcher cannot restart after stop")
        require(not gateway._desired_running, "stop clears desired worker state even with proc already gone")

    # A genuine crash while connection intent is still active must restart the
    # process and explicitly reconnect because the Node worker no longer pairs
    # automatically on process startup.
    with tempfile.TemporaryDirectory() as tmp:
        gateway = gateway_cls(Path(tmp), Path(tmp) / "auth", lambda event: None)
        gateway._desired_running = True
        gateway._stopping = False
        gateway._generation = 9
        gateway.state = "CONNECTED"
        proc = CrashedProc()
        gateway.proc = proc
        calls: list[str] = []
        gateway.start = lambda: calls.append("start")
        gateway.command = lambda command_type, timeout=15, **payload: calls.append(command_type)
        globals_ = gateway_cls._watch.__globals__
        old_sleep = globals_["time"].sleep
        old_delays = globals_["RESTART_DELAYS"]
        globals_["RESTART_DELAYS"] = (0,)
        globals_["time"].sleep = lambda delay: None
        try:
            gateway._watch(proc, 9)
        finally:
            globals_["time"].sleep = old_sleep
            globals_["RESTART_DELAYS"] = old_delays
        require(calls == ["start", "connect"], "active crash recovery restarts and reconnects")

    connection_ns = _connection_namespace()
    lifecycle_cls = connection_ns["WhatsAppConnectionLifecycle"]

    class FakeEvents:
        def __init__(self):
            self.items = []

        def publish(self, event):
            self.items.append(event)

    class FakeGateway:
        def __init__(self):
            self.state = "NOT_CONFIGURED"
            self.detail = None
            self.qr = None
            self.account = None
            self.last_connected_at = None
            self.proc = None
            self.commands = []
            self.stop_calls = 0
            self.connect_started = threading.Event()
            self.allow_connect = threading.Event()
            self.block_connect = False

        @staticmethod
        def dependency_status():
            return True, None

        def command(self, command_type, timeout=15, **payload):
            self.commands.append(command_type)
            if command_type == "connect" and self.block_connect:
                self.connect_started.set()
                require(self.allow_connect.wait(2), "resume connect test timed out")
            return {"ok": True}

        def stop(self):
            self.stop_calls += 1
            self.proc = None

    class FakeService:
        def __init__(self, auth_dir, gateway=None):
            self.auth_dir = auth_dir
            self.gateway = gateway or FakeGateway()
            self.events = FakeEvents()

        def status(self):
            return {
                "state": self.gateway.state,
                "detail": self.gateway.detail,
                "qr_available": bool(self.gateway.qr),
                "account": self.gateway.account,
                "last_connected_at": self.gateway.last_connected_at,
            }

        def qr(self):
            return self.gateway.qr

        def disconnect(self):
            self.gateway.state = "DISCONNECTED"
            return self.status()

    # Clean-slate reset removes nested credentials/keys and does not recreate
    # the auth directory or issue a new connect command.
    with tempfile.TemporaryDirectory() as tmp:
        auth_dir = Path(tmp) / "auth"
        service = FakeService(auth_dir)
        lifecycle = lifecycle_cls(service)
        (auth_dir / "keys").mkdir(parents=True)
        (auth_dir / "creds.json").write_text('{"registered": false}', encoding="utf-8")
        (auth_dir / "keys" / "signal.json").write_text("secret", encoding="utf-8")
        service.gateway.commands.clear()
        result = lifecycle.reset()
        require(result["state"] == "NOT_CONFIGURED", "reset returns clean not-configured state")
        require(result["has_local_auth_data"] is False, "reset verifies local auth data removed")
        require(not auth_dir.exists(), "reset does not recreate auth directory")
        require("connect" not in service.gateway.commands, "reset never reconnects")

    # If deletion fails, reset must fail loudly instead of returning a false
    # success that makes the UI claim all credentials were deleted.
    with tempfile.TemporaryDirectory() as tmp:
        auth_dir = Path(tmp) / "auth"
        service = FakeService(auth_dir)
        lifecycle = lifecycle_cls(service)
        auth_dir.mkdir(parents=True)
        (auth_dir / "creds.json").write_text('{"registered": false}', encoding="utf-8")
        globals_ = lifecycle_cls._delete_auth_state.__globals__
        old_rmtree = globals_["shutil"].rmtree
        old_sleep = globals_["time"].sleep
        globals_["shutil"].rmtree = lambda path: (_ for _ in ()).throw(OSError("locked"))
        globals_["time"].sleep = lambda delay: None
        try:
            try:
                lifecycle.reset()
            except RuntimeError:
                pass
            else:
                raise AssertionError("reset must fail when auth files remain")
        finally:
            globals_["shutil"].rmtree = old_rmtree
            globals_["time"].sleep = old_sleep
        require(service.gateway.state == "ERROR", "failed auth deletion leaves explicit error state")
        require((auth_dir / "creds.json").exists(), "failed deletion test kept auth evidence")

    # If an automatic resume is already in flight, reset serializes behind it
    # and wins last. No stale resume can recreate auth after reset completes.
    with tempfile.TemporaryDirectory() as tmp:
        auth_dir = Path(tmp) / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "creds.json").write_text('{"registered": true}', encoding="utf-8")
        gateway = FakeGateway()
        gateway.block_connect = True
        service = FakeService(auth_dir, gateway)
        lifecycle = lifecycle_cls(service)
        require(gateway.connect_started.wait(2), "registered session resume did not start")
        reset_result = {}

        def do_reset():
            reset_result.update(lifecycle.reset())

        reset_thread = threading.Thread(target=do_reset)
        reset_thread.start()
        gateway.allow_connect.set()
        reset_thread.join(2)
        require(not reset_thread.is_alive(), "reset completed after in-flight resume")
        require(reset_result.get("state") == "NOT_CONFIGURED", "reset wins over in-flight resume")
        require(not auth_dir.exists(), "resume cannot recreate auth after completed reset")


def check_backend() -> None:
    service = source("ladies/messaging/service_v2.py")
    require("PersonUserState" not in service, "backend discovery state removed")
    require('payload["favorite"]' not in service, "backend favorite payload removed")
    require("def _ingest_provider_batch" in service, "backend batch ingest")
    require("def _register_raw_message" in service, "backend raw-id quote index")
    require("history_metadata" in service, "backend history metadata")
    require("provider_chat_deleted" in service, "backend provider state model")
    require("ProviderPeerState" in service, "backend provider state model")
    require("message_identity" in service, "backend canonical provider identity")
    require("Build the thread index before inserting the current row" in service, "backend history quote ordering")

    models = source("ladies/messaging/models.py")
    require("class ProviderPeerState" in models, "provider state class")
    require('__tablename__ = "provider_peer_state"' in models, "provider state table")

    connection = source("ladies/messaging/connection_lifecycle.py")
    require("class WhatsAppConnectionLifecycle" in connection, "connection lifecycle boundary")
    require('payload.get("registered") is True' in connection, "registered auth detection")
    require("self._intent_generation" in connection, "connection intent generation")
    require("self._pairing_requested" in connection, "explicit pairing intent")
    require('state == "QR_REQUIRED"' in connection, "QR state gating")
    require("shutil.rmtree(self.auth_dir" in connection, "complete auth reset")
    require("gateway.command(\"logout\"" in connection, "remote logout before local reset")
    require("Messages, media and chat history" in connection, "reset scope documents chat preservation")
    require('gateway.command("connect"' in connection, "pairing starts via explicit lifecycle command")
    require("wa-resume-session" in connection, "registered sessions resume explicitly")
    require("generation != self._intent_generation" in connection, "stale resume intent is rejected")
    require("WhatsApp-Verbindungsdaten konnten nicht vollständig gelöscht werden" in connection, "reset verifies auth deletion")
    require("pairing_failed_code_" in connection, "pairing failure translated for UI")

    gateway = source("ladies/messaging/gateway.py")
    require("self._desired_running" in gateway, "gateway desired-running state")
    require("self._generation" in gateway, "gateway restart generation")
    require("if not self._desired_running" in gateway, "implicit gateway start is blocked")
    require("generation != self._generation" in gateway, "stale watcher generation is rejected")
    require('self.command("connect", timeout=15)' in gateway, "crashed worker explicitly reconnects")
    require("self._desired_running = False" in gateway.split("def stop", 1)[1], "stop cancels desired worker state")

    routes = source("ladies/messaging/routes_v2.py")
    require("WhatsAppConnectionLifecycle(service)" in routes, "lifecycle wired directly into canonical router")
    require('@app.delete("/api/messaging/connection")' in routes, "canonical connection reset endpoint")
    require('/api/messaging/qr' not in routes, "legacy QR endpoint removed")
    require('/api/messaging/logout' not in routes, "legacy logout endpoint removed")

    _check_connection_runtime()


def check_worker() -> None:
    worker = source("ladies/messaging/whatsapp_worker/worker_v2.mjs")
    require("messageIdentity" in worker, "worker canonical identity helper")
    require("provider_message_id: messageIdentity(msg.key)" in worker, "worker canonical message payload")
    require("events['messages.delete']" in worker, "worker messages.delete")
    require("events['contacts.upsert']" in worker, "worker contacts.upsert")
    require("events['contacts.update']" in worker, "worker contacts.update")
    require("events['chats.upsert']" in worker, "worker chats.upsert")
    require("events['chats.update']" in worker, "worker chats.update")
    require("events['chats.delete']" in worker, "worker chats.delete")
    require("history_metadata" in worker, "worker history metadata")
    require("messageCachePath(identity)" in worker, "worker canonical raw cache")
    require("provider_message_id: msg.key?.id" not in worker, "worker raw id no longer canonical")
    require("fetchLatestWaWebVersion" in worker, "worker prefers live WhatsApp Web version")
    require("fetchLatestBaileysVersion" in worker, "worker retains version fallback")
    require("Browsers.ubuntu('Chrome')" in worker, "worker uses web browser subplatform for pairing")
    require("Browsers.macOS('Desktop')" not in worker, "worker avoids rejected DARWIN desktop pairing identity")
    require("registeredAtStart" in worker, "worker distinguishes pairing from registered reconnect")
    require("pairing_failed_code_" in worker, "unregistered pairing failures are terminal")
    require("DisconnectReason.restartRequired" in worker, "pairing restart required is handled explicitly")
    tail = worker.split("emit({ type: 'ready' })", 1)[-1]
    require("connect().catch" not in tail, "worker does not auto-pair on process startup")


def check_frontend() -> None:
    selectors = source("ladies/static/explorer/js/app/selectors.js")
    messaging_selectors = selectors.split("export const threadRows", 1)[1].split("export const unreadTotal", 1)[0]
    require("FAVORITE" not in messaging_selectors, "frontend favorite filter removed")
    require("favorite" not in messaging_selectors, "frontend favorite state removed")
    require("preference" not in messaging_selectors, "frontend preference state removed")
    require("state.profiles" not in messaging_selectors, "frontend profile state decoupled")
    require("state.lists" not in messaging_selectors, "frontend list state decoupled")

    adapters = source("ladies/static/explorer/js/data/adapters.js")
    thread_vm = adapters.split("export function threadVm", 1)[1].split("export function messageVm", 1)[0]
    require("favorite" not in thread_vm, "thread vm favorite removed")
    require("preference" not in thread_vm, "thread vm preference removed")

    message_list = source("ladies/static/explorer/js/ui/screens/message_list.js")
    require("Favoriten" not in message_list, "message list favorites removed")
    require("FAVORITE" not in message_list, "message list favorite filter removed")
    require('data-action="message-filter"' not in message_list, "message list server filter removed")
    require('data-action="message-view-filter"' in message_list, "message list client filter")

    chat_screen = source("ladies/static/explorer/js/ui/screens/messaging.js")
    require("renderMessages" not in chat_screen, "duplicate message list renderer removed")
    require('data-action="message-filter"' not in chat_screen, "chat screen server filter removed")
    require("FAVORITE" not in chat_screen, "chat screen favorite state removed")
    require("export function renderChat" in chat_screen, "chat renderer retained")

    commands = source("ladies/static/explorer/js/app/commands.js")
    require("'message-filter'" not in commands, "message filter command removed")
    require("'chat-favorite'" not in commands, "chat favorite command removed")
    require("'wa-reset'" in commands, "WhatsApp reset command")
    require("'wa-qr'" not in commands, "legacy QR command removed")
    require("'wa-logout'" not in commands, "legacy logout command removed")
    require("effects.loadMessagingStatus()" in commands.split("name==='settings'", 1)[1].split("},", 1)[0], "settings refresh WhatsApp status")

    messaging_effects = source("ladies/static/explorer/js/effects/messaging.js")
    require("async function loadThreads()" in messaging_effects, "canonical loadThreads signature")
    require("api.messagingInbox('ALL')" in messaging_effects, "classified Ladies inbox fetch")
    require("api.messagingReview()" in messaging_effects, "unknown WhatsApp review fetch")
    require("api.threads(" not in messaging_effects, "raw provider thread fetch is not a product data source")
    require("async function resetWhatsapp()" in messaging_effects, "WhatsApp reset effect")
    require("api.messagingReset()" in messaging_effects, "reset uses canonical API")
    reset_effect = messaging_effects.split("async function resetWhatsapp()", 1)[1].split("async function loadThreads()", 1)[0]
    require("api.messagingConnect()" not in reset_effect, "reset does not immediately recreate a WhatsApp session")
    require("keine neue Verbindung automatisch gestartet" in reset_effect, "reset confirmation explains clean-slate behavior")
    require("showQr" not in messaging_effects, "QR popup effect removed")

    client = source("ladies/static/explorer/js/api/client.js")
    require("messagingReset:" in client, "reset API client")
    require("messagingQr:" not in client, "legacy QR API client removed")
    require("messagingLogout:" not in client, "legacy logout API client removed")

    settings = source("ladies/static/explorer/js/ui/screens/settings.js")
    require('data-action="wa-reset"' in settings, "settings reset action")
    require("Verbindungsdaten vollständig löschen" in settings, "settings exposes explicit full connection-data reset")
    require("actions.push(`<button class=\"danger\" data-action=\"wa-reset\">Verbindungsdaten vollständig löschen</button>`)" in settings, "reset action is unconditional across connection states")
    require('data-action="wa-qr"' not in settings, "settings QR button removed")
    require('data-action="wa-logout"' not in settings, "settings logout action removed")
    require("wa?.pairing_requested" in settings, "settings pair intent rendering")
    require("wa?.qr" in settings, "settings inline QR rendering")
    require("data-whatsapp-pairing" in settings, "settings inline pairing panel")


CHECKS = {
    "identity": check_identity,
    "backend": check_backend,
    "worker": check_worker,
    "frontend": check_frontend,
}

requested = sys.argv[1:] or list(CHECKS)
for name in requested:
    check = CHECKS.get(name)
    if check is None:
        raise SystemExit(f"unknown messaging architecture check: {name}")
    check()
    print(f"messaging architecture check passed: {name}")
