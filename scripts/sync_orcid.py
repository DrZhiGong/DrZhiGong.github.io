#!/usr/bin/env python3
"""Refresh a static publication cache from the ORCID public API (Python 3.10+).

Only public record data is requested; no OAuth token or third-party package is
needed. A failed or incomplete refresh never replaces the last good snapshot.
See https://info.orcid.org/documentation/api-tutorials/api-tutorial-read-data-on-a-record/
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


DEFAULT_ORCID = "0000-0002-1316-4675"
API_ROOT = "https://pub.orcid.org/v3.0"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "publications.json"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class SyncError(Exception):
    """A refresh cannot safely replace the saved snapshot."""


def text_value(value: object) -> str:
    """Extract ORCID's {value: ...} text without interpreting markup."""
    if isinstance(value, dict):
        value = value.get("value")
    return " ".join(value.split()) if isinstance(value, str) else ""


def validate_orcid(orcid: str) -> str:
    orcid = orcid.removeprefix("https://orcid.org/").strip().rstrip("/")
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", orcid):
        raise SyncError("Expected an ORCID iD such as 0000-0002-1316-4675.")
    digits = orcid.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    if digits[-1] != ("X" if check == 10 else str(check)):
        raise SyncError("The ORCID iD checksum is invalid.")
    return orcid


def fetch_json(url: str, *, timeout: float = 20, attempts: int = 3) -> dict:
    """Fetch bounded JSON; retry only transient HTTP/network failures."""
    request = Request(url, headers={
        "Accept": "application/vnd.orcid+json",
        "User-Agent": "ZhiGongAcademicWebsite/1.0 (public ORCID publication sync)",
    })
    for attempt in range(attempts):
        retry_after = 0.0
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SyncError("ORCID response exceeded the size limit.")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise SyncError("ORCID did not return valid JSON.") from exc
            if not isinstance(payload, dict) or "error-code" in payload:
                raise SyncError("ORCID returned an invalid response or API error.")
            return payload
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise SyncError(f"ORCID request failed with HTTP {exc.code}.") from exc
            try:
                retry_after = min(10.0, max(0.0, float(exc.headers.get("Retry-After", 0))))
            except (ValueError, AttributeError):
                pass
            failure = f"HTTP {exc.code}"
        except (URLError, TimeoutError, OSError) as exc:
            failure = str(exc)
        if attempt + 1 < attempts:
            time.sleep(max(retry_after, min(2 ** attempt, 8)))
    raise SyncError(f"ORCID request failed after {attempts} attempt(s): {failure}")


def normalize_doi(value: object) -> str:
    doi = unquote(text_value(value)).strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    doi = doi.strip().lower()
    if re.fullmatch(r"10\.\d{4,9}/[^\s<>\"']+", doi):
        return doi
    return ""


def get_doi(record: dict) -> str:
    ids = record.get("external-ids") or {}
    for identifier in ids.get("external-id", []) if isinstance(ids, dict) else []:
        if not isinstance(identifier, dict):
            raise SyncError("Malformed ORCID external identifier.")
        if (identifier.get("external-id-type") == "doi"
                and identifier.get("external-id-relationship", "self") == "self"):
            doi = normalize_doi(identifier.get("external-id-value"))
            if doi:
                return doi
    return ""


def safe_url(value: object) -> str:
    url = text_value(value)
    try:
        parts = urlsplit(url)
        if (parts.scheme in {"https", "http"} and parts.hostname
                and not parts.username and not parts.password
                and not re.search(r"[\s<>\"']", url)):
            return url
    except ValueError:
        pass
    return ""


