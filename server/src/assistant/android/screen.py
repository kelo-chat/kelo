"""The screen of the device as the model reads it: the uiautomator dump (XML) -> items with a short ref (a1, a2, ...) in reading order.

Pure. Nodes with no area or outside the screen are dropped; a clickable node with no words of its own takes those of the nodes
inside it (a list row is one button, not a row and two texts); the rest with words is text. Refs are numbered on from the last
screen of the same owner (`start`), so a ref of an earlier screen is never the name of something else on this one."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

MAX_ITEMS = 120
MAX_LABEL = 80
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_KINDS = ("edit", "check", "button", "text")


@dataclass(frozen=True, slots=True)
class Item:
    ref: str
    kind: str                         # edit | check | button | text
    text: str = ""                    # what it says (for a field: what was typed in it)
    desc: str = ""                    # its content description
    hint: str = ""                    # a field's hint
    cls: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    package: str = ""
    clickable: bool = False
    editable: bool = False
    checked: bool | None = None       # a check's state; None = not a check
    multiline: bool = False
    password: bool = False
    focused: bool = False

    @property
    def words(self) -> str:
        """The name the model and the owner know it by."""
        return self.text or self.desc or self.hint

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return (l + r) // 2, (t + b) // 2

    def contains(self, x: int, y: int) -> bool:
        l, t, r, b = self.bounds
        return l <= x < r and t <= y < b

    @property
    def area(self) -> int:
        l, t, r, b = self.bounds
        return max(0, r - l) * max(0, b - t)


@dataclass(slots=True)
class Screen:
    app: str = ""                     # "package/activity" in front ("" when unknown)
    size: tuple[int, int] = (0, 0)
    items: list[Item] = field(default_factory=list)
    more: int = 0                     # items left out beyond MAX_ITEMS
    package_seen: str = ""            # the package of the root node, for when `app` is empty

    @property
    def package(self) -> str:
        return self.app.split("/", 1)[0] if self.app else self.package_seen

    @property
    def activity(self) -> str:
        """The activity's short name ("ReviewWriteActivity"), "" when unknown."""
        if "/" not in self.app:
            return ""
        return self.app.split("/", 1)[1].rsplit(".", 1)[-1]

    def item(self, ref: str) -> Item | None:
        ref = (ref or "").strip().lower()
        return next((i for i in self.items if i.ref == ref), None)

    def at(self, x: int, y: int) -> Item | None:
        """The smallest item that holds the point (a button before text on it), None when there is none."""
        hits = [i for i in self.items if i.contains(x, y)]
        return min(hits, key=lambda i: (i.kind == "text", i.area), default=None)

    def render(self) -> str:
        w, h = self.size
        where = f"{self.package}" + (f" ({self.activity})" if self.activity else "")
        lines = [f"App: {where or '?'}   {w}x{h}"]
        for i in self.items:
            lines.append(_line(i))
        if self.more:
            lines.append(f"… {self.more} more, scroll")
        return "\n".join(lines)


def _quote(text: str) -> str:
    return '"' + " ".join(text.split()).replace('"', "'") + '"'


def _line(i: Item) -> str:
    if i.kind == "edit":
        label = i.hint or i.desc or ""
        value = "" if i.password else i.text
        if value and value in (i.hint, i.desc) and not i.password:
            value = ""   # some versions of Android report an empty field's hint as its text
        state = "(password, filled)" if i.password and i.text else "(password, empty)" if i.password else \
            f"= {_quote(value)}" if value else "(empty)"
        return f'{i.ref} edit {_quote(label)} {state}' if label else f"{i.ref} edit {state}"
    if i.kind == "check":
        return f'{i.ref} check {_quote(i.words)} ({"on" if i.checked else "off"})'
    return f"{i.ref} {i.kind} {_quote(i.words)}"


def _bool(v: str | None) -> bool:
    return (v or "").lower() == "true"


def _bounds(node: ElementTree.Element) -> tuple[int, int, int, int] | None:
    m = _BOUNDS.fullmatch((node.get("bounds") or "").strip())
    return (int(m[1]), int(m[2]), int(m[3]), int(m[4])) if m else None


def _own_words(node: ElementTree.Element) -> str:
    return " ".join((node.get("text") or node.get("content-desc") or "").split())


