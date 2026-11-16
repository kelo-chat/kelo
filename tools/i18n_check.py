#!/usr/bin/env python3
"""Checks the server's translations (server/src/assistant/locales/<lang>.json) against the English templates in the code.

The English source is every string-literal first argument of t(...) / N_(...) under server/src/assistant (see i18n.py). For each
language file this reports:
  - missing:  templates in the code with no entry (they show in English until translated)
  - unused:   entries no t()/N_() uses any more
  - fields:   entries whose {fields} differ from the English template (t() would fall back to English)
  - format:   empty values, non-string values, keys not sorted

  uv run python tools/i18n_check.py              # every language; exit 1 on any problem
  uv run python tools/i18n_check.py fr de        # just these
  uv run python tools/i18n_check.py /tmp/fr.json # a file outside locales/ (named <lang>.json)
  uv run python tools/i18n_check.py --missing-ok # missing entries are reported but do not fail (a language being translated)
  uv run python tools/i18n_check.py --keys       # print the English templates as a JSON list (the work list for a translator)
"""

from __future__ import annotations

import ast
import json
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "server" / "src" / "assistant"
LOCALES = SRC / "locales"


def fields(template: str) -> set[str]:
    try:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError:
        return {"<broken braces>"}


def templates() -> dict[str, str]:
    """English template -> first place it is used."""
    found: dict[str, str] = {}
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
                found.setdefault(arg.value, f"{rel}:{node.lineno}")
    return found


def check(lang: str, used: dict[str, str], missing_ok: bool) -> list[str]:
    path = Path(lang) if lang.endswith(".json") else LOCALES / f"{lang}.json"   # a language code, or a file being translated
    lang = path.stem
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{lang}: cannot read {path.name}: {e}"]
    if not isinstance(data, dict):
        return [f"{lang}: not a flat JSON object"]
    problems: list[str] = []
    if list(data) != sorted(data):
        problems.append(f"{lang}: keys are not sorted (json.dump(..., sort_keys=True))")
    for k, v in data.items():
        if not isinstance(v, str) or not v.strip():
            problems.append(f"{lang}: empty or non-string value for {k!r}")
        elif k in used and fields(k) != fields(v):
            problems.append(f"{lang}: fields differ {sorted(fields(k))} vs {sorted(fields(v))}: {k!r} -> {v!r}")
        if k not in used:
            problems.append(f"{lang}: unused entry {k!r}")
    missing = [k for k in used if k not in data]
    if missing:
        head = f"{lang}: {len(missing)} missing" + (" (allowed)" if missing_ok else "")
        lines = [head] + [f"    {used[k]}: {k!r}" for k in missing[:40]] + (["    …"] if len(missing) > 40 else [])
        if missing_ok:
            print("\n".join(lines))
        else:
            problems += lines
    return problems


def main(argv: list[str]) -> int:
    used = templates()
    if "--keys" in argv:
        print(json.dumps(sorted(used), ensure_ascii=False, indent=1))
        return 0
    missing_ok = "--missing-ok" in argv
    langs = [a for a in argv if not a.startswith("--")] or sorted(p.stem for p in LOCALES.glob("*.json"))
    problems: list[str] = []
    for lang in langs:
        got = check(lang, used, missing_ok)
        problems += got
        if not got:
            print(f"{lang}: ok")
    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
