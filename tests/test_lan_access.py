from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.lan_access import LanAccessManager


class LanAccessTests(unittest.TestCase):
    def test_pairing_token_is_not_a_device_session(self):
        with tempfile.TemporaryDirectory() as td:
            access = LanAccessManager(td)
            self.assertTrue(access.valid_pairing_token(access.pairing_token))
            self.assertFalse(access.authorized(access.pairing_token))

            session = access.issue_device_session()
            self.assertTrue(access.authorized(session))
            self.assertFalse(access.valid_pairing_token(session))

    def test_device_session_survives_restart_but_pairing_token_rotates(self):
        with tempfile.TemporaryDirectory() as td:
            first = LanAccessManager(td)
            old_pairing = first.pairing_token
            session = first.issue_device_session()

            second = LanAccessManager(td)
            self.assertNotEqual(second.pairing_token, old_pairing)
            self.assertFalse(second.valid_pairing_token(old_pairing))
            self.assertTrue(second.authorized(session))

    def test_legacy_master_cookie_is_migrated_to_session_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = "legacy-" + ("x" * 40)
            (root / "lan_access_token").write_text(legacy + "\n", encoding="utf-8")

            access = LanAccessManager(root)
            self.assertTrue(access.authorized(legacy))
            self.assertFalse((root / "lan_access_token").exists())
            stored = (root / "lan_device_sessions.json").read_text(encoding="utf-8")
            self.assertNotIn(legacy, stored)


if __name__ == "__main__":
    unittest.main()
