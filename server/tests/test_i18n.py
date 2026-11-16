"""The i18n layer: Accept-Language parsing, English fallback, and a catalog that matches the code.

Every English template the code passes to t() or marks with N_() must have a Korean entry with the same {fields}, and every Korean
entry must still be used somewhere, so the catalog stays clean as the code changes.
"""

from __future__ import annotations

import ast
import json
import string
from pathlib import Path

from assistant import i18n

SRC = Path(i18n.__file__).parent
KO = json.loads((SRC / "locales" / "ko.json").read_text(encoding="utf-8"))


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}


def _literals() -> dict[str, list[str]]:
    """Every string-literal first argument of t(...) / N_(...) under server/src/assistant, with where it is."""
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if rel.parts[:2] == ("providers", "vendor"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            arg = node.args[0]
            if name in ("t", "N_") and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, []).append(f"{rel}:{node.lineno}")
    return found


def test_pick_reads_accept_language():
    assert i18n.pick("ko-KR,ko;q=0.9,en;q=0.8") == "ko"
    assert i18n.pick("en;q=0.5, ko;q=0.9") == "ko"
    assert i18n.pick("ko_KR") == "ko"
    assert i18n.pick("fr") == "fr"
    assert i18n.pick("sv-SE,fi;q=0.5") == "en"          # a language with no file: English
    assert i18n.pick("sv-SE,de;q=0.5") == "de"
    assert i18n.pick("zh-TW") == "zh-Hant"
    assert i18n.pick("zh-Hant-HK") == "zh-Hant"
    assert i18n.pick("zh-CN") == "zh-Hans"
    assert i18n.pick("zh") == "zh-Hans"
    assert i18n.pick("pt-PT") == "pt-BR"
    assert i18n.pick("in-ID") == "id"
    assert i18n.pick(None) is None
    assert i18n.pick("  ") is None


def test_every_template_is_in_the_korean_catalog_with_the_same_fields():
    missing, broken = [], []
    for key, where in _literals().items():
        if key not in KO:
            missing.append(f"{where[0]}: {key!r}")
        elif _fields(key) != _fields(KO[key]):
            broken.append(f"{where[0]}: {key!r} -> {KO[key]!r}")
    assert not missing, "not in locales/ko.json:\n" + "\n".join(missing)
    assert not broken, "{fields} differ between English and Korean:\n" + "\n".join(broken)


def test_no_unused_catalog_entries():
    used = _literals()
    unused = [k for k in KO if k not in used]
    assert not unused, "in locales/ko.json but no longer used by any t()/N_():\n" + "\n".join(map(repr, unused))


def test_catalog_file_is_sorted_and_flat():
    assert list(KO) == sorted(KO)
    assert all(isinstance(v, str) and v for v in KO.values())


def test_t_translates_and_falls_back_to_english():
    key = next(k for k in KO if not _fields(k))
    tok = i18n.use("ko")
    try:
        assert i18n.t(key) == KO[key]
        assert i18n.t("A sentence nobody translated") == "A sentence nobody translated"
    finally:
        i18n.reset(tok)
    tok = i18n.use("xx")
    try:
        assert i18n.t(key) == key
    finally:
        i18n.reset(tok)
    assert i18n.t(key, lang="zz") == key
    assert i18n.t(key, lang="ko") == KO[key]


def test_t_fills_fields_in_both_languages():
    key = next(k for k in KO if _fields(k))
    values = {f: "X" for f in _fields(key)}
    assert i18n.t(key, lang="en", **values) == key.format(**values)
    assert i18n.t(key, lang="ko", **values) == KO[key].format(**values)


def test_every_locale_file_is_flat_and_keeps_the_fields():
    """All languages (tools/i18n_check.py lists what each still lacks): a flat object, and no entry that would break its {fields}."""
    used = _literals()
    broken = []
    for path in sorted((SRC / "locales").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and all(isinstance(v, str) and v for v in data.values()), path.name
        broken += [f"{path.name}: {k!r} -> {v!r}" for k, v in data.items() if k in used and _fields(k) != _fields(v)]
    assert not broken, "{fields} differ from English:\n" + "\n".join(broken)


def test_the_app_languages_all_have_a_server_catalog():
    have = set(i18n.available())
    app = {"en", "ko", "ja", "zh-Hans", "zh-Hant", "es", "fr", "de", "pt-BR", "it", "ru", "id", "vi", "th", "tr", "ar", "hi", "pl",
           "nl", "uk"}
    assert app <= have, sorted(app - have)
