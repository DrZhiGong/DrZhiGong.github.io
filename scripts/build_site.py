"""Build the English academic site from JSON data and Markdown notes (Python 3.11+)."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shutil
from string import Template
import sys
import tomllib
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
try:
    import markdown
except ImportError:
    raise SystemExit("Install dependencies first: python -m pip install -r requirements.txt --target .vendor")


def e(value):
    return escape(str(value), quote=True)


def safe_url(value):
    value = str(value or "")
    parts = urlsplit(value)
    return value if parts.scheme in {"https", "http"} and parts.netloc else ""


def load_json(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8-sig"))


def parse_post(path, today=None):
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("+++\n") or "\n+++\n" not in text[4:]:
        raise ValueError(f"{path.name}: expected TOML metadata between +++ lines")
    metadata, body = text[4:].split("\n+++\n", 1)
    meta = tomllib.loads(metadata)
    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{path.name}: title is required")
    published = date.fromisoformat(str(meta.get("date", "")))
    draft = meta.get("draft", True)
    if not isinstance(draft, bool):
        raise ValueError(f"{path.name}: draft must be true or false, without quotes")
    tags = meta.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError(f"{path.name}: tags must be a list of strings")
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError(f"{path.name}: filename must use lowercase letters, numbers, and hyphens")
    if draft or published > (today or datetime.now(timezone.utc).date()):
        return None
    return {"title": title, "date": published, "description": str(meta.get("description", "")),
            "tags": tags, "slug": slug, "html": markdown.markdown(body, extensions=["fenced_code", "tables", "toc", "sane_lists"])}


def author_text(authors):
    return ", ".join(f"<strong>{e(a)}</strong>" if a.lower().replace(".", "").strip() in {"zhi gong", "gong zhi", "z gong"} else e(a) for a in authors)


def publication(work, compact=False):
    url = safe_url(work.get("url")) or safe_url(work.get("orcid_url"))
    title = f'<a href="{e(url)}">{e(work["title"])} <span aria-hidden="true">↗</span></a>' if url else e(work["title"])
    authors = f'<p class="pub-authors">{author_text(work.get("authors", []))}</p>' if work.get("authors") else ""
    search = " ".join([work["title"], work.get("journal", ""), *work.get("authors", []), work.get("doi", "")])
    return f'<article class="publication" data-year="{e(work.get("year") or "Undated")}" data-search="{e(search.lower())}"><div class="pub-year">{e(work.get("year") or "—")}</div><div><p class="eyebrow pub-journal">{e(work.get("journal") or work.get("type", "Research output").replace("-", " "))}</p><h3>{title}</h3>{"" if compact else authors}<p class="pub-doi">{e(work.get("doi", ""))}</p></div></article>'


def intro(kicker, title, description):
    return f'<div class="page-intro shell"><p class="eyebrow">{e(kicker)}</p><h1>{title}</h1><p class="lede">{e(description)}</p></div>'


def build(base_url="https://drzhigong.github.io", output=None):
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("--base-url must be an absolute HTTP(S) URL without a query or fragment")
    base_url = base_url.rstrip("/")
    base = parsed.path.rstrip("/")
    output = Path(output or ROOT / "_site").resolve()
    # Delete only a marked build folder, strictly inside this checkout.
    if not output.is_relative_to(ROOT) or output == ROOT or output.parts[len(ROOT.parts)] in {"data", "scripts", "assets", "content", "templates", "editor", ".git", "tests"}:
        raise ValueError("Output must be a dedicated generated directory inside the checkout")
    if output.exists() and any(output.iterdir()) and not (output / ".generated-site").is_file():
        raise ValueError(f"Refusing to replace unmarked nonempty directory: {output}")
    profile = load_json("profile.json")
    cache = load_json("publications.json")
    works = cache["works"]
    if not isinstance(works, list):
        raise ValueError("publications.json works must be a list")
    manual = load_json("manual-publications.json")
    posts = [post for path in sorted((ROOT / "content" / "posts").glob("*.md")) if (post := parse_post(path))]
    posts.sort(key=lambda post: post["date"], reverse=True)
    if len({p["slug"] for p in posts}) != len(posts):
        raise ValueError("Published posts must have unique slugs")
    template = Template((ROOT / "templates" / "base.html").read_text(encoding="utf-8"))
    pages = {}
    now = datetime.now(timezone.utc)

    def page(path, title, description, content, active, extra_head="", og_type="website"):
        nav = "".join(f'<a href="{base}{url}"'+(' aria-current="page"' if key == active else '')+f'>{label}</a>' for key, label, url in [("home", "Home", "/"), ("research", "Research", "/research/"), ("publications", "Publications", "/publications/"), ("blog", "Articles", "/blog/"), ("about", "About", "/about/")])
        pages[path + "index.html"] = template.substitute(title=e(title), description=e(description), canonical=e(base_url + "/" + path), base=base,
            year=now.year, nav=nav, content=content, page=active, extra_head=extra_head, og_type=og_type,
            name=e(profile["name"]), display_name=e(profile["display_name"]), email=e(profile["email"]), role=e(profile["role"]), institution=e(profile["institution"]),
            scholar_url=e(safe_url(profile["scholar_url"])), orcid_url=e(cache["source"]), location=e(profile["location"]))

    research_rows = "".join(f'<a class="research-row" href="{base}/research/#{e(r["id"])}"><span class="row-number">0{i + 1}</span><div><p class="eyebrow">{e(r["kicker"])}</p><h3>{e(r["title"])}</h3><p>{e(r["summary"])}</p></div><span class="row-arrow" aria-hidden="true">↗</span></a>' for i, r in enumerate(profile["research"]))
    selected = [next((w for w in works if w.get("doi", "").lower() == doi), None) for doi in ["10.1021/acs.jctc.6c00899", "10.1007/s12598-024-02696-8"]]
    selected = [w for w in selected if w] or works[:2]
    home = f'''<section class="hero shell"><div class="hero-copy"><p class="eyebrow">Computational chemistry · {e(profile['institution'])}</p><h1>{e(profile['display_name'])}</h1><p class="hero-name">{e(profile['role'])}</p><p class="hero-description">{e(profile['intro'])}</p><div class="actions"><a class="button" href="{base}/research/">Research overview <span aria-hidden="true">↗</span></a><a class="text-link" href="{base}/about/">Biography <span aria-hidden="true">→</span></a></div></div><div class="hero-art"><img src="{base}/assets/orbitals.svg" width="640" height="620" alt="Conceptual orbital illustration surrounding a wave function symbol; not a simulation result."><p class="art-caption">Electronic structure / Chemical reactivity</p></div></section>
    <div class="affiliation-band"><div class="shell affiliation-inner"><p><span>Currently</span> {e(profile['role'])}</p><p><span>Research group</span> <a href="{e(profile['group_url'])}">{e(profile['research'][0]['advisor'])} · {e(profile['institution'])} ↗</a></p><p><span>Based in</span> {e(profile['location'])}</p></div></div>
    <section class="section shell"><div class="section-heading"><div><p class="eyebrow">01 / Research</p><h2>Research areas</h2></div><p>My work connects electronic-structure methods with questions in chemical reactivity and materials chemistry.</p></div><div>{research_rows}</div></section>
    <section class="selected-section"><div class="section shell"><div class="section-heading"><div><p class="eyebrow">02 / Publications</p><h2>Selected publications</h2></div><a class="text-link" href="{base}/publications/">All publications <span aria-hidden="true">→</span></a></div>{''.join(publication(w, True) for w in selected)}</div></section>
    <section class="section shell home-note"><div><p class="eyebrow">03 / Articles</p><h2>Research notes &amp; methods</h2><p>Articles on computational chemistry, scientific computing, and research methods.</p></div><a class="button button-outline" href="{base}/blog/">Browse articles <span aria-hidden="true">↗</span></a></section>'''
    person = {"@context": "https://schema.org", "@type": "Person", "name": profile["display_name"], "url": base_url, "jobTitle": profile["role"], "affiliation": {"@type": "CollegeOrUniversity", "name": profile["institution"]}, "sameAs": [cache["source"], profile["scholar_url"]]}
    structured = json.dumps(person, ensure_ascii=False).replace("<", "\\u003c")
    page("", f'{profile["name"]} | Computational Chemistry at {profile["institution"]}', profile["intro"], home, "home", f'<script type="application/ld+json">{structured}</script>')

    sections = []
    for i, r in enumerate(profile["research"]):
        related = "".join(f'<li><a href="https://doi.org/{e(doi)}">{e(next((w["title"] for w in works if w.get("doi", "").lower() == doi.lower()), doi))} ↗</a></li>' for doi in r["related_dois"])
        sections.append(f'<section class="research-detail" id="{e(r["id"])}"><aside><span class="row-number">0{i+1}</span><p class="badge">{e(r["status"])}</p><p>{e(r["institution"])}<br>{e(r["period"])}</p><p>Advisor<br><strong>{e(r["advisor"])}</strong></p></aside><div><p class="eyebrow">{e(r["kicker"])}</p><h2>{e(r["title"])}</h2><p class="research-summary">{e(r["summary"])}</p>{"".join(f"<p>{e(d)}</p>" for d in r["details"])}<ul class="tags">{"".join(f"<li>{e(m)}</li>" for m in r["methods"])}</ul>{"<h3 class=small-heading>Related publications</h3><ul class=related-papers>"+related+"</ul>" if related else ""}</div></section>')
    page("research/", "Research | Zhi Gong", "Research in quantum Monte Carlo, reaction mechanisms, and sustainable materials.", intro("Research", "Research interests", "Electronic-structure methods, chemical reaction mechanisms, and sustainable materials.") + '<div class="shell">'+"".join(sections)+"</div>", "research")

    years = sorted({str(w["year"]) for w in works if w.get("year")}, reverse=True)
    options = '<option value="all">All years</option>' + "".join(f'<option value="{y}">{y}</option>' for y in years)
    snapshot = e(cache["fetched_at"].replace("T", " ").replace("Z", " UTC"))
    manuscripts = "".join(f'<article class="manual-entry"><p class="eyebrow">{e(m["status"])}</p><h3>{e(m["title"])}</h3><p>{author_text(m["authors"])}</p><p>{e(m["venue"])}</p><p class="muted small">{e(m["note"])}</p></article>' for m in manual["manuscripts"])
    patents = "".join(f'<article class="patent"><p class="eyebrow">{e(p["number"])}</p><h3>{e(p["title"])}</h3></article>' for p in manual["patents"])
    pubs = intro("Research outputs", "Publications", "Peer-reviewed publications, manuscripts, and patent contributions.")
    pubs += f'''<section class="shell publications-section"><div class="publication-source"><div><strong>Publication record</strong><p id="sync-status" role="status">Updated {snapshot}</p></div><a class="text-link" href="{e(cache['source'])}">View ORCID record ↗</a></div><div class="publication-tools" id="publication-tools" hidden><label>Search publications<input type="search" id="publication-search" placeholder="Title, author, journal, or DOI…"></label><label>Publication year<select id="publication-year">{options}</select></label></div><p class="results-count" id="results-count" aria-live="polite">{len(works)} publications</p><div id="publication-list">{''.join(publication(w) for w in works)}</div><p id="no-results" class="empty-filter" hidden>No publications match this search. Try another title or year.</p><p class="source-note">Publication information is updated from ORCID. <a href="{e(profile['scholar_url'])}">Google Scholar ↗</a></p></section><section class="section shell"><p class="eyebrow">Work in progress</p><h2>Manuscripts</h2>{manuscripts or '<p>No manuscripts listed.</p>'}</section><section class="section shell patents-section"><p class="eyebrow">Applied research</p><h2>Patents</h2><div class="patent-grid">{patents}</div></section>'''
    page("publications/", "Publications | Zhi Gong", "Publications by Zhi Gong, updated from ORCID, plus manuscripts and patents.", pubs, "publications", f'<script defer src="{base}/assets/publications.js"></script>')

    cards = "".join(f'<article class="note-card"><p class="eyebrow">{e(p["date"].strftime("%B %d, %Y"))} · {e(" / ".join(p["tags"]))}</p><h2><a href="{base}/blog/{p["slug"]}/">{e(p["title"])} ↗</a></h2><p>{e(p["description"])}</p></article>' for p in posts)
    empty = '<div class="notebook-empty"><h2>No articles published yet.</h2><p>Articles and technical notes will be listed here as they are published.</p></div>'
    page("blog/", "Articles | Zhi Gong", "Articles on computational chemistry, scientific computing, and research methods.", intro("Articles", "Research notes &amp; methods", "Articles on computational chemistry, scientific computing, and research methods.") + f'<section class="section shell notes-list">{cards or empty}</section>', "blog")
    for post in posts:
        content = f'<article class="post shell"><a class="text-link" href="{base}/blog/">← All articles</a><p class="eyebrow">{e(post["date"].strftime("%B %d, %Y"))}</p><h1>{e(post["title"])}</h1><p class="lede">{e(post["description"])}</p><div class="prose">{post["html"]}</div></article>'
        page(f'blog/{post["slug"]}/', f'{post["title"]} | Zhi Gong', post["description"], content, "blog", og_type="article")

    education = "".join(f'<div class="timeline-row"><p class="eyebrow">{e(item["period"])}</p><div><h3>{e(item["institution"])}</h3><p>{e(item["degree"])}</p></div></div>' for item in profile["education"])
    honors = "".join(f'<div class="honor-row"><span>{e(h["year"])}</span><div><h3>{e(h["title"])}</h3><p>{e(h["organization"])}</p></div></div>' for h in profile["honors"])
    skills = "".join(f'<div><h3>{e(s["category"])}</h3><p>{e(" · ".join(s["items"]))}</p></div>' for s in profile["skills"])
    about = intro("About", e(profile["display_name"]), profile["role"] + " · " + profile["institution"]) + f'<section class="shell about-grid"><aside><div class="initials-portrait" aria-label="ZG monogram">ZG<span>.</span></div><p>{e(profile["location"])}</p><a href="mailto:{e(profile["email"])}">{e(profile["email"])} ↗</a><a href="{e(profile["group_url"])}">{e(profile.get("group_name", "Research group"))} ↗</a><a href="{e(profile["scholar_url"])}">Google Scholar ↗</a><a href="{e(cache["source"])}">ORCID ↗</a></aside><div class="about-copy">{"".join(f"<p>{e(p)}</p>" for p in profile["about"])}<div class="section" id="education"><p class="eyebrow">Background</p><h2>Education</h2>{education}</div><div class="section"><p class="eyebrow">Recognition</p><h2>Selected honors</h2>{honors}</div><div class="section"><p class="eyebrow">Expertise</p><h2>Methods & skills</h2><div class="skills-grid">{skills}</div></div></div></section>'
    page("about/", "About | Zhi Gong", profile["display_name"] + ", " + profile["role"] + " at " + profile["institution"] + ".", about, "about")
    page("404/", "Page not found | Zhi Gong", "This page could not be found.", intro("404", "Page not found", "The requested page is unavailable or has moved.") + f'<div class="shell section"><a class="button" href="{base}/">Back to home →</a></div>', "")
    pages["404.html"] = pages.pop("404/index.html")

    # Construct all documents before replacing a previous successful build.
    sitemap = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for path in pages:
        if path == "404.html":
            continue
        item = ET.SubElement(sitemap, "url")
        ET.SubElement(item, "loc").text = base_url + "/" + path.removesuffix("index.html")
    ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", ns)
    feed = ET.Element(f"{{{ns}}}feed")
    def atom(parent, tag, value=None, **attrs):
        node = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
        node.text = value
        return node
    atom(feed, "title", "Zhi Gong — Articles")
    atom(feed, "id", base_url + "/blog/")
    atom(feed, "updated", (posts[0]["date"].isoformat() + "T00:00:00Z") if posts else "2026-10-15T00:00:00Z")
    atom(feed, "link", href=base_url + "/feed.xml", rel="self")
    atom(feed, "link", href=base_url + "/blog/")
    atom(atom(feed, "author"), "name", profile["display_name"])
    for post in posts:
        entry = atom(feed, "entry")
        url = f'{base_url}/blog/{post["slug"]}/'
        atom(entry, "title", post["title"])
        atom(entry, "id", url)
        atom(entry, "link", href=url)
        atom(entry, "updated", post["date"].isoformat() + "T00:00:00Z")
        atom(entry, "summary", post["description"])
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / ".generated-site").write_text("Generated by scripts/build_site.py\n", encoding="utf-8")
    for path, content in pages.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    shutil.copytree(ROOT / "assets", output / "assets")
    (output / "data").mkdir()
    (output / "data" / "publications.json").write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / ".nojekyll").touch()
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n", encoding="utf-8")
    ET.ElementTree(sitemap).write(output / "sitemap.xml", encoding="utf-8", xml_declaration=True)
    ET.ElementTree(feed).write(output / "feed.xml", encoding="utf-8", xml_declaration=True)
    print(f"Built {len(pages)} pages, {len(works)} publications, {len(posts)} published posts -> {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://drzhigong.github.io")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build(args.base_url, args.output)


if __name__ == "__main__":
    main()
