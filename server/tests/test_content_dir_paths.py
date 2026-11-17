"""2026-11-17: the server image keeps content at /app/content (ASSISTANT_CONTENT_DIR), not next to the code. Modules that built the
path from the code's location only (parents[2] / "content") found no prompt on the deployed server: the hourly situation research
failed every pass, and the memory and forget prompts read as empty. Every module that reads server/content must honour the variable."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "assistant"
CHECKOUT_ONLY = re.compile(r'Path\(__file__\)\.resolve\(\)\.parents\[2\]\s*/\s*"content"')


def test_every_content_path_honours_the_content_dir_variable():
    bad = []
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in CHECKOUT_ONLY.finditer(text):
            line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end())]
            if "ASSISTANT_CONTENT_DIR" not in line and "_default_content_dir" not in text[max(0, m.start() - 300): m.start()] \
                    and not line.lstrip().startswith("PROMPTS = "):
                bad.append(f"{p.relative_to(SRC)}: {line.strip()}")
    assert not bad, "read server/content through ASSISTANT_CONTENT_DIR:\n" + "\n".join(bad)


def test_the_rule_sees_a_checkout_only_path():
    assert CHECKOUT_ONLY.search('X = Path(__file__).resolve().parents[2] / "content" / "prompts"')
