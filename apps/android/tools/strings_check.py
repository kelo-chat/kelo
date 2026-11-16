#!/usr/bin/env python3
"""Checks the app's string resources in every language against the default (English) values/.

  python3 apps/android/tools/strings_check.py            # all languages; exit 1 on any error
  python3 apps/android/tools/strings_check.py fr de      # just these (codes as in strings_res.LOCALE_DIRS); problems of values/
                                                    # itself are then shown but do not fail
  python3 apps/android/tools/strings_check.py --missing-ok   # missing names are listed but not errors (a language in progress)

Errors:
  - a file in values/ or values-*/ that is not well-formed XML, or has an unescaped apostrophe / stray quote (aapt would fail or
    silently drop the quotes)
  - values/ still has Korean in a translatable string (it belongs in values-ko/; see strings_sync.py promote)
  - a locale lacks a translatable name of values/ (unless --missing-ok; names with nothing to translate, like "%1$s · %2$s",
    may be left out), or has a name values/ does not have
  - a locale repeats an entry marked translatable="false" (those live only in values/)
  - placeholders (%1$s, %2$d, %d …) differ from values/: a translation may reorder them, but not add, drop or change a type.
    Plural forms other than "other" may drop the number ("one" -> "a minute"). A few strings take a Korean-only argument
    (a particle), listed in LOCALE_ONLY_ARGS: locales may use it or not.
  - a plurals entry lacks a quantity the language needs (CLDR rules in strings_res.PLURALS) or has one it never uses
  - a string-array has a different number of items
  - inline markup (<b>, <i>, <u>, <xliff:g>, <a>) that is unbalanced or differs from values/
  - a literal % that is not %% in a string with placeholders (String.format would throw)
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strings_res as sr  # noqa: E402

RES = Path(__file__).resolve().parents[2] / "shared" / "strings"   # the text of all apps (Android format)

PH = re.compile(r"%(?:(\d+)\$)?[-#+ 0,(]*\d*(?:\.\d+)?([sdfxXoceEgGbh])")
STRAY_PERCENT = re.compile(r"%(?![-#+ 0,(\d.]*[sdfxXoceEgGbhn%]|\d+\$)")
TAG = re.compile(r"</?([a-zA-Z:]+)[^>]*>")

# name -> argument numbers a locale may use or leave out (Korean particles passed by the code)
LOCALE_ONLY_ARGS: dict[str, set[str]] = {
    "consent_body": {"2"},
    "chat_approval_ask": {"2"},
}


def placeholders(text: str) -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    plain = text.replace("%%", "")
    pos = 0
    for m in PH.finditer(plain):
        pos += 1
        found[(m.group(1) or f"#{pos}", m.group(2))] = 1
    return found


def neutral(e: sr.Entry) -> bool:
    """Nothing to translate: only placeholders, digits, punctuation and symbols ("%1$s · %2$s", "99+"). A locale may skip it."""
    return all(not any(ch.isalpha() for ch in PH.sub("", t)) for t in e.texts())


def tags(text: str) -> list[str]:
    return [m.group(0).split()[0].rstrip(">") for m in TAG.finditer(text)]


def balanced(text: str) -> bool:
    stack: list[str] = []
    for m in TAG.finditer(text):
        name, closing = m.group(1), m.group(0).startswith("</")
        if m.group(0).endswith("/>"):
            continue
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


def raw_problems(path: Path) -> list[str]:
    """aapt's own complaints about raw text: unescaped apostrophes outside quotes."""
    out: list[str] = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return [f"{path.relative_to(RES)}: not well-formed XML: {e}"]

    parent_name = {}
    for el in root:
        if isinstance(el.tag, str):
            for node in el.iter():
                parent_name[node] = el.get("name")
    for node in root.iter():
        if node.tag not in ("string", "item"):
            continue
        name, raw = parent_name.get(node), sr._raw_text(node)
        if True:
            quoted, i = False, 0
            while i < len(raw):
                ch = raw[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    quoted = not quoted
                elif ch == "'" and not quoted:
                    out.append(f"{path.relative_to(RES)}: {name}: unescaped apostrophe (write \\')")
                    break
                i += 1
    return out


# Strings that must keep a Korean proper noun in every language: the name the user has to search for on a Korean-only site
# (e.g. the KMA service on data.go.kr). Hangul in them is expected, not an untranslated leftover.
KOREAN_NAME_OK = {"briefing_key_help_kma"}


def check_text(where: str, name: str, ref: str, got: str, *, allow_drop: bool = False) -> list[str]:
    errs: list[str] = []
    pr, pg = placeholders(ref), placeholders(got)
    extra_ok = LOCALE_ONLY_ARGS.get(name, set())
    for key in pg:
        if key not in pr and key[0] not in extra_ok:
            same_index = [k for k in pr if k[0] == key[0]]
            what = f"type {key[1]} instead of {same_index[0][1]}" if same_index else "not in English"
            errs.append(f"{where}: {name}: placeholder %{key[0]}${key[1]} {what}")
    if not allow_drop:
        for key in pr:
            if key not in pg and key[0] not in extra_ok:
                errs.append(f"{where}: {name}: placeholder %{key[0]}${key[1]} missing")
    if pg and STRAY_PERCENT.search(got.replace("%%", "")):
        errs.append(f"{where}: {name}: a lone % in a formatted string (write %%)")
    if tags(got) and not balanced(got):
        errs.append(f"{where}: {name}: unbalanced markup")
    if sorted(tags(ref)) != sorted(tags(got)):
        errs.append(f"{where}: {name}: markup differs from English ({tags(ref)} vs {tags(got)})")
    return errs


def main(argv: list[str]) -> int:
    missing_ok = "--missing-ok" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    errors: list[str] = []
    notes: list[str] = []

    defaults: dict[str, sr.Entry] = {}
    untranslatable: set[str] = set()
    for f in sr.string_files(RES):
        errors += raw_problems(f)
        for e in sr.read(f):
            if e.name in defaults:
                errors.append(f"values/{f.name}: {e.name} defined twice")
            defaults[e.name] = e
            if not e.translatable:
                untranslatable.add(e.name)
            elif e.name not in KOREAN_NAME_OK and any(sr.HANGUL.search(t) for t in e.texts()):
                errors.append(f"values/{f.name}: {e.name} is still Korean (English goes here, Korean in values-ko/)")
            if e.kind == "plurals":
                need = set(sr.PLURALS["en"])
                if set(e.quantities) != need:
                    errors.append(f"values/{f.name}: {e.name} needs exactly {sorted(need)}, has {sorted(e.quantities)}")
            for t in e.texts():
                if tags(t) and not balanced(t):
                    errors.append(f"values/{f.name}: {e.name}: unbalanced markup")
    translatable = {k for k in defaults if k not in untranslatable}

    if wanted and errors:   # checking given languages: problems of values/ itself (someone else's work in progress) are only shown
        notes += [f"(values/) {e}" for e in errors]
        errors = []

    langs = [l for l in sr.LOCALE_DIRS if l != "en" and (not wanted or l in wanted)]
    for lang in langs:
        d = RES / sr.LOCALE_DIRS[lang]
        files = sorted(d.glob("strings*.xml")) if d.exists() else []
        if not files:
            (notes if missing_ok else errors).append(f"{lang}: no {d.name}/ yet")
            continue
        seen: set[str] = set()
        lang_errs: list[str] = []
        for f in files:
            lang_errs += raw_problems(f)
            if not (RES / "values" / f.name).exists():
                lang_errs.append(f"{d.name}/{f.name}: no such file in values/")
            for e in sr.read(f):
                where = f"{d.name}/{f.name}"
                if e.name in seen:
                    lang_errs.append(f"{where}: {e.name} defined twice")
                seen.add(e.name)
                ref = defaults.get(e.name)
                if ref is None:
                    lang_errs.append(f"{where}: {e.name} is not in values/")
                    continue
                if e.name in untranslatable or not e.translatable:
                    lang_errs.append(f"{where}: {e.name} is translatable=\"false\" (keep it only in values/)")
                    continue
                if e.kind != ref.kind:
                    lang_errs.append(f"{where}: {e.name} is a {e.kind}, values/ has a {ref.kind}")
                    continue
                if lang != "ko" and e.name not in KOREAN_NAME_OK and any(sr.HANGUL.search(t) for t in e.texts()):
                    lang_errs.append(f"{where}: {e.name} still has Korean text")
                if e.kind == "string":
                    if not e.text.strip() and ref.text.strip():
                        lang_errs.append(f"{where}: {e.name} is empty")
                    lang_errs += check_text(where, e.name, ref.text, e.text)
                elif e.kind == "array":
                    if len(e.items) != len(ref.items):
                        lang_errs.append(f"{where}: {e.name} has {len(e.items)} items, values/ has {len(ref.items)}")
                    for a, b in zip(ref.items, e.items):
                        lang_errs += check_text(where, e.name, a, b)
                else:
                    need = set(sr.PLURALS[lang])
                    have = set(e.quantities)
                    if need - have:
                        lang_errs.append(f"{where}: {e.name} lacks plural forms {sorted(need - have)} ({lang} needs {sorted(need)})")
                    if have - need:
                        lang_errs.append(f"{where}: {e.name} has plural forms {lang} never uses: {sorted(have - need)}")
                    for q, t in e.quantities.items():
                        lang_errs += check_text(where, f"{e.name}[{q}]", ref.quantities.get("other", ""), t,
                                                allow_drop=q != "other")
        missing = sorted(k for k in translatable - seen if not neutral(defaults[k]))
        if missing:
            line = f"{lang}: {len(missing)} missing: " + ", ".join(missing[:25]) + (" …" if len(missing) > 25 else "")
            (notes if missing_ok else lang_errs).append(line)
        errors += lang_errs
        if not lang_errs:
            print(f"{lang}: ok ({len(seen)}/{len(translatable)})")

    for n in notes:
        print(n)
    if errors:
        print("\n".join(errors))
        print(f"{len(errors)} problem(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
