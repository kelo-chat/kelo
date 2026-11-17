"""Kelo's own help pages, read before the agent answers a question about Kelo itself ("can you tidy my mail?", "how do schedules work?").

The pages are short English markdown files shipped with the server (server/content/help/<page>.md), one per area. Each starts with a
title line and a one-paragraph summary, then `##` sections. They are searched by section with the same FTS5/BM25 helpers as the
memory pages (recall.py: no embeddings), so the tool answers with the few best passages and the page each came from, and the agent
answers from them. The pages are the product's text, not the user's: they are never fenced and never written by the agent.

The pages are English, so the agent searches in English words (its tool description says so). When nothing matches, the tool does not
guess: it lists every page with its summary and the agent picks one by name (`page`). When the pages do not cover a question the
agent says so.

The index lives in memory and is rebuilt when a page file changes (size and time), so a new page needs no restart.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import Tool
from .recall import best_sections, sections

TOP = 3                  # passages one search returns
PASSAGE_CHARS = 1_400    # one passage as shown
TOTAL_CHARS = 3_600      # all of them together
PAGE_CHARS = 6_000       # one whole page as shown


@dataclass(frozen=True)
class HelpPage:
    name: str            # the file's name without .md: what `page` takes
    title: str
    summary: str
    text: str


def _load(path: Path) -> HelpPage | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    first, _, rest = text.partition("\n")
    title = first.lstrip("#").strip() if first.startswith("#") else path.stem.replace("-", " ").capitalize()
    body = rest if first.startswith("#") else text
    summary = next((p.strip() for p in body.split("\n\n") if p.strip() and not p.lstrip().startswith("#")), "")
    return HelpPage(path.stem, title, " ".join(summary.split()), text)


class HelpDocs:
    """The help pages of one content folder and the read-only `kelo_help` tool over them."""

    def __init__(self, folder: Path | None):
        self.folder = folder
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("CREATE VIRTUAL TABLE sec USING fts5(key UNINDEXED, path UNINDEXED, heading, body, tokenize='unicode61')")
        self.pages: dict[str, HelpPage] = {}
        self._seen: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the index
    def _files(self) -> dict[str, Path]:
        if self.folder is None or not self.folder.is_dir() or self.folder.is_symlink():
            return {}
        return {p.stem: p for p in sorted(self.folder.glob("*.md")) if p.is_file() and not p.is_symlink() and not p.name.startswith(".")}

    def refresh(self) -> None:
        with self._lock:
            files = self._files()
            for name in set(self.pages) - set(files):
                self.db.execute("DELETE FROM sec WHERE path = ?", (name,))
                self.pages.pop(name, None)
                self._seen.pop(name, None)
            for name, p in files.items():
                try:
                    st = p.stat()
                except OSError:
                    continue
                sig = (st.st_mtime_ns, st.st_size)
                if self._seen.get(name) == sig:
                    continue
                self.db.execute("DELETE FROM sec WHERE path = ?", (name,))
                page = _load(p)
                if page is None:
                    continue
                self.pages[name] = page
                self._seen[name] = sig
                for head, body in sections(page.text):
                    label = page.title if not head or head == page.title else f"{page.title} · {head}"   # the title helps a match
                    self.db.execute("INSERT INTO sec(key, path, heading, body) VALUES(?,?,?,?)", (f"{name}#{head}", name, label, body))

    # ------------------------------------------------------------------ reading
    def search(self, query: str, k: int = TOP) -> list[dict[str, Any]]:
        """The `k` passages that best match `query`: [{"page", "heading", "text"}], best first."""
        self.refresh()
        return [{"page": h["path"], "heading": h["heading"], "text": h["text"]} for h in best_sections(self.db, self._lock, query, k)]

    def index(self) -> str:
        self.refresh()
        return "\n".join(f"- {p.name}: {p.title}. {p.summary}" for p in self.pages.values())

    def read(self, name: str) -> str | None:
        self.refresh()
        page = self.pages.get(name.strip().lower().removesuffix(".md").replace(" ", "-").replace("_", "-"))
        if page is None:
            return None
        text = page.text if len(page.text) <= PAGE_CHARS else page.text[:PAGE_CHARS].rstrip() + " …"
        return f"[help page: {page.name}]\n{text}"

    def answer(self, query: str = "", page: str = "") -> str:
        self.refresh()
        if not self.pages:
            return "No help pages are installed on this server, so Kelo's own documentation cannot be read. Say so; do not answer from memory."
        if page.strip():
            text = self.read(page)
            if text:
                return text
            return f"No help page named '{page.strip()}'. Pages:\n{self.index()}"
        hits = self.search(query)
        if not hits:
            return ("No passage matches those words. The pages are English: search again with short English keywords, or read one "
                    "whole with page=<name>. If none of them covers the question, tell the user the help pages do not cover it.\n"
                    f"Pages:\n{self.index()}")
        out, used = [], 0
        for h in hits:
            body = h["text"] if len(h["text"]) <= PASSAGE_CHARS else h["text"][:PASSAGE_CHARS].rstrip() + " …"
            if out and used + len(body) > TOTAL_CHARS:
                break
            where = h["page"] + (f" · {h['heading'].split(' · ', 1)[1]}" if " · " in h["heading"] else "")
            out.append(f"[help page: {where}]\n{body}")
            used += len(body)
        pages = ", ".join(dict.fromkeys(h["page"] for h in hits))
        return ("\n\n".join(out) + f"\n\n(Pages: {pages}. Read one whole with page=<name>. Answer from these passages; "
                "if they do not cover the question, say the help pages do not cover it.)")

    # ------------------------------------------------------------------ the tool
    def tool(self) -> Tool:
        async def kelo_help(args: dict[str, Any]) -> str:
            return self.answer(str(args.get("query") or ""), str(args.get("page") or ""))

        return Tool("kelo_help", "Kelo's own help pages (read-only). Call it first for any question about what Kelo can do or how a Kelo "
                    "feature works, then answer from what it returns. Search in short English keywords (the pages are English), "
                    "whatever language the user writes in; or read a whole page with page.",
                    {"type": "object", "properties": {
                        "query": {"type": "string", "description": "English keywords, e.g. 'schedule repeat', 'forget memory', 'pay approval'"},
                        "page": {"type": "string", "description": "a page name to read whole (listed when a search finds nothing)"}}},
                    kelo_help, read_only=True)
