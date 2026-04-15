#!/opt/homebrew/bin/python3.13
"""url2md — GUI app: paste a URL (or a list), get Markdown."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urldefrag, urlparse
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from url2md import fetch_markdown, fetch_sitemap_urls, is_sitemap_url
from pdf_export import md_to_pdf


def export_pdf_dialog(md_text: str) -> None:
    """Open a save dialog and export md_text to PDF in a background thread."""
    if not md_text.strip():
        return
    path = filedialog.asksaveasfilename(
        defaultextension=".pdf",
        filetypes=[("PDF", "*.pdf")],
        title="Exporter en PDF",
    )
    if not path:
        return
    def _write():
        try:
            md_to_pdf(md_text, path)
            messagebox.showinfo("Export PDF", f"Fichier enregistré :\n{path}")
        except Exception as exc:
            messagebox.showerror("Export PDF", f"Erreur : {exc}")
    threading.Thread(target=_write, daemon=True).start()


def _url_to_anchor(url: str) -> str:
    """Stable slug usable as a Markdown/HTML anchor id."""
    p = urlparse(url)
    slug = (p.netloc + p.path).strip("/").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "page"


def _rewrite_links(md: str, url_to_anchor: dict[str, str]) -> str:
    """Replace [text](url) with [text](#anchor) when url is in the batch."""
    def replace(m):
        text, href = m.group(1), m.group(2).strip()
        base, _ = urldefrag(href)
        anchor = url_to_anchor.get(base) or url_to_anchor.get(base.rstrip("/"))
        if anchor:
            return f"[{text}](#{anchor})"
        return m.group(0)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, md)


# ── colours & sizes ──────────────────────────────────────────────────────────
BG          = "#1e1e2e"
SURFACE     = "#2a2a3e"
ACCENT      = "#7c6af7"
ACCENT_DARK = "#5a4ad1"
TEXT        = "#cdd6f4"
MUTED       = "#6c7086"
SUCCESS     = "#a6e3a1"
ERROR       = "#f38ba8"
PAD         = 16


# ── shared option bar (images checkbox) ──────────────────────────────────────

class OptionsBar(tk.Frame):
    def __init__(self, parent, keep_images_var, **kw):
        super().__init__(parent, bg=BG, padx=PAD, pady=6, **kw)
        self._small = tkfont.Font(family="SF Pro Display", size=11)
        tk.Checkbutton(
            self, text="Conserver les images",
            variable=keep_images_var,
            bg=BG, fg=MUTED, selectcolor=SURFACE,
            activebackground=BG, activeforeground=TEXT,
            font=self._small, bd=0, cursor="hand2",
        ).pack(side="left")


# ── output widget (shared look) ───────────────────────────────────────────────

def make_output(parent):
    frame = tk.Frame(parent, bg=BG)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    mono = tkfont.Font(family="Menlo", size=12)
    txt = tk.Text(
        frame,
        font=mono,
        bg=SURFACE, fg=TEXT, insertbackground=TEXT,
        relief="flat", bd=0,
        highlightthickness=1, highlightbackground=MUTED,
        wrap="word", padx=12, pady=12,
        state="disabled",
    )
    txt.grid(row=0, column=0, sticky="nsew")
    sb = tk.Scrollbar(frame, command=txt.yview, bg=SURFACE, troughcolor=SURFACE)
    sb.grid(row=0, column=1, sticky="ns")
    txt.configure(yscrollcommand=sb.set)
    return frame, txt


def set_output(txt, text, *, muted=False):
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    txt.insert("end", text)
    txt.configure(fg=MUTED if muted else TEXT, state="disabled")


# ── Tab 1 — single URL ───────────────────────────────────────────────────────

