#!/usr/bin/env python3
"""Refresh a bounded, author-verified reading list from OpenAlex metadata.

One first-page query per followed author; no browser requests or full text.
Unavailable sources retain their last good records and carry a visible stale
status. Basic requests work without a key; an optional OPENALEX_API_KEY belongs
only in the environment (for example a GitHub Actions secret).
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = "https://api.openalex.org"
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
TYPES = {"article", "review", "preprint"}
THEORY_KEYWORDS = (
    "quantum", "electronic structure", "electron correlation", "afqmc", "auxiliary field", "monte carlo",
    "many body", "wavefunction", "wave function", "coupled cluster", "density functional", "density matrix",
    "hartree fock", "configuration interaction", "tensor network", "dmrg", "embedding", "ab initio",
    "excited state", "nonadiabatic", "non adiabatic", "green function", "green s function", "hamiltonian",
    "orbital", "spin", "theoretical chemistry", "computational chemistry", "molecular dynamics",
    "molecular simulation", "force field", "perturbation theory", "correlated electron", "renormalization",
)
DEFAULT_CONFIG = ROOT / "data/theory-sources.json"
DEFAULT_OUTPUT = ROOT / "data/theory-papers.json"


class SyncError(Exception):
    """A safe diagnostic, without request URLs, credentials, or remote bodies."""


def text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def identifier(value, prefix):
    value = text(value).removeprefix("https://openalex.org/")
    return value if re.fullmatch(prefix + r"[0-9]+", value) else ""


def safe_url(value):
    value = text(value)
    try:
        parts = urlsplit(value)
        return value if parts.scheme in {"https", "http"} and parts.netloc and not parts.username and not parts.password else ""
    except ValueError:
        return ""


def normalize_doi(value):
    value = unquote(text(value))
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I).lower()
    return value if re.fullmatch(r"10\.\d{4,9}/[^\s<>\"']+", value) else ""


def fetch_json(url, *, timeout=20, attempts=3):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ZhiGongAcademicWebsite/1.0 (theory reading list)"})
    for attempt in range(attempts):
        delay = min(2 ** attempt, 4)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SyncError("OpenAlex response exceeded the size limit.")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise SyncError("OpenAlex returned invalid metadata.") from None
            if not isinstance(result, dict):
                raise SyncError("OpenAlex returned invalid metadata.")
            return result
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise SyncError(f"OpenAlex request failed (HTTP {exc.code}).") from None
            try:
                delay = max(delay, min(float(exc.headers.get("Retry-After", 0)), 10))
            except (ValueError, TypeError):
                pass
        except (URLError, TimeoutError, OSError):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise SyncError("OpenAlex is temporarily unavailable or rate-limited.")


def validate_config(config):
    if not isinstance(config, dict):
        raise SyncError("Theory sources must be a JSON object.")
    for key, default, maximum in (("lookback_days", 730, 3650), ("max_papers", 80, 500)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise SyncError(f"{key} must be an integer between 1 and {maximum}.")
    researchers = config.get("researchers")
    keywords = config.get("theory_keywords", list(THEORY_KEYWORDS))
    if not isinstance(keywords, list) or not keywords or len(keywords) > 100 or any(not isinstance(word, str) or not word.strip() or len(word) > 100 for word in keywords):
        raise SyncError("theory_keywords must be a nonempty list of up to 100 short phrases.")
    if not isinstance(researchers, list) or not 1 <= len(researchers) <= 20:
        raise SyncError("Configure between 1 and 20 theory researchers.")
    slugs, authors = set(), set()
    for researcher in researchers:
        if not isinstance(researcher, dict):
            raise SyncError("Each theory researcher must be an object.")
        slug = researcher.get("id")
        author_id = identifier(researcher.get("openalex_id"), "A")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or slug in slugs:
            raise SyncError("Theory researchers need unique lowercase IDs.")
        if not author_id or author_id in authors or not text(researcher.get("name")) or not safe_url(researcher.get("url")):
            raise SyncError("Each researcher needs a name, a website, and a unique OpenAlex author ID.")
        slugs.add(slug)
        authors.add(author_id)
    return config


def works_url(researcher, cutoff, today, api_key=""):
    author_id = identifier(researcher["openalex_id"], "A")
    filters = [f"author.id:{author_id}", f"from_publication_date:{cutoff.isoformat()}",
               f"to_publication_date:{today.isoformat()}", "type:article|review|preprint", "is_retracted:false"]
    query = {"filter": ",".join(filters), "sort": "publication_date:desc", "per_page": 100,
             "select": "id,doi,title,authorships,publication_date,type,primary_location,is_retracted,primary_topic,topics"}
    if api_key:
        query["api_key"] = api_key
    return API_ROOT + "/works?" + urlencode(query)


def normalize_work(raw, researcher, cutoff, today):
    """Return plain metadata only when the exact configured author is present."""
    if not isinstance(raw, dict):
        return None
    work_id = identifier(raw.get("id"), "W")
    title = text(raw.get("title"))
    work_type = raw.get("type")
    if not work_id or not title or work_type not in TYPES or raw.get("is_retracted") is not False:
        return None
    raw_date = raw.get("publication_date")
    try:
        published = date.fromisoformat(raw_date) if isinstance(raw_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date) else None
    except ValueError:
        return None
    if published is None or not cutoff <= published <= today:
        return None
    authorships = raw.get("authorships")
    if not isinstance(authorships, list):
        return None
    identities, names = set(), []
    for authorship in authorships:
        author = authorship.get("author") if isinstance(authorship, dict) else None
        if not isinstance(author, dict):
            continue
        identities.add(identifier(author.get("id"), "A"))
        name = text(author.get("display_name"))
        if name and name not in names:
            names.append(name)
    if identifier(researcher["openalex_id"], "A") not in identities or not names:
        return None
    primary = raw.get("primary_location") or {}
    if not isinstance(primary, dict):
        primary = {}
    source = primary.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    doi = normalize_doi(raw.get("doi"))
    raw_topics = raw.get("topics") if isinstance(raw.get("topics"), list) else []
    primary_topic = raw.get("primary_topic")
    topics = sorted({text(topic.get("display_name")) for topic in [primary_topic, *raw_topics]
                     if isinstance(topic, dict) and text(topic.get("display_name"))})
    return {"id": "https://openalex.org/" + work_id, "doi": doi, "title": title, "authors": names,
            "publication_date": published.isoformat(), "year": published.year, "type": work_type,
            "venue": text(source.get("display_name")),
            "url": "https://doi.org/" + doi if doi else safe_url(primary.get("landing_page_url")) or "https://openalex.org/" + work_id,
            "followed_authors": [researcher["id"]], "topics": topics}


def theory_related(work, keywords=THEORY_KEYWORDS):
    # Word-boundary phrase matching makes "spin" different from "spindle".
    # Hyphen variants are normalized; titles and index topics are plain data.
    words = " " + re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", " ".join([work["title"], *work.get("topics", [])])).casefold()) + " "
    return any(" " + re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", phrase).casefold()).strip() + " " in words for phrase in keywords)


def collect_source(researcher, cutoff, today, fetcher, api_key="", had_cached=False, keywords=THEORY_KEYWORDS):
    payload = fetcher(works_url(researcher, cutoff, today, api_key))
    meta = payload.get("meta") if isinstance(payload, dict) else None
    results = payload.get("results") if isinstance(payload, dict) else None
    count = meta.get("count") if isinstance(meta, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(results, list) or len(results) > 100 or len(results) > count:
        raise SyncError("OpenAlex returned an incomplete metadata response.")
    if not results:
        if count or had_cached:
            raise SyncError("OpenAlex unexpectedly returned no papers; keeping the previous records.")
        return []
    normalized = [item for raw in results if (item := normalize_work(raw, researcher, cutoff, today))]
    if not normalized:
        raise SyncError("OpenAlex returned no verifiable recent papers; keeping the previous records.")
    return [work for work in normalized if theory_related(work, keywords)]


def title_key(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def deduplicate(works):
    """Merge DOI/ID duplicates and exact-title versions sharing a followed author."""
    groups = []
    for work in sorted(works, key=lambda item: (item["id"], item["publication_date"])):
        matched = []
        for index, group in enumerate(groups):
            same_id = work["id"] in group["ids"]
            same_doi = bool(work["doi"]) and work["doi"] in group["dois"]
            key = title_key(work["title"])
            same_version = len(key) >= 20 and key in group["titles"] and bool(set(work["followed_authors"]) & group["followed"])
            if same_id or same_doi or same_version:
                matched.append(index)
        merged = {"items": [work], "ids": {work["id"]}, "dois": {work["doi"]} - {""},
                  "titles": {title_key(work["title"])}, "types": {work["type"]}, "followed": set(work["followed_authors"])}
        for index in reversed(matched):
            old = groups.pop(index)
            for key in ("ids", "dois", "titles", "types", "followed"):
                merged[key].update(old[key])
            merged["items"].extend(old["items"])
        groups.append(merged)
    result = []
    priority = {"article": 3, "review": 2, "preprint": 1}
    for group in groups:
        best = max(group["items"], key=lambda item: (priority[item["type"]], "/anie." in item["doi"], item["publication_date"], bool(item["doi"]), item["id"]))
        result.append({**best, "followed_authors": sorted(group["followed"])})
    return sorted(result, key=lambda item: (item["publication_date"], item["id"]), reverse=True)


def cached_for_source(previous, researcher, cutoff, today):
    """Use only valid cache entries, with attribution narrowed to this source."""
    records = []
    for work in previous.get("works", []):
        if not isinstance(work, dict) or not isinstance(work.get("followed_authors"), list) or researcher["id"] not in work["followed_authors"]:
            continue
        try:
            published = date.fromisoformat(work["publication_date"])
        except (KeyError, ValueError, TypeError):
            continue
        if not cutoff <= published <= today or work.get("type") not in TYPES or not identifier(work.get("id"), "W"):
            continue
        if not text(work.get("title")) or not isinstance(work.get("authors"), list) or not all(isinstance(name, str) for name in work["authors"]):
            continue
        doi = normalize_doi(work.get("doi"))
        topics = work.get("topics", [])
        topics = [topic for topic in topics if isinstance(topic, str)] if isinstance(topics, list) else []
        records.append({**work, "doi": doi, "year": published.year, "venue": text(work.get("venue")), "topics": topics,
                        "url": "https://doi.org/" + doi if doi else safe_url(work.get("url")) or work["id"],
                        "followed_authors": [researcher["id"]]})
    return records


def refresh(config, previous=None, *, fetcher=fetch_json, now=None, api_key=""):
    validate_config(config)
    previous = previous or {}
    if not isinstance(previous, dict) or not isinstance(previous.get("works", []), list) or not isinstance(previous.get("sources", []), list):
        raise SyncError("The existing theory cache is malformed; it has not been replaced.")
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    today = now.date()
    cutoff = today - timedelta(days=config.get("lookback_days", 730))
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    old_sources = {source["id"]: source for source in previous.get("sources", []) if isinstance(source, dict) and isinstance(source.get("id"), str)}
    sources, all_works = [], []
    succeeded = 0
    for researcher in config["researchers"]:
        old_source = old_sources.get(researcher["id"], {})
        # Do not retain records if this slug has been repointed to another person.
        same_identity = identifier(old_source.get("openalex_id"), "A") == identifier(researcher["openalex_id"], "A")
        cached = [work for work in cached_for_source(previous, researcher, cutoff, today)
                  if theory_related(work, config.get("theory_keywords", THEORY_KEYWORDS))] if same_identity else []
        source = {"id": researcher["id"], "name": researcher["name"], "url": researcher["url"],
                  "openalex_id": identifier(researcher["openalex_id"], "A"), "updated_at": old_source.get("updated_at") if same_identity else None}
        try:
            works = collect_source(researcher, cutoff, today, fetcher, api_key, had_cached=bool(cached), keywords=config.get("theory_keywords", THEORY_KEYWORDS))
            source.update(status="ok", updated_at=stamp)
            succeeded += 1
        except (SyncError, OSError, ValueError, TypeError):
            works = cached
            source.update(status="cached" if same_identity and old_source.get("updated_at") else "unavailable",
                          error="This source could not be refreshed. Previous records are shown when available.")
        sources.append(source)
        all_works.extend(works)
    # This timestamp means all configured sources were successfully refreshed.
    same_configuration = {(source["id"], source["openalex_id"]) for source in sources} == {
        (source.get("id"), identifier(source.get("openalex_id"), "A")) for source in old_sources.values()}
    fetched_at = stamp if succeeded == len(sources) else previous.get("fetched_at") if same_configuration else None
    return {"schema_version": 1, "provider": "OpenAlex", "fetched_at": fetched_at, "last_attempt_at": stamp,
            "sources": sources, "works": deduplicate(all_works)[:config.get("max_papers", 80)]}


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        raise SyncError(f"Could not read valid JSON from {path.name}.") from None


def write_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".theory-", suffix=".tmp", delete=False) as handle:
            name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
        name = None
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def sync(config_path=DEFAULT_CONFIG, output=DEFAULT_OUTPUT, *, fetcher=fetch_json, now=None, api_key="", strict=False):
    config_path, output = Path(config_path), Path(output)
    config = read_json(config_path)
    previous = read_json(output) if output.exists() else {}
    payload = refresh(config, previous, fetcher=fetcher, now=now, api_key=api_key)
    write_atomic(output, payload)
    failed = sum(source["status"] != "ok" for source in payload["sources"])
    print(f'Theory reading list: {len(payload["works"])} papers; {len(payload["sources"]) - failed}/{len(payload["sources"])} sources refreshed.')
    if failed:
        print("Some sources could not be refreshed; retained cached records are marked on the page.", file=sys.stderr)
        if strict:
            raise SyncError("Theory refresh was incomplete.")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if any source could not be refreshed.")
    args = parser.parse_args()
    try:
        sync(args.config, args.output, api_key=os.environ.get("OPENALEX_API_KEY", ""), strict=args.strict)
    except (SyncError, OSError) as exc:
        # All SyncError messages are local fixed diagnostics; network detail is never printed.
        print(f"Theory sync: {exc if isinstance(exc, SyncError) else 'Could not safely write the theory cache.'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
