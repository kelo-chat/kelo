"""Keeping the memory files current: a regular pass that runs the verified pipeline of docs/memory-pipeline.md (steps 1-15) with the
owner's main model for every model step:

  1-2   the window of what happened since the last pass (messages, finished tasks, approvals, inbox) and the branch: nothing new and
        nothing due means no model call
  3-5   memory_claims.py: extract claims with quotes, check their shape, verify with two lenses (one repair), write the dated log
  6-13  memory_reconcile.py, per file (MEMORY.md, people pages, AGENTS.md, user.md cleanup): pin, plan edits, ground them, stage, review
        with four lenses, apply by code
  14-15 the recall index is refreshed; memory_runs and memory:last_pass keep the counts the Records page shows

Files: user.md (who the person is), MEMORY.md (About the user / Ongoing / Decided & done under a size budget, MEMORY_BUDGET characters,
ASSISTANT_MEMORY_BUDGET), AGENTS.md (the assistant's own working lessons), memory/people/<slug>.md, memory/log/<date>.md (the dated
source log, written by the pipeline only).

When it runs: in the nightly jobs right after the look-back (same switch, same quiet slot, same idle wait, same model: the main one), and
after a chat has gone quiet for a while when enough new messages have piled up (idle_tick, at most once per IDLE_GAP). A pass held back
by the AI model's usage limit (model_limits.py) is kept "pending" with retry_at (the hold's end) and idle_tick runs it once that time has
passed and the app is left alone; claims already admitted wait in memory:unreconciled until a reconcile has seen them.

What the code enforces whatever the model answers: the previous version of every file a pass changes is kept under .memory-passes/<id>/
(the last KEEP_PASSES passes) and `undo` puts the whole last pass back (a file edited since is left as it is); size caps; no secrets
(scrub); a file someone else changed meanwhile is left alone; nothing runs while the look-back is switched off. Conversation text is
fenced as data in every request; output that is not valid JSON changes nothing.

Each pass is kept as memory:last_pass (counts, changed lines, stats), which the Records page shows in one line with an undo.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .compaction import TIME_TAG
from .home import Home, HomeError, front_matter
from .model_limits import limit_of
from .providers import ChatRequest
from .owner_messages import OWNER, OwnerMessages
from .reflection import IDLE_BEFORE_RUN, IDLE_POLL, IDLE_WAIT_MAX

log = logging.getLogger(__name__)

MEMORY_BUDGET = 3_000                       # characters of MEMORY.md (ASSISTANT_MEMORY_BUDGET)
CAPS = {"user.md": 1_500, "AGENTS.md": 1_500}
PERSON_CAP = 2_000
ITEM_MAX = 220                              # one MEMORY.md line, at most, once the budget has to be met
DONE_ITEM_MAX = 160
LOOKBACK_SECONDS = 24 * 3600
MAX_LOOKBACK_SECONDS = 3 * 24 * 3600
RELATED_CLAIMS = 8                          # active claims shown next to the new ones in a reconcile request
KEEP_PASSES = 10
CHANGED_LINES = 60                          # changed lines kept per file and side for the app
IDLE_AFTER = 30 * 60                        # a chat quiet this long (seconds) ...
IDLE_MIN_MESSAGES = 6                       # ... with this many new messages from the user ...
IDLE_GAP = 4 * 3600                         # ... starts a pass, at most this often
LIMIT_RETRY_MIN = 600.0                     # a pass the usage limit held back tries again no sooner than this (seconds)
MODEL_TIMEOUT = 600.0                       # seconds the model may take; a pass cut off here changes nothing
BACKUPS = ".memory-passes"
FILES = ("user.md", "MEMORY.md", "AGENTS.md")
SECTIONS = ("About the user", "Ongoing", "Decided & done")
CODES = ("no_news", "switched_off", "in_use", "no_model", "model_error", "bad_output", "no_room", "busy")
STATUSES = ("done", "skipped", "failed", "pending", "busy")

# ------------------------------------------------------------------------------------------------------------------ secrets
# Shapes only (docs/no-word-lists.md, hc-privacy): no word of any language decides. What a shape cannot tell (a spoken password, a bare
# code) is the extracting model's own `secret` flag on each claim (memory_claims.check_shape): not exactly false = not stored.
_TOKEN = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{12,}|\b(?:ghp|gho|ghs|ghu|github_pat|glpat|xox[abprs])[-_A-Za-z0-9]{10,}"
                    r"|\bAKIA[0-9A-Z]{12,}|\bAIza[0-9A-Za-z_\-]{20,}|\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+)?"
                    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----")
_LONG_RANDOM = re.compile(r"(?<![\w/.:@-])(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{28,}(?![\w/.@-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IDENTITY = re.compile(r"(?<!\d)\d{6}\s?-\s?[1-8]\d{6}(?!\d)")          # resident registration number form
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])")
_LABEL_VALUE = re.compile(r"^[^:：=]{1,60}[:：=]\s*[\"'“‘`]?([^\s\"'”’`,;)]{6,64})")   # "label: value" (the structure, not a word)
REMOVED = "[removed]"
EMPTY_ITEMS = frozenset({"none", "nothing captured yet"})   # the placeholder items older files carry


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


MEMORY_FILES = ("user.md", "MEMORY.md", "AGENTS.md")


def is_memory_file(rel: str) -> bool:
    """The files the memory pass keeps (and may cite a line of as evidence): user.md, MEMORY.md, AGENTS.md, memory/people/*.md and the
    dated memory/log/*.md. Never an upload, a note or any other file of the home."""
    rel = str(rel or "")
    if rel in MEMORY_FILES:
        return True
    return rel.endswith(".md") and ".." not in rel and rel.startswith(("memory/people/", "memory/log/"))


def scrub(text: str) -> tuple[str, int]:
    """(text without secrets, how many were taken out). A line whose "label: value" has a password-shaped value (letters mixed with
    digits or symbols, secret_guard.password_shaped) goes whole; card numbers (Luhn), identity numbers, IBANs (mod 97), keys and
    tokens are replaced by [removed]. By shape only."""
    from .privacy_guard import iban_ok
    from .secret_guard import password_shaped
    out, n = [], 0
    for line in (text or "").split("\n"):
        m = _LABEL_VALUE.match(line.strip())
        if m and password_shaped(m.group(1)):
            n += 1
            continue
        new = _TOKEN.sub(REMOVED, line)
        new = _LONG_RANDOM.sub(REMOVED, new)
        new = _IDENTITY.sub(REMOVED, new)
        new = _CARD.sub(lambda c: REMOVED if 13 <= len(re.sub(r"\D", "", c.group(0))) <= 19 and _luhn(re.sub(r"\D", "", c.group(0)))
                        else c.group(0), new)
        new = _IBAN.sub(lambda c: REMOVED if iban_ok(c.group(0)) else c.group(0), new)
        n += new.count(REMOVED) - line.count(REMOVED)
        out.append(new)
    return "\n".join(out), n


# ------------------------------------------------------------------------------------------------------------------ MEMORY.md layout
def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def _section_of(heading: str) -> str:
    h = heading.lower()
    for name in SECTIONS:
        if _norm(h) == _norm(name):
            return name
    return "About the user"   # a heading of another name (an older layout, any language) goes here; its items are kept


def parse_sections(text: str) -> dict[str, list[str]]:
    """MEMORY.md as {section: [item]} in the current layout. An item is a "- " line with the lines that continue it; text under a
    heading of another name goes to the section it fits (an unknown one to About the user), sub-headings become items."""
    out: dict[str, list[str]] = {s: [] for s in SECTIONS}
    cur = "About the user"
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip() or re.match(r"^#\s", line):
            continue
        if m := re.match(r"^##\s+(.+)$", line):
            cur = _section_of(m.group(1))
            continue
        if m := re.match(r"^#{3,}\s+(.+)$", line):
            out[cur].append(f"- {m.group(1).strip()}:")
            continue
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line) or not out[cur]:
            item = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
            if item and item.strip("().").lower() not in EMPTY_ITEMS:
                out[cur].append(f"- {item}")
        else:
            out[cur][-1] += " " + line.strip()
    return out


def render_sections(sections: dict[str, list[str]]) -> str:
    parts = ["# MEMORY.md"]
    for name in SECTIONS:
        items = sections.get(name) or []
        parts.append(f"## {name}" + ("\n" + "\n".join(items) if items else ""))
    return "\n\n".join(parts) + "\n"


def migrate_layout(text: str) -> str:
    """MEMORY.md in the current layout (the three sections, one "- " line per item), keeping every item."""
    return render_sections(parse_sections(text))


def in_layout(text: str) -> bool:
    heads = re.findall(r"^##\s+(.+?)\s*$", text or "", re.M)
    return [h for h in heads] == list(SECTIONS)


def _shorten(item: str, limit: int) -> str:
    if len(item) <= limit:
        return item
    if m := _DATE_TAG.search(item):     # the date and source stay; the wording before them is shortened
        tag = m.group(0).strip()
        return _shorten(item[:m.start()].rstrip(), max(20, limit - len(tag) - 1)) + " " + tag
    cut = item[: limit - 1]
    for sep in (". ", "; ", ", ", " · ", " "):
        at = cut.rfind(sep)
        if at > limit // 2:
            cut = cut[:at]
            break
    return cut.rstrip(" ,;·") + "…"


def fit_budget(text: str, cap: int) -> str:
    """MEMORY.md at most `cap` characters, the least useful going first: long finished items are shortened, then finished items
    go (oldest first), then long ongoing and long About-the-user items are shortened, then ongoing items go (oldest first), and
    only then About-the-user items from the end."""
    sec = parse_sections(text)
    size = lambda: len(render_sections(sec))  # noqa: E731
    if size() <= cap:
        return render_sections(sec)
    done, ongoing, about = sec["Decided & done"], sec["Ongoing"], sec["About the user"]
    done[:] = [_shorten(i, DONE_ITEM_MAX) for i in done]
    while done and size() > cap:
        _pop_oldest(done)
    for items in (ongoing, about):
        if size() > cap:
            items[:] = [_shorten(i, ITEM_MAX) for i in items]
    while ongoing and size() > cap:
        _pop_oldest(ongoing)
    while about and size() > cap:
        about.pop()
    return render_sections(sec)


_DATE_TAG = re.compile(r"\[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*$")


def line_date(item: str) -> str:
    """The date of a line's [YYYY-MM-DD, source] tag ("" when it has none: an older line)."""
    m = _DATE_TAG.search(item)
    return m.group(1) if m else ""


def tag_new_lines(before: str, after: str, today: str) -> str:
    """Every "- " line of `after` that is new (not in `before`) and has no date tag gets [today]: each line says when it was learned."""
    old = {_norm(re.sub(r"^\s*[-*+]\s+", "", ln)) for ln in (before or "").splitlines()}
    out = []
    for ln in after.split("\n"):
        m = re.match(r"^(\s*[-*+]\s+)(.+)$", ln)
        if m and not _DATE_TAG.search(ln) and _norm(m.group(2)) not in old and not m.group(2).rstrip().endswith(":"):
            ln = f"{ln.rstrip()} [{today}]"
        out.append(ln)
    return "\n".join(out)


def _pop_oldest(items: list[str]) -> None:
    """Takes out the line with the oldest date (an undated, older line first; the earliest position among equals)."""
    i = min(range(len(items)), key=lambda k: (line_date(items[k]), k))
    items.pop(i)


def fit_text(text: str, cap: int) -> str:
    """Any other file at most `cap` characters: whole lines from the end go first, then a hard cut."""
    text = text.strip() + "\n"
    if len(text) <= cap:
        return text
    lines = text.rstrip("\n").split("\n")
    while len(lines) > 1 and len("\n".join(lines)) + 1 > cap:
        lines.pop()
    out = "\n".join(lines)
    return (out[: cap - 2].rstrip() + "…" if len(out) >= cap else out) + "\n"


# ------------------------------------------------------------------------------------------------------------------ changes
def _content_lines(text: str) -> list[str]:
    """The lines that say something (no headings, no front matter), without their bullet marks."""
    body = front_matter(text or "")[1]
    out = []
    for line in body.split("\n"):
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(_DATE_TAG.sub("", re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", s)).rstrip())
    return out


def line_changes(before: str, after: str) -> dict[str, Any]:
    """{"added": [lines], "removed": [lines], "new": n, "tidied": n}: a removed line with a close new wording counts as tidied, not
    as one removed and one added."""
    b, a = _content_lines(before), _content_lines(after)
    bn, an = {_norm(x) for x in b}, {_norm(x) for x in a}
    added = [x for x in a if _norm(x) not in bn]
    removed = [x for x in b if _norm(x) not in an]
    paired = 0
    left = list(added)
    for r in removed:
        best = max(left, key=lambda x: difflib.SequenceMatcher(None, _norm(r), _norm(x)).ratio(), default=None)
        if best is not None and difflib.SequenceMatcher(None, _norm(r), _norm(best)).ratio() >= 0.5:
            left.remove(best)
            paired += 1
    clip = lambda xs: [x[:300] for x in xs[:CHANGED_LINES]]  # noqa: E731
    return {"added": clip(added), "removed": clip(removed), "new": len(left), "tidied": len(removed)}


def _overlap(a: str, b: str) -> float:
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    return len(wa & wb) / max(1, min(len(wa), len(wb))) if wa and wb else 0.0


def drop_rule_repeats(text: str, rules: list[str]) -> str:
    """MEMORY.md without the lines that only repeat a preference rule in use (the rule table is their one place)."""
    if not rules:
        return text
    sec = parse_sections(text)
    for name in SECTIONS:
        sec[name] = [i for i in sec[name] if not any(_norm(i[2:]) == _norm(r) or (_overlap(i[2:], r) >= 0.8 and
                                                                                  len(_norm(i[2:]).split()) <= len(_norm(r).split()) + 3)
                                                     for r in rules)]
    return render_sections(sec)


def _slug(name: str) -> str:
    base = Path(str(name or "")).name
    base = base[:-3] if base.lower().endswith(".md") else base
    s = re.sub(r"[^\w-]+", "-", base.strip().lower()).strip("-_")
    return s[:48]


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16] if text is not None else ""


def _clean(text: Any) -> str:
    return TIME_TAG.sub("", str(text or "")).strip()


class MemoryUpkeep:
    def __init__(self, store: Any, home: Home, activity: Any, stream_fn: Callable[..., AsyncIterator[Any]],
                 model: Callable[[], tuple[str, str] | None], key_args: Callable[[str], dict[str, Any]] = lambda _p: {},
                 *, enabled: Callable[[], bool] | None = None, rules: Callable[[], list[str]] | None = None,
                 budget: int = MEMORY_BUDGET, clock: Callable[[], float] = time.time, last_use: Callable[[], float] = lambda: 0.0,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, inbox: Any = None, recall: Any = None,
                 tombstones: Any = None):
        self.store, self.home, self.activity, self.stream_fn, self.model, self.key_args = store, home, activity, stream_fn, model, key_args
        self.inbox = inbox            # memory_inbox.MemoryInbox: notes the agent jotted down, filed by each pass
        self.recall = recall          # recall.Recall: its index is refreshed after a pass (step 14)
        self.tombstones = tombstones  # forget.Tombstones: topics the owner asked to forget; no pass writes them again (docs/forget.md)
        self._enabled = enabled or (lambda: self.store.meta("reflection:enabled") != "0")   # the look-back's switch
        self.rules = rules or (lambda: [])                                                    # the preference rules in use
        self.budget = max(800, int(budget or MEMORY_BUDGET))
        self.clock, self.last_use, self.sleep = clock, last_use, sleep
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------ state
    def enabled(self) -> bool:
        return bool(self._enabled())

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.profile().get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), self.tz()).strftime("%Y-%m-%d")

    def _meta_json(self, key: str) -> Any:
        try:
            return json.loads(self.store.meta(key) or "null")
        except ValueError:
            return None

    def last_pass(self) -> dict[str, Any] | None:
        """The last pass that changed something (kept while later passes find nothing to change, so it can still be undone)."""
        v = self._meta_json("memory:last_pass")
        return v if isinstance(v, dict) else None

    def last_attempt(self) -> dict[str, Any] | None:
        """The last pass of any outcome (skipped, failed, nothing to change, done)."""
        v = self._meta_json("memory:last_attempt")
        return v if isinstance(v, dict) else None

    def public(self) -> dict[str, Any]:
        """For GET /api/reflection ("memory"): the last pass that changed something, without its changed lines, and how the last
        attempt went."""
        p = self.last_pass()
        a = self.last_attempt()
        out = None
        if p is not None:
            out = {k: p.get(k) for k in ("at", "status", "code", "added", "tidied", "files", "trigger", "undone", "stats")}
            out["can_undo"] = bool(p.get("id")) and not p.get("undone") and (self.home.root / BACKUPS / str(p.get("id"))).is_dir()
        return {"last_pass": out, "last_attempt": {k: a.get(k) for k in ("at", "status", "code", "trigger", "retry_at", "stats")} if a else None}

    def changes(self) -> dict[str, Any]:
        """GET /api/reflection/memory: the last pass with the lines it added and removed per file."""
        p = self.last_pass()
        return {**self.public(), "changes": (p or {}).get("changes") or []}

    def _record(self, status: str, code: str = "", reason: str = "", **extra: Any) -> dict[str, Any]:
        """Keeps the attempt as last_attempt and, when it changed files, as last_pass. `reason` is for logs only."""
        run = {"at": self.clock(), "status": status, "code": code, "added": 0, "tidied": 0, "files": [], "trigger": "",
               "undone": False, "changes": [], "id": "", **extra, "reason": reason}
        self.store.set_meta("memory:last_attempt", json.dumps({k: v for k, v in run.items() if k != "changes"}, ensure_ascii=False))
        if run["id"]:
            self.store.set_meta("memory:last_pass", json.dumps(run, ensure_ascii=False))
        log.info("memory pass: %s%s · %s", status, f" ({code})" if code else "", reason)
        return {k: v for k, v in run.items() if k != "changes"}

    # ------------------------------------------------------------------ inputs
    def _since(self) -> float:
        now = self.clock()
        try:
            seen = float(self.store.meta("memory:seen_until") or 0)
        except ValueError:
            seen = 0.0
        return max(seen or now - LOOKBACK_SECONDS, now - MAX_LOOKBACK_SECONDS)

    def _messages(self, since: float) -> list[dict[str, Any]]:
        from .secret_guard import scrub_rows   # a message sent with a secret in it (after the warning) is read without it
        return scrub_rows(self.store, self.store._all("SELECT id, role, content, created_at, author FROM messages WHERE created_at > ? ORDER BY id", (since,)))

    @staticmethod
    def _from_user(m: dict[str, Any]) -> bool:
        return m.get("author") == OWNER   # the owner's own message (owner_messages.py), never a hidden row

    def _read(self, rel: str) -> str | None:
        p = self.home.root / rel
        if not p.is_file() or p.is_symlink():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def _inbox_lines(self) -> list[str]:
        return self.inbox.lines() if self.inbox is not None else []

    def _cap_of(self, rel: str) -> int:
        if rel == "MEMORY.md":
            return self.budget
        if rel in CAPS:
            return CAPS[rel]
        return PERSON_CAP

    def _resolver(self) -> Callable[[str], bool]:
        """Step 8's handle check (memory-owner-only): claim:<id> must be active; m<id> must be one of the owner's own messages (not the
        assistant's, a tool's or a hidden row); <file>#L<n> only for a memory file (is_memory_file), never an upload or a note, and only
        when that line exists."""
        owners = OwnerMessages(self.store)

        def resolve(handle: str) -> bool:
            h = str(handle)
            if h.startswith("claim:"):
                return bool(self.store._all("SELECT 1 FROM memory_claims WHERE claim_id = ? AND status = 'active'", (h[6:],)))
            if m := re.fullmatch(r"m(\d+)", h):
                return owners.is_owner(int(m.group(1)))
            path, sep, n = h.rpartition("#L")
            if sep and n.isdigit() and is_memory_file(path):
                text = self._read(path)
                return text is not None and 1 <= int(n) <= len(text.splitlines())
            return False
        return resolve

    def _owner_evidence(self, handle: str) -> bool:
        """The owner's own words for new content (reconcile step 8): m<id> of one of the owner's messages, or an active claim the owner's
        words made. A file line, a task or the assistant's text is never evidence of that kind."""
        h = str(handle)
        owners = OwnerMessages(self.store)
        if m := re.fullmatch(r"m(\d+)", h):
            return owners.is_owner(int(m.group(1)))
        if h.startswith("claim:"):
            rows = self.store._all("SELECT handle FROM memory_claims WHERE claim_id = ? AND status = 'active' AND speaker = 'user'", (h[6:],))
            m = re.fullmatch(r"m(\d+)", str(rows[0]["handle"])) if rows else None
            return bool(m) and owners.is_owner(int(m.group(1)))
        return False

    def _claims_by_ids(self, ids: list[str]) -> list[Any]:
        from .memory_types import Claim
        out = []
        for cid in ids:
            for r in self.store._all("SELECT claim_id, kind, salience, text, quote, speaker, handle, valid_until, supersedes FROM memory_claims "
                                     "WHERE claim_id = ? AND status = 'active'", (cid,)):
                out.append(Claim(r["claim_id"], r["kind"], r["salience"], r["text"], r["quote"], r["speaker"], r["handle"],
                                 r["valid_until"], r["supersedes"]))
        return out

    def _related(self, admitted: list[Any]) -> list[Any]:
        from .memory_claims import active_claims, search_claims
        from .memory_types import Claim
        skip = {c.id for c in admitted}
        if admitted:
            rows = search_claims(self.store, " ".join(c.text for c in admitted)[:600], limit=RELATED_CLAIMS + len(skip), active_only=True)
            got = [Claim(r["claim_id"], r["kind"], r["salience"], r["text"], r["quote"], r["speaker"], r["handle"], r["valid_until"],
                         r["supersedes"]) for r in rows]
        else:
            got = active_claims(self.store, RELATED_CLAIMS + len(skip))
        return [c for c in got if c.id not in skip][:RELATED_CLAIMS]

    # ------------------------------------------------------------------ backups and undo
    def _backup_dir(self) -> Path:
        d = self.home.root / BACKUPS
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d

    def _prune_backups(self) -> None:
        for old in sorted(p for p in self._backup_dir().iterdir() if p.is_dir())[:-KEEP_PASSES]:
            shutil.rmtree(old, ignore_errors=True)

    def backups(self) -> list[str]:
        d = self.home.root / BACKUPS
        return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "pass.json").is_file()) if d.is_dir() else []

    def undo(self) -> dict[str, Any]:
        """Puts the files of the last pass back as they were before it (every file its reconcile steps wrote), and the inbox lines it
        filed back in the inbox. A file changed since the pass (by the user, the assistant or anything else) is kept as it is now.
        Raises LookupError when there is nothing to undo."""
        p = self.last_pass()
        if not p or not p.get("id") or p.get("undone"):
            raise LookupError("nothing to undo")
        d = self.home.root / BACKUPS / str(p["id"])
        try:
            index = json.loads((d / "pass.json").read_text(encoding="utf-8"))["files"]
        except (OSError, ValueError, KeyError):
            raise LookupError("the backup of that pass is gone") from None
        restored, kept = [], []
        for rel, info in index.items():
            now = self._read(rel)
            if _sha(now) != info.get("after"):
                kept.append(rel)
                continue
            if info.get("existed"):
                self.home.write(rel, (d / "files" / rel).read_text(encoding="utf-8"), by="system")
            else:
                target = self.home.root / rel
                if target.is_file() and not target.is_symlink():
                    target.unlink()
            restored.append(rel)
        refiled = 0
        try:
            lines = json.loads((d / "inbox.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lines = []
        if self.inbox is not None and isinstance(lines, list) and lines:   # the notes this pass filed wait in the inbox again
            refiled = self.inbox.restore([str(x) for x in lines])
        p["undone"] = True
        p["undone_at"] = self.clock()
        self.store.set_meta("memory:last_pass", json.dumps(p, ensure_ascii=False))
        log.info("memory pass %s undone: %d restored, %d kept, %d inbox lines back", p["id"], len(restored), len(kept), refiled)
        return {"restored": sorted(restored), "kept": sorted(kept), "inbox_back": refiled, **self.public()}

    # ------------------------------------------------------------------ the pass
    async def run(self, *, trigger: str = "manual", force: bool = False) -> dict[str, Any]:
        """One pass. Returns {"at", "status": done|skipped|failed|pending|busy, "code", "added", "tidied", "files", ...}."""
        if self.lock.locked():
            return {"at": self.clock(), "status": "busy", "code": "busy", "reason": "a pass is already going"}
        async with self.lock:
            return await self._run(trigger, force)

    def _pending_claims(self) -> list[str]:
        v = self._meta_json("memory:unreconciled")
        return [str(x) for x in v] if isinstance(v, list) else []

    def _log_append(self, window: Any) -> Callable[[str], None]:
        def append(block: str) -> None:
            try:
                old = self.home.read(window.log_path)
            except HomeError:
                old = ""
            self.home.write(window.log_path, (old.rstrip() + "\n\n" if old.strip() else "") + block.rstrip("\n") + "\n", by="system")
        return append

    async def _run(self, trigger: str, force: bool) -> dict[str, Any]:
        """The driver of docs/memory-pipeline.md, steps 1-15."""
        from .memory_claims import build_window, record_run, reconcile_due, run_claims, _call_stream
        from .memory_reconcile import pin, choose_targets, reconcile_file
        from .memory_types import Claim

        if not self.enabled():
            return self._record("skipped", "switched_off", "switched off", trigger=trigger)
        t0 = self.clock()
        started = time.time()
        pass_id = datetime.fromtimestamp(t0).strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 10**6:06d}"
        stats: dict[str, Any] = {"claims_extracted": 0, "claims_admitted": 0, "claims_dropped": 0, "repair_used": False,
                                 "edits_proposed": 0, "edits_grounded": 0, "edits_withheld": 0, "edits_applied": 0,
                                 "bytes": {}, "model_requests": 0, "seconds": 0.0, "reconciled": {}}

        def finish(status: str, code: str, reason: str, **extra: Any) -> dict[str, Any]:
            stats["seconds"] = round(max(0.0, self.clock() - t0), 3)
            outcome = {"done": "changed" if extra.get("files") else "no_change"}.get(status, code or status)
            if status == "skipped" and code == "no_news":
                outcome = "no_change"
            try:
                record_run(self.store, pass_id, started, time.time(), trigger, outcome, stats)
            except Exception:  # noqa: BLE001   the receipt never decides the pass
                log.warning("memory pass: the run row could not be written", exc_info=True)
            return self._record(status, code, reason, trigger=trigger, stats=stats, **extra)

        # 1. window
        since = self._since()
        if force:
            since = min(since, self.clock() - LOOKBACK_SECONDS)
        window = build_window(self.store, self.home, self.inbox, tz=lambda: str(self.store.profile().get("timezone") or "UTC"),
                              clock=self.clock, since=since, run_id=pass_id)
        pending_ids = self._pending_claims()
        # 2. branch: nothing new and nothing due -> no model call at all
        due_now = bool(pending_ids) or reconcile_due(self.store, self.home, window.as_of, None, self.clock)
        if not window.has_new and not due_now:
            return finish("skipped", "no_news", "nothing new since the last pass and nothing is due")
        chosen = self.model()
        if not chosen:
            return finish("pending", "no_model", "no model is set")
        provider, model = chosen
        requests = [0]

        async def call(messages: list[dict[str, Any]]) -> str:
            requests[0] += 1
            return await _call_stream(self.stream_fn, provider, ChatRequest(model=model, messages=messages, tools=[], extra={}),
                                      self.key_args, MODEL_TIMEOUT)

        # 3-5. claims
        inbox_before = self._inbox_lines()
        admitted: list[Claim] = []
        if window.has_new:
            admitted, cs = await run_claims(window, self.store, self.home, self.stream_fn, model=chosen, key_args=self.key_args,
                                            inbox=self.inbox, clock=self.clock, tombstones=self.tombstones)
            stats.update(claims_extracted=cs.get("extracted", 0), claims_admitted=cs.get("admitted", 0),
                         claims_dropped=cs.get("dropped", 0), repair_used=bool(cs.get("repair_used")))
            stats["model_requests"] += int(cs.get("model_requests") or 0)
            err = cs.get("error")
            if err == "model_limit":
                retry = max(float(cs.get("limit_until") or 0), self.clock() + LIMIT_RETRY_MIN)
                return finish("pending", "model_limit", "the AI model's usage limit is used up", retry_at=retry)
            if err == "no_model":
                return finish("pending", "no_model", "no model is set")
            if err == "bad_output":
                return finish("failed", "bad_output", "the model did not answer in the expected form; nothing was changed")
            if err:
                return finish("failed", "model_error", str(err)[:160])
            if window.messages:
                self.store.set_meta("memory:seen_until", repr(window.messages[-1]["created_at"]))
        filed = [ln for ln in inbox_before if ln not in set(self._inbox_lines())]
        if admitted:    # kept until a reconcile has seen them (a usage limit may cut it short)
            pending_ids = list(dict.fromkeys([*pending_ids, *(c.id for c in admitted)]))
            self.store.set_meta("memory:unreconciled", json.dumps(pending_ids))
        carried = self._claims_by_ids([i for i in pending_ids if i not in {c.id for c in admitted}])
        to_reconcile = [*carried, *admitted]

        # 2 again, 6. reconcile due, choose targets
        if not (to_reconcile or reconcile_due(self.store, self.home, window.as_of, admitted, self.clock)):
            return finish("done", "", "nothing to change")
        try:
            counter = int(self.store.meta("memory:sweep_count") or 0) + 1
        except ValueError:
            counter = 1
        targets = choose_targets(to_reconcile, counter)
        related = self._related(to_reconcile)
        resolve, log_append = self._resolver(), self._log_append(window)

        forgotten = self.tombstones.block() if self.tombstones is not None else ""

        async def model_call(messages: list[dict[str, Any]]) -> str:
            if forgotten and messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), str):
                messages = [*messages[:-1], {**messages[-1], "content": messages[-1]["content"] + "\n\n" + forgotten}]
            return await call(messages)

        # 7-13. one pinned file at a time
        applied_files: dict[str, tuple[str, str]] = {}
        index: dict[str, Any] = {}
        failure: Exception | None = None
        for rel in targets:
            before = self._read(rel)
            pinned = pin(self.home, rel, window.as_of, self._cap_of(rel))
            try:
                res = await reconcile_file(pinned, to_reconcile, related, "", model_call, resolve, log_append, home=self.home,
                                           run_id=pass_id, owner_evidence=self._owner_evidence)
            except Exception as e:  # noqa: BLE001
                failure = e
                break
            self._merge_index(pass_id, index)
            for k in ("edits_proposed", "edits_grounded", "edits_withheld", "edits_applied"):
                stats[k] += int(res.get(k) or 0)
            stats["reconciled"][rel] = str(res.get("status") or "")
            if res.get("applied") and forgotten:
                try:
                    await self._screen_added(rel, before or "", call)
                except Exception as e:  # noqa: BLE001   no usable screening: the file goes back as it was (fail closed)
                    log.warning("forget screen failed after the pass: %s", e)
                    self.home.write(rel, before or "", by="system")
                    stats["reconciled"][rel] = "screen_failed"
                    continue
            if res.get("applied"):
                after = self._read(rel) or ""
                applied_files[rel] = (before or "", after)
                stats["bytes"][rel] = [len(before or ""), len(after)]
        stats["model_requests"] += requests[0]
        shutil.rmtree(self._backup_dir() / pass_id / "stage", ignore_errors=True)
        has_backup = (self._backup_dir() / pass_id / "pass.json").is_file()
        if not has_backup:
            shutil.rmtree(self._backup_dir() / pass_id, ignore_errors=True)
        extra: dict[str, Any] = {}
        changes, added, tidied = [], 0, 0
        for rel in sorted(applied_files, key=lambda r: (FILES.index(r) if r in FILES else len(FILES), r)):
            b, a = applied_files[rel]
            ch = line_changes(b, a)
            changes.append({"file": rel, "added": ch["added"], "removed": ch["removed"]})
            added += ch["new"]
            tidied += ch["tidied"]
        if applied_files:
            self._file_inbox_backup(pass_id, filed)
            extra = dict(added=added, tidied=tidied, files=sorted(applied_files), changes=changes, id=pass_id, filed=len(filed))
            self._prune_backups()
        if failure is not None:
            if (lim := limit_of(failure)) is not None:
                retry = max(float(lim.until or 0), self.clock() + LIMIT_RETRY_MIN)
                return finish("pending", "model_limit", f"the AI model's usage limit is used up: {str(failure)[:160]}", retry_at=retry, **extra)
            return finish("failed", "model_error", f"{type(failure).__name__}: {str(failure)[:160]}", **extra)
        # the reconcile ran to its end: its claims are seen, a sweep has been made
        self.store.set_meta("memory:unreconciled", "[]")
        self.store.set_meta("memory:last_sweep_at", repr(self.clock()))
        self.store.set_meta("memory:sweep_count", str(counter))
        # 14. refresh the indexes (the claims FTS follows its table by triggers)
        if self.recall is not None:
            try:
                self.recall.refresh()
            except Exception:  # noqa: BLE001
                log.warning("memory pass: the recall index could not be refreshed", exc_info=True)
        return finish("done", "", (f"{added} added, {tidied} tidied in {', '.join(sorted(applied_files))}" if applied_files
                                   else "nothing to change"), **extra)

    async def _screen_added(self, rel: str, before: str, call: Callable[[list[dict[str, Any]]], Awaitable[str]]) -> None:
        """Lines a pass added to a file are screened against the forgotten topics; those about one are taken out again."""
        after = self._read(rel) or ""
        seen = {_norm(x) for x in _content_lines(before)}
        added = [x for x in _content_lines(after) if _norm(x) not in seen]
        if not added:
            return
        flagged = await self.tombstones.screener(call)(added)
        if not flagged:
            return
        bad = {_norm(added[i]) for i in flagged}
        keep = [ln for ln in after.splitlines() if not any(_norm(c) in bad for c in _content_lines(ln))]
        self.home.write(rel, "\n".join(keep) + "\n", by="system")
        log.info("memory pass: %d added lines about a forgotten topic were taken out of %s", len(flagged), rel)

    def _merge_index(self, pass_id: str, acc: dict[str, Any]) -> None:
        """reconcile's apply writes pass.json for the one file it wrote; a pass writes several, so each index is merged into one."""
        d = self.home.root / BACKUPS / pass_id
        try:
            acc.update(json.loads((d / "pass.json").read_text(encoding="utf-8"))["files"])
        except (OSError, ValueError, KeyError):
            return
        (d / "pass.json").write_text(json.dumps({"id": pass_id, "at": self.clock(), "files": acc}, ensure_ascii=False), encoding="utf-8")

    def _file_inbox_backup(self, pass_id: str, lines: list[str]) -> None:
        if lines:    # kept with the backup: undoing the pass puts them back in the inbox
            (self._backup_dir() / pass_id / "inbox.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")

    async def nightly(self) -> dict[str, Any]:
        """The nightly job (after the look-back): only when switched on, and only once the app has been left alone."""
        if not self.enabled():
            return self._record("skipped", "switched_off", "switched off", trigger="nightly")
        waited = 0.0
        while self.clock() - self.last_use() < IDLE_BEFORE_RUN:
            if waited >= IDLE_WAIT_MAX:
                return self._record("skipped", "in_use", "the app was in use the whole time", trigger="nightly")
            await self.sleep(IDLE_POLL)
            waited += IDLE_POLL
        return await self.run(trigger="nightly")

    async def idle_tick(self) -> dict[str, Any] | None:
        """Checked every few minutes: a pass once the chat has gone quiet for IDLE_AFTER with at least IDLE_MIN_MESSAGES new
        messages from the user, at most once per IDLE_GAP. Costs nothing (no model call) when that does not hold."""
        if not self.enabled() or self.lock.locked() or self.model() is None:
            return None
        now = self.clock()
        last = self.last_attempt() or {}
        if last.get("code") == "model_limit" and last.get("retry_at"):
            # a pass the usage limit held back: once the hold is over (and the app is left alone) it runs, whatever the gap
            if now < float(last["retry_at"]) or now - self.last_use() < IDLE_BEFORE_RUN:
                return None
            return await self.run(trigger="retry")
        try:
            last_try = float(self.store.meta("memory:idle_at") or 0)
        except ValueError:
            last_try = 0.0
        if now - max(float(last.get("at") or 0), last_try) < IDLE_GAP or now - self.last_use() < IDLE_BEFORE_RUN:
            return None
        msgs = self._messages(self._since())
        if not msgs or now - float(msgs[-1]["created_at"]) < IDLE_AFTER or (sum(self._from_user(m) for m in msgs) < IDLE_MIN_MESSAGES
                                                                     and len(self._inbox_lines()) < IDLE_MIN_MESSAGES):
            return None
        self.store.set_meta("memory:idle_at", repr(now))
        return await self.run(trigger="idle")


def budget_from_env(env: dict[str, str] | None = None) -> int:
    """ASSISTANT_MEMORY_BUDGET: MEMORY.md's size in characters (800..20000; MEMORY_BUDGET when unset or not a number)."""
    raw = (env if env is not None else os.environ).get("ASSISTANT_MEMORY_BUDGET", "").strip()
    try:
        return min(20_000, max(800, int(raw))) if raw else MEMORY_BUDGET
    except ValueError:
        return MEMORY_BUDGET