class SingleTab(tk.Frame):
    def __init__(self, parent, keep_images_var):
        super().__init__(parent, bg=BG)
        self.keep_images_var = keep_images_var
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._sans  = tkfont.Font(family="SF Pro Display", size=13)
        self._bold  = tkfont.Font(family="SF Pro Display", size=13, weight="bold")
        self._small = tkfont.Font(family="SF Pro Display", size=11)

        self._build()

    def _build(self):
        # URL row
        url_frame = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        url_frame.grid(row=0, column=0, sticky="ew")
        url_frame.columnconfigure(0, weight=1)

        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(
            url_frame, textvariable=self.url_var,
            font=self._sans,
            bg=SURFACE, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=MUTED,
            highlightcolor=ACCENT,
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", ipady=8, padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _: self._start())

        self.btn = tk.Button(
            url_frame, text="Convertir",
            font=self._bold,
            bg=ACCENT, fg="#ffffff", activebackground=ACCENT_DARK,
            activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=16, pady=8,
            command=self._start,
        )
        self.btn.grid(row=0, column=1)

        # output
        out_frame, self.output = make_output(self)
        out_frame.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=(0, 0))

        # status bar
        bar = tk.Frame(self, bg=BG, padx=PAD, pady=8)
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self.status = tk.Label(bar, text="", bg=BG, fg=MUTED, font=self._small)
        self.status.grid(row=0, column=0, sticky="w")

        self.copy_btn = tk.Button(
            bar, text="Copier",
            font=self._small,
            bg=SURFACE, fg=TEXT, activebackground=ACCENT,
            activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
            command=self._copy,
            state="disabled",
        )
        self.copy_btn.grid(row=0, column=1, padx=(0, 6))

        self.pdf_btn = tk.Button(
            bar, text="Exporter PDF…",
            font=self._small,
            bg=SURFACE, fg=TEXT, activebackground=ACCENT,
            activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
            command=self._export_pdf,
            state="disabled",
        )
        self.pdf_btn.grid(row=0, column=2)

    def _start(self):
        url = self.url_var.get().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_var.set(url)

        set_output(self.output, "Conversion en cours…", muted=True)
        self.btn.configure(state="disabled", text="…")
        self.copy_btn.configure(state="disabled")
        self.pdf_btn.configure(state="disabled")
        self.status.configure(text="Chargement…", fg=MUTED)

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
            self.after(0, self._error, f"Sitemap : {exc}")
            return
        if not urls:
            self.after(0, self._error, "Aucune URL trouvée dans le sitemap")
            return

        total = len(urls)
        self.after(0, lambda: self.status.configure(text=f"0 / {total} pages…", fg=MUTED))

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
                    text=f"{d} / {total} pages…", fg=MUTED))

        self.after(0, self._done_sitemap, urls, results)

    def _done(self, md):
        set_output(self.output, md)
        lines, words = md.count("\n") + 1, len(md.split())
        self.status.configure(
            text=f"{lines} lignes · {words} mots · {len(md):,} caractères",
            fg=SUCCESS,
        )
        self.btn.configure(state="normal", text="Convertir")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")

    def _done_sitemap(self, urls, results):
        parts = []
        errors = []
        for url in urls:
            md, err = results[url]
            if err:
                errors.append(url)
                parts.append(f"<!-- {url} -->\n\n> **Erreur** : {err}")
            else:
                parts.append(f"<!-- {url} -->\n\n{md}")

        separator = "\n\n---\n\n<div style=\"page-break-after: always;\"></div>\n\n"
        combined = separator.join(parts)
        set_output(self.output, combined)

        ok = len(urls) - len(errors)
        msg = f"{ok} / {len(urls)} pages converties"
        if errors:
            msg += f" · {len(errors)} erreur(s)"
        self.status.configure(text=msg, fg=SUCCESS if not errors else ERROR)
        self.btn.configure(state="normal", text="Convertir")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")

    def _error(self, msg):
        set_output(self.output, f"Erreur : {msg}")
        self.status.configure(text="Échec", fg=ERROR)
        self.btn.configure(state="normal", text="Convertir")

    def _copy(self):
        text = self.output.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copy_btn.configure(text="Copié ✓", bg=ACCENT)
        self.after(1500, lambda: self.copy_btn.configure(text="Copier", bg=SURFACE))

    def _export_pdf(self):
        export_pdf_dialog(self.output.get("1.0", "end-1c"))


# ── Tab 2 — batch URLs ───────────────────────────────────────────────────────

