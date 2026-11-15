"""Reading and writing Android string resources (values*/strings*.xml) as plain text.

Shared by strings_check.py (the checks) and strings_sync.py (moving Korean out of values/, building a locale from a JSON file).

Plain text means the text a user sees: aapt's escapes (\\' \\" \\n \\t \\\\ \\@ \\? \\uXXXX), its quoting ("  kept  ") and its
whitespace folding are undone on read and redone on write, so translators work with ordinary text and never have to think about
apostrophes in French or quotes in German.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ANDROID_NS = "{urn:oasis:names:tc:xliff:document:1.2}"

# The app's languages: the code kept on the phone (core/Languages.kt) -> the values directory with its strings.
LOCALE_DIRS: dict[str, str] = {
    "en": "values",
    "ko": "values-ko",
    "ja": "values-ja",
    "zh-Hans": "values-b+zh+Hans",
    "zh-Hant": "values-b+zh+Hant",
    "es": "values-es",
    "fr": "values-fr",
    "de": "values-de",
    "pt-BR": "values-pt",
    "it": "values-it",
    "ru": "values-ru",
    "id": "values-in",
    "vi": "values-vi",
    "th": "values-th",
    "tr": "values-tr",
    "ar": "values-ar",
    "hi": "values-hi",
    "pl": "values-pl",
    "nl": "values-nl",
    "uk": "values-uk",
}

# CLDR cardinal plural categories each language needs (CLDR 44). "other" is always required.
PLURALS: dict[str, tuple[str, ...]] = {
    "en": ("one", "other"),
    "ko": ("other",),
    "ja": ("other",),
    "zh-Hans": ("other",),
    "zh-Hant": ("other",),
    "es": ("one", "many", "other"),
    "fr": ("one", "many", "other"),
    "de": ("one", "other"),
    "pt-BR": ("one", "many", "other"),
    "it": ("one", "many", "other"),
    "ru": ("one", "few", "many", "other"),
    "id": ("other",),
    "vi": ("other",),
    "th": ("other",),
    "tr": ("one", "other"),
    "ar": ("zero", "one", "two", "few", "many", "other"),
    "hi": ("one", "other"),
    "pl": ("one", "few", "many", "other"),
    "nl": ("one", "other"),
    "uk": ("one", "few", "many", "other"),
}

HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")


@dataclass
class Entry:
    kind: str                      # "string" | "plurals" | "array"
    name: str
    translatable: bool = True
    text: str = ""                 # string
    items: list[str] = field(default_factory=list)          # array
    quantities: dict[str, str] = field(default_factory=dict)  # plurals

    def texts(self) -> list[str]:
        if self.kind == "string":
            return [self.text]
        if self.kind == "array":
            return list(self.items)
        return list(self.quantities.values())

    def to_json(self) -> object:
        if self.kind == "string":
            return self.text
        if self.kind == "array":
            return list(self.items)
        return dict(self.quantities)


# --- aapt text rules ---------------------------------------------------------------------------------------------------------

def unescape(raw: str) -> str:
    """XML-decoded element text -> what the app shows (aapt2's rules: escapes, "quoted" runs, whitespace folding outside quotes)."""
    out: list[str] = []
    quoted = False
    i, n = 0, len(raw)
    pending_space = False
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if pending_space:
                out.append(" ")
                pending_space = False
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2:i + 6] or ""):
                out.append(chr(int(raw[i + 2:i + 6], 16)))
                i += 6
                continue
            else:
                out.append(nxt)   # \' \" \\ \@ \? and anything else: the character itself
            i += 2
            continue
        if c == '"':
            quoted = not quoted
            if pending_space:
                out.append(" ")
                pending_space = False
            i += 1
            continue
        if not quoted and c in " \t\r\n":
            pending_space = bool(out)   # leading whitespace is dropped
            i += 1
            continue
        if pending_space:
            out.append(" ")
            pending_space = False
        out.append(c)
        i += 1
    return "".join(out)


def escape(text: str) -> str:
    """What the app shows -> element text for strings.xml (XML-escaped, aapt-escaped; quoted when spaces at the ends matter)."""
    s = text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'").replace("\n", "\\n").replace("\t", "\\t")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if s[:1] in ("@", "?"):
        s = "\\" + s
    if text != text.strip(" ") or "  " in text:
        s = f'"{s}"'
    return s


# --- reading -----------------------------------------------------------------------------------------------------------------

