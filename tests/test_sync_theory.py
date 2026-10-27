"""Offline regression checks for author verification, deduplication, and stale data."""
import copy
from datetime import date, datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_theory", ROOT / "scripts/sync_theory.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
CUTOFF = date(2024, 10, 27)
RESEARCHERS = [{"id": "researcher-a", "name": "Researcher A", "url": "https://example.edu/a", "openalex_id": "A100"},
               {"id": "researcher-b", "name": "Researcher B", "url": "https://example.edu/b", "openalex_id": "A200"}]
CONFIG = {"lookback_days": 730, "max_papers": 80, "researchers": RESEARCHERS}


def work(number=1, *, authors=("A100",), title="An auxiliary-field quantum Monte Carlo method", doi=None, published="2026-10-01", kind="article"):
    return {"id": f"https://openalex.org/W{number}", "doi": doi or f"https://doi.org/10.1234/paper{number}", "title": title,
            "publication_date": published, "type": kind, "is_retracted": False,
            "authorships": [{"author": {"id": f"https://openalex.org/{author}", "display_name": "Author " + author}} for author in authors],
            "primary_location": {"source": {"display_name": "Journal of Theory"}, "landing_page_url": "https://example.edu/paper"},
            "primary_topic": {"display_name": "Quantum Chemistry"}, "topics": []}


def response(works):
    return {"meta": {"count": len(works)}, "results": works}


def fetcher_for(a=None, b=None):
    records = {"A100": a if a is not None else [work(1)], "A200": b if b is not None else [work(2, authors=("A200",))]}
    def fetcher(url):
        filters = parse_qs(urlsplit(url).query)["filter"][0]
        author = filters.split(",")[0].split(":")[-1]
        value = records[author]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(response(value))
    return fetcher