def publication_date(record: dict) -> tuple[int | None, str]:
    raw = record.get("publication-date")
    if raw is None:
        return None, ""
    if not isinstance(raw, dict):
        raise SyncError("Malformed ORCID publication date.")
    year = text_value(raw.get("year"))
    month = text_value(raw.get("month"))
    day = text_value(raw.get("day"))
    if not year:
        if month or day:
            raise SyncError("ORCID date has a month/day but no year.")
        return None, ""
    try:
        y = int(year)
        date(y, int(month or 1), int(day or 1))
        if day and not month:
            raise ValueError("day without month")
    except (ValueError, TypeError) as exc:
        raise SyncError("Invalid ORCID publication date.") from exc
    formatted = f"{y:04d}"
    if month:
        formatted += f"-{int(month):02d}"
    if day:
        formatted += f"-{int(day):02d}"
    return y, formatted


def put_code(record: dict) -> str:
    code = str(record.get("put-code", ""))
    if not re.fullmatch(r"[1-9]\d*", code):
        raise SyncError("An ORCID work is missing a valid put-code.")
    return code


def preference(record: dict) -> tuple:
    try:
        display = int(record.get("display-index", 0))
    except (TypeError, ValueError):
        display = 0
    return (-display, -len(publication_date(record)[1]), int(put_code(record)))


def normalized_work(summary: dict, detail: dict, group: dict, orcid: str) -> dict:
    code = put_code(summary)
    if put_code(detail) != code:
        raise SyncError("ORCID returned details for the wrong work.")
    expected_path = f"/{orcid}/work/{code}"
    if detail.get("path") != expected_path or detail.get("visibility") != "public":
        raise SyncError("ORCID work details are not the requested public work.")
    record = {**summary, **{key: value for key, value in detail.items() if value is not None}}
    title = record.get("title")
    title = text_value(title.get("title")) if isinstance(title, dict) else ""
    if not title:
        raise SyncError("An ORCID work has no title; keeping the saved snapshot.")
    doi = get_doi(record) or get_doi(group)
    year, date_string = publication_date(record)
    authors = []
    contributors = record.get("contributors") or {}
    if not isinstance(contributors, dict):
        raise SyncError("Malformed ORCID contributors.")
    names = contributors.get("contributor") or []
    if not isinstance(names, list):
        raise SyncError("Malformed ORCID contributor list.")
    for contributor in names:
        if not isinstance(contributor, dict):
            raise SyncError("Malformed ORCID contributor.")
        name = text_value(contributor.get("credit-name"))
        if name:
            authors.append(name)
    profile = f"https://orcid.org/{orcid}"
    url = f"https://doi.org/{quote(doi, safe='/:;(),.+-_')}" if doi else safe_url(record.get("url"))
    return {
        "id": f"doi:{doi}" if doi else f"orcid:{code}",
        "title": title,
        "authors": authors,
        "journal": text_value(record.get("journal-title")),
        "year": year,
        "date": date_string,
        "type": text_value(record.get("type")),
        "doi": doi,
        "url": url or profile,
        "put_code": code,
        "put_codes": sorted({put_code(item) for item in group["work-summary"]}, key=int),
        "orcid_url": profile,
    }


def deduplicate(works: list[dict]) -> list[dict]:
    """Merge both shared DOIs and shared put-codes, including transitive matches."""
    result = []
    for work in works:
        matches = []
        codes = set(work["put_codes"])
        for index, previous in enumerate(result):
            if ((work["doi"] and work["doi"] == previous["doi"])
                    or codes.intersection(previous["put_codes"])):
                matches.append(index)
        if not matches:
            result.append(work)
            continue
        # Preserve the first preferred record; fill missing metadata only.
        merged = result[matches[0]]
        for other in [work] + [result[index] for index in matches[1:]]:
            for key in ("authors", "journal", "date", "year", "doi", "type"):
                if not merged[key] and other[key]:
                    merged[key] = other[key]
            merged["put_codes"] = sorted(set(merged["put_codes"] + other["put_codes"]), key=int)
        if merged["doi"]:
            merged["id"] = f"doi:{merged['doi']}"
            merged["url"] = f"https://doi.org/{quote(merged['doi'], safe='/:;(),.+-_')}"
        for index in reversed(matches[1:]):
            result.pop(index)
    # ISO partial dates sort naturally; missing month/day fall below known dates.
    result.sort(key=lambda work: (work["title"].casefold(), work["id"]))
    result.sort(key=lambda work: work["date"], reverse=True)
    return result