def _raw_text(el: ET.Element) -> str:
    """The element's content as XML-decoded text; inline markup (<b>, <xliff:g>) is kept as tags."""
    parts = [el.text or ""]
    for child in el:
        tag = child.tag.replace(ANDROID_NS, "xliff:")
        attrs = "".join(f' {k}="{html.escape(v)}"' for k, v in child.attrib.items())
        parts.append(f"<{tag}{attrs}>{_raw_text(child)}</{tag}>")
        parts.append(child.tail or "")
    return "".join(parts)


def read(path: Path) -> list[Entry]:
    root = ET.parse(path).getroot()
    out: list[Entry] = []
    for el in root:
        if not isinstance(el.tag, str):
            continue
        name = el.get("name", "")
        tr = el.get("translatable", "true") != "false"
        if el.tag == "string":
            out.append(Entry("string", name, tr, text=unescape(_raw_text(el))))
        elif el.tag == "plurals":
            q = {i.get("quantity", ""): unescape(_raw_text(i)) for i in el if i.tag == "item"}
            out.append(Entry("plurals", name, tr, quantities=q))
        elif el.tag == "string-array":
            out.append(Entry("array", name, tr, items=[unescape(_raw_text(i)) for i in el if i.tag == "item"]))
    return out


def string_files(res: Path, values_dir: str = "values") -> list[Path]:
    return sorted((res / values_dir).glob("strings*.xml"))


# --- writing -----------------------------------------------------------------------------------------------------------------

def render_locale(template: list[Entry], values: dict[str, object], header: str) -> str:
    """A locale's file: the translatable entries of the default file, in its order, with `values` (name -> text / list / dict)."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>', f"<!-- {header} -->", "<resources>"]
    for e in template:
        if not e.translatable or e.name not in values:
            continue
        v = values[e.name]
        if e.kind == "string":
            lines.append(f'    <string name="{e.name}">{escape(str(v))}</string>')
        elif e.kind == "array":
            lines.append(f'    <string-array name="{e.name}">')
            lines += [f"        <item>{escape(str(x))}</item>" for x in v]  # type: ignore[union-attr]
            lines.append("    </string-array>")
        else:
            lines.append(f'    <plurals name="{e.name}">')
            lines += [f'        <item quantity="{q}">{escape(str(x))}</item>' for q, x in v.items()]  # type: ignore[union-attr]
            lines.append("    </plurals>")
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ENTRY = re.compile(r'(<(string|plurals|string-array)\s+name="([^"]+)"([^>]*)>)(.*?)(</\2>)', re.S)
_ITEM = re.compile(r'(<item(?:\s+quantity="([a-z]+)")?\s*>)(.*?)(</item>)', re.S)


def replace_texts(source: str, values: dict[str, object]) -> str:
    """The default file with the text of each named entry replaced by `values`, keeping its comments, order and layout."""
    out: list[str] = []
    pos = 0
    for m in _COMMENT.finditer(source):
        out.append(_replace_in(source[pos:m.start()], values))
        out.append(m.group(0))
        pos = m.end()
    out.append(_replace_in(source[pos:], values))
    return "".join(out)


def _replace_in(chunk: str, values: dict[str, object]) -> str:
    def entry(m: re.Match[str]) -> str:
        open_tag, kind, name, _attrs, body, close = m.groups()
        if name not in values:
            return m.group(0)
        v = values[name]
        if kind == "string":
            return f"{open_tag}{escape(str(v))}{close}"
        indent = re.search(r"\n([ \t]*)<item", body)
        pad = indent.group(1) if indent else "        "
        end_pad = re.search(r"\n([ \t]*)$", body)
        tail = "\n" + (end_pad.group(1) if end_pad else "    ")
        if kind == "string-array":
            items = "".join(f"\n{pad}<item>{escape(str(x))}</item>" for x in v)  # type: ignore[union-attr]
        else:
            items = "".join(f'\n{pad}<item quantity="{q}">{escape(str(x))}</item>' for q, x in v.items())  # type: ignore[union-attr]
        return f"{open_tag}{items}{tail}{close}"
    return _ENTRY.sub(entry, chunk)


def without_untranslatable(source: str) -> str:
    """A copy of a default file for a locale: entries marked translatable="false" (and their own comment line) dropped."""
    lines = source.split("\n")
    keep: list[str] = []
    for line in lines:
        if 'translatable="false"' in line and re.search(r"<(string|plurals|string-array)\s", line):
            if keep and keep[-1].strip().startswith("<!--") and keep[-1].strip().endswith("-->"):
                keep.pop()
            continue
        keep.append(line)
    return "\n".join(keep)
