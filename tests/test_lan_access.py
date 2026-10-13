from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ladies_v9.lan_access import LanAccessManager, MAX_DEVICE_SESSIONS


class LanAccessTests(unittest.TestCase):
    def test_pairing_secret_is_not_a_device_session(self):
        with tempfile.TemporaryDirectory() as td:
            access = LanAccessManager(td)
            pairing = access.pairing_token
            self.assertFalse(access.validate_session(pairing))

            session = access.pair(pairing)
            self.assertIsNotNone(session)
            self.assertTrue(access.validate_session(session))
            self.assertIsNone(access.pair("not-the-pairing-token"))

    def test_session_survives_restart_while_pairing_token_rotates(self):
        with tempfile.TemporaryDirectory() as td:
            first = LanAccessManager(td)
            old_pairing = first.pairing_token
            session = first.pair(old_pairing)
            self.assertIsNotNone(session)

            second = LanAccessManager(td)
            self.assertNotEqual(second.pairing_token, old_pairing)
            self.assertTrue(second.validate_session(session))
            self.assertIsNone(second.pair(old_pairing))

    def test_legacy_token_is_hashed_then_raw_file_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = "legacy-" + ("x" * 40)
            (root / "lan_access_token").write_text(legacy + "\n", encoding="utf-8")

            access = LanAccessManager(root)
            self.assertTrue(access.validate_session(legacy))
            self.assertFalse((root / "lan_access_token").exists())
            stored = (root / "lan_access.json").read_text(encoding="utf-8")
            self.assertNotIn(legacy, stored)

    def test_device_sessions_are_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            access = LanAccessManager(td)
            newest = None
            for _ in range(MAX_DEVICE_SESSIONS + 5):
                newest = access.pair(access.pairing_token)
                self.assertIsNotNone(newest)

            state = json.loads((Path(td) / "lan_access.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["sessions"]), MAX_DEVICE_SESSIONS)
            self.assertTrue(access.validate_session(newest))


if __name__ == "__main__":
    unittest.main()
