"""Goals as a workspace the agent works in, not just a line in a list.

A goal (table goals in store.py: title, tag, note, status) gets, here:
  facts        what the agent learned about the user for this goal: key -> value, or "skipped" when the user chose not to say
  steps        the plan: ordered steps with a status (todo | doing | done), an owner (user | agent) and an optional due date
  metrics      numbers the agent decided to follow for this goal (a name, a unit, an optional start and target) with their history
  log          what happened along the way (the agent's notes, steps done, facts learned, values added), newest first
  documents    files in the user's library (or notes) that were made for the goal
  schedules    jobs that serve the goal (schedules.goal_id, schedules.py)
Nothing here knows what any goal is about: what to ask, what to plan and what to track is the model's decision, every time.

Kickoff: when a goal is added (in the app, or by accepting a suggestion) the server starts a turn of the main agent with KICKOFF (a
hidden message led by GOAL_MARK, left out of GET /api/messages, answered in the chat like a job's report). The agent asks the few
facts it needs in one message, with tap-to-answer buttons (quick_replies) where they help and a way to skip anything personal, then
proposes a plan. Answers come back as ordinary chat messages; the goal is shown in every turn's prompt as "waiting for your answers"
until the agent saves facts or a plan for it (or a few days pass), so the answer is routed back to that goal. "Continue" (the app's
button, POST /api/goals/{id}/work) starts the same kind of turn with RESUME.

Quick replies: a chat message may carry questions with answers to tap (table message_replies, by message id). They are attached to
the reply the turn ends with, shown under it by the app while no newer user message exists (GET /api/messages adds "replies":
{"questions": [{"question", "options": [str], "typed"?: [str], "hint"?: str}], "goal_id"}). "typed": the options that mean the user
writes the answer ("Other", "I'll type it"; the model's mark or asks_typing): picking one opens a text box (placeholder "hint", else
the question) and the typed words are sent instead of the label. The apps also add their own "Other" (a text box) to every choice
question that has no typed option.
"kind" (left out = choice): text | number ("min"?, "max"?, "unit"?) | date | time | datetime | weekdays | weekdays_time; each shows the
device's own picker for that kind of value, "default"? (its plain form, check_answer) is where the picker starts. The app sends a
localized sentence as the chat message and, with it, "answers": [{"question": index, "value": plain form}] (POST /api/turns,
/api/talk); the server checks every value against the open question (422 when one does not fit) and adds the plain values to what the
model reads, so a date or a day is never guessed from words.

Suggestions: the nightly look-back (reflection.py) may propose up to SUGGEST_PER_NIGHT goals grounded in what the user said, each with
the reason and the evidence; the agent can also suggest one during a conversation (goal_suggest). The app shows the proposed ones at the
top of the goals tab; accepting one adds the goal (and starts its kickoff), dismissing one keeps it so the same goal is never offered
again.

GOALS API (also next to serialize() in server.py):
Goal (GET /api/goals items): {"id", "title", "category", "note", "status": tracking|done, "created_at", "updated_at", "origin":
  user|suggestion|agent, "plan": {"done", "total"}, "next_step": str|null, "asking": bool (the agent asked for this goal, nothing runs
  for it and the user has not written since: waiting for the user's answers), "working": bool (a reply or job on this goal is running),
  "schedules_on": int (linked schedules that are on: the apps ask "delete them too?" before deleting the goal; 0 = no question)}
POST /api/goals {"title", "category"?, "note"?, "kickoff"?: true}   201 Goal; with kickoff (default) the agent starts on it
GET  /api/goals/{id}   {"goal": Goal, "facts": [Fact], "steps": [Step], "metrics": [Metric], "log": [Log], "documents": [Doc],
       "schedules": [Schedule]}   404 no such goal
  Fact {"key", "value", "skipped": bool, "source": user|agent, "updated_at"}
  Step {"id", "title", "detail", "status": todo|doing|done, "owner": user|agent, "due": "YYYY-MM-DD"|null, "done_at"}
  Metric {"id", "name", "unit", "start": float|null, "target": float|null, "values": [{"value", "note", "at"}] (oldest first, last 60)}
  Log {"id", "at", "text", "kind": note|step|fact|metric|plan|kickoff|user, "by": agent|user}
  Doc {"id", "path", "title", "added_at", "exists": bool}
PATCH /api/goals/{id}/steps/{step_id} {"status"}   Step   (the user ticks a step; 404 / 422)
PUT  /api/goals/{id}/facts/{key} {"value"?, "skipped"?}   Fact;  DELETE /api/goals/{id}/facts/{key}   {"ok"}
POST /api/goals/{id}/metrics/{metric_id}/values {"value", "note"?}   Metric
POST /api/goals/{id}/work   {"ok": true}   the agent picks the goal up again (409 {"detail"} when no model is set)
DELETE /api/goals/{id}?schedules=keep|delete   {"ok", "stopped_turns", "stopped_jobs", "deleted_schedules": [id], "kept_schedules": [id]}
  (older apps send pause for "turn them off too": the schedules are deleted all the same)
  the goal and what belongs to it go; the work started for it stops (its kickoff / resume turns, also one waiting its turn, and the
  jobs they started; their waiting approvals and hand-overs end as cancelled). Linked schedules: keep (default, older apps) = they keep
  running, taken off the goal; pause = paused like POST /api/schedules/{id}/pause (a run in progress stops). The apps ask first, and
  only when `schedules` of GET /api/goals/{id} is not empty. 404 no such goal, 422 another `schedules` value
GET  /api/goals/suggestions   {"items": [Suggestion]} the proposed ones, newest first
  Suggestion {"id", "title", "reason", "evidence", "source": nightly|chat, "created_at"}
POST /api/goals/suggestions/{id}/accept   201 Goal (kickoff starts);  POST /api/goals/suggestions/{id}/dismiss   {"ok"}
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import Tool, emit_ui
from .i18n import N_, t

GOAL_MARK = "[Goal work, not from the user]"   # leads the hidden message of a kickoff / resume turn
STEP_STATUSES = ("todo", "doing", "done")
OWNERS = ("user", "agent")
SUG_STATUSES = ("proposed", "accepted", "dismissed")
MAX_STEPS, MAX_FACTS, MAX_METRICS = 30, 30, 6
KEY_MAX, VALUE_MAX, TITLE_MAX, TEXT_MAX = 60, 300, 200, 1000
ASK_HOURS = 72                  # a goal shows as "waiting for your answers" at most this long after the questions
SUGGEST_PER_NIGHT = 3
SUGGEST_PROPOSED_MAX = 6        # proposed suggestions kept at once; the oldest go first
PROMPT_GOALS, PROMPT_CHARS = 8, 2_200
MAX_QUESTIONS, MAX_OPTIONS, OPTION_MAX, QUESTION_MAX = 4, 6, 60, 120
SAYS_MAX = 200                   # an option's whole sentence (quick_replies "says")
# what kind of answer a quick-reply question takes; the app shows the matching picker (choice: buttons, plus "Other" with a text box)
QUESTION_KINDS = ("choice", "text", "number", "date", "time", "datetime", "weekdays", "weekdays_time")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ANSWER_MAX = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS goal_facts (
    goal_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL DEFAULT '', skipped INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'agent', updated_at REAL NOT NULL, PRIMARY KEY (goal_id, key));
CREATE TABLE IF NOT EXISTS goal_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, pos INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'todo', owner TEXT NOT NULL DEFAULT 'user', due TEXT,
    done_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS goal_steps_goal ON goal_steps(goal_id);
CREATE TABLE IF NOT EXISTS goal_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '',
    start REAL, target REAL, created_at REAL NOT NULL, UNIQUE (goal_id, name));
CREATE TABLE IF NOT EXISTS goal_metric_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT, metric_id INTEGER NOT NULL, value REAL NOT NULL, note TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS goal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, at REAL NOT NULL, text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note', by TEXT NOT NULL DEFAULT 'agent');
CREATE INDEX IF NOT EXISTS goal_log_goal ON goal_log(goal_id);
CREATE TABLE IF NOT EXISTS goal_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL, UNIQUE (goal_id, path));
CREATE TABLE IF NOT EXISTS goal_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, norm TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'nightly', status TEXT NOT NULL DEFAULT 'proposed',
    goal_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS message_replies (
    message_id INTEGER PRIMARY KEY, questions TEXT NOT NULL, goal_id INTEGER, created_at REAL NOT NULL);
"""

