"""Forget on request (docs/forget.md): find every place a topic lives, show one list, ask once, delete, and never write it again.

Two agent tools (`forget_tools`):
  forget_plan(subject, terms)   read-only: searches the places below, lets the main model pick what is really about the subject, keeps
                                the picked items as a plan (store table forget_plans) and returns counts per everyday category
  forget_confirm(plan_id)       the owner's card (requires_approval, never remembered): re-checks every item, deletes it, removes the
                                older copies, records a tombstone, verifies, deletes the plan

A tombstone (store table forget_tombstones: label, search terms, date, counts) is what "never again" means. The memory pass respects
it: the extract request is told what not to take, candidate claims are screened by the main model against it before they are stored
(memory_claims.run_claims), and lines a pass added to a memory file are screened after it (memory_upkeep) and taken out again. The
agent's own prompt carries a one-line notice. No word list decides anything: the places are searched with the owner's own terms (a
search, like grep), and what is "about the subject" is the main model's judgement (docs/no-word-lists.md).

Places (docs/forget.md has the full table): the rolling summary (the chat messages themselves stay; they are searched only to reach
what came of them), memory claims and the dated log lines that cite them,
MEMORY.md / user.md / AGENTS.md / people pages / notes / inbox lines, older saved versions of those files (history, memory-pass
backups, trash), schedules and their runs, watchers, follow-ups, goals, offers, skills Kelo wrote, task / approval / notification /
journal / proactive / reply-log rows. Never touched: the owner's uploaded files and library, outside services, other people's copies.
"""

from __future__ import annotations

import os
import hashlib
import json
import logging
import re
import secrets
import shutil
import time
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .agent.fence import fence
from .home import ALLOWED_SUFFIXES, CORE, SIDE_FILES, WRITABLE_DIRS, Home, HomeError
from .i18n import N_, t
from .model_json import parse_object

log = logging.getLogger(__name__)

PLAN_TTL = 3600.0          # a plan that waits longer than this is stale: it is made again
MAX_TERMS = 12
MIN_TERM = 2
MAX_CANDIDATES = 400       # more places than this: the subject is too broad, the owner is asked to narrow it
BATCH = 40                 # candidates per judging request
JUDGE_TOKENS = 2000        # room for one batch's answer (500 cut it off mid-JSON on every call, 2026-11-17)
SNIPPET = 300
SHOW = 60                  # characters of an item the owner's list shows
MAX_FILE_BYTES = 1_000_000
HIDDEN_COPIES = (".history", ".memory-passes", ".trash", ".backup")   # where older versions of files are kept (home.py, memory_upkeep.py)

# everyday words for the places (the card and the tool result use these, never table or file names)
CATEGORIES = ("memory", "notes", "reminders", "skills", "history")
CATEGORY_LABELS = {
    "memory": N_("Memory"),
    "notes": N_("Notes"),
    "reminders": N_("Reminders"),
    "skills": N_("Skills"),
    "history": N_("Logs"),
}

# (table, text columns, category): rows found by the terms, judged, then deleted by rowid. A table this server does not have is skipped.
ROW_TABLES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("tasks", ("title", "result", "answer", "error"), "history"),
    ("approval_log", ("label", "detail", "target", "edit_before", "edit_after"), "history"),
    ("notifications", ("title", "body"), "history"),
    ("journal", ("title", "summary"), "history"),
    ("proactive_log", ("title", "reason", "message"), "history"),
    ("proactive_held", ("title", "reason", "message"), "history"),
    ("schedule_runs", ("result", "error"), "history"),
    ("turn_events", ("ev",), "history"),
    ("trail_labels", ("title", "party"), "history"),
    ("learned_rules", ("rule", "evidence"), "memory"),
    ("goal_suggestions", ("title", "reason", "evidence"), "reminders"),
    ("goals", ("title", "note"), "reminders"),
    ("goal_steps", ("title", "detail"), "reminders"),
    ("goal_facts", ("key", "value"), "reminders"),
    ("goal_log", ("text",), "reminders"),
    ("followups", ("title", "detail"), "reminders"),
    ("offers", ("sentence", "request", "evidence"), "reminders"),
    ("watchers", ("name", "url", "note", "match"), "reminders"),
)
WHOLE_ONLY = ("turn_events", "trail_labels")   # structured rows are never rewritten, only deleted
GOAL_CHILDREN = ("goal_steps", "goal_facts", "goal_log", "goal_docs", "goal_metrics")
ROW_SPEC = {name: (cols, cat) for name, cols, cat in ROW_TABLES}


