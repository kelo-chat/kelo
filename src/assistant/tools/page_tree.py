"""Reading a page as Playwright's accessibility tree with refs (`page.aria_snapshot(mode="ai")`), pure functions, no I/O.

A reading looks like this (one element per line, children indented two spaces):

    - heading "Shop" [level=1] [ref=e2]
    - textbox "Search" [ref=e4]
    - button "Go" [ref=e5]
    - link "Cart (2)" [ref=e6] [cursor=pointer]:
      - /url: "#x"
    - generic [ref=e7] [cursor=pointer]: Clickable div
    - iframe [ref=e11]:
      - button "Inner btn" [ref=f1e3]

`page.locator("aria-ref=e5")` is that element (refs of frames inside the page look like f1e3 and resolve from the page too), so the
agent clicks and types by ref instead of writing selectors. With `boxes=True` every line carries `[box=x,y,w,h]`: CSS pixels in the
viewport of its own frame (an element inside a frame is relative to that frame's viewport).
"""

from __future__ import annotations

import re

REF = re.compile(r"(?:f\d+)?e\d+")
_REF_ARG = re.compile(r"^(?:\[\s*)?(?:aria-ref|ref)?\s*=?\s*((?:f\d+)?e\d+)\s*\]?$")
_LINE = re.compile(r'^(?P<indent>\s*)- (?P<role>[A-Za-z][\w-]*)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<rest>.*)$')
_REF_IN = re.compile(r"\[ref=((?:f\d+)?e\d+)\]")
_BOX_IN = re.compile(r" ?\[box=(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)\]")
_INLINE = re.compile(r"\]: (.+)$")
_TEXT_LINE = re.compile(r'^\s*- (?:text|[A-Za-z][\w-]*(?: "(?:[^"\\]|\\.)*")?(?: \[[^\]]*\])*): (.+)$')

INTERACTIVE_ROLES = frozenset({"button", "link", "textbox", "searchbox", "combobox", "listbox", "option", "checkbox", "radio",
                               "switch", "slider", "spinbutton", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem",
                               "gridcell"})
WORDS_MAX = 80    # characters of an element's own words kept for judging a step by ref


def ref_of(selector: str) -> str | None:
    """The ref a selector names: "e12", "f1e3", "[ref=e12]", "ref=e12" and "aria-ref=e12" give "e12" / "f1e3"; anything else None."""
    m = _REF_ARG.match((selector or "").strip())
    return m.group(1) if m else None


def ref_selector(selector: str) -> str:
    """What Playwright gets: `aria-ref=<ref>` for a ref, else the selector unchanged."""
    ref = ref_of(selector)
    return f"aria-ref={ref}" if ref else selector


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _role(line: str) -> str:
    m = _LINE.match(line)
    return m.group("role") if m else ""


def interactive(line: str) -> bool:
    """A line for something that can be clicked or typed into: an interactive role, or the pointer cursor over it."""
    return _role(line) in INTERACTIVE_ROLES or "[cursor=pointer]" in line


def controls_only(tree: str) -> str:
    """Only what can be clicked or typed into, plus the headings (where on the page it is) and the address line (`- /url:`) under a
    kept link. Original indentation and order; everything else dropped."""
    out: list[str] = []
    link_at: int | None = None   # indentation of the kept link whose /url line may follow
    for line in tree.splitlines():
        if not line.strip():
            continue
        ind = _indent(line)
        if line.strip().startswith("- /url:"):
            if link_at is not None and ind > link_at:
                out.append(line)
            continue
        if link_at is not None and ind <= link_at:
            link_at = None
        role = _role(line)
        if role == "heading" or interactive(line):
            out.append(line)
            if role == "link":
                link_at = ind
    return "\n".join(out)


def _unescape(s: str) -> str:
    return re.sub(r'\\(.)', r"\1", s)


