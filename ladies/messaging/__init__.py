from .routes import install_messaging
from .service import MessagingService, jid_phone

# Baileys includes ``last_seen`` on presence events. The canonical service
# intentionally normalizes the browser event, so retain this optional provider
# value without changing the persistent messaging schema.
_original_presence_browser_event = MessagingService._presence_browser_event


def _presence_browser_event_with_last_seen(self, payload):
    event = _original_presence_browser_event(self, payload)
    if event is not None and payload.get("last_seen") is not None:
        event["last_seen"] = payload.get("last_seen")
    return event


MessagingService._presence_browser_event = _presence_browser_event_with_last_seen

__all__ = ["MessagingService", "install_messaging", "jid_phone"]
