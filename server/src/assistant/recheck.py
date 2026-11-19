"""What is left of the recheck service (retired 2026-11-19, card p-recheck-like-reference).

Kelo no longer keeps a list of its own claims with a fixed look-again ladder, a nightly cap or a daily budget. Corrections of what Kelo
told the owner come from corrections.CorrectionCheck, used by the morning briefing and the hourly look. Follow-up checks the owner asks
for are schedules or watchers.

Kept here because other modules use them:
  * RECHECK_MARK     leads the hidden messages the old service wrote; rows that start with it are still never the owner's words.
  * TopicKey         a model's short English slug -> a stable key (offers.py).
  * SourceRule       a change needs two sources from different registrable domains, or one official source (offers.py, corrections.py).
  * SCHEMA           the old table `rechecks`; disk_limits.py still deletes its old rows.
  * retire_rechecks  once per server: every row still waiting is closed, so no list holds work nothing reads any more.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .tools.sites import public_suffix, registrable

log = logging.getLogger(__name__)

RECHECK_MARK = "[Handoff from a recheck, not from the user]"   # leads the old service's hidden messages; GET /api/messages leaves these out
SLUG_MAX = 60
SOURCE_MAX = 6
RETIRED = "recheck:retired"                                    # meta key: when the waiting rows were closed
RETIRED_REASON = "rechecks are part of the briefing and the hourly look now"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rechecks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, topic_key TEXT NOT NULL, claim TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '',
    how TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'agent', turn_id TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
    next_at REAL NOT NULL, expires_at REAL, last_checked REAL, attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'open',
    skip_reason TEXT NOT NULL DEFAULT '', draft TEXT NOT NULL DEFAULT '', sources_json TEXT NOT NULL DEFAULT '[]',
    verdict_json TEXT NOT NULL DEFAULT '{}', decide_by REAL, every_days INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS rechecks_key ON rechecks(topic_key);
CREATE INDEX IF NOT EXISTS rechecks_due ON rechecks(state, next_at);
"""


class TopicKey:
    """The same topic, the same key. The model writes a short English slug; this makes it ascii, lower case, hyphens, at most SLUG_MAX
    characters, and appends the month (":YYYY-MM"). Nothing smarter: two wordings match when their slugs are equal."""

    MONTH = re.compile(r":\d{4}-\d{2}$")

    @staticmethod
    def slug(text: Any) -> str:
        raw = TopicKey.MONTH.sub("", str(text or "").strip())
        s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").lower()
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:SLUG_MAX].strip("-")
        return s or ("topic-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8])   # a slug with no ascii letters at all

    @staticmethod
    def of(text: Any, now: float, tz: ZoneInfo | None = None) -> str:
        return f"{TopicKey.slug(text)}:{datetime.fromtimestamp(now, tz or ZoneInfo('UTC')).strftime('%Y-%m')}"

    @staticmethod
    def slug_of(key: str) -> str:
        return TopicKey.MONTH.sub("", key or "")


def topic_key(text: Any, now: float | None = None, tz: ZoneInfo | None = None) -> str:
    return TopicKey.of(text, time.time() if now is None else now, tz)


_HOST = re.compile(r"(?<![\w.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])", re.I)


@dataclass
class Judgement:
    ok: bool
    sources: list[dict[str, Any]]
    reason: str = ""


class SourceRule:
    """What may be claimed: a change or a mistake needs at least two sources from different registrable domains, or one official source.
    "Official" is decided here, not by the model: the source's domain is named in the row's `how` (the institution or the company
    itself) or is a government domain (.go.kr, .gov, .gov.<country>); and for a domain only named in `how`, the model must also say so."""

    def __init__(self, how: str):
        self.named = {registrable(h) for h in _HOST.findall((how or "").lower())}

    @staticmethod
    def host_of(url: Any) -> str:
        try:
            u = urlsplit(str(url or "").strip())
        except ValueError:
            return ""
        return (u.hostname or "").lower() if u.scheme in ("http", "https") else ""

    @staticmethod
    def government(host: str) -> bool:
        return host.endswith((".go.kr", ".gov")) or public_suffix(host).startswith("gov.")

    def official(self, host: str, claimed: bool) -> bool:
        if not host:
            return False
        return self.government(host) or (claimed and registrable(host) in self.named)

    def judge(self, sources: Any) -> Judgement:
        marked: list[dict[str, Any]] = []
        for s in (sources if isinstance(sources, list) else [])[:SOURCE_MAX]:
            if not isinstance(s, dict):
                continue
            host = self.host_of(s.get("url"))
            if not host:
                continue
            claimed = s.get("official") is True
            marked.append({"url": str(s["url"]).strip()[:300], "publisher": str(s.get("publisher") or "")[:100], "domain": registrable(host),
                           "official": self.official(host, claimed), "claimed_official": claimed,
                           "date": str(s["date"])[:20] if s.get("date") else None})
        if any(m["official"] for m in marked):
            return Judgement(True, marked)
        domains = {m["domain"] for m in marked}
        if len(domains) >= 2:
            return Judgement(True, marked)
        return Judgement(False, marked, "one unofficial source" if domains else "no usable source")


def retire_rechecks(store: Any, now: float) -> int:
    """Once per server (meta RETIRED): every row still waiting (open, unconfirmed, drafted) is closed with RETIRED_REASON. Rows the owner
    stopped, and rows already told or closed, stay as they are; disk_limits.py deletes them when they are old. Returns the rows closed."""
    if store.meta(RETIRED):
        return 0
    with store._lock:
        store._db.executescript(SCHEMA)   # a server that never had the table
        store._db.commit()
    cur = store._run("UPDATE rechecks SET state = 'closed', skip_reason = ? WHERE state IN ('open', 'unconfirmed', 'drafted')", (RETIRED_REASON,))
    store.set_meta(RETIRED, str(now))
    closed = int(cur.rowcount or 0)
    if closed:
        log.info("rechecks retired: %d waiting row(s) closed", closed)
    return closed
