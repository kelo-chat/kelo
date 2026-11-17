"""App layout rule (owner 2026-11-17: the connector search field scrolled away with the list).

The search field, the "back to popular" link and the group chips of the connector list must not be a plain scrolling item:
- Android/desktop (shared Compose): they are drawn over the LazyColumn, not inside its lambda, and the list starts below them
  (measured with onSizeChanged into the list's top padding).
- iPhone (SwiftUI): they are the header of a Section pinned with pinnedViews: [.sectionHeaders].
"""
import re
from pathlib import Path

APPS = Path(__file__).resolve().parents[2] / "apps"
KT = APPS / "shared/src/commonMain/kotlin/chat/kelo/app/connectors/ConnectorsScreen.kt"
SWIFT = APPS / "ios/KeloUI/Sources/KeloConnectors/ConnectorsScreen.swift"


def lazy_column_body(src: str) -> str:
    """Text inside the LazyColumn { ... } lambda of the list (brace matching from the call's lambda)."""
    start = src.index("LazyColumn(")
    depth, i = 0, src.index("(", start)
    while True:
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    open_brace = src.index("{", i)
    depth, j = 0, open_brace
    while True:
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return src[open_brace:j]


def kotlin_search_is_outside_list(src: str) -> bool:
    body = lazy_column_body(src)
    return "connectors_search" not in body and 'item(key = "top"' not in src


def swift_search_is_pinned(src: str) -> bool:
    return "pinnedViews: [.sectionHeaders]" in src and re.search(r"\}\s*header:\s*\{\s*searchBar\(s\)", src) is not None


def test_connector_search_is_not_a_scrolling_item():
    assert kotlin_search_is_outside_list(KT.read_text(encoding="utf-8")), "the search field is inside the connector LazyColumn"
    assert swift_search_is_pinned(SWIFT.read_text(encoding="utf-8")), "the iPhone search block is not a pinned section header"


def test_the_rule_sees_the_wrong_shape():
    bad = 'LazyColumn(\n    Modifier.fillMaxSize(),\n) {\n    item(key = "top") { KeloTextField(state.query, vm::setQuery, string(Res.string.connectors_search)) }\n}\n'
    assert not kotlin_search_is_outside_list(bad)
    good = 'Box {\n    LazyColumn(\n        Modifier.fillMaxSize(),\n    ) {\n        item { Text("x") }\n    }\n    Column { KeloTextField(state.query, vm::setQuery, string(Res.string.connectors_search)) }\n}\n'
    assert kotlin_search_is_outside_list(good)
    assert not swift_search_is_pinned('LazyVStack {\n    top(s)\n}\n')
    assert swift_search_is_pinned('LazyVStack(pinnedViews: [.sectionHeaders]) {\n Section {\n} header: {\n    searchBar(s)\n }\n}')
