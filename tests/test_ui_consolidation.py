import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiConsolidationTests(unittest.TestCase):
    def test_unified_search_patch_is_loaded_after_main_app(self):
        index = (ROOT / "ladies" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/static/ui_consolidation.css', index)
        self.assertIn('/static/ui_consolidation.js', index)
        self.assertLess(index.index('/static/app.js'), index.index('/static/ui_consolidation.js'))

    def test_home_is_the_only_filter_destination(self):
        source = (ROOT / "ladies" / "static" / "ui_consolidation.js").read_text(encoding="utf-8")
        self.assertIn('renderHome = renderUnifiedHome', source)
        self.assertIn('openFilter = openUnifiedFilter', source)
        self.assertIn("setScreen('home')", source)
        self.assertIn("$('#discoverFilterBtn').onclick = openFilter", source)
        self.assertIn("$('#gridFilterBtn').onclick = openFilter", source)
        self.assertIn("legacyFilterLayer.classList.add('hidden')", source)

    def test_home_has_no_duplicate_title_or_explainer(self):
        source = (ROOT / "ladies" / "static" / "ui_consolidation.js").read_text(encoding="utf-8")
        home = source.split('function renderUnifiedHome()', 1)[1].split('function openUnifiedFilter()', 1)[0]
        self.assertNotIn('<h1>Suche &amp; Filter</h1>', home)
        self.assertNotIn('Lege hier deine Suche fest', home)
        self.assertIn('Letzte Suchen', home)
        self.assertIn('Suche starten', home)

    def test_live_result_preview_is_debounced_and_read_only(self):
        source = (ROOT / "ladies" / "static" / "ui_consolidation.js").read_text(encoding="utf-8")
        self.assertIn("api('/api/search-preview'", source)
        self.assertIn('setTimeout(() => runSearchPreview(signature), immediate ? 0 : 650)', source)
        self.assertIn('Suche starten · ${previewCount} Treffer', source)
        self.assertIn('AbortController', source)

    def test_full_service_catalog_is_rendered(self):
        source = (ROOT / "ladies" / "static" / "ui_consolidation.js").read_text(encoding="utf-8")
        self.assertIn('COMPLETE_SERVICE_FILTERS', source)
        self.assertIn("'Messebegleitung'", source)
        self.assertIn("'Dusch-/Badeservice'", source)
        self.assertIn('SERVER_FILTERS.service = COMPLETE_SERVICE_FILTERS', source)
        self.assertIn('ensureCompleteServiceFilters()', source)

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
