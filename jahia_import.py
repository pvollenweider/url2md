#!/opt/homebrew/bin/python3.13
"""
Import Jahia XML exports directly into the url2md page cache.

Each exported XML file may contain a tree of jnt:page nodes.
For each page the importer extracts:
  - canonical URL  (active default vanity URL → prepend base URL)
  - title          (j:translation_en jcr:title on the page node)
  - HTML content   (textContent attribute on j:translation_en nodes inside
                    document areas — may span multiple content blocks)
  - lastmod        (jcr:lastModified date → YYYY-MM-DD)

Usage (CLI):
    python3 jahia_import.py file.xml [https://academy.jahia.com]
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Iterator

import html2text
from bs4 import BeautifulSoup

# ── XML namespaces ────────────────────────────────────────────────────────────

_NS_J   = "http://www.jahia.org/jahia/1.0"
_NS_JCR = "http://www.jcp.org/jcr/1.0"

def _j(name: str)   -> str: return f"{{{_NS_J}}}{name}"
def _jcr(name: str) -> str: return f"{{{_NS_JCR}}}{name}"

_TAG_TRANSLATION_EN = _j("translation_en")
_TYPE_PAGE          = "jnt:page"
_TYPE_VANITY_URL    = "jnt:vanityUrl"


# ── XML parsing ───────────────────────────────────────────────────────────────

def iter_pages(xml_path: str | Path) -> Iterator[dict]:
    """
    Parse a Jahia XML export file and yield one dict per page:
      url_path  – canonical vanity path  (e.g. /documentation/...)
      title     – page title in English
      html      – concatenated HTML content from all document blocks
      lastmod   – YYYY-MM-DD or None
    """
    tree = ET.parse(xml_path)
    yield from _walk(tree.getroot())


def _walk(el) -> Iterator[dict]:
    if el.get(_jcr("primaryType")) == _TYPE_PAGE:
        page = _extract_page(el)
        if page:
            yield page
    for child in el:
        yield from _walk(child)


def _extract_page(el) -> dict | None:
    # Skip pages marked for deletion
    mixins = el.get(_jcr("mixinTypes"), "")
    if "jmix:markedForDeletion" in mixins:
        return None

    # ── title ────────────────────────────────────────────────────────────────
    title = ""
    for child in el:
        if child.tag == _TAG_TRANSLATION_EN:
            t = child.get(_jcr("title"), "")
            if t:
                title = t
                break

    # ── canonical URL path ───────────────────────────────────────────────────
    url_path = _pick_vanity_url(el)
    if not url_path:
        return None

    # ── lastmod ──────────────────────────────────────────────────────────────
    lastmod_raw = el.get(_jcr("lastModified"), "")
    lastmod = lastmod_raw[:10] if lastmod_raw else None

    # ── HTML content (all document blocks, English only) ─────────────────────
    html_parts: list[str] = []
    _collect_html(el, html_parts)

    if not title and not html_parts:
        return None

    return {
        "url_path": url_path,
        "title":    title,
        "html":     "\n".join(html_parts),
        "lastmod":  lastmod,
    }


def _pick_vanity_url(el) -> str | None:
    """
    Among all active jnt:vanityUrl children of the vanityUrlMapping container,
    prefer the one marked j:default="true"; fall back to most recently modified.
    """
    for child in el:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local != "vanityUrlMapping":
            continue
        best_default: tuple[str, str] | None = None  # (path, lastmod)
        best_active:  tuple[str, str] | None = None
        for url_node in child:
            if url_node.get(_jcr("primaryType")) != _TYPE_VANITY_URL:
                continue
            if url_node.get(_j("active")) != "true":
                continue
            path = url_node.get(_j("url"), "").strip()
            if not path:
                continue
            lm = url_node.get(_jcr("lastModified"), "")
            if url_node.get(_j("default")) == "true":
                if best_default is None or lm > best_default[1]:
                    best_default = (path, lm)
            if best_active is None or lm > best_active[1]:
                best_active = (path, lm)
        chosen = best_default or best_active
        return chosen[0] if chosen else None
    return None


def _collect_html(el, parts: list[str]) -> None:
    """
    Recursively collect HTML strings from all j:translation_en nodes in the
    subtree, skipping nested jnt:page children (separate pages).

    Supported content attributes (both are raw HTML, always on translation_en):
      textContent  – used by jacademy:document nodes
      text         – used by jnt:bigText (rich-text) nodes
    """
    for child in el:
        ptype = child.get(_jcr("primaryType"), "")
        if ptype == _TYPE_PAGE:
            continue                        # nested page — handled separately
        if child.tag == _TAG_TRANSLATION_EN:
            for attr in ("textContent", "text"):
                tc = child.get(attr, "").strip()
                if tc:
                    parts.append(tc)
                    break               # only one content attr per node
            # no need to recurse inside a translation node
        else:
            _collect_html(child, parts)


# ── HTML → Markdown ───────────────────────────────────────────────────────────

def _html_to_md(html: str, keep_images: bool = False) -> str:
    """Convert an HTML fragment to Markdown (same pipeline as fetch_markdown)."""
    if not html.strip():
        return ""
    soup = BeautifulSoup(f"<div>{html}</div>", "html.parser")
    content = soup.find("div")
    for tag in soup(["script", "style", "nav", "aside", "noscript"]):
        tag.decompose()
    pre_blocks: dict[str, str] = {}
    for idx, pre in enumerate(content.find_all("pre")):
        inner = pre.find("code")
        text  = (inner if inner else pre).get_text()
        token = f"URLMD_PRE_{idx}_ENDBLOCK"
        pre_blocks[token] = text.strip("\n")
        pre.replace_with(BeautifulSoup(f"<p>{token}</p>", "html.parser").find("p"))
    converter = html2text.HTML2Text()
    converter.ignore_links  = False
    converter.ignore_images = not keep_images
    converter.body_width    = 0
    md = converter.handle(str(content)).strip()
    for token, code in pre_blocks.items():
        md = md.replace(token, f"```\n{code}\n```")
    return md


# ── main entry point ──────────────────────────────────────────────────────────

def import_xml_to_cache(
    xml_path: str | Path,
    cache,
    base_url: str = "https://academy.jahia.com",
    keep_images: bool = False,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """
    Parse xml_path and inject all pages into cache.
    Returns (imported_count, skipped_count).
    progress_cb(done, total) is called after each page.
    """
    pages  = list(iter_pages(xml_path))
    total  = len(pages)
    imported = skipped = 0

    for i, page in enumerate(pages):
        url = base_url.rstrip("/") + page["url_path"]
        md_parts: list[str] = []
        if page["title"]:
            md_parts.append(f"# {page['title']}\n")
        body_md = _html_to_md(page["html"], keep_images=keep_images)
        if body_md:
            md_parts.append(body_md)
        md = "\n\n".join(md_parts).strip()
        if md:
            cache.put(url, md, lastmod=page["lastmod"])
            imported += 1
        else:
            skipped += 1
        if progress_cb:
            progress_cb(i + 1, total)

    return imported, skipped


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 jahia_import.py <export.xml> [https://academy.jahia.com]")
        sys.exit(1)

    xml_path = sys.argv[1]
    base_url = sys.argv[2] if len(sys.argv) > 2 else "https://academy.jahia.com"

    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from url2md import PageCache

    cache = PageCache()
    before = cache.entry_count()

    def _progress(done: int, total: int) -> None:
        filled = done * 30 // total
        bar = "█" * filled + "░" * (30 - filled)
        pct = done * 100 // total
        print(f"\r  [{bar}] {pct:3d}%  {done}/{total}", end="", flush=True)

    print(f"Parsing {Path(xml_path).name}…")
    imported, skipped = import_xml_to_cache(xml_path, cache, base_url, progress_cb=_progress)
    print(f"\nImported : {imported} page{'s' if imported != 1 else ''}")
    if skipped:
        print(f"Skipped  : {skipped} (no content)")
    print(f"Cache    : {before} → {cache.entry_count()} entries")
