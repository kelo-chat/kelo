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
{"questions": [{"question", "options": [str], "connect"?: [str], "hint"?: str}], "goal_id"}). One question per message, at most three answers
(a question about connecting a service: two or three services, "connect" = their ids in the order of the options, plus a last answer for
not now). The apps add no answer of their own; the user can always type in the message box. "typed" in older stored messages is still
read by the apps.
Choice cards: a choice question may carry "cards" (max 6 options), one per option in the same order: {"image"?: {"image_id", "url":
"/api/chat-images/<id>" (auth like every chat image), "width", "height"} (a thumbnail, longest side 480 px; a picture that could not be
fetched is left out), "detail"?: str (one line, at most 80 characters: price · arrival), "recommended"?: true (on at most one)}; an
option with nothing to show has {}. "kind" stays "choice" and "options"/"says" are unchanged, so an app that ignores "cards" shows the
plain buttons. The same shape is in the "replies" turn event and in GET /api/messages. Card thumbnails are chat images of the turn
(table chat_images, turn_id "<turn>#cards"): they go with the message, are not in its "images", and are removed with the chat.
"kind" (left out = choice): text | number ("min"?, "max"?, "unit"?) | date | time | datetime | weekdays | weekdays_time; each shows the
device's own picker for that kind of value, "default"? (its plain form, check_answer) is where the picker starts. The app sends a
localized sentence as the chat message and, with it, "answers": [{"question": index, "value": plain form}] (POST /api/turns,
/api/talk); the server checks every value against the open question (422 when one does not fit) and adds the plain values to what the
model reads, so a date or a day is never guessed from words.

Suggestions: the nightly look-back (reflection.py) may propose up to reflection.GOALS_PER_NIGHT (2) goals grounded in what the user said, each
with the reason and the evidence (one that nobody answers is gone after SUGGEST_DAYS); the agent can also suggest one during a conversation (goal_suggest). The app shows the proposed ones at the
top of the goals tab; accepting one adds the goal (and starts its kickoff), dismissing one keeps it so the same goal is never offered
again.

