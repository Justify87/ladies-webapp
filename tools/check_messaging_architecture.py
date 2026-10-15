from __future__ import annotations

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def check_identity() -> None:
    identity = runpy.run_path(str(ROOT / "ladies/messaging/provider_identity.py"))
    message_identity = identity["message_identity"]
    message_raw_id = identity["message_raw_id"]
    key = {"remoteJid": "491701234567@s.whatsapp.net", "id": "ABC123", "fromMe": False}
    assert message_identity(key) == "491701234567@s.whatsapp.net,ABC123,0"
    assert message_identity({**key, "fromMe": True}) == "491701234567@s.whatsapp.net,ABC123,1"
    assert message_identity({**key, "remoteJid": "12345@lid"}) == "12345@lid,ABC123,0"
    assert message_raw_id(message_identity(key)) == "ABC123"
    assert message_raw_id({"id": "ABC123"}) == "ABC123"


def check_backend() -> None:
    service = source("ladies/messaging/service_v2.py")
    assert "PersonUserState" not in service
    assert 'payload["favorite"]' not in service
    assert "def _ingest_provider_batch" in service
    assert "def _register_raw_message" in service
    assert "history_metadata" in service
    assert "provider_chat_deleted" in service
    assert "ProviderPeerState" in service
    assert "message_identity" in service
    assert "Build the thread index before inserting the current row" in service

    models = source("ladies/messaging/models.py")
    assert "class ProviderPeerState" in models
    assert '__tablename__ = "provider_peer_state"' in models


def check_worker() -> None:
    worker = source("ladies/messaging/whatsapp_worker/worker_v2.mjs")
    assert "messageIdentity" in worker
    assert "provider_message_id: messageIdentity(msg.key)" in worker
    assert "events['messages.delete']" in worker
    assert "events['contacts.upsert']" in worker
    assert "events['contacts.update']" in worker
    assert "events['chats.upsert']" in worker
    assert "events['chats.update']" in worker
    assert "events['chats.delete']" in worker
    assert "history_metadata" in worker
    assert "messageCachePath(identity)" in worker
    assert "provider_message_id: msg.key?.id" not in worker


def check_frontend() -> None:
    selectors = source("ladies/static/explorer/js/app/selectors.js")
    messaging_selectors = selectors.split("export const threadRows", 1)[1].split("export const unreadTotal", 1)[0]
    assert "FAVORITE" not in messaging_selectors
    assert "favorite" not in messaging_selectors
    assert "preference" not in messaging_selectors
    assert "state.profiles" not in messaging_selectors
    assert "state.lists" not in messaging_selectors

    adapters = source("ladies/static/explorer/js/data/adapters.js")
    thread_vm = adapters.split("export function threadVm", 1)[1].split("export function messageVm", 1)[0]
    assert "favorite" not in thread_vm
    assert "preference" not in thread_vm

    message_list = source("ladies/static/explorer/js/ui/screens/message_list.js")
    assert "Favoriten" not in message_list
    assert "FAVORITE" not in message_list
    assert "message-filter" not in message_list
    assert 'data-action="message-view-filter"' in message_list

    chat_screen = source("ladies/static/explorer/js/ui/screens/messaging.js")
    assert "renderMessages" not in chat_screen
    assert "message-filter" not in chat_screen
    assert "FAVORITE" not in chat_screen
    assert "export function renderChat" in chat_screen

    commands = source("ladies/static/explorer/js/app/commands.js")
    assert "'message-filter'" not in commands
    assert "'chat-favorite'" not in commands

    messaging_effects = source("ladies/static/explorer/js/effects/messaging.js")
    assert "async function loadThreads()" in messaging_effects
    assert "api.threads('ALL')" in messaging_effects
    assert "api.threads(filter)" not in messaging_effects


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
