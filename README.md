# Zhi Gong — academic website

An English personal website for **Zhi (Daniel) Gong**, Ph.D. student in Chemistry
at Rice University. Prepared for **https://drzhigong.github.io/**.

The public site includes Home, Research, Publications, Articles (the blog), and About.
Research and biography are based on the CV supplied in October 2026. Public
publications come from [ORCID](https://orcid.org/0000-0002-1316-4675).

## Public source and local editing

This repository contains the website source, public articles, and the build and
publication-sync scripts. The owner's browser editor and private drafts are kept
only in the local checkout and excluded from Git. A fresh clone can build and
deploy the public site without those tools.

On the owner's computer, **double-click `Edit Website.cmd`** to continue editing
in the browser. Local usage and backup instructions are in `.local/EDITING.md`.
Those files are intentionally absent from the public repository. Neither the
editor nor private drafts are included in the GitHub Pages website.

Removing previously tracked files from the current branch does not remove them
from older Git commits. This separation prevents subsequent private drafts and
local-tool changes from entering normal website updates; it does not rewrite
repository history.

## Preview on this computer

Open PowerShell in this folder and run:

```powershell
.\scripts\preview.ps1
```

Visit **http://127.0.0.1:8000**. Press **Ctrl+C** in the terminal to stop.
The preview rebuilds `_site/` using the saved publication snapshot and does not
publish anything. After editing content, restart the preview script to rebuild.
`-Port 8001` selects a different local port.

Install Git and Python 3.11+ (3.12 recommended), then install the dependency:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\preview.ps1
```

The scripts choose `.venv`, the Windows Python launcher, the Codex bundled Python
when available, or `python`. You can override with
`-PythonExecutable 'C:\path\to\python.exe'`.
If PowerShell blocks local scripts, run a script for this process only with
`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\preview.ps1`.

## GitHub Pages deployment

The repository is **DrZhiGong/DrZhiGong.github.io**, and the public site is
**https://drzhigong.github.io/**. In **Settings → Pages → Build and deployment**,
the source is **GitHub Actions**. The workflow builds the public source and uploads
only `_site/`; it does not need the local editor or private drafts.

On a new computer, clone this repository and install the dependencies above.
Check `git config user.name` and `git config user.email` before committing. Use
your GitHub-verified email or GitHub no-reply address. Authentication is handled
by the installed Git credential manager; the scripts contain no access tokens.

After pushing changes, follow **Actions → Refresh research and deploy GitHub
Pages**. You can also trigger **Run workflow** manually. Deployment is complete
when the workflow succeeds.

## Everyday editing

| What you want to update | File or command |
| --- | --- |
| Biography, research descriptions, education, honors, skills | `data/profile.json` |
| Manuscript status or patent entries | `data/manual-publications.json` |
| Published works | Update their public visibility/details in ORCID |
| Public articles | `content/posts/*.md` |
| Site styling | `assets/site.css` |
| Shared header, navigation, footer | `templates/base.html` |
| Page structure | `scripts/build_site.py` |

After editing, preview, then publish:

```powershell
.\scripts\update-site.ps1 -Message "Add research updates"
```

`-WhatIf` shows intended actions without writes. `-NoPush` refreshes, builds, and
commits locally. `-SkipSync` uses the saved ORCID snapshot for the local build;
the GitHub deployment still requires a successful fresh ORCID request.
An existing local commit can be pushed by rerunning the update script even if
there are no new edits. Git errors stop the script; it never pulls, rebases,
or force-pushes. Only the listed website source paths are staged. Local editing
tools, `.local/`, raw PDFs, temporary files, and generated output are excluded.
The update script refuses draft Markdown in `content/posts/`; private drafts
belong in `.local/drafts/` until they are ready for public source control.

## Writing blog posts

Public posts are ordinary Markdown files in `content/posts/` with a small TOML
metadata block:

```markdown
+++
title = "A research note"
date = "2026-10-15"
description = "One sentence about this post."
tags = ["AFQMC", "Methods"]
draft = false
+++

## The question

Write the article here.
```

Use filenames such as `2026-10-15-a-research-note.md`. Write private drafts under
the ignored `.local/drafts/` directory. When ready to make a post's source public,
set `draft = false` and move it into `content/posts/` (the local editor does this
when saving a note with its draft checkbox cleared). Preview, then run the update
script to commit and push it.

Future dates are compared against the current UTC date, consistently in local
previews and GitHub builds; publication happens at the next build. Future-dated
posts are omitted from the website, sitemap, and feed, but **their source is public
as soon as it is pushed**. Keep a post in `.local/drafts/` for as long as its content
needs to remain private. Moving an already published post back to drafts does not
erase its earlier source from Git history.

Markdown headings, links, lists, code blocks, images, and tables are supported.
Raw HTML is supported for your own trusted content. Put only public images in
`assets/` and use paths such as `/assets/figure.png`. Private draft attachments
should stay under `.local/` until publication too.

Each published post gets a separate URL at `/blog/your-slug/`. An Atom feed is
generated at `/feed.xml`.

## How publication updates work

There are two complementary update paths:

- **On each visit to Publications:** the browser requests the current public
  ORCID record and refreshes the list automatically. No token, sign-in, or
  browser storage is used.
- **Daily static rebuild:** GitHub Actions refreshes ORCID and rebuilds the
  complete website daily at 11:17 UTC (06:17 Chicago in summer, 05:17 in winter),
  on each push to `main`, and on manual **Run workflow**. This keeps the
  searchable, no-JavaScript version current too.

The public pages omit ORCID links, branding, and source prompts. Paper titles
link to DOI/publisher pages when available; an ORCID-only record remains visible
as an unlinked title. This is a presentation setting: the ORCID identifier remains
in the public source/data and browser requests so automatic updates can continue.
It does not change the visibility of any record on ORCID itself.

ORCID is the source of truth; add missing works to ORCID and mark them public.
This is live-on-visit plus a scheduled snapshot, not a push notification from
publishers. New papers do not appear until they are present in the public ORCID
record. Manuscripts and patents listed separately from the CV are edited manually.
Citation counts and h-index are not displayed because ORCID does not supply them.

If ORCID is unavailable or returns incomplete data, the browser keeps the rendered
snapshot. The sync script writes atomically and preserves the previous local file
on failure; GitHub stops deployment and leaves the existing live site in place.
An unexpected empty record does not silently erase a nonempty snapshot; use
`python scripts/sync_orcid.py --allow-empty` only after confirming the empty record
is intentional. Scheduled runs deploy the new snapshot but do not commit it back
to Git, so the repository snapshot is refreshed on your next local update.

GitHub may delay scheduled jobs and disables public-repository schedules after
60 days without repository activity. If this happens, re-enable the workflow in
Actions. The on-visit ORCID refresh continues independently of the schedule.

## Files and verification

```text
assets/                 Public images, CSS, browser scripts, SVG illustrations
content/posts/          Markdown articles ready for public source control
data/                   Profile, publication snapshot, manual outputs
templates/base.html     Shared page template
scripts/build_site.py   Static site generator
scripts/sync_orcid.py   Public ORCID API sync, no third-party dependencies
scripts/preview.ps1     Local build and preview
scripts/setup-github.ps1
scripts/update-site.ps1
.github/workflows/pages.yml
tests/                  Sync, build, browser-data, and Git-script regression checks
_site/                  Generated website (ignored by Git)
.local/                 Owner's drafts, backups, instructions (ignored by Git)
```

To run checks (use your Python executable in place of `python` if needed):

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
node tests/test_publications_js.cjs
powershell -NoProfile -ExecutionPolicy Bypass -File tests/test_update_scripts.ps1
```

The original `Zhi Gong_CV_Rice.pdf` remains local source material and is not copied
into the website. The website displays an HTML biography and education history.
The decorative orbital artwork is a conceptual illustration, not calculated data.

Official references: [GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[scheduled workflow limits](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows),
[ORCID public API](https://info.orcid.org/documentation/api-tutorials/api-tutorial-read-data-on-a-record/).