class ForgetError(Exception):
    """The plan or the cleanup cannot go on; the text is for the model."""


class ScreenFailed(Exception):
    """The screening request got no usable answer: the caller fails closed (nothing is stored this time)."""


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def sha(text: str) -> str:
    return hashlib.sha256(norm(" ".join(str(text or "").split())).encode()).hexdigest()[:16]


def clean_terms(subject: str, terms: Any) -> list[str]:
    """The search terms: the given ones (and the subject when it is short), trimmed, unique, at most MAX_TERMS."""
    raw = [*(terms if isinstance(terms, list) else []), *([subject] if 0 < len(" ".join(str(subject).split())) <= 40 else [])]
    out: list[str] = []
    for x in raw:
        s = " ".join(str(x or "").split())
        if len(s) >= MIN_TERM and norm(s) not in {norm(o) for o in out}:
            out.append(s[:80])
    return out[:MAX_TERMS]


def hits(text: str, terms: list[str]) -> bool:
    low = norm(text)
    return any(norm(x) in low for x in terms)


def _prompt(name: str) -> str:
    p = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content") / "prompts" / "forget" / name
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# ------------------------------------------------------------------------------------------------------------------ tombstones
class Tombstones:
    """What the owner asked Kelo never to write again: a short label and the terms it was searched with, in the store only."""

    def __init__(self, store: Any, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock

    def all(self) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT id, label, terms, created_at, removed FROM forget_tombstones ORDER BY created_at")
        out = []
        for r in rows:
            try:
                terms, removed = json.loads(r["terms"]), json.loads(r["removed"])
            except ValueError:
                terms, removed = [], {}
            out.append({"id": r["id"], "label": r["label"], "terms": terms if isinstance(terms, list) else [],
                        "created_at": r["created_at"], "removed": removed if isinstance(removed, dict) else {}})
        return out

    def public(self) -> list[dict[str, Any]]:
        """For the apps: what is kept out of memory, and how much was removed (the terms stay on the server)."""
        return [{"id": x["id"], "label": x["label"], "created_at": x["created_at"], "removed": x["removed"]} for x in self.all()]

    def add(self, label: str, terms: list[str], removed: dict[str, int]) -> str:
        tid = "f" + secrets.token_hex(5)
        self.store._run("INSERT INTO forget_tombstones(id, label, terms, created_at, removed) VALUES (?,?,?,?,?)",
                        (tid, label[:120], json.dumps(terms, ensure_ascii=False), self.clock(), json.dumps(removed)))
        return tid

    def remove(self, tid: str) -> bool:
        """The owner lets Kelo write about it again."""
        return self.store._run("DELETE FROM forget_tombstones WHERE id = ?", (tid,)).rowcount > 0

    def block(self) -> str:
        """The note for a model request: what must not be taken, written, researched or brought up. Fenced (it is data, not rules)."""
        rows = self.all()
        if not rows:
            return ""
        lines = [f"- {x['label']} (also known as: {', '.join(x['terms'])})" for x in rows]
        return ("<forgotten_topics>\nThe owner asked Kelo to forget the topics below and never write them down again.\n"
                + fence("\n".join(lines), budget=4000) + "\n</forgotten_topics>")

    def notice(self) -> str:
        """One section of the agent's own prompt ("" when nothing is forgotten)."""
        rows = self.all()
        if not rows:
            return ""
        return ("## Forgotten topics\nThe owner asked you to forget these topics. Never save them to memory, notes, skills or goals, "
                "never research, schedule or bring them up yourself, and do not repeat them when you talk about forgetting. If the "
                "owner raises one again, help with what they ask but do not write it down; they can allow it again in Settings.\n"
                + "\n".join(f"- {x['label']}" for x in rows))

    def screener(self, call: Callable[[list[dict[str, Any]]], Awaitable[str]]) -> Callable[[list[str]], Awaitable[set[int]]]:
        """`screen(texts) -> indexes` of the texts that are about a forgotten topic, judged by the main model through `call(messages)`.
        Raises ScreenFailed when the answer is unusable (callers store nothing then). Costs nothing when nothing is forgotten."""
        async def screen(texts: list[str]) -> set[int]:
            if not texts or not self.all():
                return set()
            flagged: set[int] = set()
            for start in range(0, len(texts), BATCH):
                part = texts[start:start + BATCH]
                body = "\n".join(f"[{i}] {' '.join(x.split())[:SNIPPET]}" for i, x in enumerate(part))
                try:
                    raw = await call([{"role": "system", "content": _prompt("screen.md")},
                                      {"role": "user", "content": f"{self.block()}\n\n<texts>\n{fence(body, budget=20_000)}\n</texts>"}])
                except Exception as e:  # noqa: BLE001
                    raise ScreenFailed(str(e)) from e
                got = parse_object(raw)
                rows = got.get("about") if got else None
                if not isinstance(rows, list):
                    raise ScreenFailed("the screening answer was not in the expected form")
                flagged |= {start + int(i) for i in rows if isinstance(i, int) and 0 <= i < len(part)}
            return flagged
        return screen


# ------------------------------------------------------------------------------------------------------------------ the plan
def _short(text: str) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= SHOW else s[:SHOW - 1] + "…"


class Forget:
    """Finds, plans and deletes. `ask(system, user) -> dict | None` is the main-model JSON judge (model_json.ModelJson.ask).
    `schedules` (schedules.Schedules), `skills` (packages.Registry) and `recall` are optional: a place without its owner is skipped."""

    def __init__(self, store: Any, home: Home | None, ask: Callable[[str, str], Awaitable[dict[str, Any] | None]], *,
                 schedules: Any = None, skills: Any = None, recall: Any = None, upkeep: Any = None,
                 clock: Callable[[], float] = time.time):
        self.store, self.home, self.ask, self.schedules, self.skills, self.recall, self.upkeep = store, home, ask, schedules, skills, recall, upkeep
        self.clock = clock
        self.tombstones = Tombstones(store, clock)
        self._terms: list[str] = []

    # ---- finding candidates (a search with the owner's terms; the model decides what is about the subject)
    def _candidates(self, terms: list[str], *, before: float | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        def add(kind: str, ref: str, cat: str, text: str, guard: str | None = None, **extra: Any) -> None:
            out.append({"k": kind, "ref": ref, "cat": cat, "text": " ".join(text.split()), "guard": guard if guard is not None else sha(text), **extra})

        for r in self.store._all("SELECT id, content, created_at FROM messages ORDER BY id"):
            if before is not None and float(r["created_at"] or 0) > before:
                continue   # what is said after the plan (the owner's answer) is not part of it
            if hits(r["content"], terms):
                add("msg", str(r["id"]), "memory", r["content"])   # searched only to reach what came of it; never deleted
        try:
            sm = self.store._all("SELECT summary FROM conversation_summary WHERE id = 1")
        except Exception:  # noqa: BLE001
            sm = []
        if sm and hits(sm[0]["summary"], terms):
            add("summary", "1", "memory", sm[0]["summary"])
        for r in self.store._all("SELECT claim_id, text, quote FROM memory_claims"):
            if hits(r["text"] + " " + r["quote"], terms):
                add("claim", r["claim_id"], "memory", r["text"], guard=sha(r["text"] + r["quote"]))
        for table, cols, cat in ROW_TABLES:
            try:
                rows = self.store._all(f"SELECT rowid AS rid, {', '.join(cols)} FROM {table}")
            except Exception:  # noqa: BLE001   (a table this server has not made)
                continue
            for r in rows:
                text = " · ".join(str(r[c] or "") for c in cols if r[c])
                if text and hits(text, terms):
                    add("row", f"{table}:{r['rid']}", cat, text)
        self._file_candidates(terms, add)
        if self.schedules is not None:
            for s in [*self.schedules.list(), *self.schedules.archived()]:
                if hits(f"{s.get('name', '')} {s.get('prompt', '')}", terms):
                    add("schedule", str(s["id"]), "reminders", f"{s.get('name', '')} · {s.get('prompt', '')}")
        if self.skills is not None:
            try:
                for sid in sorted(self.skills.custom_ids()):
                    body = "\n".join(self.skills.custom_files(sid).values())
                    if hits(body + " " + sid, terms):
                        add("skill", sid, "skills", f"{sid}: {body}")
            except Exception:  # noqa: BLE001
                log.warning("forget: the skills could not be read", exc_info=True)
        return out

    def _text_files(self) -> list[str]:
        if self.home is None:
            return []
        rels = [r for r in (*CORE, *SIDE_FILES) if (self.home.root / r).is_file()]
        for d in WRITABLE_DIRS:
            base = self.home.root / d
            if base.is_dir() and not base.is_symlink():
                for p in sorted(base.rglob("*")):
                    rel = p.relative_to(self.home.root).as_posix()
                    if p.is_file() and not p.is_symlink() and p.suffix.lower() in ALLOWED_SUFFIXES and not any(x.startswith(".") for x in rel.split("/")):
                        rels.append(rel)
        return rels

    def _read(self, rel: str) -> str:
        p = self.home.root / rel   # type: ignore[union-attr]
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")

    def _file_candidates(self, terms: list[str], add: Callable[..., None]) -> None:
        for rel in self._text_files():
            for line in self._read(rel).splitlines():
                if line.strip() and hits(line, terms):
                    add("line", rel, "history" if rel.startswith("memory/log/") else "notes" if rel.startswith("notes/") else "memory",
                        line, guard=sha(line))

    # ---- judging
    async def _ask_long(self, system: str, user: str) -> dict[str, Any] | None:
        """One judging request with room for a whole batch's answer (BATCH indexes plus `mixed`)."""
        import inspect
        try:
            takes = "max_tokens" in inspect.signature(self.ask).parameters
        except (TypeError, ValueError):
            takes = False
        return await (self.ask(system, user, max_tokens=JUDGE_TOKENS) if takes else self.ask(system, user))

    async def _judge(self, subject: str, terms: list[str], cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The candidates the main model says are about the subject (the others only shared a word). Raises ForgetError when it cannot."""
        keep: list[dict[str, Any]] = []
        for start in range(0, len(cands), BATCH):
            part = cands[start:start + BATCH]
            body = "\n".join(f"[{i}] ({c['cat']}) {c['text'][:SNIPPET]}" for i, c in enumerate(part))
            got = await self._ask_long(_prompt("select.md"),
                                       f"Subject the owner wants forgotten: {subject}\nTerms searched: {', '.join(terms)}\n\n<items>\n{fence(body, budget=30_000)}\n</items>")
            rows = got.get("about") if got else None
            if not isinstance(rows, list):
                raise ForgetError("the AI model could not judge the matches; nothing was planned. Try again in a moment.")
            mixed = {x for x in (got.get("mixed") if isinstance(got.get("mixed"), list) else []) if isinstance(x, int)}
            for i in dict.fromkeys(x for x in rows if isinstance(x, int) and 0 <= x < len(part)):
                if i in mixed and part[i]["k"] == "row" and part[i]["ref"].split(":")[0] not in WHOLE_ONLY:
                    part[i]["mixed"] = True   # other content shares the row: only the subject's part is rewritten out
                keep.append(part[i])
        return keep

    # ---- derived items: what only exists because of an item that goes
    def _derived(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Claims that only exist because of an item that goes (cited from a removed message, or linked to a removed claim by the
        supersession chain), and the dated log lines of every removed claim. Found by structure, never by words."""
        have = {(i["ref"], i["guard"]) for i in items if i["k"] == "line"}
        gone_msgs = {i["ref"] for i in items if i["k"] == "msg"}
        claims = {i["ref"] for i in items if i["k"] == "claim"}
        rows = self.store._all("SELECT claim_id, handle, supersedes, text, quote FROM memory_claims")
        extra: list[dict[str, Any]] = []
        grew = True
        while grew:
            grew = False
            for r in rows:
                cid = r["claim_id"]
                if cid in claims:
                    continue
                cited = r["handle"].startswith("m") and r["handle"][1:] in gone_msgs
                chain = r["supersedes"] in claims or any(x["claim_id"] in claims and x["supersedes"] == cid for x in rows)
                if cited or chain:
                    claims.add(cid)
                    grew = True
                    extra.append({"k": "claim", "ref": cid, "cat": "memory", "text": r["text"], "guard": sha(r["text"] + r["quote"]), "derived": True})
        if claims and self.home is not None:
            for rel in self._text_files():
                if rel.startswith("memory/log/"):
                    for line in self._read(rel).splitlines():
                        m = re.match(r"\s*-\s*claim:(c[0-9a-f]+)\b", line)
                        if m and m.group(1) in claims and (rel, sha(line)) not in have:
                            extra.append({"k": "line", "ref": rel, "cat": "history", "text": line, "guard": sha(line), "derived": True})
                            have.add((rel, sha(line)))
        return extra

    # ---- the plan
    async def plan(self, subject: str, terms: Any) -> dict[str, Any]:
        """Search, judge, keep the plan. Returns {"plan_id", "counts": {category: n}, "total"} or raises ForgetError."""
        subject = " ".join(str(subject or "").split())[:120]
        words = clean_terms(subject, terms)
        if not subject or not words:
            raise ForgetError("say in a few words what to forget, and give the words or names it may be stored under")
        self._purge_old()
        cands = self._candidates(words, before=self.clock())
        if len(cands) > MAX_CANDIDATES:
            raise ForgetError("those words match too many places to be about one thing; give more specific words or names")
        chosen = await self._judge(subject, words, cands) if cands else []
        items = [*chosen, *self._derived(chosen)]
        items = [i for i in items if i["k"] != "msg"]   # the visible chat is not a cleanup target: its messages stay (docs/forget.md)
        counts = {c: sum(1 for i in items if i["cat"] == c) for c in CATEGORIES}
        counts = {c: n for c, n in counts.items() if n}
        pid = "p" + secrets.token_hex(4)
        keep = [{k: v for k, v in i.items() if k != "text"} | {"show": _short(i["text"])} for i in items]
        self.store._run("INSERT INTO forget_plans(id, label, terms, items, created_at, state) VALUES (?,?,?,?,?, 'open')",
                        (pid, subject, json.dumps(words, ensure_ascii=False), json.dumps(keep, ensure_ascii=False), self.clock()))
        return {"plan_id": pid, "counts": counts, "total": len(items)}

    def _purge_old(self) -> None:
        self.store._run("DELETE FROM forget_plans WHERE created_at < ?", (self.clock() - PLAN_TTL,))

    def get_plan(self, pid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT id, label, terms, items, created_at, state FROM forget_plans WHERE id = ?", (str(pid),))
        if not rows or self.clock() - float(rows[0]["created_at"]) > PLAN_TTL or rows[0]["state"] != "open":
            return None
        r = rows[0]
        return {"id": r["id"], "label": r["label"], "terms": json.loads(r["terms"]), "items": json.loads(r["items"]), "created_at": r["created_at"]}

    def plan_view(self, pid: str) -> dict[str, Any] | None:
        """For the apps' list: every item with its everyday category and a short preview of the owner's own text."""
        p = self.get_plan(pid)
        if p is None:
            return None
        return {"id": p["id"], "label": p["label"], "items": [{"category": i["cat"], "show": i.get("show", "")} for i in p["items"]]}

    def card(self, pid: str) -> str:
        """The owner's card text, in the owner's language (at most about 300 characters)."""
        p = self.get_plan(pid)
        if p is None:
            return ""
        counts = {c: sum(1 for i in p["items"] if i["cat"] == c) for c in CATEGORIES}
        found = "; ".join(f"{t(CATEGORY_LABELS[c])}: {n}" for c, n in counts.items() if n) or "0"
        return " ".join([t("Forget for good: “{subject}”. This cannot be undone, and Kelo will not write it down again.", subject=p["label"][:40]),
                         t("Found: {found}.", found=found),
                         t("Copies elsewhere (backups, other services, other people) are not touched.")])

    # ---- doing it
    async def confirm(self, pid: str) -> dict[str, Any]:
        """After the owner's yes: re-check each item, delete, remove older copies, tombstone, verify. Returns the counts and whether the
        check afterwards found nothing left."""
        plan = self.get_plan(pid)
        if plan is None:
            raise ForgetError("that plan is gone or too old; make a new plan first")
        self.store._run("UPDATE forget_plans SET state = 'running' WHERE id = ?", (pid,))
        self._terms = plan["terms"]
        removed = {c: 0 for c in CATEGORIES}
        changed = 0
        lines: dict[str, set[str]] = {}   # file -> sha of the lines to take out
        texts: list[str] = []             # exact text of what went (used to find older copies of it)
        claim_ids: list[str] = []
        order = {"schedule": 0, "row": 1, "summary": 3, "claim": 4, "line": 5, "skill": 6}
        for it in sorted(plan["items"], key=lambda i: order.get(i["k"], 9)):
            try:
                n = await self._do(it, lines, texts, claim_ids, plan["terms"])
            except Exception:  # noqa: BLE001
                log.warning("forget: an item could not be removed", exc_info=True)
                n = -1
            if n > 0:
                removed[it["cat"]] += n
            elif n == 0:
                changed += 1   # changed since the plan: left alone, the owner asks again
        self._edit_files(lines, texts)
        copies = self._purge_copies(texts)
        self._tidy_state(claim_ids)
        if self.recall is not None:
            try:
                self.recall.refresh()
            except Exception:  # noqa: BLE001
                log.warning("forget: the recall index could not be refreshed", exc_info=True)
        left = sum(1 for it in plan["items"] if self._present(it))
        done = {c: n for c, n in removed.items() if n}
        self.tombstones.add(plan["label"], plan["terms"], done)   # whatever was left alone: the owner said never again
        self._scrub_traces(plan["terms"])
        self.store._run("DELETE FROM forget_plans WHERE id = ?", (pid,))
        return {"removed": done, "older_copies": copies, "changed_since_plan": changed, "left": left, "verified": left == 0}

    async def _do(self, it: dict[str, Any], lines: dict[str, set[str]], texts: list[str], claim_ids: list[str], terms: list[str]) -> int:
        """Remove one item. Returns the number of things removed; 0 when it changed since the plan (left alone)."""
        k, ref = it["k"], it["ref"]
        if k == "summary":
            self.store._run("UPDATE conversation_summary SET summary = '', up_to_message_id = 0 WHERE id = 1")
            return 1
        if k == "claim":
            r = self.store._all("SELECT text, quote FROM memory_claims WHERE claim_id = ?", (ref,))
            if not r:
                return 1
            if sha(r[0]["text"] + r[0]["quote"]) != it["guard"]:
                return 0
            texts += [r[0]["text"]]
            claim_ids.append(ref)
            self.store._run("DELETE FROM memory_claims WHERE claim_id = ?", (ref,))
            return 1
        if k == "row":
            table, _, rid = ref.partition(":")
            if table not in ROW_SPEC or not rid.isdigit():
                return -1
            cols = ROW_SPEC[table][0]
            r = self.store._all(f"SELECT {', '.join(cols)} FROM {table} WHERE rowid = ?", (int(rid),))
            if not r:
                return 1
            text = " · ".join(str(r[0][c] or "") for c in cols if r[0][c])
            if sha(text) != it["guard"]:
                return 0
            texts.append(text)
            if it.get("mixed"):
                return await self._rewrite_row(table, int(rid), cols, terms)
            if table == "goals":
                for child in GOAL_CHILDREN:
                    if self._has(child):
                        self.store._run(f"DELETE FROM {child} WHERE goal_id = (SELECT id FROM goals WHERE rowid = ?)", (int(rid),))
            if table == "watchers" and self._has("watch_events"):
                self.store._run("DELETE FROM watch_events WHERE watcher_id = (SELECT id FROM watchers WHERE rowid = ?)", (int(rid),))
            self.store._run(f"DELETE FROM {table} WHERE rowid = ?", (int(rid),))
            return 1
        if k == "line":
            lines.setdefault(ref, set()).add(it["guard"])
            return 1
        if k == "schedule" and self.schedules is not None:
            s = self.schedules.get(int(ref)) or next((x for x in self.schedules.archived() if str(x["id"]) == ref), None)
            if s is None:
                return 1
            if sha(f"{s.get('name', '')} · {s.get('prompt', '')}") != it["guard"]:
                return 0
            texts.append(str(s.get("prompt") or ""))
            return 1 if self.schedules.erase(int(ref)) else 0
        if k == "skill" and self.skills is not None:
            return 1 if self.skills.remove_custom(ref) else 0
        return -1

    async def _rewrite_row(self, table: str, rid: int, cols: tuple[str, ...], terms: list[str]) -> int:
        """A row with other content: only the subject's part is taken out of each text column that holds it (the main model rewrites
        the column); a row left empty goes. The model failing leaves the row as it is (0 = left alone)."""
        row = self.store._all(f"SELECT {', '.join(cols)} FROM {table} WHERE rowid = ?", (rid,))[0]
        new: dict[str, str] = {}
        for c in cols:
            val = str(row[c] or "")
            if not val or not hits(val, terms):
                continue
            got = await self.ask(_prompt("rewrite.md"), f"Terms of the subject: {', '.join(terms)}\n\n<text>\n{fence(val, budget=20_000)}\n</text>")
            out = got.get("text") if got else None
            if not isinstance(out, str) or hits(out, terms):
                return 0
            new[c] = out.strip()
        if not new:
            return 0
        if all(not (new.get(c) if c in new else str(row[c] or "")).strip() for c in cols):
            self.store._run(f"DELETE FROM {table} WHERE rowid = ?", (rid,))
            return 1
        self.store._run(f"UPDATE {table} SET {', '.join(c + ' = ?' for c in new)} WHERE rowid = ?", (*new.values(), rid))
        return 1

    def _has(self, table: str) -> bool:
        return bool(self.store._all("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)))

    def _edit_files(self, lines: dict[str, set[str]], texts: list[str]) -> None:
        """Take the picked lines out of each file (a whole file goes when nothing else is in it and it is not a core file)."""
        for rel, guards in lines.items():
            try:
                old = self._read(rel)
            except OSError:
                continue
            keep, gone = [], []
            for line in old.splitlines():
                (gone if line.strip() and sha(line) in guards else keep).append(line)
            if not gone:
                continue
            texts += [x.strip() for x in gone]
            body = [x for x in keep if x.strip() and not x.lstrip().startswith("#")]
            target = self.home.root / rel   # type: ignore[union-attr]
            if not body and rel not in CORE and rel not in SIDE_FILES:
                target.unlink(missing_ok=True)
            else:
                try:
                    self.home.write(rel, "\n".join(keep) + ("\n" if keep else ""), by="system")   # type: ignore[union-attr]
                except HomeError:
                    target.write_text("\n".join(keep) + "\n", encoding="utf-8")

    def _purge_copies(self, texts: list[str]) -> int:
        """Older saved versions that still hold a removed text: the file's history, a memory pass's backup, the trash. A whole pass
        backup goes (the pass can then no longer be undone)."""
        if self.home is None:
            return 0
        needles = [x.strip() for x in texts if len(x.strip()) >= 6][:200]
        if not needles:
            return 0
        n = 0
        for d in HIDDEN_COPIES:
            base = self.home.root / d
            if not base.is_dir() or base.is_symlink():
                continue
            if d in (".memory-passes", ".trash"):
                for entry in sorted(base.iterdir()):
                    if entry.is_dir() and not entry.is_symlink() and self._holds(entry, needles):
                        shutil.rmtree(entry, ignore_errors=True)
                        n += 1
            else:
                for p in sorted(base.rglob("*")):
                    if p.is_file() and not p.is_symlink() and self._holds(p, needles):
                        p.unlink(missing_ok=True)
                        n += 1
        return n

    @staticmethod
    def _holds(path: Path, needles: list[str]) -> bool:
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file() and not p.is_symlink()]
        for p in files:
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                body = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(x in body for x in needles):
                return True
        return False

    def _tidy_state(self, claim_ids: list[str]) -> None:
        """What the memory pass kept about the removed claims: its wait list, and the approval of this very request."""
        try:
            waiting = json.loads(self.store.meta("memory:unreconciled") or "[]")
        except ValueError:
            waiting = []
        if isinstance(waiting, list) and claim_ids:
            self.store.set_meta("memory:unreconciled", json.dumps([x for x in waiting if x not in set(claim_ids)]))
        self.store._run("UPDATE approval_log SET detail = '' WHERE tool = 'forget_confirm'")

    def _scrub_traces(self, terms: list[str]) -> None:
        """The reply log keeps tool calls: the plan's own call carried the subject. Rows of finished replies that hold it go too."""
        try:
            for r in self.store._all("SELECT rowid AS rid, ev FROM turn_events"):
                if hits(r["ev"], terms):
                    self.store._run("DELETE FROM turn_events WHERE rowid = ?", (r["rid"],))
        except Exception:  # noqa: BLE001
            pass

    def _present(self, it: dict[str, Any]) -> bool:
        """Is this planned item still there (unchanged)? The verification afterwards."""
        k, ref = it["k"], it["ref"]
        if k == "claim":
            return bool(self.store._all("SELECT 1 FROM memory_claims WHERE claim_id = ?", (ref,)))
        if k == "row":
            table, _, rid = ref.partition(":")
            if table not in ROW_SPEC or not rid.isdigit():
                return False
            got = self.store._all(f"SELECT {', '.join(ROW_SPEC[table][0])} FROM {table} WHERE rowid = ?", (int(rid),))
            if it.get("mixed"):   # rewritten, not deleted: it is "left" only while the subject is still in it
                return bool(got) and hits(" ".join(str(v or "") for v in got[0].values()), self._terms)
            return bool(got)
        if k == "line":
            return self.home is not None and any(line.strip() and sha(line) == it["guard"] for line in self._read(ref).splitlines())
        if k == "schedule" and self.schedules is not None:
            return self.schedules.get(int(ref)) is not None or any(str(x["id"]) == ref for x in self.schedules.archived())
        if k == "skill" and self.skills is not None:
            return ref in self.skills.custom_ids()
        return False


# ------------------------------------------------------------------------------------------------------------------ the tools
def forget_tools(forget: Forget) -> list[Any]:
    """forget_plan and forget_confirm (loaded on demand, never remembered: every forget asks the owner)."""
    from .agent.loop import Ours, Tool

    async def forget_plan(args: dict[str, Any]) -> str:
        try:
            got = await forget.plan(str(args.get("subject") or ""), args.get("terms"))
        except ForgetError as e:
            return Ours(f"error: {e}")
        if not got["total"]:
            return Ours("Nothing stored on this server matches that subject, so there is nothing to forget. Say so in one short line. "
                        "Do not call forget_confirm.")
        parts = ", ".join(f"{CATEGORY_LABELS[c]}: {n}" for c, n in got["counts"].items())
        return Ours(f"Plan {got['plan_id']} is ready ({got['total']} places: {parts}). Next call forget_confirm with plan_id {got['plan_id']}: "
                    "the owner sees one card with these categories and answers yes or no, so do not ask the same question in the chat. "
                    "Speak of it only as 'that information'; never write the subject in notes, memory, skills or your answer.")

    async def forget_confirm(args: dict[str, Any]) -> str:
        try:
            got = await forget.confirm(str(args.get("plan_id") or ""))
        except ForgetError as e:
            return Ours(f"error: {e}")
        say = ", ".join(f"{CATEGORY_LABELS[c]}: {n}" for c, n in got["removed"].items()) or "nothing"
        lines = [f"Forgotten ({say})."]
        if got["older_copies"]:
            lines.append(f"{got['older_copies']} older saved copies were removed too (this also ends undo for the memory pass that held them).")
        if got["changed_since_plan"] or got["left"]:
            lines.append("Some places changed after the plan and were left alone; offer to plan again.")
        else:
            lines.append("A check afterwards found nothing left. Kelo will not write it down again.")
        lines.append("Not touched: copies outside Kelo (backups the owner made, other services, other people), and the owner's own uploaded files. "
                     "Tell the owner that in one short line. Refer to the subject only as 'that information'.")
        return Ours(" ".join(lines))

    async def describe(args: dict[str, Any]) -> str:
        return forget.card(str(args.get("plan_id") or ""))

    def validate(args: dict[str, Any]) -> str | None:
        return None if forget.get_plan(str(args.get("plan_id") or "")) else "that plan is gone or too old; make a new plan with forget_plan"

    return [
        Tool("forget_plan",
             "Use ONLY when the owner explicitly asks Kelo to forget or erase information it stored about a topic (memory, notes, "
             "reminders, saved skills). Not for cancelling a task. Finds every place the topic lives and prepares one cleanup plan; deletes "
             "nothing. `subject`: a short neutral description. `terms`: the words, names and spellings (any language) the copies may "
             "contain. Then call forget_confirm.",
             {"type": "object", "properties": {"subject": {"type": "string", "description": "short neutral description of what to forget"},
                                               "terms": {"type": "array", "items": {"type": "string"},
                                                         "description": "words, names, spellings the stored copies may contain (up to 12)"}},
              "required": ["subject", "terms"]},
             forget_plan),
        Tool("forget_confirm",
             "After forget_plan: delete everything in the plan. The owner gets one approval card; deleting cannot be undone and Kelo will "
             "not write the topic down again. Never call it without a fresh plan for this request.",
             {"type": "object", "properties": {"plan_id": {"type": "string", "description": "the id forget_plan returned"}}, "required": ["plan_id"]},
             forget_confirm, requires_approval=True, describe=describe, validate=validate,
             declined="The owner said no. Nothing was deleted. Tell them in one short line."),
    ]