class BatchTab(tk.Frame):
    def __init__(self, parent, keep_images_var, internal_links_var):
        super().__init__(parent, bg=BG)
        self.keep_images_var = keep_images_var
        self.internal_links_var = internal_links_var
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)  # input pane
        self.rowconfigure(2, weight=2)  # output pane

        self._bold  = tkfont.Font(family="SF Pro Display", size=13, weight="bold")
        self._small = tkfont.Font(family="SF Pro Display", size=11)
        self._mono  = tkfont.Font(family="Menlo", size=12)

        self._build()

    def _build(self):
        # ── input pane ──
        in_frame = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        in_frame.grid(row=0, column=0, sticky="nsew")
        in_frame.columnconfigure(0, weight=1)
        in_frame.rowconfigure(0, weight=1)

        self.url_input = tk.Text(
            in_frame,
            font=self._mono,
            bg=SURFACE, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=MUTED,
            highlightcolor=ACCENT,
            wrap="none", padx=12, pady=12,
            height=6,
        )
        self.url_input.grid(row=0, column=0, sticky="nsew")
        self.url_input.insert("end", "Colle tes URLs ici, une par ligne…")
        self.url_input.configure(fg=MUTED)
        self.url_input.bind("<FocusIn>", self._clear_placeholder)

        sb_in = tk.Scrollbar(in_frame, command=self.url_input.yview,
                             bg=SURFACE, troughcolor=SURFACE)
        sb_in.grid(row=0, column=1, sticky="ns")
        self.url_input.configure(yscrollcommand=sb_in.set)

        # ── action bar ──
        action = tk.Frame(self, bg=BG, padx=PAD, pady=6)
        action.grid(row=1, column=0, sticky="ew")
        action.columnconfigure(0, weight=1)

        self.status = tk.Label(action, text="", bg=BG, fg=MUTED, font=self._small)
        self.status.grid(row=0, column=0, sticky="w")

        tk.Checkbutton(
            action, text="Liens internes",
            variable=self.internal_links_var,
            bg=BG, fg=MUTED, selectcolor=SURFACE,
            activebackground=BG, activeforeground=TEXT,
            font=self._small, bd=0, cursor="hand2",
        ).grid(row=0, column=1, padx=(0, 12))

        self.copy_btn = tk.Button(
            action, text="Copier tout",
            font=self._small,
            bg=SURFACE, fg=TEXT, activebackground=ACCENT,
            activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
            command=self._copy,
            state="disabled",
        )
        self.copy_btn.grid(row=0, column=2, padx=(0, 6))

        self.pdf_btn = tk.Button(
            action, text="Exporter PDF…",
            font=self._small,
            bg=SURFACE, fg=TEXT, activebackground=ACCENT,
            activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
            command=self._export_pdf,
            state="disabled",
        )
        self.pdf_btn.grid(row=0, column=3, padx=(0, 8))

        self.btn = tk.Button(
            action, text="Convertir tout",
            font=self._bold,
            bg=ACCENT, fg="#ffffff", activebackground=ACCENT_DARK,
            activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=16, pady=8,
            command=self._start,
        )
        self.btn.grid(row=0, column=4)

        # ── output pane ──
        out_frame, self.output = make_output(self)
        out_frame.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=(0, PAD))

    def _clear_placeholder(self, _event):
        if self.url_input.cget("fg") == MUTED:
            self.url_input.delete("1.0", "end")
            self.url_input.configure(fg=TEXT)

    def _get_urls(self):
        raw = self.url_input.get("1.0", "end-1c").strip()
        urls = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line == "Colle tes URLs ici, une par ligne…":
                continue
            if not line.startswith(("http://", "https://")):
                line = "https://" + line
            urls.append(line)
        return urls

    def _start(self):
        raw_urls = self._get_urls()
        if not raw_urls:
            return

        set_output(self.output, "", muted=True)
        self.btn.configure(state="disabled", text="…")
        self.copy_btn.configure(state="disabled")
        self.status.configure(text="Préparation…", fg=MUTED)

        keep   = self.keep_images_var.get()
        intern = self.internal_links_var.get()
        threading.Thread(target=self._expand_and_fetch_all, args=(raw_urls, keep, intern), daemon=True).start()

    def _expand_and_fetch_all(self, raw_urls, keep_images, internal_links):
        # Expand sitemap URLs inline
        urls = []
        for u in raw_urls:
            if is_sitemap_url(u):
                try:
                    urls.extend(fetch_sitemap_urls(u))
                except Exception:
                    urls.append(u)  # fallback: treat as regular URL
            else:
                urls.append(u)

        if not urls:
            self.after(0, lambda: self.status.configure(text="Aucune URL", fg=ERROR))
            self.after(0, lambda: self.btn.configure(state="normal", text="Convertir tout"))
            return

        total = len(urls)
        self.after(0, lambda: self.status.configure(text=f"0 / {total} pages…", fg=MUTED))

        results = {}
        lock = threading.Lock()
        done_count = [0]

        def fetch_one(url):
            try:
                md = fetch_markdown(url, keep_images=keep_images)
                return url, md, None
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
                self.after(0, self._update_progress, done, total)

        self.after(0, self._assemble, urls, results, internal_links)

    def _update_progress(self, done, total):
        self.status.configure(text=f"{done} / {total} pages…", fg=MUTED)

    def _assemble(self, urls, results, internal_links):
        url_to_anchor = {url: _url_to_anchor(url) for url in urls}

        parts = []
        errors = []
        for url in urls:
            md, err = results[url]
            if err:
                errors.append(url)
                parts.append(f"<!-- {url} -->\n\n> **Erreur** : {err}")
            else:
                if internal_links:
                    md = _rewrite_links(md, url_to_anchor)
                anchor = url_to_anchor[url]
                parts.append(f'<a id="{anchor}"></a>\n\n<!-- {url} -->\n\n{md}')

        separator = '\n\n---\n\n<div style="page-break-after: always;"></div>\n\n'
        combined = separator.join(parts)
        set_output(self.output, combined)

        ok = len(urls) - len(errors)
        msg = f"{ok} / {len(urls)} pages converties"
        if errors:
            msg += f" · {len(errors)} erreur(s)"
        self.status.configure(text=msg, fg=SUCCESS if not errors else ERROR)
        self.btn.configure(state="normal", text="Convertir tout")
        self.copy_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")

    def _copy(self):
        text = self.output.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copy_btn.configure(text="Copié ✓", bg=ACCENT)
        self.after(1500, lambda: self.copy_btn.configure(text="Copier tout", bg=SURFACE))

    def _export_pdf(self):
        export_pdf_dialog(self.output.get("1.0", "end-1c"))


