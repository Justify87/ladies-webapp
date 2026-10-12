from __future__ import annotations

import sys

from sqlalchemy import desc, select

from ladies_v9.models import PersonUserState
from .models import ChatMessage, ChatThread
from . import service_runtime as _service_runtime

# Keep legacy import paths working while all physical source paths remain neutral.
sys.modules.setdefault(__name__ + ".service_v92", _service_runtime)


class MessagingService(_service_runtime.MessagingService):
    """Current messaging service plus user-facing thread visibility rules."""

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
                state = db.get(PersonUserState, int(thread.person_id))
                if state is None or not (state.favorite or state.preference == "LIKE"):
                    continue
                has_outgoing = db.scalar(
                    select(ChatMessage.message_id)
                    .where(
                        ChatMessage.thread_id == thread.thread_id,
                        ChatMessage.direction == "OUT",
                    )
                    .limit(1)
                )
                if has_outgoing is None:
                    continue
                if filter_name == "UNREAD" and not thread.unread_count:
                    continue
                last = db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.thread_id)
                    .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                    .limit(1)
                )
                payload = self._thread_payload(db, thread, last)
                payload["favorite"] = bool(state.favorite)
                payload["preference"] = state.preference
                out.append(payload)

            # Preserve newest-message order inside each group while pinning
            # contacted favorites before contacted likes.
            out.sort(key=lambda item: (
                0 if item.get("favorite") else 1,
                0 if item.get("preference") == "LIKE" else 1,
            ))
            return out


from .web import install_messaging

__all__ = ["MessagingService", "install_messaging"]
