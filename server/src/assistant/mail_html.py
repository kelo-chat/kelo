"""The designed (HTML) version of an outgoing mail: the sanitizer and the plain-text version of it.

`clean(html)` rebuilds the markup from an allow-list (html.parser, no regular expressions over tags): only the tags in ALLOWED_TAGS
survive, each with only its ALLOWED_ATTRS; text is escaped again; `<script>`, `<style>`, `<iframe>`, `<object>`, `<embed>`, forms,
inputs and anything unknown lose the tag (and, for the ones that carry code or content, their children too). Event handler
attributes (on*) never match an allow-list, `href` takes only http, https and mailto, and inline `style` declarations are kept only
when their value has no url(), expression, javascript, behaviour or binding. Remote images are not kept: an `<img>` becomes a link
to its address, so mail is never fetched while it is sent (and no tracking pixel goes out).

`to_text(html)` is the plain-text version a message needs next to the HTML (its body when the caller gave none).

`preview(html)` is what the owner sees before a designed mail is allowed: the body `clean` sends (byte for byte), in a document with
a strict Content-Security-Policy and a fixed head (nothing it contains can load or run), plus the same content as plain blocks for
an app that has no web view. The apps show it; the server never sends it anywhere.

Used by mail.py (IMAP/SMTP and Gmail), microsoft_mail.py (Graph), api/approvals.py (the preview route) and docs/mail-html.md
describes the rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_CHARS = 50_000   # the same cap as a plain body: the input is cut here before it is read

BLOCK = frozenset({"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section",
                   "article", "header", "footer", "main", "hr", "caption"})
VOID = frozenset({"br", "hr", "col"})
NO_END_TAG = frozenset({"embed", "frame"})   # dropped with no content: they never close, so they must not start a skip
ALLOWED_TAGS = frozenset({"a", "b", "strong", "i", "em", "u", "s", "small", "span", "font", "center", "p", "div", "br", "hr", "h1", "h2",
                          "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "table", "caption", "thead", "tbody", "tfoot", "tr",
                          "td", "th", "section", "article", "header", "footer", "main"})
# content that is code or belongs to another document: the tag and everything inside it go
DROP_WITH_CONTENT = frozenset({"script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "noscript", "template",
                               "textarea", "select", "option", "svg", "math", "head", "title", "audio", "video", "canvas", "xml"})
ALLOWED_ATTRS = frozenset({"style", "align", "valign", "width", "height", "bgcolor", "border", "cellpadding", "cellspacing", "colspan",
                           "rowspan", "color", "size", "face", "title", "dir", "lang"})
PLAIN_VALUE = re.compile(r"^[A-Za-z0-9#%.\- ]{0,80}$")
SCHEMES = frozenset({"http", "https", "mailto"})
BAD_STYLE = re.compile(r"[\\<>{}@;]|url\s*\(|expression|javascript|behaviou?r|binding|image\s*\(|import", re.IGNORECASE)
PROPERTY = re.compile(r"^[A-Za-z-]{1,40}$")


def _safe_href(value: str | None) -> str | None:
    """The address when it is http, https or mailto (control characters and spaces removed first), else None."""
    if not value:
        return None
    raw = re.sub(r"[\x00-\x20\x7f]", "", value)
    scheme = urlsplit(raw).scheme.lower()
    return raw if scheme in SCHEMES and raw.lower().startswith(scheme + ":") else None


def _safe_style(value: str) -> str:
    """Only `property: value` declarations whose text has nothing that loads or runs anything."""
    keep: list[str] = []
    for decl in value.split(";"):
        prop, sep, val = decl.partition(":")
        prop, val = prop.strip(), val.strip()
        if not sep or not PROPERTY.match(prop) or not val or BAD_STYLE.search(val):
            continue
        keep.append(f"{prop}: {val}")
    return "; ".join(keep)


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []      # allowed tags still open, innermost last
        self.skip: str = ""            # the dropped-with-content tag being skipped
        self.depth = 0
        self.pictures = 0              # <img> with a safe address: sent as a link instead

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag == self.skip:
                self.depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            if tag not in NO_END_TAG:
                self.skip, self.depth = tag, 1
            return
        if tag == "img":
            # never fetched: the address becomes a link, the alt text its words
            src = _safe_href(dict(attrs).get("src") or "")
            if src:
                alt = (dict(attrs).get("alt") or "").strip() or src
                self.out.append(f'<a href="{escape(src, quote=True)}">{escape(alt)}</a>')
                self.pictures += 1
            return
        if tag == "a":
            href = _safe_href(dict(attrs).get("href"))
            kept = [f' href="{escape(href, quote=True)}"'] if href else []
            self._open(tag, kept + self._attrs(attrs))
            return
        if tag not in ALLOWED_TAGS:
            return   # form, input, button, meta, unknown tags: the tag goes, the text inside stays
        self._open(tag, self._attrs(attrs))

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> list[str]:
        out: list[str] = []
        for name, value in attrs:
            if value is None or name not in ALLOWED_ATTRS:
                continue
            if name == "style":
                clean = _safe_style(value)
                if clean:
                    out.append(f' style="{escape(clean, quote=True)}"')
            elif PLAIN_VALUE.match(value):
                out.append(f' {name}="{escape(value.strip(), quote=True)}"')
        return out

    def _open(self, tag: str, attrs: list[str]) -> None:
        self.out.append(f"<{tag}{''.join(attrs)}>")
        if tag not in VOID:
            self.open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip:
                self.depth -= 1
                if self.depth == 0:
                    self.skip = ""
            return
        if tag in VOID or tag not in self.open:
            return
        while self.open:   # close what the writer left open, up to this tag
            top = self.open.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def close(self) -> None:
        super().close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")


def _sanitize(html: str) -> _Cleaner:
    parser = _Cleaner()
    parser.feed((html or "")[:MAX_CHARS])
    parser.close()
    return parser


def clean(html: str) -> str:
    """The sanitized HTML of an outgoing mail, wrapped as one document ("" when nothing is left to send)."""
    body = "".join(_sanitize(html).out).strip()
    return f"<html><body>{body}</body></html>" if body else ""


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = ""
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag == self.skip:
                self.depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.skip, self.depth = tag, 1
        elif tag in BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip:
                self.depth -= 1
                if self.depth == 0:
                    self.skip = ""
            return
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def to_text(html: str) -> str:
    """The words of an HTML mail as plain text: one line per block, the code and the hidden parts left out."""
    parser = _Text()
    parser.feed((html or "")[:MAX_CHARS])
    parser.close()
    lines = [re.sub(r"[ \r\f\v\u00a0]+", " ", line).strip(" \t") for line in "".join(parser.parts).split("\n")]
    return "\n".join(line for line in lines if line.strip()).strip()


# ---- the preview: what the owner sees before a designed mail goes out (api/approvals.py serves it) -----------------------------

PREVIEW_MAX_BYTES = 256 * 1024   # the preview document, UTF-8; 50,000 input characters fit (Korean is 3 bytes a character)
PREVIEW_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; media-src 'none'; "
               "frame-src 'none'; child-src 'none'; object-src 'none'; script-src 'none'; connect-src 'none'; "
               "form-action 'none'; base-uri 'none'")
PREVIEW_HEAD = ('<head><meta charset="utf-8">'
                f'<meta http-equiv="Content-Security-Policy" content="{PREVIEW_CSP}">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<meta name="referrer" content="no-referrer">'
                '<style>html,body{margin:0;padding:0;background:#fff;color:#111}body{padding:12px;'
                'font-family:-apple-system,system-ui,Roboto,"Segoe UI",sans-serif;font-size:15px;line-height:1.45;'
                'overflow-wrap:anywhere;word-break:keep-all}table{max-width:100%!important;border-collapse:collapse}'
                'td,th{overflow-wrap:anywhere}a{color:#0b57d0;text-decoration:underline}</style></head>')
BLOCK_MAX = 600   # blocks in one preview; beyond that the blocks stop (cut) but the document is whole
SPAN_MAX = 300    # text pieces in one block (or one table cell)
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
PARAGRAPHS = frozenset({"p", "div", "section", "article", "header", "footer", "main", "center", "caption"})
LINE_ENDS = HEADINGS | PARAGRAPHS | frozenset({"li", "blockquote", "table", "ul", "ol"})
FORMAT_KEYS = ("b", "i", "u", "s", "color", "href")
NAMED_COLORS = {"black": "#000000", "silver": "#c0c0c0", "gray": "#808080", "grey": "#808080", "white": "#ffffff",
                "maroon": "#800000", "red": "#ff0000", "purple": "#800080", "fuchsia": "#ff00ff", "green": "#008000",
                "lime": "#00ff00", "olive": "#808000", "yellow": "#ffff00", "navy": "#000080", "blue": "#0000ff",
                "teal": "#008080", "aqua": "#00ffff"}


@dataclass(frozen=True)
class Preview:
    html: str          # the preview document: PREVIEW_HEAD + exactly the body clean() sends
    blocks: list[dict] # the same content as plain blocks (the desktop app draws these)
    cut: bool          # blocks stopped at the cap (the html is never cut)
    pictures: int      # <img> with an address in the input: sent (and shown) as links
    links: int         # links kept


def _color(value: str | None) -> str:
    """A #rgb or #rrggbb value, or one of the 16 basic colour names, as #rrggbb; anything else ""."""
    v = (value or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", v):
        return v
    if re.fullmatch(r"#[0-9a-f]{3}", v):
        return "#" + "".join(c * 2 for c in v[1:])
    return NAMED_COLORS.get(v, "")


def _style(value: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in (value or "").split(";"):
        prop, sep, val = decl.partition(":")
        if sep:
            out[prop.strip().lower()] = val.strip().lower()
    return out


def _align(value: str | None) -> str:
    return {"left": "start", "right": "end", "center": "center", "justify": "start"}.get((value or "").strip().lower(), "")


def _format(tag: str, a: dict[str, str | None], st: dict[str, str]) -> dict[str, object]:
    """What one tag sets for the words inside it (bold, italic, underline, strike, colour, link)."""
    what: dict[str, object] = {}
    if tag in ("b", "strong", "th") or tag in HEADINGS:
        what["b"] = True
    if tag in ("i", "em"):
        what["i"] = True
    if tag == "u":
        what["u"] = True
    if tag == "s":
        what["s"] = True
    if tag == "a" and a.get("href"):
        what["href"] = str(a["href"])
    color = _color(st.get("color")) or (_color(str(a.get("color") or "")) if tag == "font" else "")
    if color:
        what["color"] = color
    weight = st.get("font-weight", "")
    if weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600):
        what["b"] = True
    if st.get("font-style") == "italic":
        what["i"] = True
    deco = st.get("text-decoration", "")
    if "underline" in deco:
        what["u"] = True
    if "line-through" in deco:
        what["s"] = True
    return what


def _trim(spans: list[dict]) -> list[dict]:
    out = [dict(s) for s in spans if s["text"]]
    if out:
        out[0]["text"] = out[0]["text"].lstrip(" \n")
        out[-1]["text"] = out[-1]["text"].rstrip(" \n")
    return [s for s in out if s["text"]]


def _has_words(spans: list[dict]) -> bool:
    return any(s["text"].strip() for s in spans)


class _Blocks(HTMLParser):
    """The cleaned body as blocks: paragraphs, headings, list items, quotes, rules and table rows. A table row's cells hold their words;
    a table inside a cell is flattened into that cell. Reads clean()'s output only (never the raw input)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.cut = False
        self.stopped = False
        self.links = 0
        self.fmt: list[tuple[str, dict]] = []   # open tags and what each sets, innermost last
        self.lists: list[list] = []             # open lists: [tag, last ordinal]
        self.rows: list[dict] = []              # open rows: {"cells": [...], "bg": ..., "align": ...}
        self.cells: list[dict] = []             # open cells, innermost last
        self.cur: dict | None = None            # the block being filled

    # -- the block being filled -------------------------------------------------------------------------------------------------

    def _new(self, kind: str, *, align: str = "", bg: str = "", depth: int = 0, level: int = 0, ordinal: int = 0) -> dict:
        return {"kind": kind, "level": level, "ordinal": ordinal, "depth": depth, "align": align or "start", "bg": bg, "spans": []}

    def _emit(self, block: dict) -> None:
        if len(self.blocks) >= BLOCK_MAX:
            self.cut = self.stopped = True
            return
        self.blocks.append(block)

    def _flush(self) -> None:
        if self.cur is None:
            return
        block, self.cur = self.cur, None
        spans = _trim(block["spans"])
        if spans:
            block["spans"] = spans
            self._emit(block)

    def _begin(self, kind: str, **kw: object) -> None:
        """A block starts. Inside a table cell it is a line break; a list item keeps its paragraph (`<li><p>`)."""
        if self.cells:
            self._newline()
            return
        if self.cur is not None and self.cur["kind"] == "item" and kind == "paragraph" and not _has_words(self.cur["spans"]):
            return
        self._flush()
        self.cur = self._new(kind, **kw)   # type: ignore[arg-type]

    def _separate(self) -> None:
        if self.cells:
            self._newline()
        else:
            self._flush()

    def _sink(self, create: bool = False) -> list[dict] | None:
        if self.cells:
            return self.cells[-1]["spans"]
        if self.cur is None and create:
            self.cur = self._new("paragraph")
        return self.cur["spans"] if self.cur is not None else None

    def _newline(self) -> None:
        sink = self._sink()
        if sink and not sink[-1]["text"].endswith("\n"):
            sink[-1]["text"] = sink[-1]["text"].rstrip(" ") + "\n"

    def _current(self) -> dict:
        cur: dict = {"b": False, "i": False, "u": False, "s": False, "color": "", "href": ""}
        for _tag, what in self.fmt:
            for key in ("b", "i", "u", "s"):
                cur[key] = cur[key] or bool(what.get(key))
            cur["color"] = str(what.get("color") or cur["color"])
            cur["href"] = str(what.get("href") or cur["href"])
        if cur["href"]:
            cur["u"] = True
        return cur

    def _text(self, data: str) -> None:
        if self.stopped or not data:
            return
        text = re.sub(r"[ \t\r\n\f\v]+", " ", data)
        sink = self._sink(create=True)
        if sink is None or not sink or sink[-1]["text"].endswith((" ", "\n")):
            text = text.lstrip(" ")
        if not text or sink is None:
            return
        fmt = self._current()
        last = sink[-1] if sink else None
        if last is not None and all(last[k] == fmt[k] for k in FORMAT_KEYS):
            last["text"] += text
            return
        if len(sink) >= SPAN_MAX:
            self.cut = self.stopped = True
            return
        sink.append({"text": text, **fmt})

    # -- table rows ---------------------------------------------------------------------------------------------------------------

    def _end_row(self) -> None:
        row = self.rows.pop()
        if self.cells:   # a table inside an open cell: its words go into that cell, row after row
            outer = self.cells[-1]["spans"]
            for i, cell in enumerate(row["cells"]):
                if i and outer:
                    outer.append({"text": "\t", "b": False, "i": False, "u": False, "s": False, "color": "", "href": ""})
                outer.extend(cell["spans"])
            self._newline()
        elif row["cells"]:
            self._emit({"kind": "row", "level": 0, "ordinal": 0, "depth": 0, "align": row["align"] or "start", "bg": row["bg"],
                        "cells": row["cells"]})

    # -- the tags ---------------------------------------------------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.stopped:
            return
        a = dict(attrs)
        st = _style(a.get("style") or "")
        if tag not in VOID:
            self.fmt.append((tag, _format(tag, a, st)))
        align = _align(a.get("align")) or _align(st.get("text-align")) or ("center" if tag == "center" else "")
        bg = _color(st.get("background-color")) or _color(a.get("bgcolor")) or _color(st.get("background"))
        if tag == "br":
            self._newline()
        elif tag == "a":
            if a.get("href"):
                self.links += 1
        elif tag == "hr":
            if self.cells:
                self._newline()
            else:
                self._flush()
                self._emit({"kind": "rule", "level": 0, "ordinal": 0, "depth": 0, "align": "start", "bg": ""})
        elif tag in HEADINGS:
            if self.cells:
                self._newline()
            else:
                self._begin("heading", level=int(tag[1]), align=align, bg=bg)
        elif tag in PARAGRAPHS:
            self._begin("paragraph", align=align, bg=bg)
        elif tag == "blockquote":
            self._begin("quote", align=align, bg=bg)
        elif tag in ("ul", "ol"):
            self._separate()
            self.lists.append([tag, 0])
        elif tag == "li":
            list_tag, last = self.lists[-1] if self.lists else ("ul", 0)
            ordinal = 0
            if list_tag == "ol":
                ordinal = last + 1
                if self.lists:
                    self.lists[-1][1] = ordinal
            depth = max(len(self.lists) - 1, 0)
            if self.cells:
                self._newline()
                self._text(f"{ordinal}. " if ordinal else "• ")
            else:
                self._begin("item", align=align, bg=bg, depth=depth, ordinal=ordinal)
        elif tag == "table":
            self._separate()
        elif tag == "tr":
            self._separate()
            self.rows.append({"cells": [], "bg": bg, "align": align})
        elif tag in ("td", "th"):
            if not self.rows:
                self.rows.append({"cells": [], "bg": "", "align": ""})
            self.cells.append({"spans": [], "bg": bg, "align": align or "start"})

    def handle_endtag(self, tag: str) -> None:
        if self.stopped or tag in VOID:
            return
        for i in range(len(self.fmt) - 1, -1, -1):   # the innermost open tag of this name
            if self.fmt[i][0] == tag:
                del self.fmt[i]
                break
        if tag in ("td", "th"):
            if self.cells:
                cell = self.cells.pop()
                cell["spans"] = _trim(cell["spans"])
                self.rows[-1]["cells"].append(cell)
        elif tag == "tr":
            if self.rows:
                self._end_row()
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._separate()
        elif tag in LINE_ENDS:
            self._separate()

    def handle_data(self, data: str) -> None:
        self._text(data)

    def close(self) -> None:
        super().close()
        self._separate()
        while self.rows:
            self._end_row()


def preview(html: str) -> Preview | None:
    """What the owner sees before a designed mail is allowed: the sanitized HTML that will be sent, in a document that can load and
    run nothing (CSP), plus its blocks. None when nothing survives clean() (the send would be refused anyway)."""
    parser = _sanitize(html)
    body = "".join(parser.out).strip()
    if not body:
        return None
    walk = _Blocks()
    walk.feed(body)
    walk.close()
    doc = f"<!doctype html><html>{PREVIEW_HEAD}<body>{body}</body></html>"
    return Preview(html=doc, blocks=walk.blocks, cut=walk.cut, pictures=parser.pictures, links=walk.links)


__all__ = ["BLOCK_MAX", "MAX_CHARS", "PREVIEW_CSP", "PREVIEW_HEAD", "PREVIEW_MAX_BYTES", "Preview", "clean", "preview", "to_text"]
