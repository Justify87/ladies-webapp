import unittest
from pathlib import Path


class PricingUiTests(unittest.TestCase):
    def test_partial_review_shows_effective_revision_preview(self):
        source = (Path(__file__).resolve().parents[1] / "ladies" / "static" / "pricing.js").read_text(encoding="utf-8")
        self.assertIn("effective_proposal", source)
        self.assertIn("Wird als neue Revision gespeichert", source)
        self.assertIn("Unveränderte bestehende Einträge bleiben", source)


if __name__ == "__main__":
    unittest.main()
