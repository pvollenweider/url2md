# url2md

Convert any web page to clean Markdown — with a native macOS GUI.

![macOS](https://img.shields.io/badge/macOS-arm64-black?logo=apple)
![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Single URL** — paste a URL, get Markdown in one click
- **Batch mode** — convert a list of URLs and merge them into a single document
- **Smart content extraction** — picks `<article>` when unique, falls back to `<main>` then other heuristics; strips navbars, sidebars, footers and Bootstrap nav components automatically
- **Internal link rewriting** — in batch mode, cross-page links become in-document anchors
- **PDF export** — export the Markdown to a beautifully styled A4 PDF via WeasyPrint
- **Image toggle** — optionally keep or strip images
- **Dark UI** — minimal Catppuccin-inspired dark interface built with Tkinter

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

## How content extraction works

```
1. Remove <script>, <style>, <nav>, <header>, <footer>, <aside>, <noscript>
2. Remove any element whose CSS class list contains "nav" (Bootstrap nav tabs, etc.)
3. If exactly one <article> exists  →  use it
4. Else: <main>  →  #content  →  #main  →  .content  →  .main  →  <body>
```

## Project structure

```
app.py          GUI application (Tkinter)
url2md.py       Core fetch + HTML→Markdown conversion
pdf_export.py   Markdown→PDF via WeasyPrint
url2md          Shell wrapper (CLI entry point)
```

## Building the macOS app

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name url2md \
  --add-data "url2md.py:." \
  --add-data "pdf_export.py:." \
  app.py
# App is in dist/url2md.app
```

## License

MIT
