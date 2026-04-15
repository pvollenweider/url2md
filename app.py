#!/opt/homebrew/bin/python3.13
"""url2md — GUI (CustomTkinter)"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urldefrag, urlparse
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk

from url2md import (
    PageCache,
    fetch_markdown,
    fetch_sitemap_entries,
    fetch_sitemap_urls,
    is_sitemap_url,
)
from pdf_export import md_to_pdf
from jahia_import import import_xml_to_cache

_cache = PageCache()

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── palette ───────────────────────────────────────────────────────────────────
TEXT    = "#DCE4EE"
MUTED   = "gray55"
SUCCESS = "#a6e3a1"
ERROR   = "#f38ba8"


# ── Filter presets ────────────────────────────────────────────────────────────

class FilterPresets:
    """Persist named (url_filter, content_filter) pairs to JSON."""

    _PATH = Path.home() / ".cache" / "url2md" / "presets.json"

    def __init__(self):
        self._data: list[dict] = []
        self._load()

    def _load(self):
        try:
            if self._PATH.exists():
                self._data = json.loads(self._PATH.read_text(encoding="utf-8"))
        except Exception:
            self._data = []

    def _save(self):
        try:
            self._PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PATH.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def names(self) -> list[str]:
        return [p["name"] for p in self._data]

    def get(self, name: str) -> dict | None:
        return next((p for p in self._data if p["name"] == name), None)

    def upsert(self, name: str, url_filter: str, content_filter: str) -> None:
        for p in self._data:
            if p["name"] == name:
                p["url"] = url_filter
                p["content"] = content_filter
                self._save()
                return
        self._data.append({"name": name, "url": url_filter, "content": content_filter})
        self._save()

    def delete(self, name: str) -> None:
        self._data = [p for p in self._data if p["name"] != name]
        self._save()


_presets = FilterPresets()


# ── Jahia import dialog ───────────────────────────────────────────────────────

class JahiaImportDialog(ctk.CTkToplevel):
    """Modal dialog to collect import parameters for a Jahia XML export."""

    def __init__(self, parent, xml_paths: list[str]):
        super().__init__(parent)
        self.title("Import Jahia XML")
        self.resizable(False, False)
        self.grab_set()             # modal
        self._result: dict | None = None

        pad = dict(padx=20, pady=(0, 12))

        ctk.CTkLabel(
            self, text="Import Jahia XML export",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(18, 4))

        # File list (read-only summary)
        names = [Path(p).name for p in xml_paths]
        summary = "\n".join(f"  • {n}" for n in names)
        ctk.CTkLabel(
            self, text=summary,
            font=ctk.CTkFont(size=11), text_color=MUTED,
            anchor="w", justify="left",
        ).pack(fill="x", padx=20, pady=(0, 14))

        # Base URL field
        ctk.CTkLabel(
            self, text="Site base URL",
            font=ctk.CTkFont(size=12), anchor="w",
        ).pack(fill="x", **pad)

        self._url_var = tk.StringVar(value="https://academy.jahia.com")
        self._url_entry = ctk.CTkEntry(
            self, textvariable=self._url_var,
            placeholder_text="https://example.com",
            height=36, font=ctk.CTkFont(size=13),
            width=380,
        )
        self._url_entry.pack(fill="x", padx=20, pady=(0, 6))
        self._url_entry.bind("<Return>", lambda _: self._ok())

        ctk.CTkLabel(
            self,
            text="Prepended to each vanity URL path  (e.g. /documentation/…)",
            font=ctk.CTkFont(size=10), text_color=MUTED, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 18))

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 18))
        btn_row.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            btn_row, text="Cancel", width=110, height=34,
            fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
            text_color=("gray10", "gray90"),
            font=ctk.CTkFont(size=12),
            command=self.destroy,
        ).grid(row=0, column=1, padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="Import", width=110, height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._ok,
        ).grid(row=0, column=2)

        # Center over parent
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")
        self._url_entry.focus_set()
        self.wait_window()

    def _ok(self):
        base = self._url_var.get().strip().rstrip("/")
        if not base:
            return
        if not base.startswith("http"):
            base = "https://" + base
        self._result = {"base_url": base}
        self.destroy()

    @property
    def result(self) -> dict | None:
        return self._result


# ── PDF export ────────────────────────────────────────────────────────────────

def export_pdf_dialog(md_text: str) -> None:
    if not md_text.strip():
        return
    path = filedialog.asksaveasfilename(
        defaultextension=".pdf",
        filetypes=[("PDF", "*.pdf")],
        title="Export as PDF",
    )
    if not path:
        return
    def _write():
        try:
            md_to_pdf(md_text, path)
            messagebox.showinfo("Export PDF", f"File saved:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export PDF", f"Error: {exc}")
    threading.Thread(target=_write, daemon=True).start()


# ── helpers ───────────────────────────────────────────────────────────────────

def _url_to_anchor(url: str) -> str:
    p = urlparse(url)
    slug = (p.netloc + p.path).strip("/").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "page"

def _rewrite_links(md: str, url_to_anchor: dict[str, str]) -> str:
    def replace(m):
        text, href = m.group(1), m.group(2).strip()
        base, _ = urldefrag(href)
        anchor = url_to_anchor.get(base) or url_to_anchor.get(base.rstrip("/"))
        return f"[{text}](#{anchor})" if anchor else m.group(0)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, md)

def _compile_filter(expr: str):
    """Compile '(8.2 or 8-2) and developer' → callable(url) -> bool."""
    tokens = re.findall(r'\(|\)|[^\s()]+', expr.strip())
    parts = []
    for tok in tokens:
        low = tok.lower()
        if low in ('and', 'or', 'not') or tok in ('(', ')'):
            parts.append(f' {low} ')
        else:
            escaped = tok.replace('\\', '\\\\').replace('"', '\\"').lower()
            parts.append(f'"{escaped}" in url')
    body = ''.join(parts).strip()
    if not body:
        return None
    try:
        src = f"def _f(url):\n return bool({body})\n"
        ns: dict = {}
        exec(compile(src, '<filter>', 'exec'), ns)
        return ns['_f']
    except SyntaxError:
        return None

# ── Markdown preview (native tk.Text renderer) ───────────────────────────────

_INLINE = re.compile(
    r'\*\*\*(.+?)\*\*\*'       # bold+italic
    r'|\*\*(.+?)\*\*'           # bold
    r'|__(.+?)__'               # bold alt
    r'|\*([^*\n]+?)\*'          # italic
    r'|_([^_\n]+?)_'            # italic alt
    r'|`([^`\n]+?)`'            # inline code
    r'|\[([^\]]+)\]\([^)]*\)'   # link → show text
)

class MarkdownPreview(tk.Text):
    """Read-only tk.Text that renders Markdown with tag-based styling."""

    _F  = "Helvetica Neue"
    _M  = "Menlo"
    _BG = "#ffffff"
    _FG = "#1a1a2e"

    def __init__(self, parent, **kw):
        super().__init__(
            parent,
            bg=self._BG, fg=self._FG,
            font=(self._F, 13),
            wrap="word", relief="flat", bd=0,
            padx=22, pady=16,
            state="disabled", cursor="arrow",
            selectbackground="#d0d8ff",
            highlightthickness=0,
            **kw,
        )
        F, M = self._F, self._M
        self.tag_configure("h1",  font=(F,22,"bold"), foreground="#11112a", spacing1=14, spacing3=6)
        self.tag_configure("h2",  font=(F,17,"bold"), foreground="#11112a", spacing1=10, spacing3=4)
        self.tag_configure("h3",  font=(F,14,"bold"), foreground="#11112a", spacing1=8,  spacing3=3)
        self.tag_configure("h4",  font=(F,13,"bold"), foreground="#11112a", spacing1=6,  spacing3=2)
        self.tag_configure("b",   font=(F,13,"bold"))
        self.tag_configure("i",   font=(F,13,"italic"))
        self.tag_configure("bi",  font=(F,13,"bold","italic"))
        self.tag_configure("code",font=(M,11), background="#f0f0f8", foreground="#5a4ad1")
        self.tag_configure("pre", font=(M,11), background="#f0f0f8", foreground=self._FG,
                            lmargin1=14, lmargin2=14, spacing1=6, spacing3=6)
        self.tag_configure("bq",  foreground="#555", lmargin1=24, lmargin2=24,
                            spacing1=3, spacing3=3)
        self.tag_configure("li",  lmargin1=20, lmargin2=38, spacing1=2)
        self.tag_configure("link",foreground="#5a4ad1", underline=True)
        self.tag_configure("hr",  foreground="#e0e0f0", font=(F, 3))
        self.tag_configure("dim", foreground="#c0c0c0", font=(F, 10))
        self.tag_configure("th",  font=(F,12,"bold"), background="#eeeef8")
        self.tag_configure("td",  font=(F,12))
        self.tag_configure("sep", font=(F, 4))

    # ── public ────────────────────────────────────────────────────────────────

    def render(self, md: str) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        if md:
            self._parse(md)
        self.configure(state="disabled")
        self.yview_moveto(0)

    # ── parser ────────────────────────────────────────────────────────────────

    def _parse(self, text: str) -> None:
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            s    = line.strip()

            # fenced code block
            if s.startswith("```"):
                i += 1
                block = []
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    block.append(lines[i])
                    i += 1
                self.insert("end", "\n".join(block) + "\n", "pre")
                i += 1
                continue

            # HTML comment → show URL dimmed
            if s.startswith("<!--") and "-->" in s:
                inner = s[4:s.index("-->")].strip()
                if inner:
                    self.insert("end", inner + "\n", "dim")
                i += 1
                continue

            # skip HTML tags (page-break div, anchors…)
            if re.match(r'^<[^>]+>$', s):
                i += 1
                continue

            # horizontal rule
            if re.match(r'^[-*_]{3,}\s*$', s):
                self.insert("end", "\n" + "─" * 55 + "\n\n", "hr")
                i += 1
                continue

            # header
            m = re.match(r'^(#{1,4})\s+(.*)', line)
            if m:
                lvl = min(len(m.group(1)), 4)
                self.insert("end", "\n", "sep")
                self._inline(m.group(2) + "\n", f"h{lvl}")
                i += 1
                continue

            # table (collect all consecutive pipe lines)
            if s.startswith("|") and "|" in s[1:]:
                block = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    block.append(lines[i])
                    i += 1
                self._table(block)
                continue

            # blockquote
            if s.startswith(">"):
                self._inline(re.sub(r'^>\s?', '', s) + "\n", "bq")
                i += 1
                continue

            # unordered list
            m = re.match(r'^(\s*)[*\-+]\s+(.*)', line)
            if m:
                depth = len(m.group(1)) // 2
                self.insert("end", "  " * depth + "• ", "li")
                self._inline(m.group(2) + "\n", "li")
                i += 1
                continue

            # ordered list
            m = re.match(r'^(\s*)\d+\.\s+(.*)', line)
            if m:
                depth = len(m.group(1)) // 2
                self.insert("end", "  " * depth + "  ", "li")
                self._inline(m.group(2) + "\n", "li")
                i += 1
                continue

            # blank line
            if not s:
                self.insert("end", "\n")
                i += 1
                continue

            # normal paragraph
            self._inline(line + "\n")
            i += 1

    def _inline(self, text: str, base: str = "") -> None:
        tags = (base,) if base else ()
        pos  = 0
        for m in _INLINE.finditer(text):
            if m.start() > pos:
                self.insert("end", text[pos:m.start()], tags)
            g = m.groups()
            if   g[0]: self.insert("end", g[0], tags + ("bi",))
            elif g[1]: self.insert("end", g[1], tags + ("b",))
            elif g[2]: self.insert("end", g[2], tags + ("b",))
            elif g[3]: self.insert("end", g[3], tags + ("i",))
            elif g[4]: self.insert("end", g[4], tags + ("i",))
            elif g[5]: self.insert("end", g[5], ("code",))
            elif g[6]: self.insert("end", g[6], tags + ("link",))
            pos = m.end()
        if pos < len(text):
            self.insert("end", text[pos:], tags)

    def _table(self, lines: list[str]) -> None:
        rows = []
        for line in lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # skip separator rows (--- | :---: | ---)
            if all(re.match(r'^[-: ]+$', c) for c in cells if c):
                continue
            rows.append(cells)
        if not rows:
            return
        ncols   = max(len(r) for r in rows)
        widths  = [0] * ncols
        for row in rows:
            for j, c in enumerate(row[:ncols]):
                widths[j] = max(widths[j], len(c))
        for ri, row in enumerate(rows):
            line = ""
            for j in range(ncols):
                cell = row[j] if j < len(row) else ""
                line += cell.ljust(widths[j] + 2)
            self.insert("end", line + "\n", "th" if ri == 0 else "td")
        self.insert("end", "\n")


# ── split output (markdown source left + preview right) ──────────────────────

class SplitOutput(ctk.CTkFrame):

    def __init__(self, parent, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.textbox = ctk.CTkTextbox(
            self, font=("Menlo", 12), wrap="word", state="disabled",
        )
        self.textbox.grid(row=0, column=0, sticky="nsew", padx=(0, 3))

        # Wrap preview in a plain frame so we can attach a native scrollbar
        pf = tk.Frame(self, bg="#ffffff", highlightthickness=0)
        pf.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        pf.grid_columnconfigure(0, weight=1)
        pf.grid_rowconfigure(0, weight=1)

        self._preview = MarkdownPreview(pf)
        self._preview.grid(row=0, column=0, sticky="nsew")

        sb = tk.Scrollbar(pf, command=self._preview.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._preview.configure(yscrollcommand=sb.set)

    def set(self, text: str) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        if text:
            self.textbox.insert("end", text)
        self.textbox.configure(state="disabled")
        self._preview.render(text or "")

    def get(self, *args, **kwargs):
        return self.textbox.get(*args, **kwargs)


def set_output(widget, text: str) -> None:
    if isinstance(widget, SplitOutput):
        widget.set(text)
    else:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if text:
            widget.insert("end", text)
        widget.configure(state="disabled")


# ── Tab 1 — Single URL ───────────────────────────────────────────────────────

class SingleTab(ctk.CTkFrame):
    def __init__(self, parent, keep_images_var):
        super().__init__(parent, fg_color="transparent")
        self.keep_images_var = keep_images_var
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self):
        # URL row
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", pady=(6, 4))
        row.grid_columnconfigure(0, weight=1)

        self.url_var = tk.StringVar()
        self.url_entry = ctk.CTkEntry(
            row, textvariable=self.url_var,
            placeholder_text="https://…",
            height=38, font=ctk.CTkFont(size=13),
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _: self._start())

        self.btn = ctk.CTkButton(
            row, text="Convert", width=130, height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._start,
        )
        self.btn.grid(row=0, column=1)

        # Output
        self.output = SplitOutput(self)
        self.output.grid(row=1, column=0, sticky="nsew", pady=(0, 4))

        # Status bar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        bar.grid_columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(
            bar, text="", text_color=MUTED,
            font=ctk.CTkFont(size=11), anchor="w",
        )
        self.status.grid(row=0, column=0, sticky="w")

        self.copy_btn = ctk.CTkButton(
            bar, text="Copy", width=80, height=28,
            font=ctk.CTkFont(size=11), state="disabled",
            command=self._copy,
        )
        self.copy_btn.grid(row=0, column=1, padx=(4, 0))

        self.pdf_btn = ctk.CTkButton(
            bar, text="Export PDF…", width=120, height=28,
            font=ctk.CTkFont(size=11), state="disabled",
            command=self._export_pdf,
        )
        self.pdf_btn.grid(row=0, column=2, padx=(6, 0))

    def _start(self):
        url = self.url_var.get().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_var.set(url)

        set_output(self.output, "Converting…")
        self.btn.configure(state="disabled", text="…")
        self.copy_btn.configure(state="disabled")
        self.pdf_btn.configure(state="disabled")
        self.status.configure(text="Loading…", text_color=MUTED)

        keep = self.keep_images_var.get()
        if is_sitemap_url(url):
            threading.Thread(target=self._fetch_sitemap, args=(url, keep), daemon=True).start()
        else:
            threading.Thread(target=self._fetch, args=(url, keep), daemon=True).start()

    def _fetch(self, url, keep_images):
        try:
            md = fetch_markdown(url, keep_images=keep_images)
            self.after(0, self._done, md)
        except Exception as exc:
            self.after(0, self._error, str(exc))

    def _fetch_sitemap(self, url, keep_images):
        try:
            urls = fetch_sitemap_urls(url)
        except Exception as exc:
            self.after(0, self._error, f"Sitemap: {exc}")
            return
        if not urls:
            self.after(0, self._error, "No URLs found in sitemap")
            return
        total = len(urls)
        self.after(0, lambda: self.status.configure(
            text=f"0 / {total} pages…", text_color=MUTED))
        results = {}
        lock = threading.Lock()
        done_count = [0]
        def fetch_one(u):
            try:
                return u, fetch_markdown(u, keep_images=keep_images), None
            except Exception as exc:
                return u, None, str(exc)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_one, u): u for u in urls}
            for future in as_completed(futures):
                u, md, err = future.result()
                with lock:
                    results[u] = (md, err)
                    done_count[0] += 1
                    done = done_count[0]
                self.after(0, lambda d=done: self.status.configure(
                    text=f"{d} / {total} pages…", text_color=MUTED))
        self.after(0, self._done_sitemap, urls, results)

    def _done(self, md):
        set_output(self.output, md)
        lines, words = md.count("\n") + 1, len(md.split())
        self.status.configure(
            text=f"{lines} lines · {words} words · {len(md):,} chars",
            text_color=SUCCESS,
        )
        self.btn.configure(state="normal", text="Convert")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")

    def _done_sitemap(self, urls, results):
        parts = []
        errors = []
        for url in urls:
            md, err = results[url]
            if err:
                errors.append(url)
                parts.append(f"<!-- {url} -->\n\n> **Error**: {err}")
            else:
                parts.append(f"<!-- {url} -->\n\n{md}")
        combined = "\n\n---\n\n<div style=\"page-break-after:always\"></div>\n\n".join(parts)
        set_output(self.output, combined)
        ok = len(urls) - len(errors)
        msg = f"{ok} / {len(urls)} pages converted"
        if errors:
            msg += f" · {len(errors)} error(s)"
        self.status.configure(text=msg, text_color=SUCCESS if not errors else ERROR)
        self.btn.configure(state="normal", text="Convert")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")

    def _error(self, msg):
        set_output(self.output, f"Error: {msg}")
        self.status.configure(text="Failed", text_color=ERROR)
        self.btn.configure(state="normal", text="Convert")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.output.get("1.0", "end-1c"))
        self.copy_btn.configure(text="Copied ✓")
        self.after(1500, lambda: self.copy_btn.configure(text="Copy"))

    def _export_pdf(self):
        export_pdf_dialog(self.output.get("1.0", "end-1c"))


# ── Tab 2 — Batch URLs ───────────────────────────────────────────────────────

class BatchTab(ctk.CTkFrame):
    _PLACEHOLDER = "Paste your URLs here, one per line…"

    def __init__(self, parent, keep_images_var, internal_links_var):
        super().__init__(parent, fg_color="transparent")
        self.keep_images_var    = keep_images_var
        self.internal_links_var = internal_links_var
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=2)
        self._build()

    def _build(self):
        # URL input
        self.url_input = ctk.CTkTextbox(
            self, font=("Menlo", 12), height=130,
        )
        self.url_input.grid(row=0, column=0, sticky="nsew", pady=(6, 0))
        self.url_input.insert("end", self._PLACEHOLDER)
        self.url_input.configure(text_color="gray50")
        self.url_input.bind("<FocusIn>", self._clear_placeholder)

        # Action bar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        bar.grid_columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(
            bar, text="", text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w",
        )
        self.status.grid(row=0, column=0, sticky="w")

        ctk.CTkCheckBox(
            bar, text="Internal links", variable=self.internal_links_var,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, padx=(0, 10))

        self.copy_btn = ctk.CTkButton(
            bar, text="Copy all", width=100, height=28,
            font=ctk.CTkFont(size=11), state="disabled", command=self._copy,
        )
        self.copy_btn.grid(row=0, column=2, padx=(0, 6))

        self.pdf_btn = ctk.CTkButton(
            bar, text="Export PDF…", width=120, height=28,
            font=ctk.CTkFont(size=11), state="disabled", command=self._export_pdf,
        )
        self.pdf_btn.grid(row=0, column=3, padx=(0, 8))

        self.btn = ctk.CTkButton(
            bar, text="Convert all", width=140, height=36,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._start,
        )
        self.btn.grid(row=0, column=4)

        # Output
        self.output = SplitOutput(self)
        self.output.grid(row=2, column=0, sticky="nsew", pady=(0, 4))

    def _clear_placeholder(self, _):
        if self.url_input.get("1.0", "end-1c") == self._PLACEHOLDER:
            self.url_input.delete("1.0", "end")
            self.url_input.configure(text_color=TEXT)

    def _get_urls(self):
        raw = self.url_input.get("1.0", "end-1c").strip()
        urls = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line == self._PLACEHOLDER:
                continue
            if not line.startswith(("http://", "https://")):
                line = "https://" + line
            urls.append(line)
        return urls

    def _start(self):
        raw_urls = self._get_urls()
        if not raw_urls:
            return
        set_output(self.output, "")
        self.btn.configure(state="disabled", text="…")
        self.copy_btn.configure(state="disabled")
        self.status.configure(text="Preparing…", text_color=MUTED)
        keep   = self.keep_images_var.get()
        intern = self.internal_links_var.get()
        threading.Thread(
            target=self._expand_and_fetch, args=(raw_urls, keep, intern), daemon=True,
        ).start()

    def _expand_and_fetch(self, raw_urls, keep_images, internal_links):
        urls = []
        for u in raw_urls:
            if is_sitemap_url(u):
                try:
                    urls.extend(fetch_sitemap_urls(u))
                except Exception:
                    urls.append(u)
            else:
                urls.append(u)
        if not urls:
            self.after(0, lambda: self.status.configure(text="No URLs", text_color=ERROR))
            self.after(0, lambda: self.btn.configure(state="normal", text="Convert all"))
            return
        total = len(urls)
        self.after(0, lambda: self.status.configure(
            text=f"0 / {total} pages…", text_color=MUTED))
        results = {}
        lock = threading.Lock()
        done_count = [0]
        def fetch_one(url):
            try:
                return url, fetch_markdown(url, keep_images=keep_images), None
            except Exception as exc:
                return url, None, str(exc)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_one, url): url for url in urls}
            for future in as_completed(futures):
                url, md, err = future.result()
                with lock:
                    results[url] = (md, err)
                    done_count[0] += 1
                    done = done_count[0]
                self.after(0, lambda d=done, t=total: self.status.configure(
                    text=f"{d} / {t} pages…", text_color=MUTED))
        self.after(0, self._assemble, urls, results, internal_links)

    def _assemble(self, urls, results, internal_links):
        url_to_anchor = {url: _url_to_anchor(url) for url in urls}
        parts, errors = [], []
        for url in urls:
            md, err = results[url]
            if err:
                errors.append(url)
                parts.append(f"<!-- {url} -->\n\n> **Error**: {err}")
            else:
                if internal_links:
                    md = _rewrite_links(md, url_to_anchor)
                anchor = url_to_anchor[url]
                parts.append(f'<a id="{anchor}"></a>\n\n<!-- {url} -->\n\n{md}')
        sep = '\n\n---\n\n<div style="page-break-after:always"></div>\n\n'
        set_output(self.output, sep.join(parts))
        ok = len(urls) - len(errors)
        msg = f"{ok} / {len(urls)} pages converted"
        if errors:
            msg += f" · {len(errors)} error(s)"
        self.status.configure(text=msg, text_color=SUCCESS if not errors else ERROR)
        self.btn.configure(state="normal", text="Convert all")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.output.get("1.0", "end-1c"))
        self.copy_btn.configure(text="Copied ✓")
        self.after(1500, lambda: self.copy_btn.configure(text="Copy all"))

    def _export_pdf(self):
        export_pdf_dialog(self.output.get("1.0", "end-1c"))


# ── Tab 3 — Sitemap ───────────────────────────────────────────────────────────

class SitemapTab(ctk.CTkFrame):
    def __init__(self, parent, keep_images_var):
        super().__init__(parent, fg_color="transparent")
        self.keep_images_var = keep_images_var
        self._checked      = {}
        self._item_urls    = {}
        self._all_urls     = []
        self._checked_urls = set()
        self._lastmod: dict[str, str | None] = {}
        self._current_uncached: set[str] = set()
        self.content_filter_var = tk.StringVar()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=2)  # tree
        self.grid_rowconfigure(7, weight=3)  # output
        self._build()

    def _build(self):
        # URL row
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", pady=(6, 4))
        row.grid_columnconfigure(0, weight=1)

        self.url_var = tk.StringVar()
        ctk.CTkEntry(
            row, textvariable=self.url_var,
            placeholder_text="https://site.com/sitemap.xml",
            height=38, font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.url_var.trace_add("write", lambda *_: None)
        row.children[list(row.children)[-2]].bind("<Return>", lambda _: self._fetch())

        self.fetch_btn = ctk.CTkButton(
            row, text="Fetch sitemap", width=150, height=38,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._fetch,
        )
        self.fetch_btn.grid(row=0, column=1)

        # URL filter row
        frow = ctk.CTkFrame(self, fg_color="transparent")
        frow.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        frow.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frow, text="URL filter:", text_color=MUTED, font=ctk.CTkFont(size=11),
            width=80, anchor="e",
        ).grid(row=0, column=0, padx=(0, 8))

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", self._apply_filter)
        ctk.CTkEntry(
            frow, textvariable=self.filter_var,
            placeholder_text="(8.2 or 8-2) and developer",
            height=30, font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, sticky="ew")

        # Content filter row
        crow = ctk.CTkFrame(self, fg_color="transparent")
        crow.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        crow.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            crow, text="Content:", text_color=MUTED, font=ctk.CTkFont(size=11),
            width=80, anchor="e",
        ).grid(row=0, column=0, padx=(0, 8))

        self.content_filter_var = tk.StringVar()
        self.content_filter_var.trace_add("write", self._apply_filter)
        ctk.CTkEntry(
            crow, textvariable=self.content_filter_var,
            placeholder_text="GraphQL and not deprecated",
            height=30, font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, sticky="ew")

        self.content_status = ctk.CTkLabel(
            crow, text="cache only", text_color=MUTED,
            font=ctk.CTkFont(size=10), width=70, anchor="w",
        )
        self.content_status.grid(row=0, column=2, padx=(8, 0))

        # Preset row
        _sec = dict(
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
            text_color=("gray10", "gray90"),
        )
        prow = ctk.CTkFrame(self, fg_color="transparent")
        prow.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        prow.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            prow, text="Preset:", text_color=MUTED, font=ctk.CTkFont(size=11),
            width=80, anchor="e",
        ).grid(row=0, column=0, padx=(0, 8))

        names = _presets.names()
        self._preset_var = tk.StringVar(value=names[0] if names else "(no presets)")
        self._preset_menu = ctk.CTkOptionMenu(
            prow,
            variable=self._preset_var,
            values=names if names else ["(no presets)"],
            command=self._apply_preset,
            height=30, font=ctk.CTkFont(size=11),
            state="normal" if names else "disabled",
        )
        self._preset_menu.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            prow, text="Save…", width=70, height=30,
            command=self._save_preset, **_sec,
        ).grid(row=0, column=2, padx=(0, 6))

        self._preset_delete_btn = ctk.CTkButton(
            prow, text="Delete", width=70, height=30,
            command=self._delete_preset,
            state="normal" if names else "disabled", **_sec,
        )
        self._preset_delete_btn.grid(row=0, column=3)

        # Tree (ttk — no CTk equivalent)
        tree_wrap = ctk.CTkFrame(self, corner_radius=8)
        tree_wrap.grid(row=4, column=0, sticky="nsew", pady=(0, 4))
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Sitemap.Treeview",
                        background="#2b2b2b", foreground="#dce4ee",
                        fieldbackground="#2b2b2b", borderwidth=0,
                        rowheight=24, font=("Menlo", 11))
        style.configure("Sitemap.Treeview.Heading",
                        background="#1f1f1f", foreground="#888", relief="flat")
        style.map("Sitemap.Treeview",
                  background=[("selected", "#1f538d")],
                  foreground=[("selected", "#fff")])

        self.tree = ttk.Treeview(
            tree_wrap, style="Sitemap.Treeview", show="tree", selectmode="none",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ctk.CTkScrollbar(tree_wrap, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._on_click)

        # Action bar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=5, column=0, sticky="ew", pady=(2, 4))
        bar.grid_columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(
            bar, text="", text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w",
        )
        self.status.grid(row=0, column=0, sticky="w")

        _secondary = dict(
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
            text_color=("gray10", "gray90"),
        )

        ctk.CTkButton(bar, text="Select all",   width=105, height=28,
                      command=self._select_all,   **_secondary
        ).grid(row=0, column=1, padx=(0, 6))

        self.select_cached_btn = ctk.CTkButton(
            bar, text="Select cached", width=115, height=28,
            command=self._select_cached, **_secondary,
        )
        self.select_cached_btn.grid(row=0, column=2, padx=(0, 6))
        self.select_cached_btn.grid_remove()   # hidden until uncached pages appear

        ctk.CTkButton(bar, text="Deselect all", width=105, height=28,
                      command=self._deselect_all, **_secondary
        ).grid(row=0, column=3, padx=(0, 6))

        self.copy_btn = ctk.CTkButton(
            bar, text="Copy", width=80, height=28,
            font=ctk.CTkFont(size=11), state="disabled", command=self._copy,
        )
        self.copy_btn.grid(row=0, column=4, padx=(0, 6))

        self.pdf_btn = ctk.CTkButton(
            bar, text="Export PDF…", width=120, height=28,
            font=ctk.CTkFont(size=11), state="disabled", command=self._export_pdf,
        )
        self.pdf_btn.grid(row=0, column=5, padx=(0, 8))

        self.convert_btn = ctk.CTkButton(
            bar, text="Convert selection", width=185, height=36,
            font=ctk.CTkFont(size=13, weight="bold"), state="disabled",
            command=self._convert,
        )
        self.convert_btn.grid(row=0, column=6)

        # Cache bar
        cbar = ctk.CTkFrame(self, fg_color="transparent")
        cbar.grid(row=6, column=0, sticky="ew", pady=(0, 2))
        cbar.grid_columnconfigure(0, weight=1)

        self.cache_label = ctk.CTkLabel(
            cbar, text="", text_color=MUTED,
            font=ctk.CTkFont(size=11), anchor="w",
        )
        self.cache_label.grid(row=0, column=0, sticky="w")

        _sec = dict(
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
            text_color=("gray10", "gray90"),
        )
        ctk.CTkButton(
            cbar, text="Import XML…", width=120, height=26,
            command=self._import_xml, **_sec,
        ).grid(row=0, column=1, padx=(0, 6))

        ctk.CTkButton(
            cbar, text="Clear cache", width=110, height=26,
            command=self._clear_cache, **_sec,
        ).grid(row=0, column=2)

        self._update_cache_label()

        # Output
        self.output = SplitOutput(self)
        self.output.grid(row=7, column=0, sticky="nsew", pady=(0, 4))

    # ── presets ───────────────────────────────────────────────────────────────

    def _apply_preset(self, name: str):
        p = _presets.get(name)
        if p is None:
            return
        self.filter_var.set(p.get("url", ""))
        self.content_filter_var.set(p.get("content", ""))

    def _save_preset(self):
        url_f     = self.filter_var.get().strip()
        content_f = self.content_filter_var.get().strip()
        if not url_f and not content_f:
            messagebox.showwarning("Save preset", "Both filters are empty — nothing to save.")
            return
        dialog = ctk.CTkInputDialog(text="Preset name:", title="Save preset")
        name = dialog.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        _presets.upsert(name, url_f, content_f)
        self._refresh_presets(select=name)

    def _delete_preset(self):
        name = self._preset_var.get()
        if not name or name not in _presets.names():
            return
        if not messagebox.askyesno("Delete preset", f'Delete preset "{name}"?'):
            return
        _presets.delete(name)
        self._refresh_presets()

    def _refresh_presets(self, select: str | None = None):
        names = _presets.names()
        if names:
            self._preset_menu.configure(values=names, state="normal")
            self._preset_var.set(select if select in names else names[0])
            self._preset_delete_btn.configure(state="normal")
        else:
            self._preset_menu.configure(values=["(no presets)"], state="disabled")
            self._preset_var.set("(no presets)")
            self._preset_delete_btn.configure(state="disabled")

    # ── fetch & tree ──────────────────────────────────────────────────────────

    def _fetch(self):
        url = self.url_var.get().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_var.set(url)
        self.fetch_btn.configure(state="disabled", text="…")
        self.convert_btn.configure(state="disabled")
        self.status.configure(text="Loading sitemap…", text_color=MUTED)
        threading.Thread(target=self._do_fetch, args=(url,), daemon=True).start()

    def _do_fetch(self, url):
        try:
            entries = fetch_sitemap_entries(url)
            self.after(0, self._populate_entries, entries)
        except Exception as exc:
            self.after(0, lambda: self.status.configure(
                text=f"Error: {exc}", text_color=ERROR))
            self.after(0, lambda: self.fetch_btn.configure(
                state="normal", text="Fetch sitemap"))

    def _populate_entries(self, entries: list[dict]):
        if not entries:
            self.status.configure(text="No URLs found", text_color=ERROR)
            self.fetch_btn.configure(state="normal", text="Fetch sitemap")
            return
        self._lastmod = {e["url"]: e.get("lastmod") for e in entries}
        self._populate_tree([e["url"] for e in entries])

    def _populate_tree(self, urls):
        if not urls:
            self.status.configure(text="No URLs found", text_color=ERROR)
            self.fetch_btn.configure(state="normal", text="Fetch sitemap")
            return
        self._all_urls = urls
        self._checked_urls.clear()
        self._rebuild_tree(urls)
        self.status.configure(
            text=f"{len(urls)} pages · 0 selected", text_color=MUTED)
        self.fetch_btn.configure(state="normal", text="Fetch sitemap")
        self.convert_btn.configure(state="normal")

    def _apply_filter(self, *_):
        if not self._all_urls:
            return
        url_expr     = self.filter_var.get().strip()
        content_expr = self.content_filter_var.get().strip()

        url_fn  = _compile_filter(url_expr) if url_expr else None
        url_vis = [u for u in self._all_urls if url_fn(u.lower())] if url_fn else list(self._all_urls)

        if content_expr:
            content_fn = _compile_filter(content_expr)
            # FTS5 pre-filter: positive terms via BM25; purely-negative → all pages
            fts_candidates = _cache.search_with_md(content_expr)
            fts_map = {url: (score, md) for url, score, md in fts_candidates}
            visible: list[str] = []
            uncached: set[str] = set()
            scores: dict[str, float] = {}
            cached_match = cached_total = 0
            for u in url_vis:
                if u in fts_map:
                    cached_total += 1
                    score, md = fts_map[u]
                    # full boolean post-filter handles NOT / AND / OR
                    if content_fn is None or content_fn(md.lower()):
                        scores[u] = score
                        visible.append(u)
                        cached_match += 1
                elif _cache.has(u):
                    cached_total += 1   # cached but no FTS match → hidden
                else:
                    visible.append(u)   # not cached → show dimmed
                    uncached.add(u)
            self._rebuild_tree(visible, uncached=uncached, scores=scores)
            hit_str = f"{cached_match}/{cached_total} cached" if cached_total else "cache only"
            self.content_status.configure(text=hit_str)
        else:
            self._rebuild_tree(url_vis)
            self.content_status.configure(text="cache only")

        self._update_status()

    def _rebuild_tree(self, urls, uncached: set[str] | None = None,
                      scores: dict[str, int] | None = None):
        uncached = uncached or set()
        scores   = scores   or {}
        self._current_uncached = uncached
        self.tree.delete(*self.tree.get_children())
        self._item_urls.clear()
        self._checked.clear()

        self.tree.tag_configure("normal", foreground="#dce4ee")
        self.tree.tag_configure("dim",    foreground="#555555")
        self.tree.tag_configure("folder", foreground="#7a8a9a")

        if uncached:
            self.select_cached_btn.grid()
        else:
            self.select_cached_btn.grid_remove()

        root: dict = {}
        for url in urls:
            p = urlparse(url)
            segments = [s for s in p.path.strip("/").split("/") if s] or [p.netloc]
            node = root
            for seg in segments[:-1]:
                node = node.setdefault(
                    seg, {"__url__": None, "__children__": {}})["__children__"]
            leaf = segments[-1]
            if leaf not in node:
                node[leaf] = {"__url__": None, "__children__": {}}
            node[leaf]["__url__"] = url

        max_score = max(scores.values()) if scores else 1.0

        def _node_max_score(data: dict) -> float:
            s = scores.get(data["__url__"], 0.0) if data["__url__"] else 0.0
            for child in data["__children__"].values():
                s = max(s, _node_max_score(child))
            return s

        def _score_badge(s: float) -> str:
            if max_score <= 0:
                return ""
            r = s / max_score
            if r >= 0.66: return "  ●●●"
            if r >= 0.33: return "  ●●"
            if r > 0:     return "  ●"
            return ""

        def insert(parent_iid, children):
            # Sort by relevance score desc when scores are active, else alphabetically
            def _sort_key(seg):
                return (-_node_max_score(children[seg]), seg)
            order = sorted(children, key=_sort_key if scores else str)

            for seg in order:
                data    = children[seg]
                url     = data["__url__"]
                is_page = url is not None

                if is_page:
                    checked = url in self._checked_urls
                    tag     = "dim" if url in uncached else "normal"
                    score   = scores.get(url, 0.0)
                    badge   = _score_badge(score) if scores else ""
                    text    = ("☑" if checked else "☐") + f"  {seg}{badge}"
                else:
                    checked = False
                    tag     = "folder"
                    # show best-child score badge so the folder hints at relevance
                    node_score = _node_max_score(data) if scores else 0.0
                    badge      = _score_badge(node_score) if scores else ""
                    text       = f"   {seg}{badge}"   # 3 spaces ≈ "☐  " width

                iid = self.tree.insert(
                    parent_iid, "end",
                    text=text, open=True, tags=(tag,),
                )
                self._checked[iid] = checked
                if url:
                    self._item_urls[iid] = url
                if data["__children__"]:
                    insert(iid, data["__children__"])

        insert("", root)

    # ── checkboxes ────────────────────────────────────────────────────────────

    def _set_check(self, iid, state: bool):
        self._checked[iid] = state
        url = self._item_urls.get(iid)
        if url:
            self._checked_urls.add(url) if state else self._checked_urls.discard(url)
            text = self.tree.item(iid, "text")
            self.tree.item(iid, text=("☑" if state else "☐") + text[1:])

    def _toggle_recursive(self, iid, state: bool):
        self._set_check(iid, state)
        for child in self.tree.get_children(iid):
            self._toggle_recursive(child, state)

    def _on_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_recursive(iid, not self._checked.get(iid, False))
        self._update_status()

    def _select_all(self):
        def walk(p):
            for iid in self.tree.get_children(p):
                self._set_check(iid, True); walk(iid)
        walk(""); self._update_status()

    def _select_cached(self):
        """Select only pages that are already in cache."""
        def walk(p):
            for iid in self.tree.get_children(p):
                url = self._item_urls.get(iid)
                state = _cache.has(url) if url else False
                self._set_check(iid, state)
                walk(iid)
        walk(""); self._update_status()

    def _deselect_all(self):
        def walk(p):
            for iid in self.tree.get_children(p):
                self._set_check(iid, False); walk(iid)
        walk(""); self._update_status()

    def _update_status(self):
        total    = len(self._item_urls)
        selected = sum(1 for iid in self._item_urls if self._checked.get(iid, False))
        self.status.configure(
            text=f"{total} pages · {selected} selected",
            text_color=MUTED if selected == 0 else TEXT,
        )

    def _get_selected_urls(self) -> list[str]:
        urls: list[str] = []
        def walk(p):
            for iid in self.tree.get_children(p):
                if iid in self._item_urls and self._checked.get(iid, False):
                    urls.append(self._item_urls[iid])
                walk(iid)
        walk("")
        return urls

    # ── conversion ────────────────────────────────────────────────────────────

    def _convert(self):
        urls = self._get_selected_urls()
        if not urls:
            self.status.configure(text="No page selected", text_color=ERROR)
            return
        set_output(self.output, "")
        self.convert_btn.configure(state="disabled", text="…")
        self.copy_btn.configure(state="disabled")
        self.pdf_btn.configure(state="disabled")
        self.status.configure(text=f"0 / {len(urls)} pages…", text_color=MUTED)
        threading.Thread(
            target=self._do_convert, args=(urls, self.keep_images_var.get()), daemon=True,
        ).start()

    def _do_convert(self, urls, keep_images):
        results = {}
        lock = threading.Lock()
        total = len(urls)
        done_count = [0]
        def fetch_one(url):
            lm = self._lastmod.get(url)
            try:
                return url, fetch_markdown(
                    url, keep_images=keep_images, cache=_cache, lastmod=lm
                ), None
            except Exception as exc:
                return url, None, str(exc)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_one, u): u for u in urls}
            for future in as_completed(futures):
                u, md, err = future.result()
                with lock:
                    results[u] = (md, err)
                    done_count[0] += 1
                    done = done_count[0]
                self.after(0, lambda d=done, t=total: self.status.configure(
                    text=f"{d} / {t} pages…", text_color=MUTED))
        self.after(0, self._assemble, urls, results)

    def _assemble(self, urls, results):
        parts, errors = [], []
        for url in urls:
            md, err = results[url]
            if err:
                errors.append(url)
                parts.append(f"<!-- {url} -->\n\n> **Error**: {err}")
            else:
                parts.append(f"<!-- {url} -->\n\n{md}")
        sep = '\n\n---\n\n<div style="page-break-after:always"></div>\n\n'
        set_output(self.output, sep.join(parts))
        ok = len(urls) - len(errors)
        msg = f"{ok} / {len(urls)} pages converted"
        if errors:
            msg += f" · {len(errors)} error(s)"
        self.status.configure(text=msg, text_color=SUCCESS if not errors else ERROR)
        self.convert_btn.configure(state="normal", text="Convert selection")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")
        self._update_cache_label()

    # ── cache ─────────────────────────────────────────────────────────────────

    def _update_cache_label(self):
        n = _cache.entry_count()
        sz = _cache.size_bytes()
        if sz < 1024:
            sz_str = f"{sz} B"
        elif sz < 1024 * 1024:
            sz_str = f"{sz / 1024:.1f} KB"
        else:
            sz_str = f"{sz / 1024 / 1024:.1f} MB"
        self.cache_label.configure(
            text=f"Cache: {n} page{'s' if n != 1 else ''} · {sz_str}"
        )

    def _import_xml(self):
        paths = filedialog.askopenfilenames(
            title="Import Jahia XML export(s)",
            filetypes=[("Jahia XML", "*.xml"), ("All files", "*.*")],
        )
        if not paths:
            return
        dialog = JahiaImportDialog(self.winfo_toplevel(), list(paths))
        if dialog.result is None:
            return
        base_url = dialog.result["base_url"]
        self.cache_label.configure(text="Importing…")
        threading.Thread(
            target=self._do_import_xml, args=(paths, base_url), daemon=True
        ).start()

    def _do_import_xml(self, paths, base_url):
        total_imported = total_skipped = 0
        for path in paths:
            name = Path(path).name
            def _progress(done, total, n=name):
                self.after(0, lambda d=done, t=total: self.cache_label.configure(
                    text=f"Importing {n}: {d}/{t}…"
                ))
            try:
                imp, skip = import_xml_to_cache(
                    path, _cache, base_url, progress_cb=_progress
                )
                total_imported += imp
                total_skipped  += skip
            except Exception as exc:
                self.after(0, lambda e=str(exc): messagebox.showerror(
                    "Import XML", f"Error:\n{e}"
                ))
        self.after(0, self._update_cache_label)
        self.after(0, self._apply_filter)   # refresh tree if filter is active
        msg = f"Imported {total_imported} page{'s' if total_imported != 1 else ''}"
        if total_skipped:
            msg += f"\n{total_skipped} skipped (no content)"
        self.after(0, lambda: messagebox.showinfo("Import XML", msg))

    def _clear_cache(self):
        n = _cache.entry_count()
        if not messagebox.askyesno(
            "Clear cache",
            f"Delete {n} cached page{'s' if n != 1 else ''} from disk?\nThis cannot be undone.",
        ):
            return
        _cache.clear()
        self._update_cache_label()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.output.get("1.0", "end-1c"))
        self.copy_btn.configure(text="Copied ✓")
        self.after(1500, lambda: self.copy_btn.configure(text="Copy"))

    def _export_pdf(self):
        export_pdf_dialog(self.output.get("1.0", "end-1c"))


# ── App ───────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("url2md")
        self.geometry("920x740")
        self.minsize(700, 560)

        self.keep_images    = tk.BooleanVar(value=False)
        self.internal_links = tk.BooleanVar(value=False)

        # Header
        ctk.CTkLabel(
            self, text="url  →  md",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(20, 4))

        # Options
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkCheckBox(
            opts, text="Keep images",
            variable=self.keep_images,
            font=ctk.CTkFont(size=12),
        ).pack(side="left")

        # Tabs
        self.tabs = ctk.CTkTabview(self, anchor="nw")
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        for name in ("Single URL", "Batch", "Sitemap"):
            self.tabs.add(name)

        self.single_tab  = SingleTab( self.tabs.tab("Single URL"), self.keep_images)
        self.batch_tab   = BatchTab(  self.tabs.tab("Batch"),       self.keep_images, self.internal_links)
        self.sitemap_tab = SitemapTab(self.tabs.tab("Sitemap"),     self.keep_images)

        for tab in (self.single_tab, self.batch_tab, self.sitemap_tab):
            tab.pack(fill="both", expand=True, padx=4, pady=4)


if __name__ == "__main__":
    App().mainloop()
