# url2md

Convert any web page to clean Markdown — with a native macOS GUI.

[![Platform](https://img.shields.io/badge/platform-macOS%20arm64-000000?style=flat-square&logo=apple&logoColor=white)](https://github.com/pvollenweider/url2md/releases/latest)
[![Python](https://img.shields.io/badge/python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/pvollenweider/url2md?style=flat-square&color=6366f1&label=release)](https://github.com/pvollenweider/url2md/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-22c55e?style=flat-square)](LICENSE)

---

## Features

- **Single URL** — paste a URL, get Markdown in one click
- **Batch mode** — convert a list of URLs (or sitemap.xml links) and merge them into a single document
- **Sitemap browser** — fetch a sitemap.xml, browse pages in a hierarchical tree with checkboxes, filter by boolean expression
- **Smart content extraction** — picks `<article>` when unique, falls back to `<main>` then other heuristics; strips navbars, sidebars, footers and Bootstrap nav components automatically
- **Fenced code blocks** — `<pre>` and `<pre><code>` blocks become proper ` ``` ` fenced blocks
- **Internal link rewriting** — in batch mode, cross-page links become in-document anchors
- **Page cache** — SQLite + FTS5 backend; sitemap `lastmod` dates drive automatic invalidation
- **Jahia XML import** — inject content directly from a Jahia CMS export into the cache without fetching from the web
- **Full-text content filter** — search cached page content with BM25 ranking and weighted headings (H1 › H2 › H3 › body); pages score `●●●` / `●●` / `●` by relevance
- **Filter presets** — save, apply and delete named filter combinations (URL + content)
- **PDF export** — export the Markdown to a beautifully styled A4 PDF via WeasyPrint
- **Split preview** — live Markdown preview with scrollbar alongside the raw source
- **Image toggle** — optionally keep or strip images
- **Dark UI** — minimal Catppuccin-inspired dark interface built with CustomTkinter

## Download

Grab the latest **macOS DMG** from the [Releases](../../releases/latest) page, open it and drag `url2md.app` to your Applications folder.

> Requires macOS 13 Ventura or later (Apple Silicon). If Gatekeeper blocks the app, right-click → Open.

## Running from source

### Prerequisites

```bash
brew install python@3.13 python-tk@3.13
```

### Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Launch

```bash
source .venv/bin/activate   # if not already active
python3 app.py
```

### CLI usage

```bash
source .venv/bin/activate
./url2md https://example.com
# or
python3 url2md.py https://example.com
```

### Jahia XML import (CLI)

```bash
source .venv/bin/activate
python3 jahia_import.py export.xml https://academy.jahia.com
```

## How content extraction works

```
1. Remove <script>, <style>, <nav>, <header>, <footer>, <aside>, <noscript>
2. Remove any element whose CSS class list contains "nav"
3. Convert <pre> / <pre><code> blocks to fenced Markdown code blocks
4. If exactly one <article> exists  →  use it
5. Else: <main>  →  #content  →  #main  →  .content  →  .main  →  <body>
```

## Filter syntax

The Sitemap tab has two independent filters that stack, plus named presets.

### URL filter

Matched against the page URL. Supports `and`, `or`, `not` and parentheses. Tokens are matched as case-insensitive substrings. Every token must be separated by an explicit operator — there is no implicit `and`.

| Expression | Matches |
|---|---|
| `developer` | all developer docs |
| `jahia-8.2 and developer` | developer docs for Jahia 8.2 |
| `forms-3.3 and end-user` | end-user docs for Forms 3.3 |
| `(jahia-8.2 or jahia-8-2) and developer` | developer docs regardless of URL format |
| `forms-3.3 and (developer or system-administrator)` | technical docs for Forms 3.3 |
| `(8.2 or 8-2) and not end-user and not 8-1 and not jexperience` | Jahia 8.2 non-end-user docs |
| `not release-notes` | everything except release notes |

### Content filter

Matched against the **cached Markdown text** of each page using BM25 full-text search with weighted columns (H1 = 100 · H2 = 40 · H3 = 15 · body = 1). Pages not yet in cache remain visible but appear dimmed.

Results are sorted by relevance and annotated with score badges:

| Badge | Meaning |
|---|---|
| `●●●` | score ≥ 66 % of the best match |
| `●●` | score ≥ 33 % of the best match |
| `●` | score > 0 |

| Expression | Pages matched (example corpus) |
|---|---|
| `GraphQL` | 218 pages mentioning GraphQL |
| `Elasticsearch` | pages referencing Elasticsearch |
| `OSGi` | pages involving OSGi bundles or services |
| `workflow` | pages describing publication workflows |
| `GraphQL and not deprecated` | GraphQL pages that don't mention deprecation |
| `OSGi or Maven` | pages mentioning either OSGi or Maven |
| `validation and (email or recaptcha)` | validation pages focused on email or captcha |

Combine both filters to narrow precisely — e.g. URL filter `forms-3.3 and developer` + content filter `validation` returns only Forms 3.3 developer pages that actually discuss validation.

### Presets

Save the current URL + content filter combination under a name using the **Save…** button in the Preset row. Select a saved preset from the dropdown to restore both fields instantly. Delete a preset with the **Delete** button.

Presets are stored in `~/.cache/url2md/presets.json`.

## Cache

Pages are cached at `~/.cache/url2md/pages.db` (SQLite + FTS5). When fetching a sitemap, each page's `lastmod` date is compared to the cache entry's `cached_at` timestamp — pages modified after the last fetch are automatically invalidated. Use the **Clear cache** button in the Sitemap tab to wipe everything.

The cache can also be pre-populated from a **Jahia CMS XML export** without fetching anything from the web (see below).

## Jahia XML import

Export a section of your Jahia site as XML and import it directly into the cache via **Import XML…** in the Sitemap tab. A form asks for the site base URL (e.g. `https://academy.jahia.com`) which is prepended to each vanity URL path found in the export.

Supported content node types:
- `jacademy:document` — `textContent` attribute
- `jnt:bigText` (rich-text blocks) — `text` attribute

Pages marked `jmix:markedForDeletion` are skipped automatically.

The canonical URL for each page is taken from the `jnt:vanityUrl` child with `j:default="true"` and `j:active="true"`.

## Project structure

```
app.py              GUI application (CustomTkinter)
url2md.py           Core fetch + HTML→Markdown conversion + PageCache (SQLite/FTS5)
pdf_export.py       Markdown→PDF via WeasyPrint
jahia_import.py     Jahia XML export → cache injection
url2md              Shell wrapper (CLI entry point)
```

## Building the macOS app

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name url2md \
  --add-data "url2md.py:." \
  --add-data "pdf_export.py:." \
  --add-data "jahia_import.py:." \
  app.py
# App is in dist/url2md.app
```

## License

MIT
