#!/opt/homebrew/bin/python3.13
"""Fetch a URL and output the main content as Markdown."""

import ssl
import sys
from urllib.parse import urljoin
import certifi
import requests
import html2text
from bs4 import BeautifulSoup


def fetch_markdown(url: str, keep_images: bool = False) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(url, headers=headers, timeout=15, verify=certifi.where())
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
        md = fetch_markdown(url)
        print(md)
    except requests.HTTPError as e:
        print(f"Erreur HTTP: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
