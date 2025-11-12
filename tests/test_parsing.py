import unittest
from datetime import date
from pathlib import Path

from arxiv_to_feishu import filter_by_date_window, parse_all_items


SAMPLE_SEARCH = Path(__file__).resolve().parent.parent / "sample_data" / "sample_search.html"


class ParseSampleTest(unittest.TestCase):
    def setUp(self):
        self.html = SAMPLE_SEARCH.read_text(encoding="utf-8")
        self.items = parse_all_items(self.html)

    def test_parse_all_items_returns_expected(self):
        self.assertEqual(len(self.items), 2)

        first, second = self.items
        self.assertEqual(first["title"], "Sample detection of dark matter")
        self.assertEqual(first["authors"], "A. Researcher, B. Scientist")
        self.assertEqual(first["cat"], "hep-ph")
        self.assertEqual(first["announced_date"], date(2023, 10, 31))
        self.assertTrue(first["abs"].endswith("2310.12345"))
        self.assertTrue(first["pdf"].endswith("2310.12345.pdf"))
        self.assertIn("dark matter", first["abstract"].lower())

        self.assertEqual(second["title"], "Detector calibration update")
        self.assertEqual(second["announced_date"], date(2023, 10, 30))

    def test_filter_by_date_window(self):
        filtered = filter_by_date_window(self.items, date(2023, 10, 30), date(2023, 10, 30))
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["title"], "Detector calibration update")


if __name__ == "__main__":
    unittest.main()
