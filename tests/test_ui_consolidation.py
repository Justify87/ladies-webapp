import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiConsolidationTests(unittest.TestCase):
    def test_unified_search_patch_is_loaded_after_main_app(self):
        index = (ROOT / "ladies" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/static/ui_consolidation.css', index)
        self.assertIn('/static/ui_consolidation.js', index)
        self.assertLess(index.index('/static/app.js'), index.index('/static/ui_consolidation.js'))

    def test_home_is_rebuilt_from_existing_filter_ui(self):
        source = (ROOT / "ladies" / "static" / "ui_consolidation.js").read_text(encoding="utf-8")
        self.assertIn('renderHome = renderUnifiedHome', source)
        self.assertIn('renderFilter();', source)
        self.assertIn('Letzte Suchen', source)
        self.assertIn('homeSearchBtn', source)
        self.assertIn('openFilter = openUnifiedFilter', source)
        self.assertNotIn('countPersons', source)
        self.assertNotIn('quickSwipe', source)

    def test_my_data_no_longer_edits_price_or_duration(self):
        source = (ROOT / "ladies" / "static" / "ui_consolidation.js").read_text(encoding="utf-8")
        my_data = source.split('renderMyData = function', 1)[1].split('saveMyData = async function', 1)[0]
        self.assertNotIn('dataPrice', my_data)
        self.assertNotIn('dataDuration', my_data)
        self.assertNotIn('Preis &amp; Dauer', my_data)
        self.assertIn('Erfahrung &amp; Service', my_data)
        self.assertIn('Privatnotiz', my_data)

    def test_offer_tab_remains_the_price_list_surface(self):
        source = (ROOT / "ladies" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<h2>${ico(\'euro\')}Preislisten</h2>', source)
        self.assertIn('data-add-price', source)


if __name__ == "__main__":
    unittest.main()
