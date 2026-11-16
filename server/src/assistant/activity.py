"""What the agent did, for the app's agent screen: one task record per reply (typed in chat, started by a schedule, or a browser
task), every approval it asked for and how it was answered, the user's standing "allow on this site" rules, and a feed of
notifications (each one also wakes the paired phones with a push when nobody saw it arrive; see push.py).

Everything lives in the app's SQLite file next to the conversation. No secret ever goes in: approvals keep the tool, its short
label, the site and the one-line description shown on the card (never the arguments), and tasks keep the user's words and the
assistant's answer, as the conversation does.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .agent.found_secrets import scrub

from . import i18n
from .i18n import N_, t

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, status TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'chat',
    started_at REAL NOT NULL, finished_at REAL, seen INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS tasks_source ON tasks(source);
CREATE TABLE IF NOT EXISTS approval_log (
    id TEXT PRIMARY KEY, task_id INTEGER, tool TEXT NOT NULL, label TEXT NOT NULL, site TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT 'pending', asked_at REAL NOT NULL, answered_at REAL,
    rule_id INTEGER, unattended INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS approval_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, site TEXT NOT NULL, created_at REAL NOT NULL,
    from_approval TEXT NOT NULL DEFAULT '', uses INTEGER NOT NULL DEFAULT 0, last_used REAL, UNIQUE(kind, site));
CREATE TABLE IF NOT EXISTS schedule_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL, tool TEXT NOT NULL, site TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, from_approval TEXT NOT NULL DEFAULT '', uses INTEGER NOT NULL DEFAULT 0, last_used REAL,
    UNIQUE(schedule_id, tool, site));
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
    task_id INTEGER, approval_id TEXT, schedule_id INTEGER, payment_id INTEGER, created_at REAL NOT NULL, seen INTEGER NOT NULL DEFAULT 0);
"""

TASK_STATUSES = ("running", "waiting_approval", "done", "failed", "stopped", "interrupted", "waiting_model")
DECISIONS = ("pending", "allowed_once", "allowed_always", "allowed_by_rule", "denied", "expired", "cancelled",
             "solved", "skipped", "progressed")   # the last three: a human check handed to the user (tool "browser_check");
                                                  # progressed: the user's part of a sign-in is done, the sign-in goes on
KEEP_TASKS = 5000
KEEP_APPROVALS = 3000
KEEP_NOTIFICATIONS = 1000
RESTART_RESULT = N_("Stopped because the server restarted")   # the result of a task a restart cut off (not something the user did)

# Approvals that may be remembered per site, and under which kind. A rule allows the same kind of step on the same site later.
# Never: payments (confirm_purchase and anything of the wallet), schedules, and every tool not listed here.
RULE_KINDS: dict[str, str] = {"browser_click": "browser_action", "browser_type": "browser_action", "browser_login": "login",
                              # a point step: only when its element was read (one Kelo could not read never matches a rule, turn_engine)
                              "browser_click_at": "browser_action", "browser_drag": "browser_action",
                              "workroom_install": "install"}   # install: one package (its "site" is <name>.pypi|npm.install, workroom.install_site)
KIND_LABELS = {"browser_action": N_("Clicking and typing on a site"), "login": N_("Typing a saved login"),
               "install": N_("Installing a program in the work room")}
NEVER_REMEMBER = ("confirm_purchase", "purchase_result", "browser_fill_card", "wallet_", "browser_upload", "find_login_code",   # the last two: every time
                  "task_allow")   # the setup question of a conditional task is asked once, never remembered


def restart_results() -> list[str]:
    """RESTART_RESULT in every language it may have been saved in (it is stored in the owner's language at the time)."""
    return sorted({RESTART_RESULT, *(i18n.catalog(lang).get(RESTART_RESULT, RESTART_RESULT) for lang in i18n.available())})


def rule_kind(tool: str) -> str:
    """The kind of rule an approval of `tool` may become, or "" when it can never be remembered."""
    if tool.startswith(NEVER_REMEMBER) or tool.startswith("schedule_"):
        return ""
    return RULE_KINDS.get(tool, "")