def ref_words(tree: str) -> dict[str, str]:
    """ref -> `role "name"` (e.g. `button "Pay now"`) for every line with a ref. An element without a name of its own is named by its
    inline text, else by the text of the lines under it (a button holding an icon and a word), cut at WORDS_MAX."""
    lines = tree.splitlines()
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        r = _REF_IN.search(line)
        if not m or not r:
            continue
        name = _unescape(m.group("name") or "")
        if not name and (inl := _INLINE.search(m.group("rest") or "")):
            name = inl.group(1).strip()
        if not name:
            ind, parts = _indent(line), []
            for below in lines[i + 1:]:
                if _indent(below) <= ind:
                    break
                if below.strip().startswith("- /url:"):
                    continue
                bm = _LINE.match(below)
                if bm and bm.group("name"):
                    parts.append(_unescape(bm.group("name")))
                elif tm := _TEXT_LINE.match(below):
                    parts.append(tm.group(1).strip())
                if sum(len(p) for p in parts) > WORDS_MAX:
                    break
            name = " ".join(parts)
        name = " ".join(_BOX_IN.sub("", name).split())[:WORDS_MAX]
        out[r.group(1)] = f'{m.group("role")} "{name}"' if name else m.group("role")
    return out


def ref_name(words: str) -> str:
    """The name part of ref_words' `role "name"` ("" when the element has none)."""
    m = re.match(r'^[\w-]+ "(.*)"$', words or "")
    return m.group(1) if m else ""


def role_name(words: str) -> tuple[str, str]:
    """`button "Pay now"` (a ref's words in a reading) -> ("button", "Pay now"); ("", "") when it is not such a line."""
    m = _LINE.match("- " + (words or "").strip().removeprefix("- "))
    if not m:
        return "", ""
    return m.group("role") or "", _unescape(m.group("name") or "")


def ref_boxes(tree: str) -> dict[str, tuple[float, float, float, float]]:
    """ref -> (x, y, width, height) from a reading made with boxes."""
    out: dict[str, tuple[float, float, float, float]] = {}
    for line in tree.splitlines():
        r, b = _REF_IN.search(line), _BOX_IN.search(line)
        if r and b:
            out[r.group(1)] = (float(b.group(1)), float(b.group(2)), float(b.group(3)), float(b.group(4)))
    return out


def for_reading(tree: str, width: int = 160) -> str:
    """The tree as the agent reads it: without the focus marker `[active]` (it moves with every click, so each reading after a
    click would differ in lines that did not change), and a text longer than `width` (a <pre> block or a long paragraph is one line
    in the tree) wrapped onto continuation lines under it, so what changed on the page stays a few short lines."""
    out: list[str] = []
    for line in tree.splitlines():
        line = line.replace(" [active]", "")
        if len(line) <= width:
            out.append(line)
            continue
        pad = " " * (_indent(line) + 4)
        rest = line
        first = True
        while len(rest) > width:
            cut = rest.rfind(" ", width // 2, width)
            cut = width if cut <= 0 else cut
            out.append(rest[:cut] if first else pad + rest[:cut])
            rest, first = rest[cut:].lstrip(), False
        if rest:
            out.append(rest if first else pad + rest)
    return "\n".join(out)


def strip_boxes(tree: str) -> str:
    return _BOX_IN.sub("", tree)


def interactive_refs(tree: str) -> list[str]:
    """The refs of the lines that can be clicked or typed into, in reading order."""
    out: list[str] = []
    for line in tree.splitlines():
        r = _REF_IN.search(line)
        if r and interactive(line):
            out.append(r.group(1))
    return out


def frame_hosts(tree: str) -> dict[str, str]:
    """frame prefix ("f1") -> the ref of the iframe line that holds it ("e11", or "f1e7" for a frame inside a frame)."""
    lines = tree.splitlines()
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        r = _REF_IN.search(line)
        if not r or _role(line) != "iframe":
            continue
        ind = _indent(line)
        for below in lines[i + 1:]:
            if _indent(below) <= ind:
                break
            if (br := _REF_IN.search(below)) and (fm := re.match(r"(f\d+)e\d+$", br.group(1))):
                out.setdefault(fm.group(1), r.group(1))
                break
    return out


def frame_of(ref: str) -> str:
    """"f1" for "f1e3", "" for a ref of the page itself."""
    m = re.match(r"(f\d+)e\d+$", ref or "")
    return m.group(1) if m else ""
