from __future__ import annotations

import runpy
import sys
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


def check_backend() -> None:
    service = source("ladies/messaging/service_v2.py")
    require("PersonUserState" not in service, "backend discovery state removed")
    require('payload["favorite"]' not in service, "backend favorite payload removed")
    require("def _ingest_provider_batch" in service, "backend batch ingest")
    require("def _register_raw_message" in service, "backend raw-id quote index")
    require("history_metadata" in service, "backend history metadata")
    require("provider_chat_deleted" in service, "backend provider chat delete state")
    require("ProviderPeerState" in service, "backend provider state model")
    require("message_identity" in service, "backend canonical provider identity")
    require("Build the thread index before inserting the current row" in service, "backend history quote ordering")

    models = source("ladies/messaging/models.py")
    require("class ProviderPeerState" in models, "provider state class")
    require('__tablename__ = "provider_peer_state"' in models, "provider state table")

    connection = source("ladies/messaging/connection_lifecycle.py")
    require("class WhatsAppConnectionLifecycle" in connection, "connection lifecycle boundary")
    require('payload.get("registered") is True' in connection, "registered auth detection")
    require("self._pairing_requested" in connection, "explicit pairing intent")
    require('state == "QR_REQUIRED"' in connection, "QR state gating")
    require("shutil.rmtree(self.auth_dir" in connection, "complete auth reset")
    require("gateway.command(\"logout\"" in connection, "remote logout before local reset")
    require("messages/media/history" in connection, "reset scope documents chat preservation")

    routes = source("ladies/messaging/routes_v2.py")
    require("WhatsAppConnectionLifecycle(service)" in routes, "lifecycle wired directly into canonical router")
    require('@app.delete("/api/messaging/connection")' in routes, "canonical connection reset endpoint")
    require('/api/messaging/qr' not in routes, "legacy QR endpoint removed")
    require('/api/messaging/logout' not in routes, "legacy logout endpoint removed")


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
    require("api.threads('ALL')" in messaging_effects, "canonical ALL fetch")
    require("api.threads(filter)" not in messaging_effects, "server subset fetch removed")
    require("async function resetWhatsapp()" in messaging_effects, "WhatsApp reset effect")
    require("api.messagingReset()" in messaging_effects, "reset uses canonical API")
    require("showQr" not in messaging_effects, "QR popup effect removed")

    client = source("ladies/static/explorer/js/api/client.js")
    require("messagingReset:" in client, "reset API client")
    require("messagingQr:" not in client, "legacy QR API client removed")
    require("messagingLogout:" not in client, "legacy logout API client removed")

    settings = source("ladies/static/explorer/js/ui/screens/settings.js")
    require('data-action="wa-reset"' in settings, "settings reset action")
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
