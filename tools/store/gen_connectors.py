"""The Connectors tab rows for the store screenshots, made by the server's own code (connect_ways.py + connectors.py), so names,
descriptions, icons and order are what a real server answers. A stand-in store and mailbox say what is connected: a Gmail mailbox,
Notion and GitHub. Writes tools/store/data/connectors-{en,ko}.json (read by tools/store/store_mock.py).

    uv run python tools/store/gen_connectors.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from assistant import i18n  # noqa: E402
from assistant.connect_ways import ways  # noqa: E402
from assistant.connectors import connector_rows  # noqa: E402
from assistant.mcp_oauth import SERVICES as MCP  # noqa: E402
from assistant.oauth_connect import PROVIDERS as DEVICE  # noqa: E402

GITHUB = next(p.item for p in DEVICE.values() if "github" in p.serves)
NOTION = MCP["notion"].item
EMAIL = {"en": "sam.lee@gmail.com", "ko": "jimin.kim@gmail.com"}


class Store:
    def credentials(self):
        return [{"name": GITHUB, "has_secret": True, "label": "GitHub"}, {"name": NOTION, "has_secret": True, "label": "Notion"}]

    def meta(self, *a, **k):
        return ""

    def __getattr__(self, name):   # anything else the code asks for: nothing stored
        return lambda *a, **k: None


class Mail:
    def __init__(self, lang):
        self.lang = lang

    def accounts(self):
        return [{"name": "mail-gmail", "provider": "gmail", "address": EMAIL[self.lang]}]


out = ROOT / "tools/store/data"
for lang in ("en", "ko"):
    tok = i18n.use(lang)
    try:
        rows = connector_rows(ways(Store(), Mail(lang), browser=True, google_open=False), regions={"KR"} if lang == "ko" else {"US"})
    finally:
        i18n.reset(tok)
    rows = [r for r in rows if r.get("featured")]
    rows.sort(key=lambda r: r["rank"])
    (out / f"connectors-{lang}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(lang, len(rows), [(r["id"], r["status"]) for r in rows[:12]])
