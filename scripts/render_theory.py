"""Render an accessible static reading list from the theory sync cache.

render_theory(config, cache, base="") returns the page's main content. The caller
includes assets/theory.css and assets/theory.js. Filtering is progressive
enhancement: every paper and source remains readable without JavaScript.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from html import escape
import re
from urllib.parse import quote, urlsplit


def text(value):
    return str(value) if value is not None else ""


def e(value):
    return escape(text(value), quote=True)


def safe_url(value):
    value = text(value).strip()
    if any(ord(character) < 32 for character in value):
        return ""
    try:
        parts = urlsplit(value)
        return value if parts.scheme in {"https", "http"} and parts.hostname and not parts.username and not parts.password else ""
    except ValueError:
        return ""


def paper_date(work):
    try:
        value = date.fromisoformat(text(work.get("publication_date")))
        return value.isoformat(), value.strftime("%d %b %Y"), str(value.year)
    except ValueError:
        year = text(work.get("year"))
        return "", year if re.fullmatch(r"\d{4}", year) else "Undated", year if re.fullmatch(r"\d{4}", year) else "undated"


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return ""


def source_card(researcher, source):
    name = e(researcher.get("name", "Researcher"))
    url = safe_url(researcher.get("url"))
    name = f'<a href="{e(url)}">{name} <span aria-hidden="true">↗</span></a>' if url else name
    updated = timestamp(source.get("updated_at"))
    status = source.get("status")
    if status == "ok" and updated:
        state, message = "ok", f"Updated {updated}"
    elif status == "cached":
        state = "cached"
        message = "Using saved results" + (f" · Last success {updated}" if updated else "; last successful refresh unknown")
    else:
        state, message = "unavailable", "Awaiting a successful refresh"
    return f'''<article class="theory-source" data-source-status="{state}">
      <h2>{name}</h2><p class="theory-affiliation">{e(researcher.get("affiliation", ""))}</p>
      <p class="theory-source-status"><span aria-hidden="true"></span>{e(message)}</p>
    </article>'''


def paper_card(work, researchers):
    iso_date, display_date, year = paper_date(work)
    authors = [text(author) for author in work.get("authors", []) if isinstance(author, str)]
    followed = [identifier for identifier in work.get("followed_authors", []) if identifier in researchers]
    followed = list(dict.fromkeys(followed))
    title = text(work.get("title")) or "Untitled paper"
    doi = text(work.get("doi")).strip()
    doi_url = "https://doi.org/" + quote(doi, safe="/():;.-_") if re.fullmatch(r"10\.\d{4,9}/[^\s<>\"']+", doi, re.I) else ""
    url = safe_url(work.get("url")) or doi_url
    openalex_id = text(work.get("id")).removeprefix("https://openalex.org/")
    record_url = f"https://openalex.org/{openalex_id}" if re.fullmatch(r"W\d+", openalex_id) else ""
    title_html = f'<a href="{e(url)}">{e(title)} <span aria-hidden="true">↗</span></a>' if url else e(title)
    type_label = {"article": "Article", "review": "Review", "preprint": "Preprint"}.get(work.get("type"), "Paper")
    venue = text(work.get("venue")) or "Venue not yet indexed"
    search = " ".join([title, venue, doi, *authors, *(text(researchers[identifier].get("name")) for identifier in followed)])
    badges = "".join(f'<span>{e(researchers[identifier].get("name"))}</span>' for identifier in followed)
    links = []
    if doi_url:
        links.append(f'<a href="{e(doi_url)}">DOI: {e(doi)} <span aria-hidden="true">↗</span></a>')
    if record_url:
        links.append(f'<a href="{record_url}">Index record <span aria-hidden="true">↗</span></a>')
    date_html = f'<time datetime="{iso_date}">{display_date}</time>' if iso_date else e(display_date)
    return f'''<article class="theory-paper" data-search="{e(search)}" data-year="{e(year)}" data-researchers="{e(' '.join(followed))}">
      <div class="theory-paper-date">{date_html}<span>{type_label}</span></div>
      <div class="theory-paper-content"><p class="eyebrow theory-venue">{e(venue)}</p>
        <h2>{title_html}</h2><p class="theory-authors">{e(', '.join(authors)) if authors else 'Authors not yet indexed'}</p>
        <div class="theory-followed" aria-label="Followed authors">{badges}</div>
        <div class="theory-paper-links">{' '.join(links)}</div>
      </div>
    </article>'''


def render_theory(config, cache, base=""):
    """Render configured researchers and cached papers; never fetch at render time."""
    config = config if isinstance(config, dict) else {}
    cache = cache if isinstance(cache, dict) else {}
    researchers = {
        item["id"]: item for item in config.get("researchers", [])
        if isinstance(item, dict) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", text(item.get("id")))
    }
    sources = {source.get("id"): source for source in cache.get("sources", []) if isinstance(source, dict)}
    works = [work for work in cache.get("works", []) if isinstance(work, dict)]
    works.sort(key=lambda work: (paper_date(work)[0] or (paper_date(work)[2] if paper_date(work)[2] != "undated" else ""), text(work.get("title"))), reverse=True)
    years = sorted({paper_date(work)[2] for work in works if paper_date(work)[2] != "undated"}, reverse=True)
    year_options = ''.join(f'<option value="{e(year)}">{e(year)}</option>' for year in years)
    if any(paper_date(work)[2] == "undated" for work in works):
        year_options += '<option value="undated">Undated</option>'
    researcher_options = ''.join(f'<option value="{e(identifier)}">{e(researcher.get("name", identifier))}</option>' for identifier, researcher in researchers.items())
    source_cards = ''.join(source_card(researcher, sources.get(identifier, {})) for identifier, researcher in researchers.items())
    updated = timestamp(cache.get("fetched_at"))
    snapshot = f"Last complete refresh {updated}" if updated else "No complete refresh recorded yet"
    stale = any(sources.get(identifier, {}).get("status") != "ok" for identifier in researchers)
    notice = '<p class="theory-refresh-notice" role="status">Some sources could not be refreshed. Available saved results are shown below; check the source dates above.</p>' if stale else ""
    cards = ''.join(paper_card(work, researchers) for work in works)
    empty = '<div class="theory-empty"><h2>No papers available yet.</h2><p>The reading list will appear after a successful refresh.</p></div>'
    return f'''<div class="page-intro shell theory-intro"><p class="eyebrow">Reading list</p>
      <h1>{e(config.get("title", "Theory Updates"))}</h1>
      <p class="lede">{e(config.get("description", "Recent papers from researchers in theoretical chemistry and electronic structure."))}</p>
    </div>
    <section class="shell theory-main" aria-label="Theory reading list">
      <div class="theory-overview"><p class="eyebrow">Researchers I follow</p><p>Checked daily · Newest papers first</p></div>
      <div class="theory-sources">{source_cards}</div>{notice}
      <div class="theory-list-heading"><h2>Recent papers</h2><p>{e(snapshot)}</p></div>
      <div class="theory-tools" id="theory-tools" hidden>
        <label for="theory-search">Search papers<input type="search" id="theory-search" placeholder="Title, author, journal, or DOI…"></label>
        <label for="theory-researcher">Researcher<select id="theory-researcher"><option value="all">All researchers</option>{researcher_options}</select></label>
        <label for="theory-year">Year<select id="theory-year"><option value="all">All years</option>{year_options}</select></label>
      </div>
      <p class="theory-count" id="theory-count" aria-live="polite">{len(works)} {'paper' if len(works) == 1 else 'papers'}</p>
      <div id="theory-papers">{cards or empty}</div>
      <div class="theory-empty" id="theory-no-results" hidden><h2>No papers match.</h2><p>Try another keyword or clear the filters.</p><button class="button button-outline" id="theory-reset" type="button">Clear filters</button></div>
      <p class="theory-provenance">Selected recent papers from <a href="https://openalex.org/">OpenAlex ↗</a>, filtered for theory topics and the researchers above. May include collaborations and preprints. Automated indexing can miss or misattribute papers; check the linked publication for details.</p>
    </section>'''
