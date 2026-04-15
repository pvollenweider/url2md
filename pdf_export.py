#!/opt/homebrew/bin/python3.13
"""Convert Markdown to a clean PDF via WeasyPrint."""

import markdown
import weasyprint

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

@page {
    size: A4;
    margin: 2.5cm 2.8cm 2.5cm 2.8cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: -apple-system, "Helvetica Neue", sans-serif;
        font-size: 9pt;
        color: #aaa;
    }
}

* { box-sizing: border-box; }

body {
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.7;
    color: #1a1a2e;
    background: white;
}

h1, h2, h3, h4, h5, h6 {
    font-weight: 700;
    line-height: 1.25;
    margin-top: 1.6em;
    margin-bottom: 0.4em;
    color: #11112a;
}
h1 { font-size: 22pt; margin-top: 0; border-bottom: 2px solid #e0e0f0; padding-bottom: 0.2em; }
h2 { font-size: 16pt; border-bottom: 1px solid #e8e8f4; padding-bottom: 0.15em; }
h3 { font-size: 13pt; }
h4 { font-size: 11pt; }

p { margin: 0 0 0.85em 0; orphans: 3; widows: 3; }

a { color: #5a4ad1; text-decoration: underline; word-break: break-all; }

ul, ol { margin: 0.4em 0 0.85em 0; padding-left: 1.6em; }
li { margin-bottom: 0.25em; }
li > ul, li > ol { margin: 0.2em 0 0.2em 0; }

blockquote {
    margin: 1em 0;
    padding: 0.6em 1em;
    border-left: 4px solid #7c6af7;
    background: #f6f5ff;
    color: #444;
    border-radius: 0 4px 4px 0;
}
blockquote p { margin: 0; }

code {
    font-family: "Menlo", "JetBrains Mono", "Courier New", monospace;
    font-size: 9pt;
    background: #f0f0f8;
    padding: 0.1em 0.35em;
    border-radius: 3px;
    color: #5a4ad1;
}

pre {
    background: #f0f0f8;
    border: 1px solid #ddddf0;
    border-radius: 6px;
    padding: 0.9em 1.1em;
    overflow-x: auto;
    margin: 0.8em 0 1.1em 0;
    page-break-inside: avoid;
}
pre code {
    background: none;
    padding: 0;
    color: #1a1a2e;
    font-size: 9pt;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}
th {
    background: #eeeef8;
    font-weight: 600;
    text-align: left;
    padding: 0.5em 0.8em;
    border: 1px solid #d0d0e8;
}
td {
    padding: 0.45em 0.8em;
    border: 1px solid #e0e0f0;
}
tr:nth-child(even) td { background: #f8f8fc; }

img {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
}

hr {
    border: none;
    border-top: 1px solid #e0e0f0;
    margin: 1.8em 0;
}

/* page-break divs injected between batch pages */
div[style*="page-break-after"] {
    page-break-after: always;
}

/* source comment hidden in PDF */
.source-comment { display: none; }
"""


def md_to_pdf(md_text: str, output_path: str) -> None:
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    weasyprint.HTML(string=html).write_pdf(
        output_path,
        stylesheets=[weasyprint.CSS(string=CSS)],
        uncompressed_pdf=False,
    )