class TheorySyncTests(unittest.TestCase):
    def test_query_is_exact_bounded_and_only_key_from_parameter(self):
        url = sync.works_url(RESEARCHERS[0], CUTOFF, TODAY, "secret-key")
        query = parse_qs(urlsplit(url).query)
        self.assertIn("author.id:A100", query["filter"][0])
        self.assertIn("to_publication_date:2026-10-27", query["filter"][0])
        self.assertIn("is_retracted:false", query["filter"][0])
        self.assertEqual(query["per_page"], ["100"])
        self.assertNotIn("cursor", query)
        self.assertEqual(query["api_key"], ["secret-key"])
        self.assertNotIn("api_key", sync.works_url(RESEARCHERS[0], CUTOFF, TODAY))

    def test_wrong_author_future_old_retracted_and_invalid_dates_excluded(self):
        cases = [work(authors=("A999",)), work(published="2027-01-01"), work(published="2023-01-01"),
                 work(published="2026-02-30"), work(kind="dataset"), {**work(), "is_retracted": True}, {**work(), "id": "https://evil.test/W1"}]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertIsNone(sync.normalize_work(raw, RESEARCHERS[0], CUTOFF, TODAY))
        self.assertIsNotNone(sync.normalize_work(work(), RESEARCHERS[0], CUTOFF, TODAY))

    def test_normalization_keeps_plain_text_and_rejects_unsafe_landing_url(self):
        raw = work(title='<img src=x> Quantum "theory" & methods', doi="not-a-doi")
        raw["primary_location"]["landing_page_url"] = "javascript:alert(1)"
        actual = sync.normalize_work(raw, RESEARCHERS[0], CUTOFF, TODAY)
        self.assertEqual(actual["title"], raw["title"])
        self.assertEqual(actual["doi"], "")
        self.assertEqual(actual["url"], "https://openalex.org/W1")
        self.assertEqual(sync.normalize_doi("HTTPS://DOI.ORG/10.1234/ABC"), "10.1234/abc")

    def test_theory_filter_rejects_welding_and_uses_topics_for_method_papers(self):
        welding = work(title="Atomic-to-macroscopic design of invar 36 joints")
        welding["primary_topic"] = {"display_name": "Welding Techniques"}
        normalized = sync.normalize_work(welding, RESEARCHERS[0], CUTOFF, TODAY)
        self.assertFalse(sync.theory_related(normalized))
        for title in ("Improved AFQMC overlap evaluation", "A new coupled-cluster method", "Spin correlations in a lattice"):
            normalized["title"] = title
            self.assertTrue(sync.theory_related(normalized))
        normalized.update(title="Spindle manufacturing with atomic precision", topics=[])
        self.assertFalse(sync.theory_related(normalized))
        normalized.update(title="A more efficient algorithm", topics=["Electronic Structure Calculations"])
        self.assertTrue(sync.theory_related(normalized))

    def test_coauthored_doi_duplicates_merge_followed_authors(self):
        first = work(1, authors=("A100", "A200"), doi="https://doi.org/10.1234/same")
        second = work(2, authors=("A100", "A200"), doi="10.1234/SAME")
        actual = sync.refresh(CONFIG, fetcher=fetcher_for([first], [second]), now=NOW)
        self.assertEqual(len(actual["works"]), 1)
        self.assertEqual(actual["works"][0]["followed_authors"], ["researcher-a", "researcher-b"])

    def test_exact_title_versions_prefer_article_and_keep_different_titles(self):
        raws = [work(1, kind="preprint", published="2026-10-20"), work(2, published="2026-10-10"),
                work(3, title="A genuinely different quantum Monte Carlo method")]
        actual = sync.deduplicate([sync.normalize_work(item, RESEARCHERS[0], CUTOFF, TODAY) for item in raws])
        self.assertEqual(len(actual), 2)
        self.assertIn("https://openalex.org/W2", [item["id"] for item in actual])
        self.assertNotIn("https://openalex.org/W1", [item["id"] for item in actual])

    def test_same_title_journal_editions_prefer_anie_and_dedupe(self):
        raws = [work(1, doi="10.1002/ange.20261234"), work(2, doi="10.1002/anie.20261234")]
        actual = sync.deduplicate([sync.normalize_work(item, RESEARCHERS[0], CUTOFF, TODAY) for item in raws])
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["doi"], "10.1002/anie.20261234")

    def test_partial_failure_retains_old_source_and_marks_fresh_source(self):
        old = sync.refresh(CONFIG, fetcher=fetcher_for(), now=NOW)
        later = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        actual = sync.refresh(CONFIG, old, fetcher=fetcher_for(a=sync.SyncError("secret should never appear"), b=[work(3, authors=("A200",))]), now=later)
        self.assertEqual(actual["sources"][0]["status"], "cached")
        self.assertEqual(actual["sources"][0]["updated_at"], old["sources"][0]["updated_at"])
        self.assertEqual(actual["sources"][1]["status"], "ok")
        self.assertEqual(actual["fetched_at"], old["fetched_at"])
        self.assertNotEqual(actual["last_attempt_at"], old["last_attempt_at"])
        self.assertEqual({item["id"] for item in actual["works"]}, {"https://openalex.org/W1", "https://openalex.org/W3"})
        self.assertNotIn("secret", json.dumps(actual))

    def test_complete_failure_keeps_records_and_first_run_is_honest(self):
        old = sync.refresh(CONFIG, fetcher=fetcher_for(), now=NOW)
        failed = fetcher_for(a=sync.SyncError("offline"), b=sync.SyncError("offline"))
        actual = sync.refresh(CONFIG, old, fetcher=failed, now=NOW)
        self.assertEqual(actual["works"], old["works"])
        self.assertEqual([item["status"] for item in actual["sources"]], ["cached", "cached"])
        first = sync.refresh(CONFIG, fetcher=failed, now=NOW)
        self.assertEqual(first["works"], [])
        self.assertIsNone(first["fetched_at"])
        self.assertEqual([item["status"] for item in first["sources"]], ["unavailable", "unavailable"])

    def test_empty_new_source_is_valid_but_unexpected_empty_preserves_cache(self):
        config = {**CONFIG, "researchers": RESEARCHERS[:1]}
        first = sync.refresh(config, fetcher=fetcher_for(a=[]), now=NOW)
        self.assertEqual(first["sources"][0]["status"], "ok")
        old = sync.refresh(config, fetcher=fetcher_for(), now=NOW)
        actual = sync.refresh(config, old, fetcher=fetcher_for(a=[]), now=NOW)
        self.assertEqual(actual["sources"][0]["status"], "cached")
        self.assertEqual(actual["works"], old["works"])
        unrelated = work(title="New welding techniques")
        unrelated["primary_topic"] = {"display_name": "Welding Techniques"}
        filtered = sync.refresh(config, old, fetcher=fetcher_for(a=[unrelated]), now=NOW)
        self.assertEqual(filtered["sources"][0]["status"], "ok")
        self.assertEqual(filtered["works"], [])

    def test_malformed_response_is_not_successful_empty_data(self):
        config = {**CONFIG, "researchers": RESEARCHERS[:1]}
        old = sync.refresh(config, fetcher=fetcher_for(), now=NOW)
        for response_value in ({}, {"meta": {"count": 3}, "results": []}, {"meta": {"count": True}, "results": []}, response([work(authors=("A999",))])):
            with self.subTest(response=response_value):
                actual = sync.refresh(config, old, fetcher=lambda url: response_value, now=NOW)
                self.assertEqual(actual["sources"][0]["status"], "cached")
                self.assertEqual(actual["works"], old["works"])

    def test_removed_or_reassigned_sources_are_not_retained(self):
        old = sync.refresh(CONFIG, fetcher=fetcher_for(), now=NOW)
        config = {**CONFIG, "researchers": [{**RESEARCHERS[0], "openalex_id": "A900"}]}
        actual = sync.refresh(config, old, fetcher=lambda url: (_ for _ in ()).throw(sync.SyncError("offline")), now=NOW)
        self.assertEqual(actual["works"], [])
        self.assertEqual(actual["sources"][0]["status"], "unavailable")

    def test_invalid_previous_cache_is_not_overwritten(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as folder:
            folder = Path(folder)
            config, output = folder / "sources.json", folder / "cache.json"
            config.write_text(json.dumps(CONFIG), encoding="utf-8")
            output.write_text('{"works": null}', encoding="utf-8")
            original = output.read_bytes()
            with self.assertRaises(sync.SyncError):
                sync.sync(config, output, fetcher=fetcher_for(), now=NOW)
            self.assertEqual(output.read_bytes(), original)

    def test_failed_sync_writes_stale_status_and_strict_mode_fails(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as folder:
            folder = Path(folder)
            config, output = folder / "sources.json", folder / "cache.json"
            config.write_text(json.dumps(CONFIG), encoding="utf-8")
            sync.sync(config, output, fetcher=fetcher_for(), now=NOW)
            failed = fetcher_for(a=sync.SyncError("offline"), b=sync.SyncError("offline"))
            with self.assertRaises(sync.SyncError), patch("sys.stderr", new_callable=io.StringIO):
                sync.sync(config, output, fetcher=failed, now=NOW, strict=True)
            actual = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(actual["works"]), 2)
            self.assertEqual(actual["sources"][0]["status"], "cached")
            self.assertEqual(list(folder.glob(".theory-*.tmp")), [])

    def test_network_errors_never_expose_api_keys_or_response_bodies(self):
        secret_url = "https://api.openalex.org/works?api_key=secret-value"
        failure = HTTPError(secret_url, 401, "secret-value", {}, io.BytesIO(b"private response"))
        with patch.object(sync, "urlopen", side_effect=failure):
            with self.assertRaises(sync.SyncError) as caught:
                sync.fetch_json(secret_url)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        with patch.object(sync, "urlopen", side_effect=URLError(secret_url)), patch.object(sync.time, "sleep"):
            with self.assertRaises(sync.SyncError) as caught:
                sync.fetch_json(secret_url)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
