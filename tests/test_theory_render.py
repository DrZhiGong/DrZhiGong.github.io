"""Reading-list rendering must preserve source status and escape remote text."""
from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_theory", ROOT / "scripts/render_theory.py")
theory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(theory)


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class TheoryRenderingTests(unittest.TestCase):
    def setUp(self):
        self.config = {"title": "Theory Updates", "description": "Recent papers.", "researchers": [
            {"id": "one", "name": "A Researcher", "affiliation": "University", "url": "https://example.edu/"},
            {"id": "two", "name": "Another Researcher", "affiliation": "Institute", "url": "https://example.org/"},
        ]}

    def paper(self, **changes):
        return {"id": "https://openalex.org/W123", "title": "A paper", "authors": ["A Researcher"],
                "publication_date": "2026-10-20", "year": 2026, "venue": "Journal", "type": "article",
                "doi": "10.1234/example", "url": "https://doi.org/10.1234/example", "followed_authors": ["one"], **changes}

    def test_remote_markup_and_unsafe_urls_are_never_interpreted(self):
        self.config["researchers"][0]["name"] = '<img src=x onerror="alert(1)">'
        self.config["researchers"][0]["url"] = "javascript:alert(1)"
        paper = self.paper(title='<script>alert("x")</script>', authors=['<img src=x>'],
                           venue='Journal " & <script>', url="javascript:alert(1)", doi="bad<doi>", id="W123\" onclick=alert(1)")
        html = theory.render_theory(self.config, {"works": [paper]})
        parsed = Elements(html)
        self.assertFalse(any(tag in {"img", "script"} for tag, _ in parsed.elements))
        self.assertNotIn('href="javascript:', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertFalse(any(name.startswith("on") for _, attrs in parsed.elements for name in attrs))

    def test_dates_are_sorted_and_unknown_sources_do_not_break_rendering(self):
        works = [self.paper(title="Old paper", publication_date="2024-01-02", year=2024),
                 self.paper(title="Undated paper", publication_date="bad", year=None, followed_authors=["unknown"]),
                 self.paper(title="Newest paper", publication_date="2026-10-20")]
        html = theory.render_theory(self.config, {"works": works})
        self.assertLess(html.index('<h2><a href="https://doi.org/10.1234/example">Newest paper'), html.index('<h2><a href="https://doi.org/10.1234/example">Old paper'))
        self.assertLess(html.index('<h2><a href="https://doi.org/10.1234/example">Old paper'), html.index('<h2><a href="https://doi.org/10.1234/example">Undated paper'))
        cards = [attrs for tag, attrs in Elements(html).elements if tag == "article" and attrs.get("class") == "theory-paper"]
        self.assertEqual(len(cards), 3)
        self.assertTrue(all("hidden" not in attrs for attrs in cards))
        self.assertEqual(cards[-1]["data-year"], "undated")
        self.assertEqual(cards[-1]["data-researchers"], "")

    def test_source_freshness_does_not_claim_a_failed_refresh_succeeded(self):
        html = theory.render_theory(self.config, {"fetched_at": "2026-10-27T10:00:00Z", "sources": [
            {"id": "one", "status": "ok", "updated_at": "2026-10-27T10:00:00Z"},
            {"id": "two", "status": "cached", "updated_at": "2026-10-20T08:00:00Z", "error": "secret raw failure"},
        ], "works": [self.paper()]})
        self.assertIn("Using saved results", html)
        self.assertIn("Last success 20 Oct 2026, 08:00 UTC", html)
        self.assertIn("Some sources could not be refreshed", html)
        self.assertNotIn("secret raw failure", html)

    def test_empty_cache_remains_a_useful_static_page(self):
        html = theory.render_theory(self.config, {})
        self.assertIn("No papers available yet", html)
        self.assertIn("No complete refresh recorded yet", html)
        self.assertIn("A Researcher", html)
        self.assertIn('id="theory-tools" hidden', html)

    def test_external_links_reject_credentials_and_controls(self):
        for url in ("javascript:alert(1)", "https://user:password@example.org/", "https://example.org/a\nb", "//example.org/"):
            self.assertEqual(theory.safe_url(url), "")
        self.assertEqual(theory.safe_url("https://example.org/paper"), "https://example.org/paper")


if __name__ == "__main__":
    unittest.main()
