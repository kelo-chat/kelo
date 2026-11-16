"""Markdown (what the model writes) -> a messenger's markup, and splitting long text. Pure functions."""

from __future__ import annotations

import re
from html import escape

TELEGRAM_HARD_LIMIT = 4096


def _units(text: str) -> list[str]:
    """Paragraphs (split on blank lines); a ``` fence is one unit, blank lines inside it included."""
    lines, out, cur = text.split("\n"), [], []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            if cur:
                out.append("\n".join(cur))
                cur = []
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("```"):
                j += 1
            out.append("\n".join(lines[i:j + 1]))
            i = j + 1
            continue
        if ln.strip() == "":
            if cur:
                out.append("\n".join(cur))
                cur = []
        else:
            cur.append(ln)
        i += 1
    if cur:
        out.append("\n".join(cur))
    return out


def chunks(text: str, limit: int) -> list[str]:
    """Pieces of at most `limit` characters: split on blank lines, then lines, then a hard cut. A cut inside a ``` fence closes
    it and opens it again in the next piece."""
    text = text.strip("\n")
    if len(text) <= limit:
        return [text] if text else []
    eff = limit - 12 if "```" in text else limit      # room for the closing and reopening fence lines
    toks: list[tuple[str, str]] = []                      # (separator before, text)
    for u in _units(text):
        if len(u) <= eff:
            toks.append(("\n\n", u))
            continue
        first = True
        for ln in u.split("\n"):
            while len(ln) > eff:
                toks.append(("\n\n" if first else "\n", ln[:eff]))
                first, ln = False, ln[eff:]
            toks.append(("\n\n" if first else "\n", ln))
            first = False
    raw: list[str] = []
    cur = ""
    for sep, tk in toks:
        if cur and len(cur) + len(sep) + len(tk) <= eff:
            cur += sep + tk
        else:
            if cur:
                raw.append(cur)
            cur = tk
    if cur:
        raw.append(cur)
    out, lang = [], None                                   # lang: None = outside a fence, else the opening line
    for piece in raw:
        if lang is not None:
            piece = lang + "\n" + piece
        state = None
        for ln in piece.split("\n"):
            if ln.lstrip().startswith("```"):
                state = None if state is not None else ln.strip()
        if state is not None:
            piece += "\n```"
            lang = state
        else:
            lang = None
        out.append(piece)
    return out


_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITAL_STAR = re.compile(r"(?<![\*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\*\w])")
_ITAL_UND = re.compile(r"(?<![A-Za-z0-9_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![A-Za-z0-9_])")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^(\s*)[-*]\s+(.*)$")


def _inline(raw: str) -> str:
    s = escape(raw, quote=False)
    keep: list[str] = []

    def hold(html: str) -> str:
        keep.append(html)
        return f"\x00{len(keep) - 1}\x00"
    s = _CODE.sub(lambda m: hold(f"<code>{m.group(1)}</code>"), s)
    s = _LINK.sub(lambda m: hold('<a href="' + m.group(2).replace('"', "&quot;") + '">') + m.group(1) + "</a>", s)
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _ITAL_STAR.sub(r"<i>\1</i>", s)
    s = _ITAL_UND.sub(r"<i>\1</i>", s)
    return re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], s)


def telegram_html(md: str) -> str:
    """Telegram's HTML subset (b, i, code, pre, a). Everything is escaped first; what does not match stays plain text."""
    out: list[str] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("```"):
                j += 1
            out.append("<pre>" + escape("\n".join(lines[i + 1:j]), quote=False) + "</pre>")
            i = j + 1
            continue
        if ln.lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.append("<pre>" + escape("\n".join(lines[i:j]), quote=False) + "</pre>")
            i = j
            continue
        h = _HEAD.match(ln)
        m = _ITEM.match(ln)
        if h:
            out.append(f"<b>{_inline(h.group(1))}</b>")
        elif m:
            out.append(f"{m.group(1)}• {_inline(m.group(2))}")
        else:
            out.append(_inline(ln))
        i += 1
    return "\n".join(out)


def telegram_parts(md: str, limit: int = 4000) -> list[tuple[str, str]]:
    """[(html, plain markdown)] pieces that each fit Telegram's message size after the markup (the plain text is for the retry
    when Telegram refuses the HTML)."""
    lim = limit
    while True:
        plain = chunks(md, lim)
        html = [telegram_html(p) for p in plain]
        if all(len(h) <= TELEGRAM_HARD_LIMIT for h in html) or lim < 200:
            return list(zip(html, plain))
        lim = int(lim * 0.7)


_SLACK_ESC = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def slack_mrkdwn(md: str) -> str:
    """Markdown (what the model writes) -> Slack's mrkdwn: **bold** -> *bold*, [text](url) -> <url|text>, "# Title" -> *Title*,
    ~~x~~ -> ~x~; &, < and > are escaped; ``` fences and `code` stay as they are (Slack reads them the same way)."""
    out: list[str] = []
    fence = False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        parts = re.split(r"(`[^`]+`)", line)
        for i, p in enumerate(parts):
            if i % 2:
                continue
            for a, b in _SLACK_ESC:
                p = p.replace(a, b)
            p = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f"<{m.group(2)}|{m.group(1)}>", p)
            p = re.sub(r"\*\*(.+?)\*\*", r"*\1*", p)
            p = re.sub(r"~~(.+?)~~", r"~\1~", p)
            parts[i] = p
        line = "".join(parts)
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        out.append(f"*{m.group(1).strip()}*" if m else line)
    return "\n".join(out)