GOALS API (also next to serialize() in server.py):
Goal (GET /api/goals items): {"id", "title", "category", "note", "status": tracking|done, "created_at", "updated_at", "origin":
  user|suggestion|agent, "plan": {"done", "total"}, "next_step": str|null, "asking": bool (the agent asked for this goal, nothing runs
  for it and the user has not written since: waiting for the user's answers), "working": bool (a reply or job on this goal is running),
  "schedules_on": int (linked schedules that are on: the apps ask "delete them too?" before deleting the goal; 0 = no question),
  "kind": goal|work, "parent_id": int|null (the top-level goal this smaller goal sits under), "summary": str (one line: where it stands),
  "description": str (two or three sentences written by Kelo; "note" stays the user's own words), "area": wellbeing|people|finances|job|
  hobbies|workload|other|"", "done_reason": str (the reason of the last status change), "children": int (smaller goals under it)}
  (a note's last line "[due:YYYY-MM-DD progress:N]" is the due date and progress, as in the app's GoalMeta)
POST /api/goals {"title", "category"?, "note"?, "kickoff"?: true}   201 Goal; with kickoff (default) the agent starts on it
GET  /api/goals?include=work   the same list with the work items Kelo carries (kind "work"); without include: goals only; other 422
GET  /api/goals/{id}   {"goal": Goal, "facts": [Fact], "steps": [Step], "metrics": [Metric], "log": [Log] (the first page), "log_next":
       int|null (the offset of the next page), "documents": [Doc], "schedules": [Schedule], "children": [Goal]}   404 no such goal
GET  /api/goals/{id}/log?offset=<int>&limit=<int, at most 100>   {"items": [Log], "next": int|null}   404 no such goal; 422 bad numbers
  Fact {"key", "value", "skipped": bool, "source": user|agent, "updated_at"}
  Step {"id", "title", "detail", "status": todo|doing|done, "owner": user|agent, "due": "YYYY-MM-DD"|null, "done_at"}
  Metric {"id", "name", "unit", "start": float|null, "target": float|null, "values": [{"value", "note", "at"}] (oldest first, all of them)}
  Log {"id", "at", "text", "kind": note|step|fact|metric|plan|kickoff|user, "by": agent|user, "title", "outcome", "happened_at": float|null,
       "from_goal": {"id", "title"}|null (an entry of a smaller goal, shown on the goal above)}
  Doc {"id", "path", "title", "added_at", "exists": bool}
PATCH /api/goals/{id}/steps/{step_id} {"status"}   Step   (the user ticks a step; 404 / 422)
PUT  /api/goals/{id}/facts/{key} {"value"?, "skipped"?}   Fact;  DELETE /api/goals/{id}/facts/{key}   {"ok"}
POST /api/goals/{id}/metrics/{metric_id}/values {"value", "note"?}   Metric
POST /api/goals/{id}/work   {"ok": true}   the agent picks the goal up again (409 {"detail"} when no model is set)
DELETE /api/goals/{id}?schedules=keep|delete   {"ok", "stopped_turns", "stopped_jobs", "deleted_schedules": [id], "kept_schedules": [id], "deleted_goals": [id]}
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

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import Tool, emit_ui
from .i18n import N_, t
from .shopping import ID_RE, Product

GOAL_MARK = "[Goal work, not from the user]"   # leads the hidden message of a kickoff / resume turn
# the goal tools a turn on a goal (and the chat once a goal is saved in it) loads without a search
GOAL_TURN_TOOLS = ("goal_get", "goal_update", "goal_set_status", "goal_log", "goal_set_facts", "goal_plan", "read_skill")
STEP_STATUSES = ("todo", "doing", "done")
OWNERS = ("user", "agent")
SUG_STATUSES = ("proposed", "accepted", "dismissed")
ASK_HOURS = 72                  # a goal shows as "waiting for your answers" at most this long after the questions
SUGGEST_DAYS = 7                # owner 2026-11-20 (decision 16): a nightly goal suggestion nobody answers disappears after 7 days
PROMPT_GOALS, PROMPT_CHARS = 8, 2_200
MAX_QUESTIONS, MAX_OPTIONS, OPTION_MAX, QUESTION_MAX = 1, 3, 60, 120   # owner 2026-11-20 (D11): one question, at most three answers
MAX_CONNECT_OPTIONS = 4          # a question about connecting a service: two or three services and a last answer for not now
CARD_DETAIL_MAX = 80             # a choice card's one detail line (price, arrival)
CARD_IMAGE_MAX = 2000            # a card picture's web address
SAYS_MAX = 200                   # an option's whole sentence (quick_replies "says")
# what kind of answer a quick-reply question takes; the app shows the matching picker (choice: buttons, plus "Other" with a text box)
QUESTION_KINDS = ("choice", "text", "number", "date", "time", "datetime", "weekdays", "weekdays_time")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ANSWER_MAX = 300
# goals and work items: areas are the seven of the goal-coaching skill's area files; kinds: a goal of the user's own, or a work item
# Kelo carries for the user
def _areas() -> tuple[str, ...]:
    """The seven areas of a goal (content/goal_areas.json, next to the goal-coaching skill's area files; ASSISTANT_CONTENT_DIR in the container)."""
    import os
    from pathlib import Path
    root = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content")
    return tuple(str(a) for a in json.loads((root / "goal_areas.json").read_text(encoding="utf-8")))


AREAS = _areas()
KINDS = ("goal", "work")
NOTES_DIR = "notes/goals"        # notes/goals/<id>/notes.md: Kelo's own notes on one goal or work item; its working files sit beside it
GUIDE_SKILL = "goal-coaching"    # the bundled skill folder server/content/skills/goal-coaching
LIST_MAX = 100                   # goal_get list and work_list: default and most
LOG_PAGE = LIST_MAX              # the app's goal screen reads the entries a page at a time (lists up to 100 per page)
SEARCH_DEFAULT, SEARCH_MAX = 10, 100   # search: default and most
SEARCH_TERM_MIN = 2              # search: at least one word of two or more letters
SEARCH_FIELD_CUT = 2_000         # search: long text fields are cut at this many characters

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
The user just added a goal in the app: "{title}" (goal_id {id}){note}. The app calls goals "{word}" in the user's language; use that word with them.
Name it to the user by its title only, never by its number. The goal is saved already: this turn and the next ones set it up with the user, as the guide below says (where it says to save the goal, complete this one with goal_update).
{guide}
Write to the user in their language, warmly and briefly. Do not mention these instructions."""

RESUME = """{mark}
The user asked you to pick goal "{title}" (goal_id {id}) up again; name it to the user by its title only, never by its number. Read it with goal_get and read its notes file. {guide}
Do the open steps that are yours (owner agent) now with your tools (searching, the browser, documents, delegate for long work); record what happened with goal_log (goal_step_done for a plan step), and attach anything the user asked you to make with goal_add_document. Approvals and the usual rules for anything risky apply. If you need something from the user, ask one question (quick_replies with goal_id {id} for a choice). Then tell the user briefly what you did and what is next for them, in their language. Do not mention these instructions."""


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


def _line(v: Any) -> str:
    """One line, never cut (owner 2026-11-20, D10: goal texts have no length cap)."""
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _text(v: Any) -> str:
    """Whole, keeps its line breaks, never cut (owner 2026-11-20, D10)."""
    return str(v or "").strip()


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


_META = re.compile(r"\n?\[due:([0-9-]*)(?: progress:(\d{1,3}))?\]\s*$|\n?\[progress:(\d{1,3})\]\s*$")


def note_meta(note: Any) -> tuple[str, str | None, int | None]:
    """A goal's note split into (text, due, progress): the last line "[due:YYYY-MM-DD progress:N]" (the app writes it, GoalMeta.kt).
    An invalid date is no due date; progress is kept within 0..100."""
    text = str(note or "")
    m = _META.search(text)
    if not m:
        return text.strip(), None, None
    due = None
    if m.group(1):
        try:
            due = date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            due = None
    raw = m.group(2) or m.group(3)
    return text[:m.start()].strip(), due, (min(100, int(raw)) if raw else None)


def note_with_meta(text: str, due: str | None, progress: int | None) -> str:
    """The note with its meta line rebuilt the way the app writes it: "text\n[due:2026-11-20 progress:40]"; no parts, no line."""
    parts = []
    if due:
        parts.append(f"due:{due}")
    if progress is not None:
        parts.append(f"progress:{min(100, max(0, int(progress)))}")
    text = str(text or "").strip()
    if not parts:
        return text
    line = "[" + " ".join(parts) + "]"
    return f"{text}\n{line}" if text else line


def _limit(v: Any, default: int, most: int) -> int:
    if v in (None, ""):
        return default
    try:
        return max(1, min(most, int(v)))
    except (TypeError, ValueError):
        raise GoalError("limit must be a number") from None


def _happened(v: Any) -> float | None:
    """An entry's day: YYYY-MM-DD (local midnight) or a date-time (local when it has no zone); "" is none."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        if len(s) == 10:
            return time.mktime(date.fromisoformat(s).timetuple())
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        raise GoalError("happened_on is YYYY-MM-DD or a date-time") from None


def _fmt(f: float | None) -> str:
    if f is None:
        return "-"
    return str(int(f)) if float(f).is_integer() else f"{f:g}"


SUMMARY_PROMPT = """You keep the one-line summary of a goal, or of a piece of work an assistant carries, current. You get its title, its description, its current summary and one new entry.
Decide whether the entry supports a newer or more useful line about where things stand now or what comes next: a result, a progress mark, a specific wait, a blocker, something being watched, or a planned event. If it does, write that line; if not, keep the current one.
The line: one short sentence, in the language of the entry; never say that work is going on right now; no generic next step; do not repeat the title or the description.
Answer with exactly one line: KEEP, or NEW: followed by the line."""


class GoalError(ValueError):
    pass


def _row_line(r: dict[str, Any]) -> str:
    """One goal or work item in a list or a search: "#id title [status] · summary · under #parent"."""
    line = f"#{r['id']} {r['title']} [{r['status']}]"
    if r.get("summary"):
        line += f" · {r['summary']}"
    if r.get("parent_id"):
        line += f" · under #{r['parent_id']}"
    return line


def _history_props(gid: dict[str, Any]) -> dict[str, Any]:
    return {"goal_id": gid,
            "title": {"type": "string", "description": "a few words: what happened"},
            "outcome": {"type": "string", "description": "one line: its main result"},
            "text": {"type": "string", "description": "one sentence: what happened and, when there is one, the result"},
            "happened_on": {"type": "string", "description": "YYYY-MM-DD or a date-time; default now"},
            "entry_id": {"type": "integer", "description": "an existing entry to fix, or to remove with remove"},
            "remove": {"type": "boolean", "description": "true removes the entry named by entry_id"}}


GOAL_GET_DESCRIPTION = (
    "Read one of the user's goals in full, with its entries, smaller goals and the path of its notes file (goal_id); search their goals "
    "by words (query: matches id, title, summary and description, done ones too; at least one word of two letters or more; up to "
    "`limit`, default 10, at most 100); or, with neither, list them (open first; up to `limit`, default and most 100; `offset` from the "
    "last page). Work you carry for the user is not here: work_list. Check here before you save a goal, so an open one that covers the "
    "same aim is changed instead.")
GOAL_CREATE_DESCRIPTION = (
    "Save a new goal of the user's: a lasting aim they are working toward (an outcome, a way of acting, a part of their life), also when "
    "they never call it a goal: a wish they come back to, progress or setbacks they tell you, a commitment with a time frame, or a request "
    "for help with a whole part of their life. Not a goal: a quick to-do, a one-time reminder, a small update that belongs on a goal they "
    "already have, or work you carry for them (work_create). Before you save it, follow the goal-coaching skill (start.md and the area's file) "
    "and check with goal_get (query) that no open goal covers the same aim. Save it once, yourself, then read it back with goal_get.")
GOAL_UPDATE_DESCRIPTION = (
    "Correct a goal: only the fields you give change. News is not a correction: put it in goal_log (the summary follows entries by "
    "itself); done or open again is goal_set_status. parent_id puts it under a top-level goal (0 takes it back to the top). due is the "
    "goal's end date, the same one the user sets in the app: move it when the user wants more time, '' clears it.")
GOAL_STATUS_DESCRIPTION = (
    "Mark a goal done, or open it again, with a short reason the user will read, in their language. Done when the user reached it, ended "
    "it or clearly moved on; open again only when the user restarts that same aim (never save a new goal for it).")
GOAL_LOG_DESCRIPTION = (
    "Add an entry to a goal's history when the user reports progress, a result, a setback, a milestone or a decision that moves or changes "
    "the goal; a milestone in the same turn, without leaving the topic. Short and readable by the user, each event once, also when you "
    "wrote it in the notes already. happened_on for something that happened earlier. The goal's summary line follows new entries by "
    "itself: do not change it separately. An entry on a smaller goal also shows on the goal above it. With entry_id: fix that entry, or "
    "remove: true for a duplicate or a mistaken one.")
WORK_CREATE_DESCRIPTION = (
    "Keep track of something you carry for the user beyond this chat: a booking, a delivery, a reminder, a trip, an ongoing project. It "
    "needs no request to track it and no minimum size. Keep it open while the event, delivery or follow-up is still ahead, even when the "
    "booking or checkout is done. Not for ordinary talk or anything finished within this turn. Not for check-ins, nudges or monitoring of "
    "one of the user's goals: those are schedules with that goal's id and entries on the goal. Check work_list (query) first: if an item "
    "covers it, log to that one, or open a closed one again with work_close; never make a replacement. Tie any schedule or watcher for it "
    "to the item (schedule_create goal_id = the item's number) when you set it up. A reminder the user asked for is closed once it was "
    "delivered and nothing more is owed.")
WORK_LOG_DESCRIPTION = (
    "Add an entry to a work item each time something happens on it: a step started or finished, a check, new or confirmed facts, progress "
    "on what you make, a result, feedback, a blocker, a change of plan or time. Do not wait for a milestone. Short, readable by the user, "
    "each event once. Its summary line follows by itself. An entry never closes the item: when the whole thing is over, close it with "
    "work_close in the same turn. With entry_id: fix that entry, or remove: true.")
WORK_CLOSE_DESCRIPTION = (
    "Close a work item, or open it again, with a short reason the user will read. Close it in the same turn the best evidence shows it is "
    "over; do not wait for the user. Evidence: what was said, the state of a goal, finished work or tool results, reliable facts, and "
    "today's date against known dates (it arrived, the event is over, the target was met, the time window ended, the user called it off). "
    "Silence, or one finished step, is not enough. An explicit stop from the user closes it at once. Close it even when its entries already "
    "tell how it ended. Open it again only when the user restarts the same thing.")
WORK_LIST_DESCRIPTION = (
    "List the work items you carry for the user (open first; up to `limit`, default and most 100; `offset` from the last page), search them "
    "by words (query: matches id, title, summary and description, closed ones too; at least one word of two letters or more; default 10, "
    "at most 100), or read one in full with its entries and the path of its notes file (goal_id). The user's own goals are not here: "
    "goal_get.")


class GoalBook:
    def __init__(self, store: Any, home: Any = None, clock: Callable[[], float] = _now):
        self.store, self.home, self.clock = store, home, clock
        self.hidden_marks: tuple[str, ...] = (GOAL_MARK,)   # user-role messages that are not the user's words (the server adds its own)
        self.busy: Callable[[], set[int]] = set   # goals a running reply or job works on (the server sets it)
        self.guides_dir: Path | None = None       # the goal-coaching skill folder (the server sets it)
        self.summarize: Callable[[dict[str, Any], dict[str, Any]], Awaitable[str | None]] | None = None   # the server sets it
        self.summary_tasks: set[asyncio.Task] = set()
        self.carried: Any = None                  # carried.py: the one list's links (the server sets it)
        with store._lock:
            store._db.executescript(SCHEMA)
            for table, adds in (("goals", (("origin", "TEXT NOT NULL DEFAULT 'user'"), ("asking_at", "REAL"),
                                           ("kind", "TEXT NOT NULL DEFAULT 'goal'"), ("parent_id", "INTEGER"),
                                           ("summary", "TEXT NOT NULL DEFAULT ''"), ("description", "TEXT NOT NULL DEFAULT ''"),
                                           ("area", "TEXT NOT NULL DEFAULT ''"), ("done_reason", "TEXT NOT NULL DEFAULT ''"),
                                           ("refs", "TEXT NOT NULL DEFAULT '{}'"))),
                                ("goal_log", (("title", "TEXT NOT NULL DEFAULT ''"), ("outcome", "TEXT NOT NULL DEFAULT ''"),
                                              ("happened_at", "REAL")))):
                have = {r[1] for r in store._db.execute(f"PRAGMA table_info({table})")}
                for col, ddl in adds:
                    if col not in have:
                        store._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            # the notes folder is never a goal's document (owner 2026-11-20, D16): rows an earlier version attached are removed
            store._db.execute("DELETE FROM goal_docs WHERE path LIKE 'notes/goals/%'")
            store._db.commit()

    def _follow_free(self, key: str) -> bool:
        """A follow item the agent may name as one work item's: it exists and is linked to no work item yet."""
        if self.carried is None:
            return False
        return self.carried.follow_of(key) is not None and self.carried.work_of(key) is None

    def merge_refs(self, goal_id: int, refs: dict[str, Any]) -> dict[str, str]:
        """A work item's outside ids (event, mail, flight, date, tracking, booking), merged into what it has: the same thing is found again
        by these ids (carried.py), never by its title."""
        row = self.store.goal(int(goal_id)) or {}
        try:
            have = json.loads(row.get("refs") or "{}")
        except (TypeError, ValueError):
            have = {}
        have = have if isinstance(have, dict) else {}
        for k, v in (refs or {}).items():
            if k in ("event", "mail", "flight", "date", "tracking", "booking") and isinstance(v, (str, int, float)) and str(v).strip():
                have[k] = str(v).strip()[:200]
        self.store._run("UPDATE goals SET refs = ? WHERE id = ?", (json.dumps(have, ensure_ascii=False), int(goal_id)))
        if self.carried is not None:
            self.carried.match_work(int(goal_id))
        return have

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
        out["kind"] = g.get("kind") or "goal"
        out["parent_id"] = g.get("parent_id")
        out["summary"] = g.get("summary") or ""
        out["description"] = g.get("description") or ""
        out["area"] = g.get("area") or ""
        out["done_reason"] = g.get("done_reason") or ""
        out["children"] = int(self.store._all("SELECT COUNT(*) AS n FROM goals WHERE parent_id = ?", (g["id"],))[0]["n"])
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

    def listing(self, kinds: tuple[str, ...] = ("goal",)) -> list[dict[str, Any]]:
        """GET /api/goals: the user's goals only, unless asked for the work items too (kinds)."""
        return [self.public(g) for g in self.store.goals() if (g.get("kind") or "goal") in kinds]

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

    # ------------------------------------------------------------------ goals and work items
    def need_kind(self, goal_id: Any, kind: str) -> dict[str, Any]:
        """The goal or work item `goal_id` names, checked to be of `kind` ("goal" or "work")."""
        g = self.need(goal_id)
        if (g.get("kind") or "goal") != kind:
            raise GoalError(f"#{g['id']} is a work item: use the work_ tools" if kind == "goal"
                            else f"#{g['id']} is one of the user's goals: use the goal_ tools")
        return g

    def _check_parent(self, kind: str, parent_id: Any, goal_id: int | None) -> int | None:
        """A smaller goal sits under one top-level goal of the same kind (one level only); "" or 0 means top level."""
        if parent_id in (None, "", 0, "0"):
            return None
        try:
            pid = int(parent_id)
        except (TypeError, ValueError):
            raise GoalError("parent_id must be a goal's number") from None
        p = self.store.goal(pid)
        word = "goal" if kind == "goal" else "work item"
        if p is None or (p.get("kind") or "goal") != kind or p.get("parent_id") is not None or pid == goal_id:
            raise GoalError(f"parent_id must be a top-level {word}")
        if goal_id is not None and self.children(goal_id):
            raise GoalError("a goal that has smaller goals cannot go under another one")
        return pid

    def children(self, goal_id: int) -> list[dict[str, Any]]:
        return [self.public(c) for c in self.store._all("SELECT * FROM goals WHERE parent_id = ? ORDER BY id", (goal_id,))]

    def notes_path(self, g: dict[str, Any]) -> str:
        return f"{NOTES_DIR}/{g['id']}/notes.md"

    def seed_notes(self, g: dict[str, Any]) -> None:
        """The notes file of a goal is written once, when it is saved; nothing in the server writes it again. A file the agent already
        wrote stays as it is."""
        if self.home is None:
            return
        path = self.notes_path(g)
        if self._exists(path):
            return
        about = g.get("description") or note_meta(g.get("note") or "")[0] or "(not written yet)"
        word = "Goal" if (g.get("kind") or "goal") == "goal" else "Work item"
        head = f"{word} {g['id']}" + (f" · area {g['area']}" if g.get("area") else "") + \
            (f" · under {g['parent_id']}" if g.get("parent_id") else "") + f" · saved {date.today().isoformat()}"
        self.home.write(path, f"# {g['title']}\n{head}\n\n## About\n{about}\n", by="system")

    def create(self, title: str, *, kind: str = "goal", summary: str = "", description: str = "", note: str = "", area: str = "",
               category: str = "", parent_id: int | None = None, origin: str = "user") -> dict[str, Any]:
        """Saves a goal (kind goal) or a work item (kind work) with its summary, description and area, and seeds its notes file once."""
        title = _line(title)
        if not title:
            raise GoalError("a title is required")
        if kind not in KINDS:
            raise GoalError(f"kind must be one of {', '.join(KINDS)}")
        if area and area not in AREAS:
            raise GoalError(f"area must be one of {', '.join(AREAS)}")
        parent = self._check_parent(kind, parent_id, None)
        g = self.store.add_goal(title, category, note)
        self.store._run("UPDATE goals SET kind = ?, parent_id = ?, summary = ?, description = ?, area = ?, origin = ?, updated_at = ? "
                        "WHERE id = ?", (kind, parent, _line(summary), _text(description), area or "",
                                         origin if origin in ("user", "suggestion", "agent") else "user", self.clock(), g["id"]))
        g = self.store.goal(g["id"]) or g
        self.seed_notes(g)
        return g

    def update(self, goal_id: int, kind: str, **changes: Any) -> dict[str, Any]:
        """Changes only the fields given: title, summary, description, area, parent_id, due ("" clears the due date). The due date lives in
        the note's last line, as the app writes it."""
        g = self.need_kind(goal_id, kind)
        unknown = set(changes) - {"title", "summary", "description", "area", "parent_id", "due"}
        if unknown:
            raise GoalError(f"unknown field: {', '.join(sorted(unknown))}")
        sets: dict[str, Any] = {}
        if "title" in changes:
            title = _line(changes["title"])
            if not title:
                raise GoalError("a title cannot be empty")
            sets["title"] = title
        if "summary" in changes:
            sets["summary"] = _line(changes["summary"])
        if "description" in changes:
            sets["description"] = _text(changes["description"])
        if "area" in changes:
            area = str(changes["area"] or "")
            if area and area not in AREAS:
                raise GoalError(f"area must be one of {', '.join(AREAS)}")
            sets["area"] = area
        if "parent_id" in changes:
            sets["parent_id"] = self._check_parent(kind, changes["parent_id"], goal_id)
        if "due" in changes:
            text, _, progress = note_meta(g.get("note") or "")
            sets["note"] = note_with_meta(text, _day(changes["due"]), progress)
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            self.store._run(f"UPDATE goals SET {cols}, updated_at = ? WHERE id = ?", (*sets.values(), self.clock(), goal_id))
        return self.public(self.store.goal(goal_id) or g)

    def set_status(self, goal_id: int, kind: str, status: str, reason: str, by: str = "agent") -> tuple[dict[str, Any], bool]:
        """Marks a goal or work item done, or open again ("tracking"), with the reason the user reads. Returns (row, changed): the same status
        changes nothing and records nothing. A status change needs a reason and may record a closing or reopening entry."""
        g = self.need_kind(goal_id, kind)
        if status not in ("tracking", "done"):
            raise GoalError("status must be tracking or done")
        reason = _line(reason)
        if not reason:
            raise GoalError("a reason the user will read is required")
        if g["status"] == status:
            return g, False
        self.store._run("UPDATE goals SET status = ?, done_reason = ?, updated_at = ? WHERE id = ?",
                        (status, reason, self.clock(), goal_id))
        self.log_t(goal_id, N_("Marked done: {reason}") if status == "done" else N_("Opened again: {reason}"), "status", by, reason=reason)
        return self.store.goal(goal_id) or g, True

    def search(self, kind: str, query: str, limit: Any = None) -> tuple[list[dict[str, Any]], int]:
        """Goals (or work items) matched by id, title, summary, description or note, done ones too: ranked by the number of words of the
        query that match, then the newest update. Returns (rows, how many matched)."""
        terms = [w for w in re.findall(r"[^\W_]+", str(query or "").lower()) if len(w) >= SEARCH_TERM_MIN]
        if not terms:
            raise GoalError("query needs a word of two or more letters")
        lim = _limit(limit, SEARCH_DEFAULT, SEARCH_MAX)
        scored = []
        for g in self.store._all("SELECT * FROM goals WHERE kind = ?", (kind,)):
            hay = [str(x).lower()[:SEARCH_FIELD_CUT] for x in (g["id"], g["title"], g.get("summary"), g.get("description"), g.get("note"))]
            n = sum(any(t in h for h in hay) for t in terms)
            if n:
                scored.append((n, float(g.get("updated_at") or 0), g["id"], g))
        scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        return [self.public(x[3]) for x in scored[:lim]], len(scored)

    def page(self, kind: str, limit: Any = None, offset: Any = None) -> tuple[list[dict[str, Any]], int | None]:
        """Goals (or work items), open ones first, newest update first, a page at a time. Returns (rows, next offset or None)."""
        lim = _limit(limit, LIST_MAX, LIST_MAX)
        try:
            off = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise GoalError("offset must be a number") from None
        rows = self.store._all("SELECT * FROM goals WHERE kind = ? ORDER BY status = 'done', updated_at DESC, id DESC LIMIT ? OFFSET ?",
                               (kind, lim, off))
        total = int(self.store._all("SELECT COUNT(*) AS n FROM goals WHERE kind = ?", (kind,))[0]["n"])
        nxt = off + len(rows) if off + len(rows) < total else None
        return [self.public(r) for r in rows], nxt

    # ------------------------------------------------------------------ facts
    def facts(self, goal_id: int) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT key, value, skipped, source, updated_at FROM goal_facts WHERE goal_id = ? ORDER BY updated_at, key",
                               (goal_id,))
        return [{**r, "skipped": bool(r["skipped"])} for r in rows]

    def set_fact(self, goal_id: int, key: str, value: str = "", skipped: bool = False, source: str = "agent") -> dict[str, Any]:
        key = _line(key)
        if not key:
            raise GoalError("a fact needs a key")
        value = "" if skipped else _line(value)
        if not skipped and not value:
            raise GoalError(f"fact '{key}' needs a value, or skipped: true")
        known = {f["key"] for f in self.facts(goal_id)}
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
        for item in steps:
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
            title = _line(item.get("title") or (current[sid]["title"] if sid else ""))
            if not title:
                raise GoalError("each step needs a title")
            rows.append(("upd" if sid else "new", (sid, title, _line(item.get("detail") if "detail" in item else (current[sid]["detail"] if sid else "")),
                                                    status, owner, _day(item["due"]) if "due" in item else (current[sid]["due"] if sid else None))))
            if sid:
                keep.add(sid)
        now = self.clock()
        if replace:
            for sid, s in current.items():
                if sid not in keep and s["status"] != "done":
                    self.store._run("DELETE FROM goal_steps WHERE id = ?", (sid,))
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
            vals = self.store._all("SELECT value, note, at FROM goal_metric_values WHERE metric_id = ? ORDER BY at DESC, id DESC",
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
        name = _line(name)
        if not name:
            raise GoalError("name is required")
        v, st, tg = _num(value, "value"), _num(start, "start"), _num(target, "target")
        rows = self.store._all("SELECT id FROM goal_metrics WHERE goal_id = ? AND name = ?", (goal_id, name))
        if rows:
            mid = rows[0]["id"]
            sets = [(c, x) for c, x in (("unit", _line(unit) if unit is not None else None), ("start", st), ("target", tg)) if x is not None]
            for col, x in sets:
                self.store._run(f"UPDATE goal_metrics SET {col} = ? WHERE id = ?", (x, mid))
        else:
            mid = int(self.store._run("INSERT INTO goal_metrics(goal_id, name, unit, start, target, created_at) VALUES(?,?,?,?,?,?)",
                                      (goal_id, name, _line(unit or ""), st, tg, self.clock())).lastrowid)
        if v is not None:
            self.store._run("INSERT INTO goal_metric_values(metric_id, value, note, at) VALUES(?,?,?,?)", (mid, v, _line(note), self.clock()))
            m = self.metric(goal_id, mid)
            self.log(goal_id, f"{name}: {_fmt(v)}{(' ' + m['unit']) if m['unit'] else ''}", "metric", by)
        self.touch(goal_id)
        return self.metric(goal_id, mid)

    # ------------------------------------------------------------------ log, documents
    def log(self, goal_id: int, text: str, kind: str = "note", by: str = "agent") -> dict[str, Any]:
        text = _text(text)
        if not text:
            raise GoalError("text is required")
        lid = self.store._run("INSERT INTO goal_log(goal_id, at, text, kind, by) VALUES(?,?,?,?,?)",
                              (goal_id, self.clock(), text, kind, by if by in ("user", "agent") else "agent")).lastrowid
        return {"id": lid, "text": text, "kind": kind}

    def log_t(self, goal_id: int, template: str, kind: str, by: str = "agent", suffix: str = "", **fields: Any) -> dict[str, Any]:
        """A history line from an English template, kept untranslated and shown in the reader's language (`logs()`).
        Translating when written left lines in whatever language the writing task happened to run in."""
        f = {k: ([[_line(a), None if b is None else _line(b)] for a, b in v] if isinstance(v, list) else _line(v)) for k, v in fields.items()}
        return self.log(goal_id, _LOG_T + json.dumps({"t": template, "f": f, "x": _line(suffix)}, ensure_ascii=False), kind, by)

    def _log_ids(self, goal_id: int) -> tuple[dict[int, str], list[int]]:
        names = {c["id"]: c["title"] for c in self.store._all("SELECT id, title FROM goals WHERE parent_id = ?", (goal_id,))}
        return names, [goal_id, *names]

    def log_count(self, goal_id: int) -> int:
        """Every entry of a goal and of its smaller goals (the apps page through them)."""
        _, ids = self._log_ids(goal_id)
        marks = ",".join("?" * len(ids))
        return int(self.store._all(f"SELECT COUNT(*) AS n FROM goal_log WHERE goal_id IN ({marks})", tuple(ids))[0]["n"])

    def logs(self, goal_id: int, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        """The entries of a goal and of its smaller goals, newest by their day, a page at a time (limit None: all of them); an entry of
        a smaller goal says so (from_goal)."""
        names, ids = self._log_ids(goal_id)
        marks = ",".join("?" * len(ids))
        page, args = ("", ()) if limit is None else (" LIMIT ? OFFSET ?", (int(limit), int(offset)))
        rows = self.store._all(f"SELECT id, goal_id, at, happened_at, title, outcome, text, kind, by FROM goal_log WHERE goal_id IN ({marks}) "
                               f"ORDER BY COALESCE(happened_at, at) DESC, id DESC{page}", (*ids, *args))
        return [self._entry_row(r, goal_id, names.get(r["goal_id"])) for r in rows]

    @staticmethod
    def _entry_row(r: dict[str, Any], goal_id: int, child_title: str | None) -> dict[str, Any]:
        return {"id": r["id"], "at": r["at"], "happened_at": r["happened_at"], "title": r["title"], "outcome": r["outcome"],
                "text": _log_text(r["text"]), "kind": r["kind"], "by": r["by"],
                "from_goal": {"id": r["goal_id"], "title": child_title or ""} if r["goal_id"] != goal_id else None}

    def _entry(self, goal_id: int, lid: int) -> dict[str, Any]:
        r = self.store._all("SELECT id, goal_id, at, happened_at, title, outcome, text, kind, by FROM goal_log WHERE id = ?", (lid,))[0]
        return self._entry_row(r, goal_id, None)

    def entry(self, goal_id: int, title: str, outcome: str, text: str, happened: Any = None, by: str = "agent") -> dict[str, Any]:
        """A dated entry: a few words (title), one line of result (outcome) and one sentence (text). Then the goal's one-line summary
        follows it when a summarizer is set."""
        text = _text(text)
        if not text:
            raise GoalError("text is required")
        now = self.clock()
        happened_at = _happened(happened)
        lid = self.store._run("INSERT INTO goal_log(goal_id, at, text, kind, by, title, outcome, happened_at) VALUES(?,?,?,?,?,?,?,?)",
                              (goal_id, now, text, "note", by if by in ("user", "agent") else "agent", _line(title),
                               _line(outcome), happened_at)).lastrowid
        self.touch(goal_id)
        row = self._entry(goal_id, lid)
        if self.summarize is not None:
            task = asyncio.ensure_future(self._refresh_summary(goal_id, row))
            self.summary_tasks.add(task)
            task.add_done_callback(self.summary_tasks.discard)
        return row

    def _own_entry(self, goal_id: int, entry_id: Any) -> None:
        try:
            eid = int(entry_id)
        except (TypeError, ValueError):
            raise GoalError("entry id must be a number") from None
        if not self.store._all("SELECT id FROM goal_log WHERE id = ? AND goal_id = ?", (eid, goal_id)):
            raise GoalError(f"goal #{goal_id} has no entry {eid}")

    def fix_entry(self, goal_id: int, entry_id: int, **fields: Any) -> dict[str, Any]:
        """Fixes an entry in place (title, outcome, text, happened). It never refreshes the summary."""
        given = {k: v for k, v in fields.items() if k in ("title", "outcome", "text", "happened") and v is not None}
        if not given:
            raise GoalError("give title, outcome, text or happened_on to change")
        self._own_entry(goal_id, entry_id)
        sets, args = [], []
        if "title" in given:
            sets.append("title = ?")
            args.append(_line(given["title"]))
        if "outcome" in given:
            sets.append("outcome = ?")
            args.append(_line(given["outcome"]))
        if "text" in given:
            text = _text(given["text"])
            if not text:
                raise GoalError("text is required")
            sets.append("text = ?")
            args.append(text)
        if "happened" in given:
            sets.append("happened_at = ?")
            args.append(_happened(given["happened"]))
        self.store._run(f"UPDATE goal_log SET {', '.join(sets)} WHERE id = ?", (*args, int(entry_id)))
        self.touch(goal_id)
        return self._entry(goal_id, int(entry_id))

    def remove_entry(self, goal_id: int, entry_id: int) -> dict[str, Any]:
        self._own_entry(goal_id, entry_id)
        self.store._run("DELETE FROM goal_log WHERE id = ?", (int(entry_id),))
        self.touch(goal_id)
        return {"id": int(entry_id)}

    async def _refresh_summary(self, goal_id: int, row: dict[str, Any]) -> None:
        """Replaces the summary line when the new entry supports a newer one; keeps it on KEEP, on no answer, or on an error."""
        g = self.store.goal(goal_id)
        if g is None or self.summarize is None:
            return
        try:
            line = await self.summarize(g, row)
        except Exception:  # noqa: BLE001 - a summary is a bonus: the entry stays and the line is kept
            return
        line = _line(line)
        if line and line != (g.get("summary") or ""):
            self.store._run("UPDATE goals SET summary = ? WHERE id = ?", (line, goal_id))

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
        if path == NOTES_DIR or path.startswith(NOTES_DIR + "/"):
            raise GoalError("that is your own notes folder: only documents the user asked for are attached to a goal")
        if not self._exists(path):
            raise GoalError(f"there is no file {path}")
        title = _line(title) or path.rsplit("/", 1)[-1]
        self.store._run("INSERT INTO goal_docs(goal_id, path, title, added_at) VALUES(?,?,?,?) ON CONFLICT(goal_id, path) DO UPDATE SET "
                        "title = excluded.title", (goal_id, path, title, self.clock()))
        self.touch(goal_id)
        return {"path": path, "title": title}

    def documents(self, goal_id: int) -> list[dict[str, Any]]:
        return [{**d, "exists": self._exists(d["path"])}
                for d in self.store._all("SELECT id, path, title, added_at FROM goal_docs WHERE goal_id = ? AND path NOT LIKE ? ORDER BY id",
                                         (goal_id, NOTES_DIR + "/%"))]

    def workspace(self, goal_id: int, schedules: list[dict[str, Any]] | None = None, *, all_log: bool = False) -> dict[str, Any]:
        """The goal screen's data: the first page of entries (log, log_next = the offset of the next page or None), or every entry when
        `all_log` (Kelo reads them all)."""
        g = self.need(goal_id)
        more = not all_log and self.log_count(g["id"]) > LOG_PAGE
        return {"goal": self.public(g), "facts": self.facts(g["id"]), "steps": self.steps(g["id"]), "metrics": self.metrics(g["id"]),
                "log": self.logs(g["id"], None if all_log else LOG_PAGE), "log_next": LOG_PAGE if more else None,
                "documents": self.documents(g["id"]), "schedules": schedules or [], "children": self.children(g["id"])}

    # ------------------------------------------------------------------ as text, for the model
    def text(self, goal_id: int, schedules: list[dict[str, Any]] | None = None) -> str:
        """The model's view of one goal or work item (goal_get / work_list): its header lines, then the plan, numbers, documents,
        schedules and the recent entries."""
        w = self.workspace(goal_id, schedules, all_log=True)
        g = w["goal"]
        note, due, _ = note_meta(self.store.goal(goal_id).get("note") or "")
        follow_names = []   # the follow items linked to this work item (carried.py): the same thing, shown once
        if self.carried is not None:
            for k in self.carried.of_work(goal_id):
                f = self.carried.follow_of(k) or {}
                follow_names.append(f"{k} {f.get('title', '')} ({f.get('state') or 'planned'})")
        word = "Goal" if g["kind"] == "goal" else "Work item"
        head = f"{word} #{g['id']}: {g['title']} [{g['status']}]"
        head += f" #{g['category']}" if g.get("category") else ""
        head += f" · area {g['area']}" if g.get("area") else ""
        if g.get("parent_id"):
            parent = self.store.goal(g["parent_id"]) or {}
            head += f" · under #{g['parent_id']} {parent.get('title', '')}".rstrip()
        out = [head,
               f"Summary: {g['summary']}" if g.get("summary") else "",
               f"About: {g['description']}" if g.get("description") else "",
               f"Note: {note}" if note else "",
               f"Due: {due}" if due else "",
               f"Done because: {g['done_reason']}" if g["status"] == "done" and g.get("done_reason") else "",
               f"Notes file: {self.notes_path(g)}" + ("" if self._exists(self.notes_path(g)) else " (none yet: write it with write_file)"),
               "Smaller goals: " + "; ".join(f"#{c['id']} {c['title']} [{c['status']}]" for c in w["children"]) if w["children"] else "",
               "Follow items: " + "; ".join(follow_names) if follow_names else "",
               "Waiting for the user's answers to your questions." if self.asking(self.need(goal_id)) else ""]
        out.append("Facts: " + ("; ".join(f"{f['key']} = " + ("(the user chose not to say)" if f["skipped"] else f["value"]) for f in w["facts"])
                                or "none yet"))
        out.append("Plan:" if w["steps"] else "Plan: none yet")
        out += [f"  {s['id']}. [{s['status']}] ({'you' if s['owner'] == 'agent' else 'user'}) {s['title']}" + (f" (due {s['due']})" if s["due"] else "")
                + (f": {s['detail']}" if s["detail"] else "") for s in w["steps"]]
        for m in w["metrics"]:
            vals = ", ".join(_fmt(v["value"]) for v in m["values"]) or "no values yet"
            out.append(f"Number '{m['name']}'{(' (' + m['unit'] + ')') if m['unit'] else ''}: start {_fmt(m['start'])}, target {_fmt(m['target'])}; values: {vals}")
        if w["documents"]:
            out.append("Documents: " + "; ".join(f"{d['title']} ({d['path']})" for d in w["documents"]))
        if w["schedules"]:
            out.append("Schedules: " + "; ".join(f"#{s['id']} {s['name']} ({s.get('description', '')})" for s in w["schedules"]))
        if w["log"]:
            out.append("Entries:")
            for r in w["log"]:
                left = f"{r['title']}: {r['outcome']}" if r["title"] and r["outcome"] else (r["title"] or r["outcome"])
                day = time.strftime("%Y-%m-%d", time.localtime(r["happened_at"] or r["at"]))
                origin = f" (from #{r['from_goal']['id']} {r['from_goal']['title']})" if r["from_goal"] else ""
                out.append(f"  - {day} " + (f"{left} — " if left else "") + f"{r['text']}{origin}")
        return "\n".join(x for x in out if x)

    def for_prompt(self) -> list[dict[str, Any]]:
        """The open goals and work items for the per-turn block (prompt.py goals_block): a smaller goal right after its parent, the same
        count cap for both kinds together; each one with its summary, area, due date and the note without its meta line."""
        open_ = [g for g in self.store.goals() if g.get("status") == "tracking"]
        ids = {g["id"] for g in open_}
        ordered: list[dict[str, Any]] = []
        for g in open_:
            if g.get("parent_id") in ids:
                continue                                          # listed right after its parent
            ordered.append(g)
            ordered += [c for c in open_ if c.get("parent_id") == g["id"]]
        out = []
        for g in ordered[:PROMPT_GOALS]:
            p = self.public(g)
            facts = self.facts(g["id"])
            steps = self.steps(g["id"])
            nxt = next((s for s in steps if s["status"] == "doing"), None) or next((s for s in steps if s["status"] == "todo"), None)
            note, due, _ = note_meta(g.get("note") or "")
            out.append({**p, "note": note, "due": due, "asking": self.asking(g), "facts": facts[:6],
                        "next_owner": nxt["owner"] if nxt else None})
        return out

    # ------------------------------------------------------------------ quick replies under a chat message
    @staticmethod
    def clean_questions(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise GoalError("questions must be a non-empty list of {question, options}")
        if len(raw) > MAX_QUESTIONS:
            raise GoalError("ask one question at a time: one question per message")
        out = []
        for q in raw:
            if not isinstance(q, dict):
                raise GoalError("each question is an object with options")
            kind = _clip(q.get("kind"), 20).lower() or "choice"
            if kind not in QUESTION_KINDS:
                raise GoalError(f"kind must be one of {', '.join(QUESTION_KINDS)}")
            opts = [o for o in (_clip(o, OPTION_MAX) for o in (q.get("options") or []) if isinstance(o, (str, int, float))) if o]
            opts = list(dict.fromkeys(opts))
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
            connect = None
            if q.get("connect") not in (None, [], ""):
                if kind != "choice" or not isinstance(q.get("connect"), list) or not all(isinstance(c, str) for c in q["connect"]):
                    raise GoalError("connect lists the ids of services to connect, as the connector list gives them")
                connect = [c.strip() for c in q["connect"]]
                if not 2 <= len(connect) <= 3 or len(opts) != len(connect) + 1:
                    raise GoalError("a connect question has two or three services and a last answer for not now")
                from .catalog import BY_ID
                if any(c not in BY_ID for c in connect):
                    raise GoalError("connect lists the ids of services to connect, as the connector list gives them")
            if connect is None and kind == "choice" and len(opts) > MAX_OPTIONS:
                raise GoalError("at most 3 answers: offer the concrete ones that matter")
            item: dict[str, Any] = {"question": _clip(q.get("question"), QUESTION_MAX), "options": opts}
            if connect is not None:
                item["connect"] = connect   # apps that ignore it show plain buttons
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
            if kind == "choice" and said_by:
                item["says"] = [said_by.get(o, "") for o in opts]
            if kind == "text" and (hint := _clip(q.get("hint"), QUESTION_MAX)):
                item["hint"] = hint
            if q.get("cards") not in (None, [], ""):
                if kind != "choice":
                    raise GoalError("cards only go with a choice question")
                if cards := GoalBook.clean_cards(q.get("cards"), q.get("options") or [], opts, connect=connect is not None):
                    item["cards"] = cards
            out.append(item)
        return out

    @staticmethod
    def clean_cards(raw: Any, raw_options: list[Any], opts: list[str], connect: bool = False) -> list[dict[str, Any]]:
        """The cards of a choice question, one per cleaned option (matched by label like "says"; {} = a plain option), or [] when none
        carries anything. A card's "image" stays the web address here; the quick_replies tool swaps it for a stored picture."""
        if not isinstance(raw, list) or len(raw) != len(raw_options):
            raise GoalError("cards must be a list with one card per option, in the same order as options")
        if len(raw_options) > (MAX_CONNECT_OPTIONS if connect else MAX_OPTIONS):
            raise GoalError("at most 3 answers: offer the concrete ones that matter (4 with a last answer for not now, for connecting)")
        by_label: dict[str, dict[str, Any]] = {}
        for o, c in zip(raw_options, raw):
            label = _clip(o, OPTION_MAX)
            if not label or label in by_label:
                continue
            if c is None:
                c = {}
            if not isinstance(c, dict):
                raise GoalError("each card is an object: {image?, detail?, recommended?}")
            card: dict[str, Any] = {}
            if (img := _clip(c.get("image"), CARD_IMAGE_MAX)):
                if not img.lower().startswith("https://"):
                    raise GoalError("a card's image is a public https address")
                card["image"] = img
            if detail := _clip(c.get("detail"), CARD_DETAIL_MAX):
                card["detail"] = detail
            if c.get("product") not in (None, ""):
                prod = str(c.get("product")).strip()
                if not ID_RE.match(prod):
                    raise GoalError("product is a result id from a job's product report")
                card["product"] = prod
            if c.get("recommended") is True:
                card["recommended"] = True
            by_label[label] = card
        if sum(1 for c in by_label.values() if c.get("recommended")) > 1:
            raise GoalError("at most one card is recommended")
        cards = [by_label.get(o, {}) for o in opts]
        return cards if any(cards) else []

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

    @staticmethod
    async def resolve_card_images(turn_id: str, qs: list[dict[str, Any]],
                                  card_images: Callable[[str, list[str]], Awaitable[list[dict[str, Any] | None]]] | None) -> list[str]:
        """Swaps each card's "image" address for the stored picture {"image_id", "url", "width", "height"} (in place); a picture that
        could not be had is dropped from its card. Returns the labels of the options whose picture failed."""
        failed: list[str] = []
        for q in qs:
            cards = q.get("cards")
            if not cards:
                continue
            at = [i for i, c in enumerate(cards) if c.get("image")]
            got: list[dict[str, Any] | None] = [None] * len(at)
            if at and card_images is not None:
                try:
                    got = list(await card_images(turn_id, [cards[i]["image"] for i in at]))
                except Exception:  # noqa: BLE001 - pictures are a bonus: the buttons still go
                    got = [None] * len(at)
            for i, pic in zip(at, got):
                if pic:
                    cards[i]["image"] = {"image_id": pic["id"], "url": pic["url"], "width": pic["width"], "height": pic["height"]}
                else:
                    cards[i].pop("image", None)
                    failed.append(q["options"][i])
            if not any(cards):
                del q["cards"]
        return failed

    def attach_replies(self, message_id: int, questions: list[dict[str, Any]], goal_id: int | None) -> None:
        self.store._run("INSERT OR REPLACE INTO message_replies(message_id, questions, goal_id, created_at) VALUES(?,?,?,?)",
                        (message_id, json.dumps(questions, ensure_ascii=False), goal_id, self.clock()))

    def detach_replies(self, message_id: int) -> None:
        """The quick-reply buttons of a message go (the message stays): an answered or outdated question."""
        self.store._run("DELETE FROM message_replies WHERE message_id=?", (message_id,))

    def questions_of(self, message_id: int) -> list[dict[str, Any]]:
        """The quick-reply questions of a message ([] when it has none)."""
        row = self.store._all("SELECT questions FROM message_replies WHERE message_id=?", (message_id,))
        return json.loads(row[0]["questions"] or "[]") if row else []

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
    def expire_suggestions(self) -> None:
        """A nightly suggestion nobody answered within SUGGEST_DAYS is gone (dismissed, so it never comes back)."""
        cut = self.clock() - SUGGEST_DAYS * 86400
        self.store._run("UPDATE goal_suggestions SET status = 'dismissed', updated_at = ? WHERE status = 'proposed' AND source = 'nightly' "
                        "AND created_at < ?", (self.clock(), cut))

    def suggestions(self, statuses: tuple[str, ...] = ("proposed",)) -> list[dict[str, Any]]:
        self.expire_suggestions()
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
        g = self.create(s["title"], note=s["reason"], origin="suggestion")
        self.store._run("UPDATE goal_suggestions SET status = 'accepted', goal_id = ?, updated_at = ? WHERE id = ?", (g["id"], self.clock(), sid))
        return self.store.goal(g["id"])

    def taken_titles(self) -> list[str]:
        """Goals and earlier suggestions, so the nightly run does not propose them again."""
        return [*(g["title"] for g in self.store.goals()), *(s["title"] for s in self.suggestions(SUG_STATUSES))]

    def guide(self, name: str) -> str:
        """A file of the goal-coaching skill (its front matter cut); "" when there is none."""
        if self.guides_dir is None:
            return ""
        try:
            text = (self.guides_dir / name).read_text(encoding="utf-8")
        except OSError:
            return ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[end + 4:]
        return text.strip()

    def kickoff_text(self, g: dict[str, Any]) -> str:
        """The setup turn: the goal-coaching start file and the goal's area file."""
        note_text = note_meta(g.get("note") or "")[0]
        note = f" (the user's note: {note_text})" if note_text else ""
        if self.guides_dir is None:
            guide = "Follow the goal-coaching skill (read_skill goal-coaching, file start.md)."
        elif g.get("area"):
            guide = self.guide("start.md") + "\n\n" + self.guide(f"area-{g['area']}.md")
        else:
            guide = (self.guide("start.md") + "\n\nThis goal has no area yet. Pick the one it belongs to (" + ", ".join(AREAS)
                     + "; other when none fits), save it with goal_update, and read that area's file with read_skill (name goal-coaching, "
                     "file area-<area>.md) before your first question.")
        return KICKOFF.format(mark=GOAL_MARK, id=g["id"], title=g["title"], note=note, word=feature_name(), guide=guide)

    def resume_text(self, g: dict[str, Any]) -> str:
        """Picking a goal up again: its area file only; the setup file is not read again (it would start the first conversation over)."""
        if g.get("area"):
            guide = (f"Help with it the way the goal-coaching skill says: read_skill goal-coaching and its file area-{g['area']}.md. "
                     "Do not read start.md: this goal is set up already.")
        else:
            guide = "Help with it the way the goal-coaching skill says (read_skill goal-coaching; this goal has no area, so that file alone)."
        return RESUME.format(mark=GOAL_MARK, id=g["id"], title=g["title"], guide=guide)

    # ------------------------------------------------------------------ the agent's tools
    def tools(self, current_turn: Callable[[], dict[str, Any] | None] = lambda: None,
              on_replies: Callable[[str, list[dict[str, Any]], int | None], None] = lambda _t, _q, _g: None,
              goal_schedules: Callable[[int], list[dict[str, Any]]] = lambda _g: [],
              link_schedule: Callable[[int, int], str] | None = None,
              on_goal: Callable[[str, int], None] = lambda _t, _g: None,
              card_images: Callable[[str, list[str]], Awaitable[list[dict[str, Any] | None]]] | None = None,
              product_of: Callable[[str], Product | None] | None = None,
              on_created: Callable[[int], None] = lambda _g: None) -> list[Tool]:
        """`on_goal(turn_id, goal_id)`: a tool of the running turn used that goal (the app then shows the goal as being worked on).
        `card_images(turn_id, urls)`: stores the pictures of choice cards (chat_images.ChatImages.card_pictures); one entry per address,
        None where a picture could not be had."""
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
            return await _read(a, "goal", "The user has no goals yet.")

        async def _read(a: dict[str, Any], kind: str, empty: str) -> str:
            if a.get("goal_id") not in (None, ""):
                g = self.need_kind(a.get("goal_id"), kind)
                return self.text(g["id"], goal_schedules(g["id"]))
            query = a.get("query")
            if query not in (None, ""):
                rows, total = self.search(kind, str(query), a.get("limit"))
                head = f"{total} match{'' if total == 1 else 'es'}"
                return "\n".join([head, *(_row_line(r) for r in rows)])
            rows, nxt = self.page(kind, a.get("limit"), a.get("offset"))
            if not rows:
                return empty
            return "\n".join([*(_row_line(r) for r in rows), *([f"More: offset {nxt}"] if nxt is not None else [])])

        async def _history(a: dict[str, Any], kind: str) -> str:
            g = self.need_kind(a.get("goal_id"), kind)
            word = "goal" if kind == "goal" else "work item"
            if a.get("entry_id") not in (None, ""):
                if a.get("remove") is True:
                    e = self.remove_entry(g["id"], a.get("entry_id"))
                    return f"Entry {e['id']} of {word} #{g['id']} removed."
                e = self.fix_entry(g["id"], a.get("entry_id"), title=a.get("title"), outcome=a.get("outcome"), text=a.get("text"),
                                   happened=a.get("happened_on"))
                return f"Entry {e['id']} of {word} #{g['id']} fixed."
            self.entry(g["id"], str(a.get("title") or ""), str(a.get("outcome") or ""), str(a.get("text") or ""), a.get("happened_on"))
            return f"Noted on {word} #{g['id']}."

        async def goal_create(a: dict[str, Any]) -> str:
            g = self.create(str(a.get("title") or ""), kind="goal", summary=str(a.get("summary") or ""),
                            description=str(a.get("description") or ""), area=str(a.get("area") or ""), parent_id=a.get("parent_id"),
                            origin="agent")
            on_created(int(g["id"]))
            return f'Saved goal #{g["id"]} "{g["title"]}". Notes file: {self.notes_path(g)} (write your setup notes there before this turn ends).'

        async def goal_update(a: dict[str, Any]) -> str:
            g = self.need_kind(a.get("goal_id"), "goal")
            changes = {k: a[k] for k in ("title", "summary", "description", "area", "parent_id", "due") if a.get(k) is not None}
            if not changes:
                raise GoalError("give what to change: title, summary, description, area, parent_id or due")
            self.update(g["id"], "goal", **changes)
            return self.text(g["id"], goal_schedules(g["id"]))

        async def goal_set_status(a: dict[str, Any]) -> str:
            return await _status(a, "goal")

        async def _status(a: dict[str, Any], kind: str) -> str:
            g, changed = self.set_status(a.get("goal_id"), kind, str(a.get("status") or ""), str(a.get("reason") or ""), "agent")
            word = "Goal" if kind == "goal" else "Work item"
            if not changed:
                return f"{word} #{g['id']} already was {g['status']}; nothing changed."
            return f"{word} #{g['id']} is now {g['status']}."

        async def work_create(a: dict[str, Any]) -> str:
            key = str(a.get("follow_key") or "").strip()
            if key and not self._follow_free(key):
                return "Error: that follow item is already one of the work items, or there is no such follow item."
            w = self.create(str(a.get("title") or ""), kind="work", summary=str(a.get("summary") or ""),
                            description=str(a.get("description") or ""), parent_id=a.get("parent_id"), origin="agent")
            if isinstance(a.get("refs"), dict):
                self.merge_refs(int(w["id"]), a["refs"])
            if key and self.carried is not None:
                self.carried.link(int(w["id"]), key, "kelo")
            return f'Keeping work item #{w["id"]} "{w["title"]}". Notes file: {self.notes_path(w)}.'

        async def work_list(a: dict[str, Any]) -> str:
            return await _read(a, "work", "No work items yet.")

        async def work_close(a: dict[str, Any]) -> str:
            return await _status(a, "work")

        async def goal_set_facts(a: dict[str, Any]) -> str:
            g = self.need_kind(a.get("goal_id"), "goal")
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
            g = self.need_kind(a.get("goal_id"), "goal")
            had = bool(self.steps(g["id"]))
            steps = self.plan(g["id"], a.get("steps"), bool(a.get("replace")))
            self.set_asking(g["id"], False)
            if not had or a.get("replace"):
                self.log_t(g["id"], N_("Made a plan of {n} steps"), "plan", n=len(steps))
            return f"Plan of goal #{g['id']}:\n" + "\n".join(f"{s['id']}. [{s['status']}] ({s['owner']}) {s['title']}" + (f" due {s['due']}" if s["due"] else "")
                                                             for s in steps)

        async def goal_step_done(a: dict[str, Any]) -> str:
            g = self.need_kind(a.get("goal_id"), "goal")
            s = self.set_step_status(g["id"], a.get("step_id"), str(a.get("status") or "done"), "agent", str(a.get("note") or ""))
            return f"Step {s['id']} of goal #{g['id']} is now {s['status']}."

        async def goal_log(a: dict[str, Any]) -> str:
            return await _history(a, "goal")

        async def work_log(a: dict[str, Any]) -> str:
            if isinstance(a.get("refs"), dict):
                self.merge_refs(int(self.need_kind(a.get("goal_id"), "work")["id"]), a["refs"])
            return await _history(a, "work")

        async def goal_metric_add(a: dict[str, Any]) -> str:
            g = self.need_kind(a.get("goal_id"), "goal")
            m = self.metric_add(g["id"], str(a.get("name") or ""), a.get("value"), a.get("unit"), a.get("start"), a.get("target"),
                                str(a.get("note") or ""))
            last = m["values"][-1]["value"] if m["values"] else None
            return (f"Number '{m['name']}' on goal #{g['id']}: start {_fmt(m['start'])}, target {_fmt(m['target'])}, latest {_fmt(last)}, "
                    f"{len(m['values'])} value(s).")

        async def goal_link_schedule(a: dict[str, Any]) -> str:
            g = self.need_kind(a.get("goal_id"), "goal")
            if link_schedule is None:
                raise GoalError("schedules are not available here")
            try:
                sid = int(a.get("schedule_id"))
            except (TypeError, ValueError):
                raise GoalError("schedule_id must be a schedule's number") from None
            return link_schedule(g["id"], sid)

        async def goal_add_document(a: dict[str, Any]) -> str:
            g = self.need_kind(a.get("goal_id"), "goal")
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
                gid = self.need_kind(a.get("goal_id"), "goal")["id"]
            turn = current_turn() or {}
            if not turn.get("turn_id"):
                return "Error: buttons can only go with a chat reply."
            for q in qs:
                cards = q.get("cards")
                if not cards:
                    continue
                if any(c.get("product") for c in cards):
                    for card in cards:
                        if not card.get("product"):
                            return ("Error: not shown. A choice of products to buy offers only products from a job's report, "
                                    "each card with its product id; do not mix in items from elsewhere.")
                    for card in cards:
                        if not card.get("product"):
                            continue
                        pid = card["product"]
                        p = product_of(pid) if product_of is not None else None
                        if p is None:
                            return (f"Error: not shown. {pid} is not a product a job saw on a seller's page. "
                                    "Offer only the ids listed in a job's report; never items or prices from search results or articles.")
                        detail_text = (f"{p.sale_price} ({p.price})" if p.sale_price else p.price) + f" · {p.seller}"
                        card["detail"] = detail_text[:CARD_DETAIL_MAX]
                        if p.image_url:
                            card["image"] = p.image_url
                        else:
                            card.pop("image", None)
                        card.pop("product", None)
            failed = await self.resolve_card_images(str(turn["turn_id"]), qs, card_images)
            on_replies(str(turn["turn_id"]), qs, gid)
            if gid:
                self.set_asking(gid, True)
            await emit_ui({"type": "replies", "questions": qs, "goal_id": gid})
            note = (f" The pictures of these options could not be loaded, so they show without one: {', '.join(failed)}." if failed else "")
            return (note.strip() + " " if note else "") + ("The buttons are attached to the message you already wrote in this reply. Do not write that message again and do not "
                    "repeat the questions: end your turn now with no more text. (If you have not written the questions yet, write them "
                    "once, briefly, and stop.)")

        gid = {"type": "integer", "description": "the goal's number (#id)"}
        step_props = {"id": {"type": "integer", "description": "an existing step to change; leave out to add a step"},
                      "title": {"type": "string"}, "detail": {"type": "string"},
                      "owner": {"type": "string", "enum": list(OWNERS), "description": "who does it: the user, or you (agent)"},
                      "status": {"type": "string", "enum": list(STEP_STATUSES)},
                      "due": {"type": "string", "description": "YYYY-MM-DD, if a date helps"}}
        return [
            Tool("goal_get", GOAL_GET_DESCRIPTION, {"type": "object", "properties": {
                "goal_id": gid, "query": {"type": "string", "description": "words to search the goals by"},
                "limit": {"type": "integer", "description": "how many: default 10 for a search, 100 for a list; at most 100"},
                "offset": {"type": "integer", "description": "the offset from the last page of a list"}}}, wrap(goal_get), read_only=True),
            Tool("goal_create", GOAL_CREATE_DESCRIPTION, {"type": "object", "properties": {
                "title": {"type": "string", "description": "in the user's own language and words"},
                "summary": {"type": "string", "description": "one line: where it stands now"},
                "description": {"type": "string", "description": "two or three plain sentences about the goal; progress goes in entries"},
                "area": {"type": "string", "enum": list(AREAS), "description": "the part of life it belongs to; other when none fits"},
                "parent_id": {"type": "integer", "description": "a top-level goal this smaller goal belongs under"}},
                "required": ["title", "summary", "description", "area"]}, wrap(goal_create)),
            Tool("goal_update", GOAL_UPDATE_DESCRIPTION, {"type": "object", "properties": {
                "goal_id": gid, "title": {"type": "string"}, "summary": {"type": "string"}, "description": {"type": "string"},
                "area": {"type": "string", "enum": list(AREAS)},
                "parent_id": {"type": "integer", "description": "a top-level goal to put it under; 0 takes it back to the top"},
                "due": {"type": "string", "description": "YYYY-MM-DD, or '' to clear the end date"}}, "required": ["goal_id"]}, wrap(goal_update)),
            Tool("goal_set_status", GOAL_STATUS_DESCRIPTION, {"type": "object", "properties": {
                "goal_id": gid, "status": {"type": "string", "enum": ["tracking", "done"]},
                "reason": {"type": "string", "description": "a short reason the user will read, in their language"}},
                "required": ["goal_id", "status", "reason"]}, wrap(goal_set_status)),
            Tool("work_create", WORK_CREATE_DESCRIPTION, {"type": "object", "properties": {
                "title": {"type": "string", "description": "a short noun phrase naming the work itself, not an order"},
                "summary": {"type": "string", "description": "one line: where it stands"},
                "description": {"type": "string", "description": "two or three plain sentences; progress goes in entries"},
                "parent_id": {"type": "integer", "description": "a top-level work item it belongs under"},
                "follow_key": {"type": "string", "description": "the key of the item on follow_list that is this same thing, if there is one"},
                "refs": {"type": "object", "description": "ids of this thing outside, copied exactly from a tool result: event, mail, flight, date, tracking, booking"}},
                "required": ["title", "summary", "description"]}, wrap(work_create)),
            Tool("work_list", WORK_LIST_DESCRIPTION, {"type": "object", "properties": {
                "goal_id": {"type": "integer", "description": "the work item's number, to read it in full"},
                "query": {"type": "string", "description": "words to search the work items by"},
                "limit": {"type": "integer"}, "offset": {"type": "integer"}}}, wrap(work_list), read_only=True),
            Tool("work_close", WORK_CLOSE_DESCRIPTION, {"type": "object", "properties": {
                "goal_id": {"type": "integer", "description": "the work item's number"},
                "status": {"type": "string", "enum": ["done", "tracking"]},
                "reason": {"type": "string", "description": "a short reason the user will read, in their language"}},
                "required": ["goal_id", "status", "reason"]}, wrap(work_close)),
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
            Tool("goal_log", GOAL_LOG_DESCRIPTION, {"type": "object", "properties": _history_props(gid), "required": ["goal_id"]},
                 wrap(goal_log)),
            Tool("work_log", WORK_LOG_DESCRIPTION, {"type": "object", "properties": {
                **_history_props({**gid, "description": "the work item's number"}),
                "refs": {"type": "object", "description": "ids of this thing outside, copied exactly from a tool result: event, mail, flight, date, tracking, booking"}},
                                                    "required": ["goal_id"]}, wrap(work_log)),
            Tool("goal_metric_add", "Follow a number that shows a goal's progress: create it by name and/or add a value (only one the user "
                 "gave or a tool returned).",
                 {"type": "object", "properties": {"goal_id": gid, "name": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"},
                                                   "start": {"type": "number"}, "target": {"type": "number"}, "note": {"type": "string"}},
                  "required": ["goal_id", "name"]}, wrap(goal_metric_add)),
            Tool("goal_link_schedule", "Tie an existing scheduled job to a goal.", {"type": "object", "properties": {"goal_id": gid, "schedule_id": {"type": "integer"}},
                                            "required": ["goal_id", "schedule_id"]}, wrap(goal_link_schedule)),
            Tool("goal_add_document", "Attach a document the user asked for (in their files) to a goal so it shows there. Never your notes or working files.",
                 {"type": "object", "properties": {"goal_id": gid, "path": {"type": "string"}, "title": {"type": "string"}},
                  "required": ["goal_id", "path"]}, wrap(goal_add_document)),
            Tool("goal_suggest", "Suggest a goal the user clearly wants to reach but did not ask you to track; they accept or decline it (never "
                 "add one for them). Never base one on a guess about health, money, relationships or anything else sensitive they did not "
                 "state.",
                 {"type": "object", "properties": {"title": {"type": "string"},
                                                   "reason": {"type": "string", "description": "one sentence, in the user's language"},
                                                   "evidence": {"type": "string", "description": "what they said"}},
                  "required": ["title", "reason"]}, wrap(goal_suggest)),
            Tool("quick_replies", "Put one tap-to-answer question under the message you are writing; ask one question at a time. A choice has "
                 "two or three concrete answers, your recommendation first; never a catch-all answer (other, not sure, rather not say): the user "
                 "can always type their own answer. A question about connecting a service may offer two or three real services to connect "
                 "(connect: their ids, in the order of the options) and a last answer for not now. When the user does not answer, go on with "
                 "general advice that does not need it. Options may carry cards (picture, one detail line, one recommended) when looks or price "
                 "matter. A choice of things to buy: each card's product is a result id from a job's product report and the card shows that "
                 "seller's price and picture; never offer items, prices or links from search results or articles. Only for a question you need "
                 "answered; never to confirm what the user already asked for.",
                 {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "object", "properties": {
                     "question": {"type": "string", "description": "the question in a few words"},
                     "kind": {"type": "string", "enum": list(QUESTION_KINDS),
                              "description": "default choice; any other kind opens the phone's own picker"},
                     "options": {"type": "array", "items": {"type": "string"},
                                 "description": "choice: 2-3 short answers in the user's language (a connect question: one per service, then not now)"},
                     "connect": {"type": "array", "items": {"type": "string"},
                                 "description": "only for a question about connecting a service: the service ids, one per option except the last (not now)"},
                     "cards": {"type": "array", "description": "optional, one per option in the same order (max 3 options; a connect question 4): shows the option as a card",
                               "items": {"type": "object", "properties": {
                                   "image": {"type": "string", "description": "public https picture address"},
                                   "detail": {"type": "string", "description": "one short line, e.g. price · arrival"},
                                   "recommended": {"type": "boolean", "description": "at most one"},
                                   "product": {"type": "string", "description": "a result id from a job's product report (p + 10 hex)"}}}},
                     "says": {"type": "array", "items": {"type": "string"},
                              "description": "optional, one per option: the whole sentence the user sends by tapping it, in their "
                                             "language (option 'Plan B' -> 'Let's go with Plan B, the 500M internet + TV bundle'); the "
                                             "button keeps the short label"},
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
    value: str = ""
    skipped: bool = False


class MetricValueIn(BaseModel):
    value: float
    note: str = ""


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

    @app.get("/api/goals/{goal_id}/log", dependencies=[Depends(auth)])
    async def goal_log_page(goal_id: int, offset: int = 0, limit: int = LOG_PAGE) -> dict[str, Any]:
        """The entries of a goal a page at a time: newest first; `next` is the offset of the next page (null at the last one)."""
        need(goal_id)
        if offset < 0 or limit < 1:
            raise HTTPException(422, "offset and limit must be numbers from 0 (limit from 1)")
        limit = min(limit, LOG_PAGE)
        items = book.logs(goal_id, limit, offset)
        more = book.log_count(goal_id) > offset + len(items)
        return {"items": items, "next": offset + len(items) if more and items else None}

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