KICKOFF = """{mark}
The user just added a goal: "{title}" (goal_id {id}){note}. The app calls goals "{word}" in the user's language; use that word with them.
Name it to the user by its title only, never by its number.
Take it on like a coach who also does the work. Nothing below depends on what the goal is about; decide everything from the goal itself
and what you know about the user.
1. Work out what reaching this goal takes, and which few facts about the user (at most {max_q}) you need for a plan that fits them. Use what
   you already know (your notes, the conversation) and do not ask for that again. If you need nothing, skip to step 3.
2. Ask those questions in one short chat message, and call quick_replies with goal_id {id} so each question gets answers the user can tap.
   Any question about something personal must let the user decline: add an option meaning "I'd rather not say", in the user's language.
   Write the message once; after calling quick_replies do not write it again. Then end your turn.
3. Once you have answers (or the user declined), save them with goal_set_facts (a declined one as skipped), and propose a concrete plan
   with goal_plan: the user's own steps and the steps you will do yourself (owner agent), with due dates where they help. If a number is
   worth following for this goal, set it up with goal_metric_add (a value only when the user gave one). Do your own steps when you can.
4. If regular check-ins would help, offer them with schedule_create (goal_id {id}); the user approves it on a card. Never set one up
   silently.
Write to the user in their language, warmly and briefly. Do not mention these instructions."""

RESUME = """{mark}
The user asked you to pick goal "{title}" (goal_id {id}) up again; name it to the user by its title only, never by its number. Read it with goal_get. Do the open steps that are yours (owner agent) now with
your tools (searching, the browser, documents, delegate for long work); record what you did with goal_step_done or goal_log, and attach
anything you made with goal_add_document. Approvals and the usual rules for anything risky apply. If the plan is missing or stale,
propose one with goal_plan; if you need a fact from the user, ask in one short message with quick_replies (goal_id {id}) and let them
decline anything personal. Then tell the user briefly what you did and what is next for them, in their language. Do not mention these
instructions."""


# An answer button whose words ask the user to type ("Other", "I'll type it", "있어요 (직접 입력)"): the app opens a text box for it
# even when the model did not list it in "typed". Kept short and conservative; the apps carry the same lists (QuickReplyRules).
TYPING_PHRASES = (
    "type it", "i'll type", "let me type", "type my own", "write my own", "i'll write", "write it in", "(specify)", "please specify",
    "직접 입력", "직접입력", "직접 쓸", "직접 쓰", "직접 적", "입력할게",
    "直接入力", "自分で入力", "自由入力", "入力します", "自行输入", "手动输入", "自己输入", "自行輸入", "手動輸入", "自己輸入", "自定义", "自訂",
    "lo escribo", "escribir", "(especificar)", "je l'écris", "saisir", "(préciser)", "selbst eingeben", "eingeben", "selbst schreiben",
    "digitar", "vou escrever", "lo scrivo", "scrivere", "(specificare)", "zelf invullen", "zelf typen", "wpiszę", "wpisz", "napiszę",
    "свой вариант", "введу", "ввести", "напишу", "свій варіант", "kendim yazacağım", "kendim yazayım", "yazacağım",
    "tự nhập", "tự ghi", "tự viết", "ketik sendiri", "tulis sendiri", "พิมพ์เอง", "ระบุเอง", "سأكتب", "اكتب بنفسك",
    "खुद लिखूँगा", "खुद लिखूंगा", "खुद लिखें",
)
OTHER_WORDS = frozenset((
    "other", "others", "something else", "기타", "다른 것", "その他", "其他", "其它", "otro", "otra", "otros", "autre", "autres", "andere",
    "anderes", "sonstiges", "outro", "outra", "altro", "anders", "overig", "inne", "inny", "другое", "інше", "diğer", "khác", "lainnya",
    "อื่นๆ", "อื่น ๆ", "أخرى", "غير ذلك", "अन्य",
))


def asks_typing(label: str) -> bool:
    """The answer means "I will write it myself": a typing phrase anywhere, or just "Other" (with an optional "(...)" or "…")."""
    s = " ".join(str(label or "").lower().split())
    if any(p in s for p in TYPING_PHRASES):
        return True
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip(" .…:!?·-–—")
    return s in OTHER_WORDS


def _check_date(v: str) -> str:
    try:
        return date.fromisoformat(v.strip()).isoformat()
    except ValueError:
        raise GoalError("a date is YYYY-MM-DD") from None


def _check_time(v: str) -> str:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", v.strip())
    if not m:
        raise GoalError("a time is 24-hour HH:MM")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _check_days(v: str) -> str:
    days = [d.strip().lower()[:3] for d in re.split(r"[,\s]+", v) if d.strip()]
    if not days or any(d not in WEEKDAYS for d in days):
        raise GoalError(f"days are a list of {', '.join(WEEKDAYS)}")
    return ",".join(sorted(set(days), key=WEEKDAYS.index))


def feature_name() -> str:
    """What the app calls goals in the user's language (the owner's app language in background turns)."""
    return t(N_("Goals"))


def _now() -> float:
    return time.time()


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def _clip(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:n]


_LOG_T = "⟦t⟧"   # goal_log.text that holds {"t": English template, "f": fields, "x": suffix}, translated when read
_LEGACY_LOG = (N_("Learned: {facts}"), N_("Made a plan of {n} steps"), N_("Attached: {title}"), N_("Done: {title}"))


def _log_text(text: str) -> str:
    if text.startswith(_LOG_T):
        try:
            d = json.loads(text[len(_LOG_T):])
            f = {k: ("; ".join(a + (t(" (not said)") if b is None else f" = {b}") for a, b in v) if isinstance(v, list) else v)
                 for k, v in (d.get("f") or {}).items()}
            return t(str(d["t"]), **f) + (f" ({d['x']})" if d.get("x") else "")
        except (ValueError, KeyError, TypeError, AttributeError):
            return text[len(_LOG_T):]
    for tmpl in _LEGACY_LOG:   # lines written before log_t(), in English: translate the label at least
        head = tmpl.split("{", 1)[0]
        if head.endswith(": ") and text.startswith(head):
            return t(tmpl).split("{", 1)[0] + text[len(head):]
    return text


