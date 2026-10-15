"""Regression checks for publication rendering, draft privacy, and build safety."""
from datetime import date
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for name in ("href", "src"):
            if name in attrs:
                self.urls.append(attrs[name])


class BuildTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def post(self, metadata):
        path = self.folder / "2026-10-15-example.md"
        path.write_text(f'+++\ntitle = "A note"\ndate = "2026-10-15"\n{metadata}\n+++\n\n## Example\n\nText.', encoding="utf-8")
        return path

    def test_drafts_and_future_posts_never_publish(self):
        self.assertIsNone(site.parse_post(self.post("draft = true"), date(2026, 10, 20)))
        self.assertIsNone(site.parse_post(self.post("draft = false"), date(2026, 10, 14)))
        self.assertIsNone(site.parse_post(self.post(""), date(2026, 10, 20)))
        self.assertIn("<h2", site.parse_post(self.post("draft = false"), date(2026, 10, 20))["html"])
        with self.assertRaisesRegex(ValueError, "without quotes"):
            site.parse_post(self.post('draft = "false"'))

    def test_remote_text_is_escaped_and_script_urls_are_rejected(self):
        work = {"title": '<script>alert("x")</script>', "authors": ["<img src=x>"], "journal": "A & B", "url": "javascript:alert(1)", "year": 2026}
        html = site.publication(work)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("&lt;script&gt;", html)

    def test_refuses_to_delete_unmarked_existing_directory(self):
        keep = self.folder / "precious.txt"
        keep.write_text("Keep me", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unmarked"):
            site.build(output=self.folder)
        self.assertEqual(keep.read_text(), "Keep me")
        with self.assertRaises(ValueError):
            site.build(output=ROOT / "data")
        with self.assertRaises(ValueError):
            site.build(output=ROOT.parent / "outside")

    def test_publication_links_hide_orcid_but_preserve_publisher_links(self):
        work = {"title": "A paper", "orcid_url": "https://orcid.org/example/work/1"}
        for url in ["", "https://orcid.org/example", "https://pub.orcid.org/v3.0/example", "https://ORCID.ORG./example"]:
            self.assertNotIn('<a href=', site.publication({**work, "url": url}))
        self.assertIn('href="https://doi.org/10.1234/example"', site.publication({**work, "url": "https://doi.org/10.1234/example"}))

    def test_generated_site_links_metadata_and_private_sources(self):
        output = site.build(output=self.folder / "generated")
        pages = {}
        for path in output.rglob("*.html"):
            html = path.read_text(encoding="utf-8")
            self.assertIn('<html lang="en">', html)
            self.assertIn('name="description"', html)
            self.assertNotRegex(html, r"(?<!\d)\d{3}[-. ]\d{3}[-. ]\d{4}(?!\d)")
            self.assertNotIn("private draft template", html)
            parser = Links()
            parser.feed(html)
            pages[path] = parser
        for path, parser in pages.items():
            for url in parser.urls:
                parts = urlsplit(url)
                if parts.scheme or parts.netloc:
                    continue
                target = output / unquote(parts.path.lstrip("/")) if parts.path.startswith("/") else path.parent / unquote(parts.path)
                if not parts.path:
                    target = path
                elif target.is_dir():
                    target /= "index.html"
                self.assertTrue(target.exists(), f"Broken link in {path}: {url}")
                if parts.fragment and target in pages:
                    self.assertIn(parts.fragment, pages[target].ids, f"Broken anchor: {url}")
        self.assertFalse(list(output.rglob("*.pdf")))
        self.assertFalse(list(output.rglob("*.md")))
        self.assertFalse((output / "editor").exists())
        self.assertFalse((output / "scripts").exists())
        self.assertFalse((output / ".local").exists())
        self.assertTrue((output / "feed.xml").exists())
        self.assertTrue((output / "sitemap.xml").exists())
        for name in ["index.html", "about/index.html", "publications/index.html"]:
            self.assertNotIn("orcid", (output / name).read_text(encoding="utf-8").lower())
        expected = json.loads((ROOT / "data" / "publications.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((output / "data" / "publications.json").read_text(encoding="utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
