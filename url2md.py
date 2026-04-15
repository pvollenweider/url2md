#!/opt/homebrew/bin/python3.13
"""Fetch a URL and output the main content as Markdown."""

import sys
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import certifi
import requests
import html2text
from bs4 import BeautifulSoup

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


def fetch_sitemap_urls(url: str) -> list[str]:
    """
    Fetch a sitemap (or sitemap index) and return all <loc> page URLs.
    Recursively follows sitemap index files.
    """
    response = requests.get(url, headers=_HEADERS, timeout=15, verify=certifi.where())
    response.raise_for_status()

    root = ET.fromstring(response.content)
    local = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    def _locs(el):
        for child in el.iter(f"{{{_SITEMAP_NS}}}loc"):
            if child.text and child.text.strip():
                yield child.text.strip()

    # Sitemap index → recurse into each child sitemap
    if local == "sitemapindex":
        urls = []
        for child_url in _locs(root):
            try:
                urls.extend(fetch_sitemap_urls(child_url))
            except Exception:
                pass
        return urls

    return list(_locs(root))


def fetch_markdown(url: str, keep_images: bool = False) -> str:
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

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = not keep_images
    converter.body_width = 0  # no wrapping

    return converter.handle(str(content)).strip()


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