class ScreenReader:
    """Reads a dump. Holds no state; `start` is the number the first ref gets (the owner's screens go on counting)."""

    def parse(self, xml: str, app: str = "", start: int = 1) -> Screen:
        if "<!DOCTYPE" in xml or "<!ENTITY" in xml:   # a dump never has them; never let one expand
            raise ValueError("the screen could not be read")
        try:
            root = ElementTree.fromstring(xml.strip())
        except ElementTree.ParseError:
            raise ValueError("the screen could not be read") from None
        nodes = [n for n in root.iter("node")]
        if not nodes:
            return Screen(app=app)
        first = _bounds(nodes[0]) or (0, 0, 0, 0)
        w, h = first[2] - first[0], first[3] - first[1]
        screen = Screen(app=app, size=(max(w, 0), max(h, 0)), package_seen=nodes[0].get("package") or "")
        found: list[Item] = []
        self._walk(root, screen.size, found, set())
        found.sort(key=lambda i: (i.bounds[1], i.bounds[0]))
        screen.more = max(0, len(found) - MAX_ITEMS)
        screen.items = [_with_ref(i, f"a{start + n}") for n, i in enumerate(found[:MAX_ITEMS])]
        return screen

    def _walk(self, parent: ElementTree.Element, size: tuple[int, int], out: list[Item], eaten: set[int]) -> None:
        for node in parent.findall("node"):
            b = _bounds(node)
            if b is not None and self._visible(b, size):
                item = self._item(node, b, eaten)
                if item is not None:
                    out.append(item)
            self._walk(node, size, out, eaten)

    @staticmethod
    def _visible(b: tuple[int, int, int, int], size: tuple[int, int]) -> bool:
        l, t, r, bt = b
        if r <= l or bt <= t:
            return False   # no area
        w, h = size
        return not (w and h and (r <= 0 or bt <= 0 or l >= w or t >= h))

    def _item(self, node: ElementTree.Element, b: tuple[int, int, int, int], eaten: set[int]) -> Item | None:
        if id(node) in eaten:
            return None
        cls = node.get("class") or ""
        text, desc = " ".join((node.get("text") or "").split()), " ".join((node.get("content-desc") or "").split())
        base = dict(text=text, desc=desc, hint=" ".join((node.get("hint") or "").split()), cls=cls, bounds=b, package=node.get("package") or "",
                    clickable=_bool(node.get("clickable")), password=_bool(node.get("password")),
                    focused=_bool(node.get("focused")))
        if cls.endswith("EditText") or (_bool(node.get("focusable")) and "Edit" in cls):
            return Item(ref="", kind="edit", editable=True, multiline="multiline" in (node.get("resource-id") or "").lower() or b[3] - b[1] > 200,
                        **{**base, "clickable": True})
        if _bool(node.get("checkable")):
            if not (text or desc):
                base["text"] = self._inner_words(node, eaten)
            return Item(ref="", kind="check", checked=_bool(node.get("checked")), **base) if base["text"] or desc else None
        if _bool(node.get("clickable")):
            if not (text or desc):
                base["text"] = self._inner_words(node, eaten)
            return Item(ref="", kind="button", **base) if base["text"] or desc else None
        return Item(ref="", kind="text", **base) if text or desc else None

    def _inner_words(self, node: ElementTree.Element, eaten: set[int]) -> str:
        """The words of the nodes inside a clickable one, joined (at most MAX_LABEL characters); the nodes are then no items of their own.
        A node inside that is a button, field or switch itself stays its own item and adds nothing here."""
        parts: list[str] = []

        def go(n: ElementTree.Element) -> None:
            for c in n.findall("node"):
                if _bool(c.get("clickable")) or _bool(c.get("checkable")) or (c.get("class") or "").endswith("EditText"):
                    continue
                words = _own_words(c)
                if words:
                    parts.append(words)
                    eaten.add(id(c))
                go(c)

        go(node)
        joined = " ".join(parts)
        return joined if len(joined) <= MAX_LABEL else joined[:MAX_LABEL - 1].rstrip() + "…"


def _with_ref(i: Item, ref: str) -> Item:
    return Item(ref=ref, kind=i.kind, text=i.text, desc=i.desc, hint=i.hint, cls=i.cls, bounds=i.bounds, package=i.package,
                clickable=i.clickable, editable=i.editable, checked=i.checked, multiline=i.multiline, password=i.password, focused=i.focused)
