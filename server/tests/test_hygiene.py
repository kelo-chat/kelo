"""Repository hygiene before publishing: no material, names or paths from the products Kelo was compared with during development,
and no Korean left in the English source of the prompt, the skills and the docs (translations live in server/src/assistant/locales/)."""

import codecs
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The other products' names, kept encoded so that this file itself does not name them.
BANNED = re.compile("|".join(codecs.decode(w, "rot13") for w in (r"\oZhfr\o", r"zhfr[\.\-_]", r"\oUngpu\o", r"/bcg/ungpu", r"\oCvpxyr\o",
                                                                   r"\oZrgn NV\o", r"Zrgn'f")), re.I)
SCANNED = ("server/src", "server/tests", "server/content", "docs", "scripts", "NOTICE", "compose.yml", "docker/server.Dockerfile", "docker/cli.Dockerfile", "docker/workroom.Dockerfile",
           "pyproject.toml", ".dockerignore")


def files():
    for name in SCANNED:
        p = ROOT / name
        for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix not in (".pyc", ".png", ".jpg", ".webp"):
                yield f


def test_no_other_products_names_anywhere():
    hits = [f"{f.relative_to(ROOT)}:{n}" for f in files() for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if BANNED.search(line)]
    assert hits == []


def test_the_removed_reference_material_stays_removed():
    for gone in ("content", "server/content/library", "server/content/assets", "server/content/connectors", "server/content/skill-scripts",
                 "server/content/skills/index.json", "server/content/prompts/system.original.md", "server/content/prompts/CHANGES.md",
                 "scripts/import_content.py", "server/src/assistant/library.py"):
        assert not (ROOT / gone).exists(), gone


def test_english_source_for_the_prompt_skills_and_docs():
    for f in [ROOT / "server" / "content" / "prompts" / "system.md", *(ROOT / "server" / "content" / "skills").rglob("*.md"), *(ROOT / "server" / "content" / "help").glob("*.md"),
              *(ROOT / "docs").glob("*.md")]:
        assert not re.search(r"[가-힣]", f.read_text(encoding="utf-8")), f.relative_to(ROOT)