def _day(v: Any) -> str | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        raise GoalError("due must be a date like 2026-11-20") from None


def _num(v: Any, name: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise GoalError(f"{name} must be a number") from None
    if f != f or f in (float("inf"), float("-inf")):
        raise GoalError(f"{name} must be a number")
    return f


def _fmt(f: float | None) -> str:
    if f is None:
        return "-"
    return str(int(f)) if float(f).is_integer() else f"{f:g}"


class GoalError(ValueError):
    pass


class GoalBook:
    def __init__(self, store: Any, home: Any = None, clock: Callable[[], float] = _now):
        self.store, self.home, self.clock = store, home, clock
        self.hidden_marks: tuple[str, ...] = (GOAL_MARK,)   # user-role messages that are not the user's words (the server adds its own)
        self.busy: Callable[[], set[int]] = set   # goals a running reply or job works on (the server sets it)
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(goals)")}
            for col, ddl in (("origin", "TEXT NOT NULL DEFAULT 'user'"), ("asking_at", "REAL")):
                if col not in have:
                    store._db.execute(f"ALTER TABLE goals ADD COLUMN {col} {ddl}")
            store._db.commit()

    # ------------------------------------------------------------------ goals
    def need(self, goal_id: Any) -> dict[str, Any]:
        try:
            gid = int(goal_id)
        except (TypeError, ValueError):
            raise GoalError("goal_id must be a goal's number") from None
        g = self.store.goal(gid)
        if not g:
            raise GoalError(f"there is no goal #{gid}")
        return g

    def touch(self, goal_id: int) -> None:
        self.store._run("UPDATE goals SET updated_at = ? WHERE id = ?", (self.clock(), goal_id))

    def asking(self, g: dict[str, Any]) -> bool:
        at = g.get("asking_at")
        return bool(at) and self.clock() - float(at) < ASK_HOURS * 3600

    def set_asking(self, goal_id: int, on: bool) -> None:
        self.store._run("UPDATE goals SET asking_at = ? WHERE id = ?", (self.clock() if on else None, goal_id))

    def written_since(self, at: float) -> int:
        """How many messages the user wrote in the chat after `at` (their own words, not hidden messages; counted up to 2)."""
        rows = self.store._all("SELECT content FROM messages WHERE role = 'user' AND created_at > ? ORDER BY id DESC LIMIT 20", (at,))
        return min(2, sum(not str(r["content"]).startswith(self.hidden_marks) for r in rows))

    def answered_since(self, at: float) -> bool:
        """The user wrote in the chat after `at`: the question's buttons are gone."""
        return self.written_since(at) > 0

    def answering(self) -> set[int]:
        """Goals whose questions the user's newest message most likely answers: asked, and that message is the first one since."""
        return {int(g["id"]) for g in self.store.goals()
                if g.get("status") == "tracking" and self.asking(g) and self.written_since(float(g["asking_at"])) <= 1}

    def public(self, g: dict[str, Any]) -> dict[str, Any]:
        """The app's Goal. "working": a reply or job on this goal is running now. "asking" (what the app shows as waiting for the
        user): the agent asked for this goal, nothing is running for it and the user has not written since."""
        steps = self.steps(g["id"])
        nxt = next((s for s in steps if s["status"] == "doing"), None) or next((s for s in steps if s["status"] == "todo"), None)
        out = {k: v for k, v in g.items() if k != "asking_at"}
        out["origin"] = g.get("origin") or "user"
        working = g.get("status") != "done" and g["id"] in self.busy()
        asking = not working and self.asking(g) and not self.answered_since(float(g["asking_at"]))
        return {**out, "plan": {"done": sum(s["status"] == "done" for s in steps), "total": len(steps)},
                "next_step": nxt["title"] if nxt else None, "asking": asking, "working": working,
                "schedules_on": self.schedules_on(g["id"])}

    def schedules_on(self, goal_id: int) -> int:
        """How many schedules serve this goal and are on (the apps ask whether to turn them off when the goal is deleted)."""
        try:
            return int(self.store._all("SELECT COUNT(*) AS n FROM schedules WHERE goal_id = ? AND enabled = 1 AND archived_at IS NULL",
                                       (goal_id,))[0]["n"])
        except Exception:  # noqa: BLE001 - a store without schedules
            return 0

    def listing(self) -> list[dict[str, Any]]:
        return [self.public(g) for g in self.store.goals()]

    def delete(self, goal_id: int) -> None:
        """What belongs to a goal goes with it (its schedules stay, taken off the goal)."""
        for sql in ("DELETE FROM goal_facts WHERE goal_id = ?", "DELETE FROM goal_steps WHERE goal_id = ?",
                    "DELETE FROM goal_metric_values WHERE metric_id IN (SELECT id FROM goal_metrics WHERE goal_id = ?)",
                    "DELETE FROM goal_metrics WHERE goal_id = ?", "DELETE FROM goal_log WHERE goal_id = ?",
                    "DELETE FROM goal_docs WHERE goal_id = ?"):
            self.store._run(sql, (goal_id,))
        try:
            self.store._run("UPDATE schedules SET goal_id = NULL WHERE goal_id = ?", (goal_id,))
        except Exception:  # noqa: BLE001 - a store without schedules
            pass

    # ------------------------------------------------------------------ facts
    def facts(self, goal_id: int) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT key, value, skipped, source, updated_at FROM goal_facts WHERE goal_id = ? ORDER BY updated_at, key",
                               (goal_id,))
        return [{**r, "skipped": bool(r["skipped"])} for r in rows]

    def set_fact(self, goal_id: int, key: str, value: str = "", skipped: bool = False, source: str = "agent") -> dict[str, Any]:
        key = _clip(key, KEY_MAX)
        if not key:
            raise GoalError("a fact needs a key")
        value = "" if skipped else _clip(value, VALUE_MAX)
        if not skipped and not value:
            raise GoalError(f"fact '{key}' needs a value, or skipped: true")
        known = {f["key"] for f in self.facts(goal_id)}
        if key not in known and len(known) >= MAX_FACTS:
            raise GoalError(f"a goal keeps at most {MAX_FACTS} facts")
        self.store._run("INSERT INTO goal_facts(goal_id, key, value, skipped, source, updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(goal_id, key) "
                        "DO UPDATE SET value = excluded.value, skipped = excluded.skipped, source = excluded.source, updated_at = excluded.updated_at",
                        (goal_id, key, value, int(skipped), source if source in ("user", "agent") else "agent", self.clock()))
        self.touch(goal_id)
        return next(f for f in self.facts(goal_id) if f["key"] == key)

    def delete_fact(self, goal_id: int, key: str) -> bool:
        return self.store._run("DELETE FROM goal_facts WHERE goal_id = ? AND key = ?", (goal_id, key)).rowcount > 0

    # ------------------------------------------------------------------ steps
    def steps(self, goal_id: int) -> list[dict[str, Any]]:
        return self.store._all("SELECT id, title, detail, status, owner, due, done_at FROM goal_steps WHERE goal_id = ? ORDER BY pos, id",
                               (goal_id,))

    def step(self, goal_id: int, step_id: Any) -> dict[str, Any]:
        try:
            sid = int(step_id)
        except (TypeError, ValueError):
            raise GoalError("step id must be a number") from None
        s = next((s for s in self.steps(goal_id) if s["id"] == sid), None)
        if s is None:
            raise GoalError(f"goal #{goal_id} has no step {sid}")
        return s

    def set_step_status(self, goal_id: int, step_id: Any, status: str, by: str = "agent", note: str = "") -> dict[str, Any]:
        s = self.step(goal_id, step_id)
        if status not in STEP_STATUSES:
            raise GoalError(f"status must be one of {', '.join(STEP_STATUSES)}")
        now = self.clock()
        self.store._run("UPDATE goal_steps SET status = ?, done_at = ?, updated_at = ? WHERE id = ?",
                        (status, now if status == "done" else None, now, s["id"]))
        if status == "done" and s["status"] != "done":
            self.log_t(goal_id, N_("Done: {title}"), "step", by, suffix=note, title=s["title"])
        self.touch(goal_id)
        return self.step(goal_id, s["id"])

    def plan(self, goal_id: int, steps: list[Any], replace: bool = False) -> list[dict[str, Any]]:
        """Adds or updates steps. An item with an `id` changes that step; one without adds a step at the end (or, with `replace`,
        the plan is rebuilt: steps not named by id go, done ones are kept)."""
        if not isinstance(steps, list) or not steps:
            raise GoalError("steps must be a non-empty list")
        current = {s["id"]: s for s in self.steps(goal_id)}
        keep: set[int] = set()
        rows: list[tuple[str, Any]] = []
        for item in steps[:MAX_STEPS]:
            if not isinstance(item, dict):
                raise GoalError("each step is an object with a title")
            sid = item.get("id")
            if sid not in (None, ""):
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    raise GoalError("step id must be a number") from None
                if sid not in current:
                    raise GoalError(f"goal #{goal_id} has no step {sid}")
            status = str(item.get("status") or (current[sid]["status"] if sid else "todo"))
            owner = str(item.get("owner") or (current[sid]["owner"] if sid else "user"))
            if status not in STEP_STATUSES:
                raise GoalError(f"status must be one of {', '.join(STEP_STATUSES)}")
            if owner not in OWNERS:
                raise GoalError("owner must be user or agent")
            title = _clip(item.get("title") or (current[sid]["title"] if sid else ""), TITLE_MAX)
            if not title:
                raise GoalError("each step needs a title")
            rows.append(("upd" if sid else "new", (sid, title, _clip(item.get("detail") if "detail" in item else (current[sid]["detail"] if sid else ""), 400),
                                                    status, owner, _day(item["due"]) if "due" in item else (current[sid]["due"] if sid else None))))
            if sid:
                keep.add(sid)
        now = self.clock()
        if replace:
            for sid, s in current.items():
                if sid not in keep and s["status"] != "done":
                    self.store._run("DELETE FROM goal_steps WHERE id = ?", (sid,))
        live = len(self.steps(goal_id))
        if live + sum(1 for kind, _ in rows if kind == "new") > MAX_STEPS:
            raise GoalError(f"a plan keeps at most {MAX_STEPS} steps")
        pos = len(self.steps(goal_id))
        for i, (kind, (sid, title, detail, status, owner, due)) in enumerate(rows):
            done_at = now if status == "done" else None
            if kind == "upd":
                if status == "done" and current[sid]["status"] == "done":
                    done_at = current[sid]["done_at"]
                self.store._run("UPDATE goal_steps SET title = ?, detail = ?, status = ?, owner = ?, due = ?, done_at = ?, updated_at = ?"
                                + (", pos = ?" if replace else "") + " WHERE id = ?",
                                (title, detail, status, owner, due, done_at, now, *((i,) if replace else ()), sid))
            else:
                self.store._run("INSERT INTO goal_steps(goal_id, pos, title, detail, status, owner, due, done_at, created_at, updated_at) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?)", (goal_id, i if replace else pos, title, detail, status, owner, due, done_at, now, now))
                pos += 1
        self.touch(goal_id)
        return self.steps(goal_id)

    # ------------------------------------------------------------------ metrics
    def metrics(self, goal_id: int) -> list[dict[str, Any]]:
        out = []
        for m in self.store._all("SELECT id, name, unit, start, target FROM goal_metrics WHERE goal_id = ? ORDER BY id", (goal_id,)):
            vals = self.store._all("SELECT value, note, at FROM goal_metric_values WHERE metric_id = ? ORDER BY at DESC, id DESC LIMIT 60",
                                   (m["id"],))
            out.append({**m, "values": vals[::-1]})
        return out

    def metric(self, goal_id: int, metric_id: int) -> dict[str, Any]:
        m = next((m for m in self.metrics(goal_id) if m["id"] == metric_id), None)
        if m is None:
            raise GoalError(f"goal #{goal_id} has no number {metric_id}")
        return m

    def metric_add(self, goal_id: int, name: str, value: Any = None, unit: str | None = None, start: Any = None, target: Any = None,
                   note: str = "", by: str = "agent") -> dict[str, Any]:
        name = _clip(name, 60)
        if not name:
            raise GoalError("name is required")
        v, st, tg = _num(value, "value"), _num(start, "start"), _num(target, "target")
        rows = self.store._all("SELECT id FROM goal_metrics WHERE goal_id = ? AND name = ?", (goal_id, name))
        if rows:
            mid = rows[0]["id"]
            sets = [(c, x) for c, x in (("unit", _clip(unit, 20) if unit is not None else None), ("start", st), ("target", tg)) if x is not None]
            for col, x in sets:
                self.store._run(f"UPDATE goal_metrics SET {col} = ? WHERE id = ?", (x, mid))
        else:
            if len(self.metrics(goal_id)) >= MAX_METRICS:
                raise GoalError(f"a goal follows at most {MAX_METRICS} numbers")
            mid = int(self.store._run("INSERT INTO goal_metrics(goal_id, name, unit, start, target, created_at) VALUES(?,?,?,?,?,?)",
                                      (goal_id, name, _clip(unit or "", 20), st, tg, self.clock())).lastrowid)
        if v is not None:
            self.store._run("INSERT INTO goal_metric_values(metric_id, value, note, at) VALUES(?,?,?,?)", (mid, v, _clip(note, 200), self.clock()))
            m = self.metric(goal_id, mid)
            self.log(goal_id, f"{name}: {_fmt(v)}{(' ' + m['unit']) if m['unit'] else ''}", "metric", by)
        self.touch(goal_id)
        return self.metric(goal_id, mid)

    # ------------------------------------------------------------------ log, documents
    def log(self, goal_id: int, text: str, kind: str = "note", by: str = "agent") -> dict[str, Any]:
        text = str(text or "").strip()[:TEXT_MAX]
        if not text:
            raise GoalError("text is required")
        lid = self.store._run("INSERT INTO goal_log(goal_id, at, text, kind, by) VALUES(?,?,?,?,?)",
                              (goal_id, self.clock(), text, kind, by if by in ("user", "agent") else "agent")).lastrowid
        self.store._run("DELETE FROM goal_log WHERE goal_id = ? AND id <= (SELECT MAX(id) FROM goal_log WHERE goal_id = ?) - 300", (goal_id, goal_id))
        return {"id": lid, "text": text, "kind": kind}

    def log_t(self, goal_id: int, template: str, kind: str, by: str = "agent", suffix: str = "", **fields: Any) -> dict[str, Any]:
        """A history line from an English template, kept untranslated and shown in the reader's language (`logs()`).
        Translating when written left lines in whatever language the writing task happened to run in."""
        f = {k: ([[_clip(a, KEY_MAX), None if b is None else _clip(b, VALUE_MAX)] for a, b in v][:30] if isinstance(v, list) else _clip(v, TITLE_MAX))
             for k, v in fields.items()}
        return self.log(goal_id, _LOG_T + json.dumps({"t": template, "f": f, "x": _clip(suffix, 300)}, ensure_ascii=False), kind, by)

    def logs(self, goal_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT id, at, text, kind, by FROM goal_log WHERE goal_id = ? ORDER BY id DESC LIMIT ?", (goal_id, limit))
        return [{**r, "text": _log_text(r["text"])} for r in rows]

    def _exists(self, path: str) -> bool:
        root = getattr(self.home, "root", None)
        if root is None:
            return True
        try:
            p = (root / path).resolve()
            return p.is_file() and p.is_relative_to(root.resolve())
        except (OSError, ValueError):
            return False

    def add_document(self, goal_id: int, path: str, title: str = "") -> dict[str, Any]:
        path = str(path or "").strip().lstrip("/")
        if not path or ".." in path.split("/"):
            raise GoalError("path must be a file in your files, like files/plan.pdf")
        if not self._exists(path):
            raise GoalError(f"there is no file {path}")
        title = _clip(title, TITLE_MAX) or path.rsplit("/", 1)[-1]
        self.store._run("INSERT INTO goal_docs(goal_id, path, title, added_at) VALUES(?,?,?,?) ON CONFLICT(goal_id, path) DO UPDATE SET "
                        "title = excluded.title", (goal_id, path, title, self.clock()))
        self.touch(goal_id)
        return {"path": path, "title": title}

    def documents(self, goal_id: int) -> list[dict[str, Any]]:
        return [{**d, "exists": self._exists(d["path"])}
                for d in self.store._all("SELECT id, path, title, added_at FROM goal_docs WHERE goal_id = ? ORDER BY id", (goal_id,))]

    def workspace(self, goal_id: int, schedules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        g = self.need(goal_id)
        return {"goal": self.public(g), "facts": self.facts(g["id"]), "steps": self.steps(g["id"]), "metrics": self.metrics(g["id"]),
                "log": self.logs(g["id"]), "documents": self.documents(g["id"]), "schedules": schedules or []}

    # ------------------------------------------------------------------ as text, for the model
    def text(self, goal_id: int, schedules: list[dict[str, Any]] | None = None) -> str:
        w = self.workspace(goal_id, schedules)
        g = w["goal"]
        out = [f"Goal #{g['id']}: {g['title']} [{g['status']}]" + (f" #{g['category']}" if g.get("category") else ""),
               f"Note: {g['note']}" if g.get("note") else "",
               "Waiting for the user's answers to your questions." if self.asking(self.need(goal_id)) else ""]
        out.append("Facts: " + ("; ".join(f"{f['key']} = " + ("(the user chose not to say)" if f["skipped"] else f["value"]) for f in w["facts"])
                                or "none yet"))
        out.append("Plan:" if w["steps"] else "Plan: none yet")
        out += [f"  {s['id']}. [{s['status']}] ({'you' if s['owner'] == 'agent' else 'user'}) {s['title']}" + (f" (due {s['due']})" if s["due"] else "")
                + (f": {s['detail']}" if s["detail"] else "") for s in w["steps"]]
        for m in w["metrics"]:
            vals = ", ".join(_fmt(v["value"]) for v in m["values"][-8:]) or "no values yet"
            out.append(f"Number '{m['name']}'{(' (' + m['unit'] + ')') if m['unit'] else ''}: start {_fmt(m['start'])}, target {_fmt(m['target'])}; recent: {vals}")
        if w["documents"]:
            out.append("Documents: " + "; ".join(f"{d['title']} ({d['path']})" for d in w["documents"]))
        if w["schedules"]:
            out.append("Schedules: " + "; ".join(f"#{s['id']} {s['name']} ({s.get('description', '')})" for s in w["schedules"]))
        if w["log"]:
            out.append("Recent log:")
            out += [f"  - {time.strftime('%Y-%m-%d', time.localtime(r['at']))} {r['text'][:200]}" for r in w["log"][:8]]
        return "\n".join(x for x in out if x)

    def for_prompt(self) -> list[dict[str, Any]]:
        """The goals being tracked, each with what the per-turn prompt shows (prompt.py turn_lines)."""
        out = []
        for g in [g for g in self.store.goals() if g.get("status") == "tracking"][:PROMPT_GOALS]:
            p = self.public(g)
            facts = self.facts(g["id"])
            steps = self.steps(g["id"])
            nxt = next((s for s in steps if s["status"] == "doing"), None) or next((s for s in steps if s["status"] == "todo"), None)
            out.append({**p, "asking": self.asking(g), "facts": facts[:6], "next_owner": nxt["owner"] if nxt else None})
        return out

    # ------------------------------------------------------------------ quick replies under a chat message
    @staticmethod
    def clean_questions(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise GoalError("questions must be a non-empty list of {question, options}")
        out = []
        for q in raw[:MAX_QUESTIONS]:
            if not isinstance(q, dict):
                raise GoalError("each question is an object with options")
            kind = _clip(q.get("kind"), 20).lower() or "choice"
            if kind not in QUESTION_KINDS:
                raise GoalError(f"kind must be one of {', '.join(QUESTION_KINDS)}")
            opts = [o for o in (_clip(o, OPTION_MAX) for o in (q.get("options") or []) if isinstance(o, (str, int, float))) if o]
            opts = list(dict.fromkeys(opts))[:MAX_OPTIONS]
            # "says": per option, the whole sentence the user sends by tapping it (the button keeps the short label); parallel to the
            # options as given, matched by label after cleaning, so a duplicate or dropped option never shifts the others
            said_by: dict[str, str] = {}
            raw_says = q.get("says") if isinstance(q.get("says"), list) else []
            for o, s in zip(q.get("options") or [], raw_says):
                label, sentence = _clip(o, OPTION_MAX), _clip(s, SAYS_MAX)
                if label and sentence and sentence != label:
                    said_by.setdefault(label, sentence)
            if kind == "choice" and len(opts) < 2:
                raise GoalError("a choice question needs at least two options (or give it another kind)")
            said = {_clip(o, OPTION_MAX) for o in (q.get("typed") or []) if isinstance(o, str)} if isinstance(q.get("typed"), list) else set()
            item: dict[str, Any] = {"question": _clip(q.get("question"), QUESTION_MAX), "options": opts}
            if kind != "choice":
                item["kind"] = kind   # the options stay for apps that only know buttons
                if kind == "number":
                    for k in ("min", "max"):
                        if q.get(k) not in (None, ""):
                            try:
                                item[k] = float(q[k])
                            except (TypeError, ValueError):
                                raise GoalError(f"{k} must be a number") from None
                    if "min" in item and "max" in item and item["min"] > item["max"]:
                        raise GoalError("min must not be above max")
                    if unit := _clip(q.get("unit"), 20):
                        item["unit"] = unit
                if q.get("default") not in (None, ""):
                    try:
                        item["default"] = GoalBook.check_answer(item, q["default"])
                    except GoalError as e:
                        raise GoalError(f"default: {e}") from None
            if typed := [o for o in opts if o in said or asks_typing(o)]:
                item["typed"] = typed   # answers that open a text box in the app; the typed words are sent instead of the label
            if kind == "choice" and said_by:
                item["says"] = [("" if o in item.get("typed", []) else said_by.get(o, "")) for o in opts]   # a typed answer sends what is typed
            if (typed or kind == "text") and (hint := _clip(q.get("hint"), QUESTION_MAX)):
                item["hint"] = hint
            out.append(item)
        return out

    @staticmethod
    def check_answer(q: dict[str, Any], value: Any) -> str:
        """An answer to question `q` (a cleaned one) in its plain form, or GoalError when it does not fit the question's kind:
        choice/text: the words (a choice may be any words: "Other" lets the user write their own); number: within min..max;
        date: YYYY-MM-DD; time: HH:MM (24-hour); datetime: YYYY-MM-DD HH:MM; weekdays: "mon,wed" (WEEKDAYS codes);
        weekdays_time: "mon,wed 12:00"."""
        kind = q.get("kind") or "choice"
        v = _clip(value, ANSWER_MAX)
        if not v:
            raise GoalError("the answer is empty")
        if kind in ("choice", "text"):
            return v
        if kind == "number":
            try:
                n = float(v.replace(",", ""))
            except ValueError:
                raise GoalError("not a number") from None
            if n != n or n in (float("inf"), float("-inf")):
                raise GoalError("not a number")
            if "min" in q and n < q["min"] or "max" in q and n > q["max"]:
                raise GoalError("out of range")
            return str(int(n)) if n == int(n) else repr(n)
        if kind == "date":
            return _check_date(v)
        if kind == "time":
            return _check_time(v)
        if kind == "datetime":
            day, _, hm = v.replace("T", " ").partition(" ")
            return f"{_check_date(day)} {_check_time(hm.strip())}"
        if kind == "weekdays":
            return _check_days(v)
        if kind == "weekdays_time":
            days, _, hm = v.rpartition(" ")
            return f"{_check_days(days)} {_check_time(hm)}"
        raise GoalError("unknown kind")

    def open_questions(self) -> list[dict[str, Any]]:
        """The questions under the newest agent message that still takes answers (no user message after it), else []."""
        rows = self.store._all("SELECT id FROM messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1")
        if not rows:
            return []
        shown = self.replies_for([{"id": rows[0]["id"], "role": "assistant"}])
        return list((shown[0].get("replies") or {}).get("questions") or [])

    def attach_replies(self, message_id: int, questions: list[dict[str, Any]], goal_id: int | None) -> None:
        self.store._run("INSERT OR REPLACE INTO message_replies(message_id, questions, goal_id, created_at) VALUES(?,?,?,?)",
                        (message_id, json.dumps(questions, ensure_ascii=False), goal_id, self.clock()))

    def replies_for(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """GET /api/messages rows with "replies" ({"questions", "goal_id"}) on agent messages no user message came after."""
        if not rows:
            return rows
        ids = [m["id"] for m in rows if m["role"] == "assistant"]
        if not ids:
            return rows
        marks = ",".join("?" * len(ids))
        found = {r["message_id"]: r for r in self.store._all(f"SELECT * FROM message_replies WHERE message_id IN ({marks})", tuple(ids))}
        if not found:
            return rows
        last_user = next((r["id"] for r in self.store._all("SELECT id, content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 50")
                          if not r["content"].startswith(self.hidden_marks)), 0)
        out = []
        for m in rows:
            r = found.get(m["id"])
            if r and m["id"] > last_user:
                try:
                    m = {**m, "replies": {"questions": json.loads(r["questions"]), "goal_id": r["goal_id"]}}
                except ValueError:
                    pass
            out.append(m)
        return out

    # ------------------------------------------------------------------ suggestions
    def suggestions(self, statuses: tuple[str, ...] = ("proposed",)) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(statuses))
        return self.store._all(f"SELECT id, title, reason, evidence, source, status, goal_id, created_at FROM goal_suggestions "
                               f"WHERE status IN ({marks}) ORDER BY id DESC", statuses)

    def suggest(self, title: str, reason: str = "", evidence: str = "", source: str = "nightly") -> int | None:
        """A new proposed suggestion, or None: empty, already a goal, or already suggested (proposed, accepted or dismissed)."""
        title = _clip(title, 120)
        norm = _norm(title)
        if len(norm) < 2:
            return None
        if norm in {r["norm"] for r in self.store._all("SELECT norm FROM goal_suggestions")}:
            return None
        if norm in {_norm(g["title"]) for g in self.store.goals()}:
            return None
        now = self.clock()
        sid = int(self.store._run("INSERT INTO goal_suggestions(title, norm, reason, evidence, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                                  (title, norm, _clip(reason, 300), _clip(evidence, 300), source if source in ("nightly", "chat") else "nightly",
                                   now, now)).lastrowid)
        extra = self.suggestions()[SUGGEST_PROPOSED_MAX:]
        for s in extra:   # the oldest proposed ones give way (kept as dismissed so they do not come back)
            self.store._run("UPDATE goal_suggestions SET status = 'dismissed', updated_at = ? WHERE id = ?", (now, s["id"]))
        return sid

    def suggestion(self, sid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM goal_suggestions WHERE id = ?", (sid,))
        return rows[0] if rows else None

    def dismiss(self, sid: int) -> bool:
        s = self.suggestion(sid)
        if not s or s["status"] != "proposed":
            return False
        self.store._run("UPDATE goal_suggestions SET status = 'dismissed', updated_at = ? WHERE id = ?", (self.clock(), sid))
        return True

    def accept(self, sid: int) -> dict[str, Any] | None:
        s = self.suggestion(sid)
        if not s or s["status"] != "proposed":
            return None
        g = self.store.add_goal(s["title"], "", s["reason"])
        self.store._run("UPDATE goals SET origin = 'suggestion' WHERE id = ?", (g["id"],))
        self.store._run("UPDATE goal_suggestions SET status = 'accepted', goal_id = ?, updated_at = ? WHERE id = ?", (g["id"], self.clock(), sid))
        return self.store.goal(g["id"])

    def taken_titles(self) -> list[str]:
        """Goals and earlier suggestions, so the nightly run does not propose them again."""
        return [*(g["title"] for g in self.store.goals()), *(s["title"] for s in self.suggestions(SUG_STATUSES))]

    def kickoff_text(self, g: dict[str, Any]) -> str:
        note = f" (the user's note: {_clip(g['note'], 400)})" if g.get("note") else ""
        return KICKOFF.format(mark=GOAL_MARK, id=g["id"], title=g["title"], note=note, max_q=MAX_QUESTIONS, word=feature_name())

    def resume_text(self, g: dict[str, Any]) -> str:
        return RESUME.format(mark=GOAL_MARK, id=g["id"], title=g["title"])

    # ------------------------------------------------------------------ the agent's tools
    def tools(self, current_turn: Callable[[], dict[str, Any] | None] = lambda: None,
              on_replies: Callable[[str, list[dict[str, Any]], int | None], None] = lambda _t, _q, _g: None,
              goal_schedules: Callable[[int], list[dict[str, Any]]] = lambda _g: [],
              link_schedule: Callable[[int, int], str] | None = None,
              on_goal: Callable[[str, int], None] = lambda _t, _g: None) -> list[Tool]:
        """`on_goal(turn_id, goal_id)`: a tool of the running turn used that goal (the app then shows the goal as being worked on)."""
        def wrap(fn: Callable[[dict[str, Any]], Awaitable[str]]) -> Callable[[dict[str, Any]], Awaitable[str]]:
            async def run(args: dict[str, Any]) -> str:
                turn_id = (current_turn() or {}).get("turn_id")
                gid = args.get("goal_id")
                if turn_id and not isinstance(gid, bool) and str(gid or "").strip().isdigit():
                    on_goal(str(turn_id), int(str(gid).strip()))
                try:
                    return await fn(args)
                except GoalError as e:
                    return f"Error: {e}"
            return run

        async def goal_get(a: dict[str, Any]) -> str:
            if a.get("goal_id") in (None, ""):
                goals = self.store.goals()
                return "\n".join(f"#{g['id']} {g['title']} [{g['status']}]" for g in goals) or "The user has no goals yet."
            g = self.need(a.get("goal_id"))
            return self.text(g["id"], goal_schedules(g["id"]))

        async def goal_set_facts(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            facts = a.get("facts")
            if not isinstance(facts, list) or not facts:
                raise GoalError("facts must be a non-empty list of {key, value} or {key, skipped: true}")
            saved = []
            for f in facts:
                if isinstance(f, dict):
                    saved.append(self.set_fact(g["id"], str(f.get("key") or ""), str(f.get("value") or ""), bool(f.get("skipped")),
                                               "user" if f.get("from_user", True) else "agent"))
            self.set_asking(g["id"], False)
            self.log_t(g["id"], N_("Learned: {facts}"), "fact", facts=[[f["key"], None if f["skipped"] else f["value"]] for f in saved])
            return f"Saved {len(saved)} fact(s) on goal #{g['id']}."

        async def goal_plan(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            had = bool(self.steps(g["id"]))
            steps = self.plan(g["id"], a.get("steps"), bool(a.get("replace")))
            self.set_asking(g["id"], False)
            if not had or a.get("replace"):
                self.log_t(g["id"], N_("Made a plan of {n} steps"), "plan", n=len(steps))
            return f"Plan of goal #{g['id']}:\n" + "\n".join(f"{s['id']}. [{s['status']}] ({s['owner']}) {s['title']}" + (f" due {s['due']}" if s["due"] else "")
                                                             for s in steps)

        async def goal_step_done(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            s = self.set_step_status(g["id"], a.get("step_id"), str(a.get("status") or "done"), "agent", str(a.get("note") or ""))
            return f"Step {s['id']} of goal #{g['id']} is now {s['status']}."

        async def goal_log(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            self.log(g["id"], str(a.get("text") or ""), "note")
            self.touch(g["id"])
            return f"Noted on goal #{g['id']}."

        async def goal_metric_add(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            m = self.metric_add(g["id"], str(a.get("name") or ""), a.get("value"), a.get("unit"), a.get("start"), a.get("target"),
                                str(a.get("note") or ""))
            last = m["values"][-1]["value"] if m["values"] else None
            return (f"Number '{m['name']}' on goal #{g['id']}: start {_fmt(m['start'])}, target {_fmt(m['target'])}, latest {_fmt(last)}, "
                    f"{len(m['values'])} value(s).")

        async def goal_link_schedule(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            if link_schedule is None:
                raise GoalError("schedules are not available here")
            try:
                sid = int(a.get("schedule_id"))
            except (TypeError, ValueError):
                raise GoalError("schedule_id must be a schedule's number") from None
            return link_schedule(g["id"], sid)

        async def goal_add_document(a: dict[str, Any]) -> str:
            g = self.need(a.get("goal_id"))
            d = self.add_document(g["id"], str(a.get("path") or ""), str(a.get("title") or ""))
            self.log_t(g["id"], N_("Attached: {title}"), "note", title=d["title"])
            return f"Attached {d['path']} to goal #{g['id']}."

        async def goal_suggest(a: dict[str, Any]) -> str:
            sid = self.suggest(str(a.get("title") or ""), str(a.get("reason") or ""), str(a.get("evidence") or ""), "chat")
            if sid is None:
                return "Not added: it is empty, already a goal, or was suggested before (maybe declined)."
            return "Suggested: it shows at the top of the user's goals; they can add it or wave it off. Do not add it yourself."

        async def quick_replies(a: dict[str, Any]) -> str:
            qs = self.clean_questions(a.get("questions"))
            gid = None
            if a.get("goal_id") not in (None, "", 0):
                gid = self.need(a.get("goal_id"))["id"]
            turn = current_turn() or {}
            if not turn.get("turn_id"):
                return "Error: buttons can only go with a chat reply."
            on_replies(str(turn["turn_id"]), qs, gid)
            if gid:
                self.set_asking(gid, True)
            await emit_ui({"type": "replies", "questions": qs, "goal_id": gid})
            return ("The buttons are attached to the message you already wrote in this reply. Do not write that message again and do not "
                    "repeat the questions: end your turn now with no more text. (If you have not written the questions yet, write them "
                    "once, briefly, and stop.)")

        gid = {"type": "integer", "description": "the goal's number (#id)"}
        step_props = {"id": {"type": "integer", "description": "an existing step to change; leave out to add a step"},
                      "title": {"type": "string"}, "detail": {"type": "string"},
                      "owner": {"type": "string", "enum": list(OWNERS), "description": "who does it: the user, or you (agent)"},
                      "status": {"type": "string", "enum": list(STEP_STATUSES)},
                      "due": {"type": "string", "description": "YYYY-MM-DD, if a date helps"}}
        return [
            Tool("goal_get", "Read one of the user's goals in full, with step ids (no goal_id: list them).", {"type": "object", "properties": {"goal_id": gid}}, wrap(goal_get), read_only=True),
            Tool("goal_set_facts", "Save facts a goal's plan depends on, as the user gave them; a question they declined is saved as skipped.",
                 {"type": "object", "properties": {"goal_id": gid, "facts": {"type": "array", "items": {"type": "object", "properties": {
                     "key": {"type": "string", "description": "a few words naming the fact, in the user's language"},
                     "value": {"type": "string"}, "skipped": {"type": "boolean"}}, "required": ["key"]}}},
                  "required": ["goal_id", "facts"]}, wrap(goal_set_facts)),
            Tool("goal_plan", "Set or change a goal's plan of concrete steps.",
                 {"type": "object", "properties": {"goal_id": gid, "steps": {"type": "array", "items": {"type": "object", "properties": step_props}},
                                                   "replace": {"type": "boolean", "description": "rebuild: unlisted steps go, done ones stay"}},
                  "required": ["goal_id", "steps"]}, wrap(goal_plan)),
            Tool("goal_step_done", "Mark a plan step done (or another status), with a short note of the result.",
                 {"type": "object", "properties": {"goal_id": gid, "step_id": {"type": "integer"}, "status": {"type": "string", "enum": list(STEP_STATUSES)},
                                                   "note": {"type": "string"}}, "required": ["goal_id", "step_id"]}, wrap(goal_step_done)),
            Tool("goal_log", "Add a line to a goal's progress log, in the user's language.",
                 {"type": "object", "properties": {"goal_id": gid, "text": {"type": "string"}}, "required": ["goal_id", "text"]}, wrap(goal_log)),
            Tool("goal_metric_add", "Follow a number that shows a goal's progress: create it by name and/or add a value (only one the user "
                 "gave or a tool returned).",
                 {"type": "object", "properties": {"goal_id": gid, "name": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"},
                                                   "start": {"type": "number"}, "target": {"type": "number"}, "note": {"type": "string"}},
                  "required": ["goal_id", "name"]}, wrap(goal_metric_add)),
            Tool("goal_link_schedule", "Tie an existing scheduled job to a goal.", {"type": "object", "properties": {"goal_id": gid, "schedule_id": {"type": "integer"}},
                                            "required": ["goal_id", "schedule_id"]}, wrap(goal_link_schedule)),
            Tool("goal_add_document", "Attach a file (in the user's files or your notes) to a goal so it shows there.",
                 {"type": "object", "properties": {"goal_id": gid, "path": {"type": "string"}, "title": {"type": "string"}},
                  "required": ["goal_id", "path"]}, wrap(goal_add_document)),
            Tool("goal_suggest", "Suggest a goal the user clearly wants to reach but did not ask you to track; they accept or decline it (never "
                 "add one for them). Never base one on a guess about health, money, relationships or anything else sensitive they did not "
                 "state.",
                 {"type": "object", "properties": {"title": {"type": "string"},
                                                   "reason": {"type": "string", "description": "one sentence, in the user's language"},
                                                   "evidence": {"type": "string", "description": "what they said"}},
                  "required": ["title", "reason"]}, wrap(goal_suggest)),
            Tool("quick_replies", "Put tap-to-answer fields under the message you are writing, one per question in it. Choices are "
                 "suggestions: never narrow the answer space; the user can always answer outside them (every choice field also gets "
                 "an Other box). For anything personal include an answer meaning \"I'd rather not say\".",
                 {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "object", "properties": {
                     "question": {"type": "string", "description": "the question in a few words"},
                     "kind": {"type": "string", "enum": list(QUESTION_KINDS),
                              "description": "default choice; any other kind opens the phone's own picker"},
                     "options": {"type": "array", "items": {"type": "string"}, "description": "choice: 2-6 short answers in the user's language"},
                     "says": {"type": "array", "items": {"type": "string"},
                              "description": "optional, one per option: the whole sentence the user sends by tapping it, in their "
                                             "language (option 'Plan B' -> 'Let's go with Plan B, the 500M internet + TV bundle'); the "
                                             "button keeps the short label"},
                     "typed": {"type": "array", "items": {"type": "string"}, "description": "options meaning the user will write it"},
                     "hint": {"type": "string", "description": "what to write in a text box, a few words"},
                     "default": {"type": "string", "description": "start value in the kind's format (YYYY-MM-DD, HH:MM, mon,wed ...)"},
                     "min": {"type": "number"}, "max": {"type": "number"},
                     "unit": {"type": "string"}}}}, "goal_id": gid},
                  "required": ["questions"]}, wrap(quick_replies), closes_reply=True),
        ]


# ---------------------------------------------------------------------------------------------------------------- API
class StepPatch(BaseModel):
    status: str


class FactIn(BaseModel):
    value: str = Field(default="", max_length=VALUE_MAX)
    skipped: bool = False


class MetricValueIn(BaseModel):
    value: float
    note: str = Field(default="", max_length=200)


def goal_routes(app: FastAPI, book: GoalBook, auth: Callable[..., Any], *, schedules_of: Callable[[int], list[dict[str, Any]]],
                start_work: Callable[[dict[str, Any], str], bool]) -> None:
    """The goal workspace and suggestion endpoints (contracts in the module docstring). Registered before GET /api/goals/{id} would
    read "suggestions" as an id. `start_work(goal, text)` starts the agent's turn on the goal; False when no model is set."""

    def need(goal_id: int) -> dict[str, Any]:
        g = book.store.goal(goal_id)
        if not g:
            raise HTTPException(404, "no such goal")
        return g

    def call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except GoalError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/goals/suggestions", dependencies=[Depends(auth)])
    async def goal_suggestions() -> dict[str, Any]:
        return {"items": [{k: s[k] for k in ("id", "title", "reason", "evidence", "source", "created_at")} for s in book.suggestions()]}

    @app.post("/api/goals/suggestions/{sid}/accept", dependencies=[Depends(auth)], status_code=201)
    async def accept_suggestion(sid: int) -> dict[str, Any]:
        g = book.accept(sid)
        if g is None:
            raise HTTPException(404, "no such suggestion")
        start_work(g, book.kickoff_text(g))
        return book.public(book.store.goal(g["id"]) or g)

    @app.post("/api/goals/suggestions/{sid}/dismiss", dependencies=[Depends(auth)])
    async def dismiss_suggestion(sid: int) -> dict[str, bool]:
        if not book.dismiss(sid):
            raise HTTPException(404, "no such suggestion")
        return {"ok": True}

    @app.get("/api/goals/{goal_id}", dependencies=[Depends(auth)])
    async def goal_workspace(goal_id: int) -> dict[str, Any]:
        need(goal_id)
        return book.workspace(goal_id, schedules_of(goal_id))

    @app.patch("/api/goals/{goal_id}/steps/{step_id}", dependencies=[Depends(auth)])
    async def patch_step(goal_id: int, step_id: int, body: StepPatch) -> dict[str, Any]:
        need(goal_id)
        if step_id not in {s["id"] for s in book.steps(goal_id)}:
            raise HTTPException(404, "no such step")
        return call(lambda: book.set_step_status(goal_id, step_id, body.status, "user"))

    @app.put("/api/goals/{goal_id}/facts/{key}", dependencies=[Depends(auth)])
    async def put_fact(goal_id: int, key: str, body: FactIn) -> dict[str, Any]:
        need(goal_id)
        return call(lambda: book.set_fact(goal_id, key, body.value, body.skipped, "user"))

    @app.delete("/api/goals/{goal_id}/facts/{key}", dependencies=[Depends(auth)])
    async def delete_fact(goal_id: int, key: str) -> dict[str, bool]:
        need(goal_id)
        if not book.delete_fact(goal_id, key):
            raise HTTPException(404, "no such fact")
        return {"ok": True}

    @app.post("/api/goals/{goal_id}/metrics/{metric_id}/values", dependencies=[Depends(auth)])
    async def add_metric_value(goal_id: int, metric_id: int, body: MetricValueIn) -> dict[str, Any]:
        need(goal_id)
        m = call(lambda: book.metric(goal_id, metric_id))
        return call(lambda: book.metric_add(goal_id, m["name"], body.value, note=body.note, by="user"))

    @app.post("/api/goals/{goal_id}/work", dependencies=[Depends(auth)])
    async def work_on_goal(goal_id: int) -> dict[str, bool]:
        g = need(goal_id)
        if not start_work(g, book.resume_text(g)):
            raise HTTPException(409, "no model is set")
        return {"ok": True}
