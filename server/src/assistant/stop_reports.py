"""When Kelo gives up on a piece of work by itself (the server restarted under it MAX_RESTART_RESUMES times, the AI model stayed away
until MODEL_WAIT_GIVE_UP, the usage limit or the prepaid balance never came back), the owner gets one report in the chat: what was
done, where it stopped and why (owner 2026-11-20). The facts are built from the server's own records (the steps of the tasks, the
approvals), never by a model alone: the model writes the report text from that record when it can be reached (owner 2026-11-20); when
it cannot, the four plain lines built from the record are the report. The report goes out as a delivery (deliveries.py): it reaches
the chat exactly once, across restarts."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from . import i18n, tool_labels
from .deliveries import Deliveries
from .i18n import N_, t
from .skill_lessons import run_steps

log = logging.getLogger(__name__)

STOP_HEAD = N_("Kelo stopped this: {title}")
DONE_LINE = N_("Done so far: {steps}")
NOTHING_DONE = N_("No step had finished yet.")
AT_LINE = N_("Stopped at: {step}")
AFTER_LINE = N_("Stopped after: {step}")
WHY_LINE = N_("Why: {why}")

MODEL_WAIT = 60.0   # seconds the model gets to write the report; past it the plain lines stay
REPORT_MAX = 1500

SYSTEM = (
    "You write the report of a task that Kelo stopped by itself, for its owner. Use only the record you are given: never add a step, "
    "a result or a cause that is not in it. Say, in the language named in the record, what was done, where it stopped and why, in at "
    "most four short lines. Answer with one JSON object: {\"report\": \"<the report text>\"}."
)


@dataclass(frozen=True)
class StopFacts:
    title: str
    done: tuple[str, ...]     # labels of finished steps and of committed actions, in order, each once
    at: str                   # label of the step it stopped at ("" = none known)
    cut_mid_step: bool        # True: `at` had started and had no good result; False: `at` was the last finished step


class StopReports:
    def __init__(self, store: Any, deliveries: Deliveries, turn_log: Any, ask: Callable[..., Awaitable[dict[str, Any] | None]] | None = None) -> None:
        self.store, self.deliveries, self.turn_log, self.ask = store, deliveries, turn_log, ask

    def chain(self, task_id: int) -> list[dict[str, Any]]:
        """The task and the tasks it was resumed from (tasks.resumed_from), oldest first (a seen set guards a loop)."""
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        cur: int | None = task_id
        while cur and cur not in seen:
            seen.add(cur)
            rows = self.store._all("SELECT * FROM tasks WHERE id = ?", (cur,))
            if not rows:
                break
            out.append(dict(rows[0]))
            cur = rows[0]["resumed_from"]
        out.reverse()
        return out

    def steps(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """[{"tool", "ok"}] of one task: tasks.steps (skill_lessons.run_steps JSON) when set, else run_steps of
        turn_log.events(task["turn_id"]) (None/[] when pruned)."""
        raw = str(task.get("steps") or "")
        if not raw and self.turn_log is not None and task.get("turn_id"):
            events = self.turn_log.events(task["turn_id"])
            raw = run_steps(events) if events else ""
        if not raw:
            return []
        try:
            got = json.loads(raw).get("steps") or []
        except (ValueError, AttributeError):
            return []
        return [{"tool": str(s.get("tool") or ""), "ok": s.get("ok")} for s in got if isinstance(s, dict)]

    def facts(self, task_id: int) -> StopFacts:
        """done: for each task of the chain, each step with ok -> tool_labels.label(tool) (tool_labels.hidden ones left out), then the
        approval_log rows of these task ids with decision LIKE 'allowed%' as "<label> · <site>" (site left out when empty); a label
        equal to the one before it is not repeated. at: the last step of the last task of the chain (cut_mid_step = not ok)."""
        chain = self.chain(task_id)
        done: list[str] = []

        def add(label: str) -> None:
            if label and (not done or done[-1] != label):
                done.append(label)

        last: dict[str, Any] | None = None
        for task in chain:
            steps = self.steps(task)
            for st in steps:
                if st["ok"] and not tool_labels.hidden(st["tool"]):
                    add(tool_labels.label(st["tool"]))
            if steps:
                last = steps[-1]
        ids = [int(task["id"]) for task in chain]
        if ids:
            marks = ",".join("?" * len(ids))
            for r in self.store._all(f"SELECT tool, site FROM approval_log WHERE task_id IN ({marks}) AND decision LIKE 'allowed%' "
                                     "ORDER BY asked_at, id", tuple(ids)):
                label = tool_labels.label(str(r["tool"]))
                site = str(r["site"] or "")
                done.append(f"{label} · {site}" if site else label)
        at = tool_labels.label(last["tool"]) if last and not tool_labels.hidden(last["tool"]) else ""
        title = str(chain[0]["title"]) if chain else ""
        return StopFacts(title=title, done=tuple(done), at=at, cut_mid_step=bool(last) and last["ok"] is not True)

    def text(self, title: str, facts: StopFacts, why: str) -> str:
        """In the owner's language: STOP_HEAD, then DONE_LINE with ", ".join(done) or NOTHING_DONE, then AT_LINE/AFTER_LINE when `at`,
        then WHY_LINE. One line each."""
        with i18n.owner_language():
            lines = [t(STOP_HEAD, title=title)]
            lines.append(t(DONE_LINE, steps=", ".join(facts.done)) if facts.done else t(NOTHING_DONE))
            if facts.at:
                lines.append(t(AT_LINE if facts.cut_mid_step else AFTER_LINE, step=facts.at))
            lines.append(t(WHY_LINE, why=why))
        return "\n".join(lines)

    def put(self, task_id: int, why: str) -> str:
        """Builds the text and records the delivery `task-stopped:<task_id>` (kind "chat", ref f"task:{task_id}"). The record keeps
        the facts too, so the model can write the report from it later. Returns the key; the caller then calls send_soon(key)."""
        facts = self.facts(task_id)
        text = self.text(facts.title, facts, why)
        key = f"task-stopped:{task_id}"
        record = {"title": facts.title, "done": list(facts.done), "at": facts.at, "cut_mid_step": facts.cut_mid_step, "why": why}
        self.deliveries.put(key, "chat", f"task:{task_id}", {
            "text": text, "task_id": task_id, "record": record,
            "notify": {"type": "task_failed", "title": f"{t('It did not work out')}: {facts.title}"[:120], "body": why[:400]}})
        return key

    def send_soon(self, key: str) -> None:
        """Sends the report in the background: the model may write its text first (write_text), then the delivery goes out. Without a
        running loop nothing is sent here (the 'deliveries' tick drains it)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._send(key))

    async def _send(self, key: str) -> None:
        try:
            await self.write_text(key)
        except Exception:  # noqa: BLE001 - the plain lines stay the report
            log.exception("stop report %s: the model text failed", key)
        await self.deliveries.send(key)

    async def write_text(self, key: str) -> bool:
        """The model writes the report from the record (only the record, in the owner's language). A model that cannot be reached, or an
        answer with no report text, leaves the plain lines. True when the model's text was saved."""
        if self.ask is None:
            return False
        rows = self.store._all("SELECT payload FROM deliveries WHERE once_key = ? AND state = 'waiting'", (key,))
        if not rows:
            return False
        payload = json.loads(rows[0]["payload"])
        user = json.dumps({"language": i18n.owner(), "record": payload.get("record") or {}}, ensure_ascii=False)
        got = await asyncio.wait_for(self.ask(SYSTEM, user), MODEL_WAIT)
        report = str((got or {}).get("report") or "").strip() if isinstance(got, dict) else ""
        if not report:
            return False
        payload["text"] = report[:REPORT_MAX]
        self.store._run("UPDATE deliveries SET payload = ? WHERE once_key = ? AND state = 'waiting'",
                        (json.dumps(payload, ensure_ascii=False), key))
        return True
