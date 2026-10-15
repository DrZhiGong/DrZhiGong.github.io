# Zhi Gong — academic website

An English personal website for **Zhi (Daniel) Gong**, Ph.D. student in Chemistry
at Rice University. Prepared for **https://drzhigong.github.io/**.

The public site includes Home, Research, Publications, Articles (the blog), and About.
Research and biography are based on the CV supplied in October 2026. Public
publications come from [ORCID](https://orcid.org/0000-0002-1316-4675).

## Edit content in your browser

This is the local administration interface for the site owner. Visitors see the
academic website only; no editor link, editing controls, or draft entries are
included in the published website. The editor's **Notes** section manages the
public **Articles** section.

**Double-click `Edit Website.cmd`** in this folder. It opens the local content
editor in your browser. Keep its terminal window open while editing, then press
**Ctrl+C** in that window to stop. Alternatively, from PowerShell:

```powershell
.\scripts\edit-site.ps1
```

The editor is available at **http://127.0.0.1:8000/editor/**. If port 8000 is busy,
use `.\scripts\edit-site.ps1 -Port 8080`. `-NoBrowser` starts the editor without
opening a browser automatically.

- **Notes:** select an existing note or create a new one, fill in its title,
  date, short description, and tags, then write in the Markdown editor. Formatting
  buttons help with headings, emphasis, links, and code. The adjacent preview
  updates as you type. The URL slug is set when a note is created and stays stable
  when you later change its title or date.
- **Profile & research:** edit your biography, research projects, education,
  honors, and skills through forms.
- **Other outputs:** update manuscript status and patent entries through forms.
- **Save locally** (or **Ctrl/Cmd+S**) writes the ordinary Markdown/JSON files and
  rebuilds the local website preview. Saving never commits or pushes to GitHub.
  Open the website preview to see the result. Draft notes can be previewed inside
  the editor but stay off the public site.

After reviewing your changes, publish with the existing command:

```powershell
.\scripts\update-site.ps1 -Message "Update notes and research"
```

The editor runs only on your computer and is not included in the GitHub Pages
website. It protects unsaved changes when switching notes and refuses to overwrite
a file changed in another tab or another editor. Previous file versions are saved
under `.local/editor-backups/`, which is excluded from Git. Restore a backup by
copying its contents back to the corresponding Markdown/JSON source file.

**Drafts are hidden from the website, not from a public source repository.** When
you push this repository to GitHub, draft Markdown files are included. Keep
confidential research outside this folder. ORCID publications still update through
the existing sync; the editor does not alter your ORCID account.

## Preview on this computer

Open PowerShell in this folder and run:

```powershell
.\scripts\preview.ps1
```

Visit **http://127.0.0.1:8000**. Press **Ctrl+C** in the terminal to stop.
The preview rebuilds `_site/` using the saved publication snapshot and does not
publish anything. After editing content, restart the preview script to rebuild.
`-Port 8001` selects a different local port.

This checkout already has the Markdown dependency installed locally in `.vendor/`.
On a different computer, install Git and Python 3.11+ (3.12 recommended), then:

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

## First publication on GitHub Pages

1. Sign in to GitHub as **DrZhiGong**.
2. Create a **public**, empty repository named **DrZhiGong.github.io**.
   Do not initialize it with a README, license, or `.gitignore`; those files are
   already prepared locally. If the repository already contains work, reconcile
   it deliberately before using these scripts; they never force-push.
3. Configure the local repository:

   ```powershell
   .\scripts\setup-github.ps1 -WhatIf
   .\scripts\setup-github.ps1
   ```

4. Check your Git commit identity with `git config user.name` and
   `git config user.email`. If missing, set it with your own GitHub-verified email
   or your GitHub no-reply address. GitHub authentication is handled by your
   installed Git credential manager; the scripts contain no access tokens.
5. In the GitHub repository, open **Settings → Pages → Build and deployment**
   and set **Source = GitHub Actions**. This project uses a custom static build,
   so do not select “Deploy from a branch.”
6. Push the prepared website:

   ```powershell
   .\scripts\update-site.ps1 -WhatIf
   .\scripts\update-site.ps1 -Message "Publish my academic website"
   ```

7. Follow **Actions → Refresh research and deploy GitHub Pages**. Once the
   deployment succeeds, the site is available at **https://drzhigong.github.io/**.
   If the initial push happened before Pages was enabled, enable it and use
   **Actions → Refresh research and deploy GitHub Pages → Run workflow**.

No repository has been created or pushed by preparing these files.

## Everyday editing

| What you want to update | File or command |
| --- | --- |
| Biography, research descriptions, education, honors, skills | Editor → Profile & research, or `data/profile.json` |
| Manuscript status or patent entries | Editor → Other outputs, or `data/manual-publications.json` |
| Published works | Update their public visibility/details in ORCID |
| New blog draft | Editor → New note, or `.\scripts\new-post.ps1 "A research note"` |
| Existing blog posts | Editor → Notes, or `content/posts/*.md` |
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
or force-pushes. Only the listed website source paths are staged. Raw PDFs,
temporary files, and generated output are excluded.

## Writing blog posts

Posts are ordinary Markdown files with a small TOML metadata block:

```markdown
+++
title = "A research note"
date = "2026-10-15"
description = "One sentence about this post."
tags = ["AFQMC", "Methods"]
draft = true
+++

## The question

Write the article here.
```

Use filenames such as `2026-10-15-a-research-note.md`. Set `draft = false` to
publish. Future dates are compared against the current UTC date, consistently
in local previews and GitHub builds; publication happens at the next build.
Drafts and future-dated posts are omitted from the website, sitemap,
and feed. **Draft source files are still visible in the public GitHub repository**;
keep confidential research outside this checkout. Markdown headings, links,
lists, code blocks, images, and tables are supported. Raw HTML is supported for
your own trusted content. Put public images in `assets/` and use paths such as
`/assets/figure.png`. The starter draft is intentionally unpublished.

Each published post gets a separate URL at `/blog/your-slug/`. An Atom feed is
generated at `/feed.xml`.

## How publication updates work

There are two complementary update paths:

- **On each visit to Publications:** the browser requests the current public
  ORCID record and refreshes the list. A Refresh button lets a visitor repeat
  this without reloading the page. No token, sign-in, or browser storage is used.
- **Daily static rebuild:** GitHub Actions refreshes ORCID and rebuilds the
  complete website daily at 11:17 UTC (06:17 Chicago in summer, 05:17 in winter),
  on each push to `main`, and on manual **Run workflow**. This keeps the
  searchable, no-JavaScript version current too.

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
assets/                 CSS, browser scripts, original SVG illustrations
content/posts/          Markdown blog posts and drafts
data/                   Profile, publication snapshot, manual outputs
templates/base.html     Shared page template
editor/                 Local content editor interface (not published)
Edit Website.cmd        Double-click to start the editor on Windows
scripts/editor_server.py Local editor server and content saving
scripts/edit-site.ps1   Local editor launcher
scripts/build_site.py   Static site generator
scripts/sync_orcid.py   Public ORCID API sync, no third-party dependencies
scripts/new_post.py     Draft creator
scripts/new-post.ps1    Windows draft command with Python detection
scripts/preview.ps1     Local build and preview
scripts/setup-github.ps1
scripts/update-site.ps1
.github/workflows/pages.yml
tests/                  Sync, build, browser-data, and Git-script regression checks
_site/                  Generated website (ignored by Git)
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