def schedule_rule_ok(tool: str) -> bool:
    """Whether an approval of `tool` in a scheduled run may become a rule for that schedule ("keep allowing in this scheduled job").
    Never for payments (confirm_purchase, card filling, anything of the wallet) or for changing schedules themselves."""
    return bool(tool) and not tool.startswith(NEVER_REMEMBER) and not tool.startswith("schedule_")


def host_of(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    host = urlsplit(text if "://" in text else "https://" + text).hostname or ""
    return host.lower().removeprefix("www.")


def same_site(a: str, b: str) -> bool:
    a, b = host_of(a), host_of(b)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


_TIME_TAG = re.compile(r"^\s*\[[^\]]*\]\s*\[client_timezone=[^\]]*\]\s*", re.S)


def title_from(text: str, limit: int = 40) -> str:
    """A short title for a task from what the user wrote: its first line or sentence, trimmed at a word."""
    s = _TIME_TAG.sub("", text or "")
    s = re.sub(r"\s+", " ", s.strip().split("\n", 1)[0]).strip()
    s = re.split(r"(?<=[.!?。])\s", s, maxsplit=1)[0].strip() or s
    if len(s) <= limit:
        return s or t("Conversation")
    cut = s[:limit].rsplit(" ", 1)[0] if " " in s[:limit] else s[:limit]
    return cut.rstrip(",.;: ") + "…"


SHORT_REPLY_CHARS = 6   # letters and digits; a message this short is an answer to the assistant, not a task of its own


def is_short_reply(text: str) -> bool:
    """A short answer to the assistant's own question ("너가", "응", "yes, you do it"): it names no task, so it makes a poor title."""
    return len(re.sub(r"[\W_]+", "", _TIME_TAG.sub("", text or ""))) <= SHORT_REPLY_CHARS


def task_title(current: str, earlier_user_messages: list[str], limit: int = 40) -> str:
    """The title of a reply's task: from the owner's message, or, when that is only a short answer to a question of the assistant
    ("제가 할까요?" → "너가"), from their latest earlier message that said what to do (owner 2026-11-14)."""
    if is_short_reply(current):
        for text in earlier_user_messages:
            if not is_short_reply(text):
                return title_from(text, limit)
    return title_from(current, limit)


def result_line(answer: str, limit: int = 120) -> str:
    """The first sentence of an answer, without Markdown, for one line under the task title."""
    t = (answer or "").replace("<message_break/>", "\n")
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_`#>]+", "", t)
    for line in t.split("\n"):
        line = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", line).strip()
        if line:
            t = line
            break
    else:
        return ""
    first = re.split(r"(?<=[.!?。])\s", t, maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


class Activity:
    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self._clock = store, clock
        self.on_notify: Callable[[dict[str, Any]], None] | None = None   # set by the server: a push to the phones (push.PushClient.notify)
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(tasks)")}
            for col, ddl in (("parent_id", "INTEGER"), ("worker_id", "TEXT NOT NULL DEFAULT ''")):
                if col not in have:   # a database from before background jobs (workers.py) could be linked to the task that started them
                    store._db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
            for col, ddl in (("resume", "TEXT NOT NULL DEFAULT ''"), ("restarts", "INTEGER NOT NULL DEFAULT 0"),
                             ("resumed_from", "INTEGER"), ("resumed_as", "INTEGER"), ("resume_state", "TEXT NOT NULL DEFAULT ''"),
                             ("wait_since", "REAL"), ("retry_at", "REAL"), ("waits", "INTEGER NOT NULL DEFAULT 0"),
                             ("wait_noted", "INTEGER NOT NULL DEFAULT 0")):
                if col not in have:   # going on after a restart or a model outage (resume.py)
                    store._db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
            store._db.execute("CREATE TABLE IF NOT EXISTS resume_offers (message_id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, "
                              "why TEXT NOT NULL, label TEXT NOT NULL, created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'open')")
            note_cols = {r[1] for r in store._db.execute("PRAGMA table_info(notifications)")}
            if "followup_id" not in note_cols:
                store._db.execute("ALTER TABLE notifications ADD COLUMN followup_id INTEGER")   # a follow-up's notification (followups.py)
            if "provider" not in note_cols:   # the provider a notification is about (cli_update: which CLI's box My → Model opens at)
                store._db.execute("ALTER TABLE notifications ADD COLUMN provider TEXT NOT NULL DEFAULT ''")
            if "urgent" not in note_cols:     # time matters (a card of a task marked urgent at setup): the phone may break quiet hours
                store._db.execute("ALTER TABLE notifications ADD COLUMN urgent INTEGER NOT NULL DEFAULT 0")
            store._db.commit()
        now = clock()
        # a restart ended whatever was running: "interrupted", and the server decides at start-up whether it goes on (resume.py)
        self.interrupt("status IN ('running', 'waiting_approval')", ())
        store._run("UPDATE approval_log SET decision = 'cancelled', answered_at = ? WHERE decision = 'pending'", (now,))

    def interrupt(self, where: str, args: tuple[Any, ...]) -> int:
        """Tasks a restart cut off: "interrupted" (not "stopped": the user did not stop them), waiting to be looked at once the server
        is up again (resume_state "pending")."""
        return self.store._run("UPDATE tasks SET status = 'interrupted', finished_at = ?, resume_state = 'pending', "
                               f"result = CASE WHEN result = '' THEN ? ELSE result END WHERE {where}",
                               (self._clock(), t(RESTART_RESULT), *args)).rowcount

    def interrupt_tasks(self, task_ids: list[int]) -> int:
        """At shutdown: these running tasks are interrupted (their turns are about to be cut off)."""
        if not task_ids:
            return 0
        marks = ",".join("?" * len(task_ids))
        return self.interrupt(f"id IN ({marks}) AND status IN ('running', 'waiting_approval')", tuple(task_ids))

    # ------------------------------------------------------------------ going on after a restart or a model outage (resume.py)
    def set_resume(self, task_id: int, spec: dict[str, Any], restarts: int = 0, resumed_from: int | None = None) -> None:
        self.store._run("UPDATE tasks SET resume = ?, restarts = ?, resumed_from = ? WHERE id = ?",
                        (json.dumps(spec, ensure_ascii=False), restarts, resumed_from, task_id))

    def resume_row(self, task_id: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not rows:
            return None
        r = dict(rows[0])
        try:
            r["spec"] = json.loads(r["resume"]) if r["resume"] else None
        except ValueError:
            r["spec"] = None
        return r

    def pending_resumes(self) -> list[dict[str, Any]]:
        ids = [r["id"] for r in self.store._all("SELECT id FROM tasks WHERE resume_state = 'pending' ORDER BY id")]
        return [r for r in (self.resume_row(i) for i in ids) if r is not None]

    def resolve_resume(self, task_id: int, state: str, resumed_as: int | None = None, *, status: str | None = None,
                       result: str | None = None, seen: bool | None = None) -> None:
        """What became of a task that was cut off: resumed (as `resumed_as`) | offered (a Continue button) | failed | skipped."""
        sets, args = ["resume_state = ?"], [state]
        if resumed_as is not None:
            sets.append("resumed_as = ?")
            args.append(resumed_as)
        if status is not None:
            sets += ["status = ?", "finished_at = COALESCE(finished_at, ?)"]
            args += [status, self._clock()]
        if result is not None:
            sets.append("result = ?")
            args.append(result[:160])
        if seen is not None:
            sets.append("seen = ?")
            args.append(int(seen))
        self.store._run(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*args, task_id))

    def wait_for_model(self, task_id: int, retry_at: float) -> None:
        """The AI model is down: the task waits, and is checked again at `retry_at`."""
        self.store._run("UPDATE tasks SET status = 'waiting_model', resume_state = 'model', wait_since = COALESCE(wait_since, ?), "
                        "retry_at = ?, finished_at = NULL WHERE id = ?", (self._clock(), retry_at, task_id))

    def model_waits(self) -> list[dict[str, Any]]:
        ids = [r["id"] for r in self.store._all("SELECT id FROM tasks WHERE status = 'waiting_model' ORDER BY id")]
        return [r for r in (self.resume_row(i) for i in ids) if r is not None]

    def bump_wait(self, task_id: int, retry_at: float, noted: bool = False) -> None:
        self.store._run("UPDATE tasks SET waits = waits + 1, retry_at = ?, wait_noted = MAX(wait_noted, ?) WHERE id = ?",
                        (retry_at, int(noted), task_id))

    def add_offer(self, message_id: int, task_id: int, why: str, label: str) -> None:
        """A "Continue" button under agent message `message_id` that resumes task `task_id` (why: restart | model)."""
        self.store._run("INSERT OR REPLACE INTO resume_offers(message_id, task_id, why, label, created_at, state) VALUES(?,?,?,?,?,'open')",
                        (message_id, task_id, why, label, self._clock()))

    def open_offer(self, after_user: int) -> dict[str, Any] | None:
        """The newest open "Continue" offer on a message after the user's last message `after_user`, if any."""
        rows = self.store._all("SELECT * FROM resume_offers WHERE state = 'open' AND message_id > ? ORDER BY message_id DESC LIMIT 1",
                               (after_user,))
        return dict(rows[0]) if rows else None

    def offer_for_task(self, task_id: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM resume_offers WHERE task_id = ? AND state = 'open' ORDER BY message_id DESC LIMIT 1", (task_id,))
        return dict(rows[0]) if rows else None

    def close_offer(self, message_id: int, state: str = "used") -> None:
        self.store._run("UPDATE resume_offers SET state = ? WHERE message_id = ?", (state, message_id))

    def _tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.profile().get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    # ------------------------------------------------------------------ tasks
    def start_task(self, title: str, source: str = "chat", turn_id: str = "") -> int:
        tid = int(self.store._run("INSERT INTO tasks(turn_id, title, status, source, started_at, seen) VALUES(?,?,?,?,?,0)",
                                  (turn_id, (title or t("Conversation"))[:80], "running", source, self._clock())).lastrowid or 0)
        self.store._run("DELETE FROM tasks WHERE id <= ? - ?", (tid, KEEP_TASKS))
        return tid

    def rename_task(self, task_id: int, title: str) -> None:
        """A better title for a task (task_names.py). The Work screen reads /api/tasks, so the next refresh shows it."""
        if title.strip():
            self.store._run("UPDATE tasks SET title = ? WHERE id = ?", (title.strip()[:80], task_id))

    def link_task(self, task_id: int, parent_id: int | None, worker_id: str = "") -> None:
        """A task that a background job (workers.py) ran, or that took its report: tied to the task that started the job, so the
        agent screen can show it under that one."""
        self.store._run("UPDATE tasks SET parent_id = ?, worker_id = ? WHERE id = ?", (parent_id, worker_id, task_id))

    def set_task_status(self, task_id: int, status: str) -> None:
        self.store._run("UPDATE tasks SET status = ? WHERE id = ? AND status IN ('running', 'waiting_approval')", (status, task_id))

    def finish_task(self, task_id: int, status: str, *, answer: str = "", error: str = "", seen: bool = False,
                    input_tokens: int = 0, output_tokens: int = 0) -> dict[str, Any] | None:
        if status == "failed":
            result = t("Failed: {error}", error=error)[:160] if error else t("It failed")
        elif status == "stopped":
            result = result_line(answer) or t("Stopped partway")
        elif status == "waiting_model":
            result = t("Waiting for the AI model to come back")
        elif answer.strip() == "NO_REPLY":   # a run nobody watched that had nothing worth telling (compaction.NO_REPLY)
            result = t("There was nothing to tell")
        else:
            result = result_line(answer) or t("Finished")
        self.store._run("UPDATE tasks SET status = ?, result = ?, answer = ?, error = ?, finished_at = ?, seen = ?, input_tokens = ?, "
                        "output_tokens = ? WHERE id = ?",
                        (status, result, answer[:20000], error[:500], self._clock(), int(seen), input_tokens, output_tokens, task_id))
        return self.task(task_id)

    def _task(self, r: dict[str, Any], full: bool = False) -> dict[str, Any]:
        src = r["source"]
        out = {"id": r["id"], "turn_id": r["turn_id"], "title": r["title"], "status": r["status"], "result": r["result"], "source": src,
               "schedule_id": int(src.split(":", 1)[1]) if src.startswith("schedule:") and src.split(":", 1)[1].isdigit() else None,
               "started_at": r["started_at"], "finished_at": r["finished_at"], "seen": bool(r["seen"]),
               "day": datetime.fromtimestamp(r["started_at"], self._tz()).strftime("%Y-%m-%d"),
               "parent_id": r.get("parent_id"), "worker_id": r.get("worker_id") or None,
               "resumed_from": r.get("resumed_from"), "resumed_as": r.get("resumed_as")}
        if full:
            out.update(answer=r["answer"], error=r["error"], input_tokens=r["input_tokens"], output_tokens=r["output_tokens"])
        return out

    def task(self, task_id: int, full: bool = False) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._task(rows[0], full) if rows else None

    def tasks(self, limit: int = 50, before: int | None = None, source: str | None = None) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM tasks WHERE (? IS NULL OR id < ?) AND (? IS NULL OR source = ?) ORDER BY id DESC LIMIT ?",
                               (before, before, source, source, max(1, min(limit, 200))))
        return [self._task(r) for r in rows]

    def unseen_tasks(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM tasks WHERE seen = 0 AND status NOT IN ('running', 'waiting_approval', 'waiting_model')")[0]["n"])

    def mark_tasks_seen(self, up_to: int) -> int:
        n = self.store._run("UPDATE tasks SET seen = 1 WHERE id <= ? AND seen = 0 AND status NOT IN ('running', 'waiting_approval', 'waiting_model')",
                            (up_to,)).rowcount
        # a finished task that was looked at no longer needs its notification (else the "something new" dot never goes away)
        self.store._run("UPDATE notifications SET seen = 1 WHERE seen = 0 AND task_id IS NOT NULL AND approval_id IS NULL "
                        "AND task_id IN (SELECT id FROM tasks WHERE seen = 1)")
        return n

    # ------------------------------------------------------------------ approvals
    def log_approval(self, approval_id: str, *, task_id: int | None, tool: str, label: str, site: str, detail: str,
                     decision: str = "pending", rule_id: int | None = None, unattended: bool = False) -> None:
        now = self._clock()
        self.store._run("INSERT OR REPLACE INTO approval_log(id, task_id, tool, label, site, detail, decision, asked_at, answered_at, rule_id, "
                        "unattended) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (approval_id, task_id, tool, label, site, detail[:300], decision, now, None if decision == "pending" else now,
                         rule_id, int(unattended)))
        self.store._run("DELETE FROM approval_log WHERE rowid <= (SELECT MAX(rowid) FROM approval_log) - ?", (KEEP_APPROVALS,))

    def answer_approval(self, approval_id: str, decision: str) -> None:
        """Only the first answer counts: a late reply cannot turn an expired request into an allowed one."""
        self.store._run("UPDATE approval_log SET decision = ?, answered_at = ? WHERE id = ? AND decision = 'pending'",
                        (decision, self._clock(), approval_id))

    def approval(self, approval_id: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM approval_log WHERE id = ?", (approval_id,))
        return self._approval(rows[0]) if rows else None

    @staticmethod
    def _approval(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "task_id": r["task_id"], "tool": r["tool"], "label": r["label"], "site": r["site"], "detail": r["detail"],
                "decision": r["decision"], "asked_at": r["asked_at"], "answered_at": r["answered_at"], "rule_id": r["rule_id"],
                "unattended": bool(r["unattended"]), "can_remember": bool(rule_kind(r["tool"]) and r["site"])}

    def approvals(self, limit: int = 50, before: float | None = None, task_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM approval_log WHERE (? IS NULL OR asked_at < ?) AND (? IS NULL OR task_id = ?) "
                               "ORDER BY asked_at DESC, rowid DESC LIMIT ?", (before, before, task_id, task_id, max(1, min(limit, 200))))
        return [self._approval(r) for r in rows]

    def setbacks_since(self, since: float, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        """For the nightly look-back (reflection.py): approvals the user denied and tasks that failed or were stopped since `since`
        (newest first, at most `limit` each). Tasks a restart cut off are left out: the user did not stop those."""
        denied = self.store._all("SELECT label, site, detail, asked_at FROM approval_log WHERE decision = 'denied' AND asked_at >= ? "
                                 "ORDER BY asked_at DESC LIMIT ?", (since, limit))
        tasks = self.store._all("SELECT title, status, result, error, started_at FROM tasks WHERE status IN ('failed', 'stopped') "
                                "AND started_at >= ? AND result NOT IN (SELECT value FROM json_each(?)) ORDER BY started_at DESC LIMIT ?",
                                (since, json.dumps(restart_results()), limit))
        return {"denied": denied, "tasks": tasks}

    def denials_since(self, since: float, limit: int = 200) -> list[dict[str, Any]]:
        """Every approval the user denied since `since`, oldest first (reflection.py groups them into separate occasions)."""
        return self.store._all("SELECT tool, label, site, asked_at FROM approval_log WHERE decision = 'denied' AND asked_at >= ? "
                               "ORDER BY asked_at LIMIT ?", (since, limit))

    # ------------------------------------------------------------------ "allow on this site" rules
    def add_rule(self, tool: str, site: str, from_approval: str = "") -> dict[str, Any] | None:
        kind, host = rule_kind(tool), host_of(site)
        if not kind or not host:
            return None
        self.store._run("INSERT INTO approval_rules(kind, site, created_at, from_approval) VALUES(?,?,?,?) ON CONFLICT(kind, site) DO NOTHING",
                        (kind, host, self._clock(), from_approval))
        rows = self.store._all("SELECT * FROM approval_rules WHERE kind = ? AND site = ?", (kind, host))
        return self._rule(rows[0]) if rows else None

    @staticmethod
    def _rule(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "kind": r["kind"], "kind_label": t(KIND_LABELS[r["kind"]]) if r["kind"] in KIND_LABELS else r["kind"], "site": r["site"],
                "created_at": r["created_at"], "uses": r["uses"], "last_used": r["last_used"]}

    def rules(self) -> list[dict[str, Any]]:
        return [self._rule(r) for r in self.store._all("SELECT * FROM approval_rules ORDER BY created_at DESC")]

    def delete_rule(self, rule_id: int) -> bool:
        return self.store._run("DELETE FROM approval_rules WHERE id = ?", (rule_id,)).rowcount > 0

    def matching_rule(self, tool: str, site: str) -> dict[str, Any] | None:
        kind, host = rule_kind(tool), host_of(site)
        if not kind or not host:
            return None
        for r in self.store._all("SELECT * FROM approval_rules WHERE kind = ?", (kind,)):
            if r["site"] == host or host.endswith("." + r["site"]):   # a rule for a site covers its subdomains, never the other way
                self.store._run("UPDATE approval_rules SET uses = uses + 1, last_used = ? WHERE id = ?", (self._clock(), r["id"]))
                return self._rule(r)
        return None

    # ------------------------------------------------------------------ "keep allowing in this scheduled job" rules
    # A rule allows the same tool on the same site ("" = a tool that acts on no site) in the runs of one schedule only.
    def add_schedule_rule(self, schedule_id: int, tool: str, site: str, from_approval: str = "") -> dict[str, Any] | None:
        if not schedule_rule_ok(tool) or not schedule_id:
            return None
        host = host_of(site) if site and not site.endswith(".install") else (site or "")
        self.store._run("INSERT INTO schedule_rules(schedule_id, tool, site, created_at, from_approval) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(schedule_id, tool, site) DO NOTHING", (schedule_id, tool, host, self._clock(), from_approval))
        rows = self.store._all("SELECT * FROM schedule_rules WHERE schedule_id = ? AND tool = ? AND site = ?", (schedule_id, tool, host))
        return self._schedule_rule(rows[0]) if rows else None

    @staticmethod
    def _schedule_rule(r: dict[str, Any]) -> dict[str, Any]:
        from . import tool_labels
        return {"id": r["id"], "schedule_id": r["schedule_id"], "tool": r["tool"], "label": tool_labels.label(r["tool"]), "site": r["site"],
                "created_at": r["created_at"], "uses": r["uses"], "last_used": r["last_used"]}

    def schedule_rules(self, schedule_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM schedule_rules WHERE (? IS NULL OR schedule_id = ?) ORDER BY created_at DESC",
                               (schedule_id, schedule_id))
        return [self._schedule_rule(r) for r in rows]

    def delete_schedule_rule(self, rule_id: int, schedule_id: int | None = None) -> bool:
        return self.store._run("DELETE FROM schedule_rules WHERE id = ? AND (? IS NULL OR schedule_id = ?)",
                               (rule_id, schedule_id, schedule_id)).rowcount > 0

    def matching_schedule_rule(self, schedule_id: int | None, tool: str, site: str) -> dict[str, Any] | None:
        if not schedule_id or not schedule_rule_ok(tool):
            return None
        host = host_of(site) if site and not site.endswith(".install") else (site or "")
        for r in self.store._all("SELECT * FROM schedule_rules WHERE schedule_id = ? AND tool = ?", (schedule_id, tool)):
            if r["site"] == host or (r["site"] and host.endswith("." + r["site"])):
                self.store._run("UPDATE schedule_rules SET uses = uses + 1, last_used = ? WHERE id = ?", (self._clock(), r["id"]))
                return self._schedule_rule(r)
        return None

    # ------------------------------------------------------------------ notifications
    def notify(self, type_: str, title: str, body: str = "", *, task_id: int | None = None, approval_id: str | None = None,
               schedule_id: int | None = None, payment_id: int | None = None, push: bool = True, followup_id: int | None = None,
               provider: str = "", urgent: bool = False) -> int:
        """Adds a notification. `push`: also wake the phones (false when the user is following the reply right now). `provider`: the
        provider it is about (cli_update: the apps open My → Model at that CLI)."""
        title, body = scrub(title), scrub(body)   # a key never goes into a notification (agent/found_secrets.py)
        nid = int(self.store._run("INSERT INTO notifications(type, title, body, task_id, approval_id, schedule_id, payment_id, created_at, "
                                  "followup_id, provider, urgent) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                  (type_, title[:120], body[:400], task_id, approval_id, schedule_id, payment_id, self._clock(), followup_id,
                                   provider, int(bool(urgent)))).lastrowid or 0)
        self.store._run("DELETE FROM notifications WHERE id <= ? - ?", (nid, KEEP_NOTIFICATIONS))
        if push and self.on_notify is not None:
            try:   # ids only: the push never carries the title or body
                self.on_notify({"id": nid, "type": type_, "task_id": task_id, "approval_id": approval_id, "schedule_id": schedule_id})
            except Exception:  # noqa: BLE001 - a push problem never breaks the notification itself
                logging.getLogger("uvicorn.error").warning("push: could not queue a %s push", type_)
        return nid

    @staticmethod
    def _note(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "type": r["type"], "title": r["title"], "body": r["body"], "task_id": r["task_id"], "approval_id": r["approval_id"],
                "schedule_id": r["schedule_id"], "payment_id": r["payment_id"], "followup_id": r.get("followup_id"), "created_at": r["created_at"],
                "seen": bool(r["seen"]), "provider": r.get("provider") or "", "urgent": bool(r.get("urgent"))}

    def notification(self, nid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM notifications WHERE id = ?", (nid,))
        return self._note(rows[0]) if rows else None

    def notifications(self, after: int = 0, limit: int = 50) -> dict[str, Any]:
        rows = self.store._all("SELECT * FROM notifications WHERE id > ? ORDER BY id DESC LIMIT ?", (after, max(1, min(limit, 200))))
        last = self.store._all("SELECT COALESCE(MAX(id), 0) AS m FROM notifications")[0]["m"]
        return {"items": [self._note(r) for r in rows], "unseen": self.unseen_notifications(), "last_id": last}

    def unseen_notifications(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM notifications WHERE seen = 0")[0]["n"])

    def mark_notifications_seen(self, up_to: int) -> tuple[int, list[int]]:
        """Marks notifications up to `up_to` seen; returns how many, and the wallet payments among them (to mark those seen too)."""
        pays = [r["payment_id"] for r in self.store._all("SELECT payment_id FROM notifications WHERE id <= ? AND seen = 0 AND payment_id IS NOT NULL",
                                                         (up_to,))]
        n = self.store._run("UPDATE notifications SET seen = 1 WHERE id <= ? AND seen = 0", (up_to,)).rowcount
        return n, pays

    def mark_payment_notes_seen(self, up_to_payment: int) -> None:
        self.store._run("UPDATE notifications SET seen = 1 WHERE payment_id IS NOT NULL AND payment_id <= ? AND seen = 0", (up_to_payment,))

    def resolve_approval_notes(self, approval_id: str) -> None:
        """An approval that was answered (or ran out) no longer needs its notification."""
        self.store._run("UPDATE notifications SET seen = 1 WHERE approval_id = ? AND seen = 0", (approval_id,))
