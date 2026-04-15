#!/opt/homebrew/bin/python3.13
"""Fetch a URL and output the main content as Markdown."""

import json
import re
import sqlite3
import sys
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import certifi
import requests
import html2text
from bs4 import BeautifulSoup

# ── Page cache ───────────────────────────────────────────────────────────────

class PageCache:
    """SQLite + FTS5 Markdown cache with lastmod invalidation and full-text search."""

    _PATH      = Path.home() / ".cache" / "url2md" / "pages.db"
    _JSON_PATH = Path.home() / ".cache" / "url2md" / "pages.json"

    def __init__(self):
        self._lock = threading.Lock()
        self._PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._PATH),
            check_same_thread=False,
            isolation_level=None,   # manual transaction management
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                url       TEXT PRIMARY KEY,
                md        TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                lastmod   TEXT
            )
        """)
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                url  UNINDEXED,
                h1, h2, h3, body,
                tokenize='unicode61 remove_diacritics 1'
            )
        """)
        self._migrate_from_json()

    # ── migration ─────────────────────────────────────────────────────────────

    def _migrate_from_json(self) -> None:
        """One-time migration from pages.json → pages.db."""
        if not self._JSON_PATH.exists():
            return
        try:
            data = json.loads(self._JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        if not data:
            self._JSON_PATH.rename(self._JSON_PATH.with_suffix(".json.migrated"))
            return
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for url, entry in data.items():
                    md        = entry.get("md", "")
                    cached_at = entry.get("cached_at", datetime.now(timezone.utc).isoformat())
                    lastmod   = entry.get("lastmod")
                    h1, h2, h3, body = self._extract_sections(md)
                    self._conn.execute(
                        "INSERT OR IGNORE INTO pages (url, md, cached_at, lastmod) VALUES (?,?,?,?)",
                        (url, md, cached_at, lastmod),
                    )
                    self._conn.execute(
                        "INSERT INTO pages_fts (url, h1, h2, h3, body) VALUES (?,?,?,?,?)",
                        (url, h1, h2, h3, body),
                    )
                self._conn.execute("COMMIT")
                self._JSON_PATH.rename(self._JSON_PATH.with_suffix(".json.migrated"))
            except Exception:
                self._conn.execute("ROLLBACK")

    # ── internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_sections(md: str) -> tuple[str, str, str, str]:
        """Split Markdown into (h1, h2, h3, body) text for FTS indexing."""
        h1, h2, h3, body = [], [], [], []
        for line in md.splitlines():
            s = line.strip()
            if not s or s.startswith("<!--"):
                continue
            if s.startswith("#### ") or s.startswith("### "):
                h3.append(s.lstrip("#").strip())
            elif s.startswith("## "):
                h2.append(s[3:].strip())
            elif s.startswith("# "):
                h1.append(s[2:].strip())
            else:
                body.append(s)
        return " ".join(h1), " ".join(h2), " ".join(h3), " ".join(body)

    @staticmethod
    def _positive_fts5_query(expr: str) -> str | None:
        """Extract non-negated tokens from a boolean expression as an FTS5 OR query."""
        tokens = re.findall(r'\(|\)|[^\s()]+', expr.strip())
        terms: list[str] = []
        skip_next = False
        for tok in tokens:
            low = tok.lower()
            if low == "not":
                skip_next = True
            elif low in ("and", "or") or tok in ("(", ")"):
                pass
            elif skip_next:
                skip_next = False
            else:
                safe = tok.replace('"', '""')
                terms.append(f'"{safe}"')
        return " OR ".join(terms) if terms else None

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, url: str, lastmod: str | None = None) -> str | None:
        """Return cached Markdown if still valid, else None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT md, cached_at FROM pages WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        if lastmod and row["cached_at"][:10] < lastmod:
            return None
        return row["md"]

    def has(self, url: str) -> bool:
        """True if url is in cache (ignores lastmod)."""
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM pages WHERE url = ?", (url,)
            ).fetchone() is not None

    def put(self, url: str, md: str, lastmod: str | None = None) -> None:
        cached_at = datetime.now(timezone.utc).isoformat()
        h1, h2, h3, body = self._extract_sections(md)
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM pages_fts WHERE url = ?", (url,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO pages (url, md, cached_at, lastmod) VALUES (?,?,?,?)",
                    (url, md, cached_at, lastmod),
                )
                self._conn.execute(
                    "INSERT INTO pages_fts (url, h1, h2, h3, body) VALUES (?,?,?,?,?)",
                    (url, h1, h2, h3, body),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def delete(self, url: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM pages_fts WHERE url = ?", (url,))
                self._conn.execute("DELETE FROM pages WHERE url = ?", (url,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM pages_fts")
                self._conn.execute("DELETE FROM pages")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def entry_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM pages").fetchone()
        return row[0] if row else 0

    def size_bytes(self) -> int:
        try:
            return self._PATH.stat().st_size if self._PATH.exists() else 0
        except Exception:
            return 0

    def search_with_md(self, expr: str) -> list[tuple[str, float, str]]:
        """
        FTS5 pre-filtered search. Returns [(url, bm25_score, md)] sorted by
        relevance descending. Caller applies the full boolean post-filter
        (NOT clauses, etc.) via its own content_fn.

        Column weights: h1=100, h2=40, h3=15, body=1.
        """
        pos_query = self._positive_fts5_query(expr)
        with self._lock:
            if pos_query:
                rows = self._conn.execute(
                    "SELECT p.url, -bm25(pages_fts, 0, 100, 40, 15, 1) AS score, p.md "
                    "FROM pages_fts "
                    "JOIN pages p ON p.url = pages_fts.url "
                    "WHERE pages_fts MATCH ? "
                    "ORDER BY score DESC",
                    (pos_query,),
                ).fetchall()
            else:
                # Purely negative expression — must scan all cached pages
                rows = self._conn.execute(
                    "SELECT url, 1.0 AS score, md FROM pages"
                ).fetchall()
        return [(r["url"], float(r["score"]), r["md"]) for r in rows]


# ── ─────────────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def is_sitemap_url(url: str) -> bool:
    """True if the URL appears to point to a sitemap XML file."""
    path = url.split("?")[0].lower().rstrip("/")
    return path.endswith(".xml") and "sitemap" in path


def fetch_sitemap_entries(url: str) -> list[dict]:
    """
    Fetch a sitemap (or sitemap index) and return a list of
    {"url": ..., "lastmod": ...} dicts.  lastmod may be None.
    Recursively follows sitemap index files.
    """
    response = requests.get(url, headers=_HEADERS, timeout=15, verify=certifi.where())
    response.raise_for_status()

    root = ET.fromstring(response.content)
    local = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    def _entry(url_el):
        loc = url_el.find(f"{{{_SITEMAP_NS}}}loc")
        if loc is None or not (loc.text and loc.text.strip()):
            return None
        lm_el = url_el.find(f"{{{_SITEMAP_NS}}}lastmod")
        lastmod = lm_el.text.strip()[:10] if lm_el is not None and lm_el.text else None
        return {"url": loc.text.strip(), "lastmod": lastmod}

    # Sitemap index → recurse into each child sitemap
    if local == "sitemapindex":
        entries: list[dict] = []
        for sm in root.iter(f"{{{_SITEMAP_NS}}}sitemap"):
            loc = sm.find(f"{{{_SITEMAP_NS}}}loc")
            if loc is None or not (loc.text and loc.text.strip()):
                continue
            try:
                entries.extend(fetch_sitemap_entries(loc.text.strip()))
            except Exception:
                pass
        return entries

    entries = []
    for url_el in root.iter(f"{{{_SITEMAP_NS}}}url"):
        e = _entry(url_el)
        if e:
            entries.append(e)
    return entries


def fetch_sitemap_urls(url: str) -> list[str]:
    """Return all <loc> page URLs from a sitemap (compatibility wrapper)."""
    return [e["url"] for e in fetch_sitemap_entries(url)]


def fetch_markdown(
    url: str,
    keep_images: bool = False,
    cache: "PageCache | None" = None,
    lastmod: str | None = None,
) -> str:
    if cache is not None:
        cached = cache.get(url, lastmod)
        if cached is not None:
            return cached

    response = requests.get(url, headers=_HEADERS, timeout=15, verify=certifi.where())
    response.raise_for_status()

    # Let BeautifulSoup detect encoding from <meta charset> rather than
    # trusting requests' guess (which defaults to Latin-1 when ambiguous).
    soup = BeautifulSoup(response.content, "html.parser")

    # Rewrite relative URLs to absolute (images + file links)
    for img in soup.find_all("img", src=True):
        img["src"] = urljoin(url, img["src"])
    for a in soup.find_all("a", href=True):
        a["href"] = urljoin(url, a["href"])

    # Remove noise
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    for tag in soup.find_all(class_=lambda c: c and "nav" in c.split()):
        tag.decompose()

    # Try to find main content
    articles = soup.find_all("article")
    content = (
        (articles[0] if len(articles) == 1 else None)
        or soup.find("main")
        or soup.find(id="content")
        or soup.find(id="main")
        or soup.find(class_="content")
        or soup.find(class_="main")
        or soup.find("body")
    )

    if content is None:
        content = soup

    # Pull <pre> blocks out before html2text so they become fenced code blocks
    # instead of 4-space-indented text.  Works for <pre><code>…</code></pre>
    # and bare <pre>…</pre>, even when the content spans multiple lines.
    pre_blocks: dict[str, str] = {}
    for idx, pre in enumerate(content.find_all("pre")):
        inner = pre.find("code")
        text = (inner if inner else pre).get_text()
        token = f"URLMD_PRE_{idx}_ENDBLOCK"
        pre_blocks[token] = text.strip("\n")
        pre.replace_with(BeautifulSoup(f"<p>{token}</p>", "html.parser").find("p"))

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = not keep_images
    converter.body_width = 0  # no wrapping

    md = converter.handle(str(content)).strip()

    # Restore fenced code blocks
    for token, code in pre_blocks.items():
        md = md.replace(token, f"```\n{code}\n```")

    if cache is not None:
        cache.put(url, md, lastmod=lastmod)
    return md


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("URL: ").strip()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        if is_sitemap_url(url):
            urls = fetch_sitemap_urls(url)
            parts = []
            for u in urls:
                try:
                    parts.append(f"<!-- {u} -->\n\n{fetch_markdown(u)}")
                except Exception as e:
                    parts.append(f"<!-- {u} -->\n\n> Erreur : {e}")
            print("\n\n---\n\n".join(parts))
        else:
            print(fetch_markdown(url))
    except requests.HTTPError as e:
        print(f"Erreur HTTP: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