def collect_works(orcid: str, fetcher=fetch_json) -> list[dict]:
    payload = fetcher(f"{API_ROOT}/{orcid}/works")
    if payload.get("path") != f"/{orcid}/works" or not isinstance(payload.get("group"), list):
        raise SyncError("ORCID returned an incomplete or unexpected works response.")
    works = []
    groups = payload["group"]
    if len(groups) > 10000:
        raise SyncError("Unexpectedly large ORCID record.")
    for group in groups:
        if not isinstance(group, dict):
            raise SyncError("Malformed ORCID work group.")
        summaries = group.get("work-summary")
        if not isinstance(summaries, list) or not summaries:
            raise SyncError("An ORCID work group has no summaries.")
        if not all(isinstance(item, dict) for item in summaries):
            raise SyncError("Malformed ORCID work summary.")
        ordered = sorted(summaries, key=preference)
        summary = ordered[0]
        detail = fetcher(f"{API_ROOT}/{orcid}/work/{put_code(summary)}")
        work = normalized_work(summary, detail, group, orcid)
        # Some preferred sources omit authors. Look at other assertions in that
        # same ORCID group without changing its preferred title or other fields.
        if not work["authors"]:
            for alternate in ordered[1:]:
                alternate_detail = fetcher(f"{API_ROOT}/{orcid}/work/{put_code(alternate)}")
                alternate_work = normalized_work(alternate, alternate_detail, group, orcid)
                if alternate_work["authors"]:
                    work["authors"] = alternate_work["authors"]
                    break
        works.append(work)
    return deduplicate(works)


def load_snapshot(path: Path, orcid: str) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SyncError(f"Cannot read existing publication cache: {path}") from exc
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("orcid") != orcid or not isinstance(payload.get("works"), list)):
        raise SyncError("Existing cache is invalid or belongs to a different ORCID iD.")
    for work in payload["works"]:
        if not isinstance(work, dict) or not work.get("id") or not work.get("title"):
            raise SyncError("Existing cache contains an invalid publication.")
    return payload


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def sync(orcid: str, output: Path, *, allow_empty: bool = False, fetcher=fetch_json) -> dict:
    orcid = validate_orcid(orcid)
    previous = load_snapshot(output, orcid)
    works = collect_works(orcid, fetcher)
    if not works and previous and previous["works"] and not allow_empty:
        raise SyncError("ORCID returned zero public works. Existing results were kept. "
                        "If this removal is intentional, rerun with --allow-empty.")
    payload = {
        "schema_version": 1,
        "orcid": orcid,
        "source": f"https://orcid.org/{orcid}",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "works": works,
    }
    atomic_write(output, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orcid", default=DEFAULT_ORCID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offline", action="store_true", help="Validate the saved cache without making requests.")
    parser.add_argument("--allow-empty", action="store_true", help="Allow an explicitly empty ORCID record to replace existing works.")
    parser.add_argument("--timeout", type=float, default=20, help="Per-request timeout in seconds (0 < value <= 120).")
    parser.add_argument("--attempts", type=int, default=3, help="Maximum attempts per request (1 to 5).")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 120 or not 1 <= args.attempts <= 5:
        parser.error("--timeout must be > 0 and <= 120; --attempts must be 1 to 5.")
    try:
        orcid = validate_orcid(args.orcid)
        if args.offline:
            payload = load_snapshot(args.output, orcid)
            if payload is None:
                raise SyncError("No saved publication cache exists for offline use.")
            print(f"Validated {len(payload['works'])} cached works: {args.output}")
        else:
            def fetcher(url):
                return fetch_json(url, timeout=args.timeout, attempts=args.attempts)
            payload = sync(orcid, args.output, allow_empty=args.allow_empty, fetcher=fetcher)
            print(f"Saved {len(payload['works'])} public ORCID works: {args.output}")
    except (SyncError, OSError) as exc:
        print(f"ORCID sync failed; existing cache was not replaced. {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
