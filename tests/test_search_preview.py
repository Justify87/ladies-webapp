import unittest
from unittest.mock import patch

from ladies_v9.search_preview import fetch_search_preview_count


class _Response:
    def __init__(self, status_code=200, text="<html></html>"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SearchPreviewTests(unittest.TestCase):
    @patch('ladies_v9.search_preview.parse_search_config')
    @patch('ladies_v9.search_preview.http_get')
    def test_uses_page_one_server_count_without_pagination(self, http_get, parse_config):
        http_get.return_value = _Response()
        parse_config.return_value = {
            'advert_count': 37,
            'distance_searched': 20,
            'total_pages': 4,
        }
        result = fetch_search_preview_count(
            object(),
            city_slug='leipzig',
            city_display='Leipzig',
            radius_km=20,
            selected={'service': ['GV']},
        )
        self.assertEqual(result['count'], 37)
        self.assertEqual(result['count_source'], 'search-config')
        self.assertEqual(http_get.call_count, 1)

    @patch('ladies_v9.search_preview.http_get')
    def test_404_means_zero_results(self, http_get):
        http_get.return_value = _Response(status_code=404)
        result = fetch_search_preview_count(
            object(),
            city_slug='leipzig',
            city_display='Leipzig',
            radius_km=20,
        )
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['count_source'], 'page-1-404')

    @patch('ladies_v9.search_preview.parse_search_config')
    @patch('ladies_v9.search_preview.http_get')
    def test_preview_fails_closed_on_widened_radius(self, http_get, parse_config):
        http_get.return_value = _Response()
        parse_config.return_value = {'advert_count': 99, 'distance_searched': 50}
        with self.assertRaises(RuntimeError):
            fetch_search_preview_count(
                object(),
                city_slug='leipzig',
                city_display='Leipzig',
                radius_km=20,
            )


if __name__ == '__main__':
    unittest.main()
