from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ladies_v9.messaging.history_cache import RecentHistoryCache
from ladies_v9.messaging.models import ContactEndpoint


class MessagingHistoryCacheTests(unittest.TestCase):
    def payload(self, index: int) -> dict:
        jid = "4917612345678@s.whatsapp.net"
        return {
            "provider_message_id": f"m-{index}",
            "provider_key": {"remoteJid": jid, "id": f"m-{index}"},
            "jid": jid,
            "phone_jid": jid,
            "timestamp": 1_700_000_000 + index,
            "media": {"archive_name": f"x-{index}.jpg", "data_base64": "do-not-store"},
        }

    def test_cache_is_bounded_and_does_not_persist_inline_media(self):
        with tempfile.TemporaryDirectory() as td:
            cache = RecentHistoryCache(td, limit=3)
            cache.store([self.payload(index) for index in range(5)])

            raw = json.loads((Path(td) / "whatsapp" / "recent-history.json").read_text(encoding="utf-8"))
            rows = raw["conversations"]["phone:+4917612345678"]
            self.assertEqual([row["provider_message_id"] for row in rows], ["m-2", "m-3", "m-4"])
            self.assertNotIn("data_base64", rows[-1]["media"])

    def test_endpoint_lookup_deduplicates_phone_and_jid_keys(self):
        with tempfile.TemporaryDirectory() as td:
            cache = RecentHistoryCache(td, limit=10)
            cache.store([self.payload(1), self.payload(2)])
            endpoint = ContactEndpoint(
                normalized_phone="+4917612345678",
                provider_address="4917612345678@s.whatsapp.net",
            )

            rows = cache.for_endpoint(endpoint)
            self.assertEqual([row["provider_message_id"] for row in rows], ["m-2", "m-1"])


if __name__ == "__main__":
    unittest.main()
