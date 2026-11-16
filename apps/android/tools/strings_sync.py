#!/usr/bin/env python3
"""Moving strings between the default (English) values/ and the locale directories.

  python3 tools/strings_sync.py korean-left
      Prints, as JSON (name -> Korean text), the strings in values/ that are still Korean (new strings written in Korean by
      habit). Give them English, then run `promote`.

  python3 tools/strings_sync.py promote en.json
      For each string in values/ that is still Korean and has an English text in en.json (flat: name -> text / list / dict):
      the Korean goes to values-ko/<same file>, the English replaces it in values/ (comments and layout kept).

  python3 tools/strings_sync.py build <lang> <file.json>
      Writes values-<lang>/strings*.xml from a flat JSON (name -> text; plurals as {"one": .., "other": ..}; arrays as lists),
      merged over what that locale already has. Text in the JSON is plain (what the user sees); escaping is done here.

  python3 tools/strings_sync.py remove <name> [<name> …]
      Deletes these strings from values/ and every locale.

  python3 tools/strings_sync.py prune
      Drops from every locale the names values/ no longer has (strings removed or renamed in English).

  python3 tools/strings_sync.py dump <lang>
      Prints a locale's current strings as flat JSON (the same shape `build` reads), "en" for the default.

Paths are relative to apps/shared/strings. Run strings_check.py afterwards.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strings_res as sr  # noqa: E402

RES = Path(__file__).resolve().parents[2] / "shared" / "strings"   # the text of all apps (Android format)
HEADER = "{lang} text for {file} (generated from values/{file} by tools/strings_sync.py; edit freely, keep the names)"


def is_korean(e: sr.Entry) -> bool:
    return e.translatable and any(sr.HANGUL.search(t) for t in e.texts())


def korean_left() -> dict[str, object]:
    out: dict[str, object] = {}
    for f in sr.string_files(RES):
        for e in sr.read(f):
            if is_korean(e):
                out[e.name] = e.to_json()
    return out


def locale_values(dir_name: str, file_name: str) -> dict[str, object]:
    p = RES / dir_name / file_name
    return {e.name: e.to_json() for e in sr.read(p)} if p.exists() else {}


def write_locale(lang: str, file_name: str, values: dict[str, object]) -> None:
    template = sr.read(RES / "values" / file_name)
    d = RES / sr.LOCALE_DIRS[lang]
    d.mkdir(exist_ok=True)
    known = {e.name for e in template if e.translatable}
    kept = {k: v for k, v in values.items() if k in known}
    (d / file_name).write_text(sr.render_locale(template, kept, HEADER.format(lang=lang, file=file_name)), encoding="utf-8")


def promote(en: dict[str, object]) -> list[str]:
    left: list[str] = []
    for f in sr.string_files(RES):
        entries = sr.read(f)
        korean = {e.name: e.to_json() for e in entries if is_korean(e)}
        if not korean:
            continue
        english = {k: en[k] for k in korean if k in en}
        left += [k for k in korean if k not in en]
        if not english:
            continue
        ko = locale_values(sr.LOCALE_DIRS["ko"], f.name)
        ko.update({k: korean[k] for k in english})
        source = f.read_text(encoding="utf-8")
        f.write_text(sr.replace_texts(source, english), encoding="utf-8")
        write_locale("ko", f.name, ko)
    return left


def build(lang: str, values: dict[str, object]) -> None:
    for f in sr.string_files(RES):
        names = {e.name for e in sr.read(f) if e.translatable}
        mine = {k: v for k, v in values.items() if k in names}
        if not mine and not (RES / sr.LOCALE_DIRS[lang] / f.name).exists():
            continue
        merged = locale_values(sr.LOCALE_DIRS[lang], f.name)
        merged.update(mine)
        write_locale(lang, f.name, merged)


def _drop(path: Path, names: set[str]) -> int:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r'\n[ \t]*<(string|plurals|string-array)\s+name="(' + "|".join(map(re.escape, names)) + r')"[^>]*>.*?</\1>', re.S)
    new, n = pattern.subn("", text)
    if n:
        path.write_text(new, encoding="utf-8")
    return n


def remove(names: set[str]) -> int:
    return sum(_drop(f, names) for f in RES.glob("values*/strings*.xml")) if names else 0


def prune() -> int:
    known = {e.name for f in sr.string_files(RES) for e in sr.read(f)}
    count = 0
    for lang, d in sr.LOCALE_DIRS.items():
        if lang == "en":
            continue
        for f in sorted((RES / d).glob("strings*.xml")):
            stale = {e.name for e in sr.read(f)} - known
            if stale:
                count += _drop(f, stale)
    return count


def dump(lang: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for f in sr.string_files(RES):
        out.update({k: v for k, v in locale_values(sr.LOCALE_DIRS[lang], f.name).items()})
    if lang == "en":
        defaults = {e.name for f in sr.string_files(RES) for e in sr.read(f) if not e.translatable}
        out = {k: v for k, v in out.items() if k not in defaults}
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "korean-left":
        print(json.dumps(korean_left(), ensure_ascii=False, indent=1))
    elif cmd == "promote":
        left = promote(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
        if left:
            print("still Korean (no English given):", ", ".join(left))
            return 1
    elif cmd == "build":
        lang = argv[1]
        if lang not in sr.LOCALE_DIRS or lang == "en":
            print(f"unknown language {lang}; known: {', '.join(sr.LOCALE_DIRS)}")
            return 2
        build(lang, json.loads(Path(argv[2]).read_text(encoding="utf-8")))
    elif cmd == "remove":
        print(f"removed {remove(set(argv[1:]))} entries")
    elif cmd == "prune":
        print(f"dropped {prune()} stale entries")
    elif cmd == "dump":
        print(json.dumps(dump(argv[1]), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
