from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one match, got {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "ladies/messaging/gateway.py",
    '''    def _begin_catch_up(self) -> int:\n        self._catch_up_active = True\n        self._transport_open = False\n        self._pending_sync_ready = False\n        self._catch_up_token += 1\n        return self._catch_up_token\n\n    def _finish_catch_up(self) -> dict[str, Any] | None:\n        if not self._catch_up_active or not self._transport_open or not self._pending_sync_ready:\n            return None\n        replayed = self._provider_inbox.drain()\n        if self._provider_inbox.pending_count():\n            return None\n        if replayed:\n            LOGGER.info("Persisted %s deferred WhatsApp catch-up event(s)", replayed)\n        previous = self.state\n        self._catch_up_active = False\n        self.state = "CONNECTED"\n        self.detail = None\n        self._restart_attempt = 0\n        self._catch_up_restarts = 0\n        self.qr = None\n        self.last_connected_at = time.time()\n        if previous != "CONNECTED":\n            LOGGER.info("WhatsApp offline catch-up complete; connection is ready")\n        return {\n            "type": "connection",\n            "state": "CONNECTED",\n            "detail": None,\n            "account": self.account,\n        }\n''',
    '''    def _begin_catch_up(self) -> int:\n        with self._lock:\n            self._catch_up_active = True\n            self._transport_open = False\n            self._pending_sync_ready = False\n            self._catch_up_token += 1\n            return self._catch_up_token\n\n    def _finish_catch_up(self) -> dict[str, Any] | None:\n        # Use the gateway lock as the single catch-up barrier lock. Durable\n        # delivery and stop() use the same lock, so no provider event can slip\n        # between the final inbox-empty check and the CONNECTED transition.\n        with self._lock:\n            if not self._catch_up_active or not self._transport_open or not self._pending_sync_ready:\n                return None\n            replayed = self._provider_inbox.drain()\n            if self._provider_inbox.pending_count():\n                return None\n            if replayed:\n                LOGGER.info("Persisted %s deferred WhatsApp catch-up event(s)", replayed)\n            previous = self.state\n            self._catch_up_active = False\n            self.state = "CONNECTED"\n            self.detail = None\n            self._restart_attempt = 0\n            self._catch_up_restarts = 0\n            self.qr = None\n            self.last_connected_at = time.time()\n            if previous != "CONNECTED":\n                LOGGER.info("WhatsApp offline catch-up complete; connection is ready")\n            return {\n                "type": "connection",\n                "state": "CONNECTED",\n                "detail": None,\n                "account": self.account,\n            }\n''',
)

replace_once(
    "ladies/messaging/gateway.py",
    '''        if self._catch_up_active and event_type in DURABLE_EVENT_TYPES:\n            try:\n                self._provider_inbox.deliver(event)\n            except Exception:\n                self.state = "CONNECTING"\n                self.detail = "Ausstehende WhatsApp-Nachrichten werden erneut gespeichert."\n                LOGGER.exception("Deferred WhatsApp provider event persistence failed")\n            return\n''',
    '''        if event_type in DURABLE_EVENT_TYPES:\n            with self._lock:\n                if self._catch_up_active:\n                    try:\n                        self._provider_inbox.deliver(event)\n                    except Exception:\n                        self.state = "CONNECTING"\n                        self.detail = "Ausstehende WhatsApp-Nachrichten werden erneut gespeichert."\n                        LOGGER.exception("Deferred WhatsApp provider event persistence failed")\n                    return\n''',
)

# Guard the architectural invariant in the focused test so later refactors do
# not accidentally split delivery and barrier transition across different locks.
path = Path("tools/check_whatsapp_reconnect_sync.py")
text = path.read_text(encoding="utf-8")
needle = '''def check_service_propagates_persistence_failures() -> None:\n'''
insert = '''def check_catch_up_barrier_atomicity() -> None:\n    source = (ROOT / "ladies/messaging/gateway.py").read_text(encoding="utf-8")\n    finish = source.split("def _finish_catch_up", 1)[1].split("def _arm_catch_up_watchdog", 1)[0]\n    durable = source.split("if event_type in DURABLE_EVENT_TYPES:", 1)[1].split("forwarded_event = event", 1)[0]\n    require("with self._lock:" in finish, "catch-up completion is serialized with gateway state")\n    require("with self._lock:" in durable, "durable catch-up delivery shares the completion lock")\n\n\n'''
if text.count(needle) != 1:
    raise RuntimeError("atomicity test insertion point mismatch")
text = text.replace(needle, insert + needle, 1)
text = text.replace(
    '    check_catch_up_timeout_restarts()\n    check_service_propagates_persistence_failures()\n',
    '    check_catch_up_timeout_restarts()\n    check_catch_up_barrier_atomicity()\n    check_service_propagates_persistence_failures()\n',
    1,
)
path.write_text(text, encoding="utf-8")
