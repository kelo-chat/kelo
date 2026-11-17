"""The designed (HTML) version of an outgoing mail: the sanitizer and the plain-text version of it.

`clean(html)` rebuilds the markup from an allow-list (html.parser, no regular expressions over tags): only the tags in ALLOWED_TAGS
survive, each with only its ALLOWED_ATTRS; text is escaped again; `<script>`, `<style>`, `<iframe>`, `<object>`, `<embed>`, forms,
inputs and anything unknown lose the tag (and, for the ones that carry code or content, their children too). Event handler
attributes (on*) never match an allow-list, `href` takes only http, https and mailto, and inline `style` declarations are kept only
when their value has no url(), expression, javascript, behaviour or binding. Remote images are not kept: an `<img>` becomes a link
to its address, so mail is never fetched while it is sent (and no tracking pixel goes out).

`to_text(html)` is the plain-text version a message needs next to the HTML (its body when the caller gave none).

Used by mail.py (IMAP/SMTP and Gmail), microsoft_mail.py (Graph) and docs/mail-html.md describes the rules.
"""

from __future__ import annotations

import re
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


def clean(html: str) -> str:
    """The sanitized HTML of an outgoing mail, wrapped as one document ("" when nothing is left to send)."""
    parser = _Cleaner()
    parser.feed((html or "")[:MAX_CHARS])
    parser.close()
    body = "".join(parser.out).strip()
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


__all__ = ["MAX_CHARS", "clean", "to_text"]
