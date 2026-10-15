"""Offline regression tests for the publication synchronizer."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "sync_orcid.py"
SPEC = importlib.util.spec_from_file_location("sync_orcid", MODULE)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
ORCID = sync.DEFAULT_ORCID


def fixture_work(code, title="A research article", doi="10.1234/abc", year="2026", display="1"):
    return {
        "put-code": code,
        "path": f"/{ORCID}/work/{code}",
        "visibility": "public",
        "display-index": display,
        "title": {"title": {"value": title}},
        "journal-title": {"value": "Test Journal"},
        "type": "journal-article",
        "publication-date": {"year": {"value": year}, "month": None, "day": None},
        "external-ids": {"external-id": [{
            "external-id-type": "doi", "external-id-value": doi,
            "external-id-relationship": "self",
        }]} if doi else {"external-id": []},
        "url": {"value": "https://example.org/paper"},
        "contributors": {"contributor": [
            {"credit-name": {"value": "Zhi Gong"}},
            {"credit-name": {"value": "A. Coauthor"}},
        ]},
    }


def fixture_api(groups):
    payload = {"path": f"/{ORCID}/works", "group": [
        {"work-summary": group} for group in groups
    ]}
    details = {str(work["put-code"]): work for group in groups for work in group}

    def fetcher(url):
        return copy.deepcopy(payload if url.endswith("/works") else details[url.rsplit("/", 1)[-1]])
    return fetcher


class PublicationTests(unittest.TestCase):
    def test_preferred_assertion_doi_deduplication_and_sorting(self):
        lower = fixture_work(12, "Other source", "HTTPS://DOI.ORG/10.1234/ABC", display="0")
        preferred = fixture_work(13, "Preferred title", "10.1234/abc", display="2")
        older = fixture_work(14, "Earlier work", "10.5678/def", year="2023")
        duplicate = fixture_work(15, "Duplicate group", "doi:10.1234/AbC")
        works = sync.collect_works(ORCID, fixture_api([[lower, preferred], [older], [duplicate]]))
        self.assertEqual([work["title"] for work in works], ["Preferred title", "Earlier work"])
        self.assertEqual(works[0]["put_codes"], ["12", "13", "15"])
        self.assertEqual(works[0]["url"], "https://doi.org/10.1234/abc")
        self.assertEqual(works[0]["authors"], ["Zhi Gong", "A. Coauthor"])

    def test_alternate_authors_preserve_preferred_title(self):
        primary = fixture_work(1, "Preferred", display="2")
        primary["contributors"] = None
        alternate = fixture_work(2, "Alternate", display="1")
        works = sync.collect_works(ORCID, fixture_api([[primary, alternate]]))
        self.assertEqual(works[0]["title"], "Preferred")
        self.assertEqual(works[0]["authors"], ["Zhi Gong", "A. Coauthor"])

    def test_parent_doi_and_unsafe_url_are_not_publication_links(self):
        work = fixture_work(1)
        work["external-ids"]["external-id"][0]["external-id-relationship"] = "part-of"
        work["url"] = {"value": "javascript:alert(1)"}
        actual = sync.collect_works(ORCID, fixture_api([[work]]))[0]
        self.assertEqual(actual["doi"], "")
        self.assertEqual(actual["id"], "orcid:1")
        self.assertEqual(actual["url"], f"https://orcid.org/{ORCID}")

    def test_partial_dates_and_missing_dates(self):
        work = fixture_work(1)
        work["publication-date"].update({"month": {"value": "02"}, "day": {"value": "28"}})
        self.assertEqual(sync.publication_date(work), (2026, "2026-02-28"))
        work["publication-date"]["day"] = {"value": "30"}
        with self.assertRaises(sync.SyncError):
            sync.publication_date(work)
        self.assertEqual(sync.publication_date({}), (None, ""))

    def test_untrusted_title_is_kept_as_plain_data(self):
        work = fixture_work(1, '<script>alert("x")</script>')
        actual = sync.collect_works(ORCID, fixture_api([[work]]))[0]
        self.assertEqual(actual["title"], '<script>alert("x")</script>')

    def test_partial_failure_preserves_exact_cache_bytes(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parents[1] / "tests") as folder:
            path = Path(folder) / "publications.json"
            sync.sync(ORCID, path, fetcher=fixture_api([[fixture_work(1)]]))
            original = path.read_bytes()
            working_fetcher = fixture_api([[fixture_work(1)], [fixture_work(2, doi="10.1234/new")]])

            def partial_failure(url):
                if url.endswith("/work/2"):
                    raise sync.SyncError("HTTP 503")
                return working_fetcher(url)

            with self.assertRaises(sync.SyncError):
                sync.sync(ORCID, path, fetcher=partial_failure)
            self.assertEqual(path.read_bytes(), original)

    def test_malformed_response_and_wrong_detail_preserve_cache(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parents[1] / "tests") as folder:
            path = Path(folder) / "publications.json"
            sync.sync(ORCID, path, fetcher=fixture_api([[fixture_work(1)]]))
            original = path.read_bytes()
            for payload in ({}, {"group": [], "path": "/wrong/works"},
                            {"path": f"/{ORCID}/works", "group": [{"work-summary": []}]}):
                with self.subTest(payload=payload), self.assertRaises(sync.SyncError):
                    sync.sync(ORCID, path, fetcher=lambda url: payload)
                self.assertEqual(path.read_bytes(), original)
            work = fixture_work(1)
            broken = copy.deepcopy(work)
            broken["put-code"] = 99
            fetcher = fixture_api([[work]])
            with self.assertRaises(sync.SyncError):
                sync.sync(ORCID, path, fetcher=lambda url: broken if "/work/" in url else fetcher(url))
            self.assertEqual(path.read_bytes(), original)

    def test_zero_requires_explicit_opt_in_to_remove_saved_results(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parents[1] / "tests") as folder:
            path = Path(folder) / "publications.json"
            sync.sync(ORCID, path, fetcher=fixture_api([[fixture_work(1)]]))
            original = path.read_bytes()
            with self.assertRaises(sync.SyncError):
                sync.sync(ORCID, path, fetcher=fixture_api([]))
            self.assertEqual(path.read_bytes(), original)
            result = sync.sync(ORCID, path, fetcher=fixture_api([]), allow_empty=True)
            self.assertEqual(result["works"], [])
            self.assertEqual(json.loads(path.read_text())["works"], [])

    def test_offline_validates_without_network(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parents[1] / "tests") as folder:
            path = Path(folder) / "publications.json"
            sync.sync(ORCID, path, fetcher=fixture_api([]))
            with patch.object(sync, "urlopen", side_effect=AssertionError("network not allowed")):
                self.assertEqual(sync.main(["--offline", "--output", str(path)]), 0)

    def test_orcid_checksum_validation(self):
        self.assertEqual(sync.validate_orcid(f"https://orcid.org/{ORCID}"), ORCID)
        with self.assertRaises(sync.SyncError):
            sync.validate_orcid("0000-0002-1316-4676")


class FetchTests(unittest.TestCase):
    def test_transient_errors_retry_then_parse_json(self):
        throttled = HTTPError("https://example.org", 429, "Slow down", {"Retry-After": "1"}, None)
        with patch.object(sync, "urlopen", side_effect=[throttled, io.BytesIO(b'{"group": []}')]) as request:
            with patch.object(sync.time, "sleep") as sleep:
                self.assertEqual(sync.fetch_json("https://example.org"), {"group": []})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_permanent_http_error_is_not_retried(self):
        denied = HTTPError("https://example.org", 404, "Missing", {}, None)
        with patch.object(sync, "urlopen", side_effect=denied) as request:
            with self.assertRaises(sync.SyncError):
                sync.fetch_json("https://example.org")
        self.assertEqual(request.call_count, 1)

    def test_network_failures_stop_after_bounded_attempts(self):
        with patch.object(sync, "urlopen", side_effect=URLError("offline")) as request:
            with patch.object(sync.time, "sleep"), self.assertRaises(sync.SyncError):
                sync.fetch_json("https://example.org", attempts=2)
        self.assertEqual(request.call_count, 2)

    def test_invalid_json_is_not_retried(self):
        with patch.object(sync, "urlopen", return_value=io.BytesIO(b"not json")) as request:
            with self.assertRaises(sync.SyncError):
                sync.fetch_json("https://example.org")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
