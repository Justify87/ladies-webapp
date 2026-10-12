from __future__ import annotations

import sys

from sqlalchemy import desc, select

from ladies_v9.models import PersonUserState
from .models import ChatMessage, ChatThread
from . import service_runtime as _service_runtime

# Keep legacy import paths working while all physical source paths remain neutral.
sys.modules.setdefault(__name__ + ".service_v92", _service_runtime)

from . import service_reviewfix as _service_reviewfix


class MessagingService(_service_reviewfix.MessagingService):
    """Current messaging service plus user-facing thread visibility rules."""

    def subscribe_presence(self, thread_id: int) -> None:
        # Opening/refreshing a thread can happen while Baileys is reconnecting.
        # Presence is supplementary, so do not turn a transient disconnect into
        # repeated worker warnings or visible chat instability.
        if self.worker.state != "CONNECTED":
            return
        return super().subscribe_presence(thread_id)

    def threads(self, filter_name: str = "ALL"):
        if filter_name == "UNASSIGNED":
            return super().threads(filter_name)

        with self.database.session() as db:
            rows = db.scalars(
                select(ChatThread).order_by(
                    desc(ChatThread.last_message_at),
                    desc(ChatThread.updated_at),
                )
            ).all()
            out = []
            for thread in rows:
                if thread.person_id is None:
                    continue

                last = db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.thread_id)
                    .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                    .limit(1)
                )
                # Only empty placeholder threads stay hidden. Any real 1:1
                # conversation belongs in the inbox, including a neutral person
                # who has written first and has not yet been liked/favorited.
                if last is None:
                    continue
                if filter_name == "UNREAD" and not thread.unread_count:
                    continue

                state = db.get(PersonUserState, int(thread.person_id))
                payload = self._thread_payload(db, thread, last)
                payload["favorite"] = bool(state.favorite) if state else False
                payload["preference"] = state.preference if state else None
                out.append(payload)

            # Keep newest-message order inside each stable group while pinning
            # favorites first, then likes, then neutral conversations.
            out.sort(key=lambda item: (
                0 if item.get("favorite") else 1,
                0 if item.get("preference") == "LIKE" else 1,
            ))
            return out


from .web_reviewfix import install_messaging

__all__ = ["MessagingService", "install_messaging"]