# ── Main window ───────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("url2md")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(640, 520)

        bold = tkfont.Font(family="SF Pro Display", size=13, weight="bold")

        # shared options
        self.keep_images    = tk.BooleanVar(value=False)
        self.internal_links = tk.BooleanVar(value=False)

        # title
        tk.Label(self, text="url  →  md",
                 bg=BG, fg=ACCENT, font=bold, pady=PAD
                 ).pack(fill="x")

        # options bar (shared)
        OptionsBar(self, self.keep_images).pack(fill="x")

        # notebook
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("url2md.TNotebook",
                        background=BG, borderwidth=0, tabmargins=0)
        style.configure("url2md.TNotebook.Tab",
                        background=SURFACE, foreground=MUTED,
                        font=("SF Pro Display", 12),
                        padding=[14, 6])
        style.map("url2md.TNotebook.Tab",
                  background=[("selected", BG)],
                  foreground=[("selected", TEXT)])

        nb = ttk.Notebook(self, style="url2md.TNotebook")
        nb.pack(fill="both", expand=True)

        self.single_tab = SingleTab(nb, self.keep_images)
        self.batch_tab  = BatchTab(nb, self.keep_images, self.internal_links)

        nb.add(self.single_tab, text="  URL unique  ")
        nb.add(self.batch_tab,  text="  Lot d'URLs  ")


if __name__ == "__main__":
    app = App()
    app.geometry("860x680")
    app.mainloop()
