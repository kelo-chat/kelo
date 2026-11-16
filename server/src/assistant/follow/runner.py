"""FollowRunner: executes due checks, compares readings, and notifies on changes."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from ..home import NOTIFY_PREFS
from ..i18n import t
from ..proactive import NEVER_HEADINGS, keywords, section
from .checks import Source
from .items import LOOK_AHEAD_DAYS, ItemFinder, UpcomingItem
from .plan import MIN_EVERY, Planner, parse_offset
from .store import FollowStore

log = logging.getLogger(__name__)

PARALLEL = 4
MAX_ERRORS = 5
FIND_EVERY_SECONDS = 30 * 60   # how often the calendars are read for new items (checks still run every minute)
NOW_WINDOW = 3 * 3600  # 3 hours before start time: notify regardless of quiet hours
MAX_PREP_JOBS_PER_ITEM = 2
MAX_PREP_JOBS_PER_DAY = 4


def format_change_line(field: str, old: str, new: str) -> str:
    """Template line per field: e.g. Gate A11 -> A6."""
    f = field.lower().strip()
    if f == "gate":
        return t("Gate {old} -> {new}", old=old, new=new)
    if f in ("departure_time", "time", "departure"):
        return t("Departure {old} -> {new}", old=old, new=new)
    if f == "status":
        return t("Status: {status}", status=new)
    return t("{field}: {old} -> {new}", field=field, old=old, new=new)


def format_reminder(title: str, time_str: str, place: str = "") -> str:
    """Template line for lead reminders."""
    if place:
        return t("{title} at {time} ({place})", title=title, time=time_str, place=place)
    return t("{title} at {time}", title=title, time=time_str)


def item_end_time(item: dict[str, Any]) -> float:
    ends_at = item.get("ends_at")
    if ends_at is not None:
        return float(ends_at)
    return float(item["starts_at"]) + 2 * 3600


def item_from_dict(it: dict[str, Any]) -> UpcomingItem:
    """The stored item as the UpcomingItem the planner takes."""
    return UpcomingItem(key=it["key"], kind=it["kind"], title=it["title"], starts_at=float(it["starts_at"]),
                        ends_at=it.get("ends_at"), place=it.get("place") or "", refs=it.get("refs") or {},
                        source=it.get("source") or "")


class FollowRunner:
    def __init__(self, store: FollowStore,
                 finder: ItemFinder,
                 planner: Planner,
                 sources: list[Source],
                 activity: Any = None,
                 home: Any = None,
                 proactive: Any = None,
                 workers: Any = None,
                 tz: ZoneInfo | None = None,
                 jitter: bool = True,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.finder = finder
        self.planner = planner
        self.sources = list(sources)
        self.activity = activity
        self.home = home
        self.proactive = proactive
        self.workers = workers
        self.tz = tz or ZoneInfo("UTC")
        self.jitter = jitter
        self.clock = clock
        self._sem = asyncio.Semaphore(PARALLEL)
        self._running_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._found_at: float | None = None
        self._pending_notices: list[dict[str, Any]] = []

    def start(self) -> asyncio.Task[None]:
        """Start the background check loop."""
        self._stopped = False
        loop = asyncio.get_running_loop()
        self._running_task = loop.create_task(self.run())
        return self._running_task

    async def stop(self) -> None:
        """Cancel background loop."""
        self._stopped = True
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("FollowRunner tick failed")
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    def notify(self, note_type: str, title: str, detail: str) -> None:
        if self.activity is not None and hasattr(self.activity, "notify"):
            self.activity.notify(note_type, title, detail, push=True)
        else:
            log.info("follow notification [%s] %s: %s", note_type, title, detail)

    def is_awake(self, ts: float) -> bool:
        if self.proactive is not None and hasattr(self.proactive, "awake"):
            return bool(self.proactive.awake(ts))
        return True

    def _get_notify_prefs_text(self) -> str:
        if self.home is not None and hasattr(self.home, "root"):
            p = self.home.root / NOTIFY_PREFS
            if p.is_file() and not p.is_symlink():
                return p.read_text(encoding="utf-8", errors="replace")
        return ""

    def _is_suppressed(self, item: UpcomingItem) -> bool:
        text = self._get_notify_prefs_text()
        if not text:
            return False
        never = keywords(section(text, *NEVER_HEADINGS))
        hay = f"{item.kind} {item.title} {item.place}".lower()
        return any(k in hay for k in never)

    def _count_prep_jobs_today(self, now: float) -> int:
        cutoff = now - 86400
        count = 0
        for it in self.store.list_items():
            for p in it.get("prep") or []:
                if p.get("job_id") and p.get("started_at", 0) >= cutoff:
                    count += 1
        return count

    async def _run_preps(self, it_dict: dict[str, Any], cur_now: float) -> None:
        prep_entries = it_dict.get("prep") or []
        if not prep_entries:
            return

        item_jobs = sum(1 for p in prep_entries if p.get("job_id"))
        changed = False

        for entry in prep_entries:
            state = entry.get("state", "later")
            job_id = entry.get("job_id")

            # Check status of running jobs
            if state == "running" and job_id and self.workers is not None:
                worker = self.workers.get(job_id) if hasattr(self.workers, "get") else None
                if worker:
                    w_status = worker.get("status")
                    if w_status in ("done", "closed"):
                        entry["state"] = "done"
                        changed = True
                    elif w_status in ("failed", "stopped"):
                        entry["state"] = "failed"
                        changed = True
                continue

            # Start due prep jobs
            if state == "later" and self.workers is not None:
                when_str = entry.get("when") or ""
                offset_sec = parse_offset(when_str)
                fire_at = float(it_dict["starts_at"]) + offset_sec

                if cur_now >= fire_at:
                    if item_jobs >= MAX_PREP_JOBS_PER_ITEM:
                        log.info("Prep safety cap reached for item %s (%d jobs)", it_dict["key"], item_jobs)
                        continue
                    if self._count_prep_jobs_today(cur_now) >= MAX_PREP_JOBS_PER_DAY:
                        log.info("Daily prep safety cap reached (%d jobs today)", MAX_PREP_JOBS_PER_DAY)
                        continue

                    title = it_dict["title"]
                    dt = datetime.fromtimestamp(float(it_dict["starts_at"]), self.tz)
                    now_dt = datetime.fromtimestamp(cur_now, self.tz)
                    time_str = dt.strftime("%H:%M") if dt.date() == now_dt.date() else dt.strftime("%Y-%m-%d %H:%M")
                    what = entry.get("what") or ""
                    task = (f"Prepare for {title} at {time_str}: {what}. Follow the skills. "
                            f"Payments and anything outside existing permissions still ask the user.")

                    needs = entry.get("needs") or []
                    needs_str = " ".join(needs) if isinstance(needs, (list, tuple)) else str(needs)
                    kind = "browser" if "browser" in needs_str.lower() else "general"

                    try:
                        res = await self.workers.delegate({"task": task, "kind": kind})
                        m = re.search(r"\(job\s+([a-zA-Z0-9_-]+):", str(res))
                        wid = m.group(1) if m else ""
                        if wid:
                            entry["state"] = "running"
                            entry["job_id"] = wid
                            entry["started_at"] = cur_now
                            item_jobs += 1
                            changed = True
                        else:
                            entry["state"] = "failed"
                            changed = True
                    except Exception:
                        log.exception("Failed to start prep job for %s", it_dict["key"])
                        entry["state"] = "failed"
                        changed = True

        if changed:
            self.store.update_item(it_dict["key"], prep=prep_entries)

    async def _plan_new(self, it: UpcomingItem, cur_now: float) -> None:
        """Plans one item (a new one, or a chat item whose look-ahead window has come): its checks, lead reminders and preparations."""
        plan = await self.planner.plan(it, self._get_notify_prefs_text())
        if plan.is_empty():
            self.store.save_item(it, plan=plan.to_dict(), state="done")
        else:
            p_dict = plan.to_dict()
            tell = dict(p_dict.get("tell") or {})
            leads = tell.get("lead") or []
            past_leads = [l for l in leads if (it.starts_at + parse_offset(l)) <= cur_now]
            if past_leads:
                tell["reminded"] = sorted(set(tell.get("reminded") or []) | set(past_leads))
                p_dict["tell"] = tell
            prep_list = [{"state": "later", **p} if "state" not in p else p for p in plan.prep]
            self.store.save_item(it, plan=p_dict, state="watching", prep=prep_list)
            for ch in plan.checks:
                next_at = max(cur_now, ch["window_start"])
                self.store.save_check(
                    item_key=it.key,
                    kind=ch["kind"],
                    refs=ch["refs"],
                    fields=ch["fields"],
                    from_offset=ch["from"],
                    until_offset=ch["until"],
                    window_start=ch["window_start"],
                    window_end=ch["window_end"],
                    every_minutes=ch["every_minutes"],
                    next_at=next_at,
                    state="active",
                )

    def _find_source(self, kind: str, refs: dict[str, str]) -> Source | None:
        for s in self.sources:
            if s.kind == kind and s.available() and s.covers(refs):
                return s
        return None

    async def tick(self, now: float | None = None) -> list[int]:
        """One check pass: find new items, plan them, run due checks, report changes."""
        cur_now = self.clock() if now is None else now

        # 1. Discover items and plan new ones: the calendar is read at most every FIND_EVERY_SECONDS (the checks below run every
        #    minute; reading the calendars each minute would cost a calendar API call per minute for nothing)
        if self._found_at is not None and cur_now - self._found_at < FIND_EVERY_SECONDS:
            new_items = []
        else:
            self._found_at = cur_now
            new_items = await self.finder.find(cur_now)
        for it in new_items:
            existing = self.store.get_item(it.key)
            if existing is not None:
                # Same key and same start time: keep existing plan
                if existing.get("starts_at") == it.starts_at:
                    continue
            if self._is_suppressed(it):
                self.store.save_item(it, state="stopped")
                continue
            await self._plan_new(it, cur_now)

        # 1b. Things said in chat (kept "later" by upcoming.py) are planned once they enter the look-ahead window
        for it_dict in self.store.list_items(state="later"):
            if cur_now >= item_end_time(it_dict):
                self.store.update_item_state(it_dict["key"], "done")
                continue
            if float(it_dict["starts_at"]) - cur_now > LOOK_AHEAD_DAYS * 86400:
                continue
            item = item_from_dict(it_dict)
            if self._is_suppressed(item):
                self.store.update_item_state(item.key, "stopped")
                continue
            await self._plan_new(item, cur_now)

        # 2. Quietly complete expired checks and manage watching items
        for exp in self.store.expired_checks(cur_now):
            self.store.update_check(exp["id"], state="done")

        watching_items = self.store.list_items(state="watching")
        for it_dict in watching_items:
            end_time = item_end_time(it_dict)
            if cur_now >= end_time:
                for ch in self.store.list_checks(item_key=it_dict["key"], state="active"):
                    self.store.update_check(ch["id"], state="done")
                self.store.update_item_state(it_dict["key"], "done")
                continue

            # Lead reminders
            plan = it_dict.get("plan") or {}
            tell = plan.get("tell") or {}
            leads = tell.get("lead") or []
            if leads:
                reminded = set(tell.get("reminded") or [])
                starts_at = float(it_dict["starts_at"])
                changed_plan = False
                for lead_str in leads:
                    if lead_str in reminded:
                        continue
                    offset_sec = parse_offset(lead_str)
                    fire_at = starts_at + offset_sec
                    if cur_now >= fire_at:
                        reminded.add(lead_str)
                        changed_plan = True
                        now_dt = datetime.fromtimestamp(cur_now, self.tz)
                        dt = datetime.fromtimestamp(starts_at, self.tz)
                        time_str = dt.strftime("%H:%M") if dt.date() == now_dt.date() else dt.strftime("%Y-%m-%d %H:%M")
                        title = it_dict["title"]
                        place = it_dict.get("place") or ""
                        detail = format_reminder(title, time_str, place)

                        tell_now = (cur_now >= starts_at - NOW_WINDOW) or self.is_awake(cur_now)
                        if tell_now:
                            self.notify("follow_reminder", title, detail)
                        else:
                            self._pending_notices.append({
                                "type": "follow_reminder",
                                "title": title,
                                "detail": detail,
                                "starts_at": starts_at,
                            })
                if changed_plan:
                    tell["reminded"] = sorted(reminded)
                    plan["tell"] = tell
                    self.store.update_item(it_dict["key"], plan=plan)

            # Preparations
            await self._run_preps(it_dict, cur_now)

        # 3. Deliver pending quiet-hour notifications if user woke up
        if self._pending_notices:
            still_pending: list[dict[str, Any]] = []
            for notice in self._pending_notices:
                starts_at = notice["starts_at"]
                if cur_now >= (starts_at - NOW_WINDOW) or self.is_awake(cur_now):
                    self.notify(notice["type"], notice["title"], notice["detail"])
                else:
                    still_pending.append(notice)
            self._pending_notices = still_pending

        # 4. Run due checks
        due = self.store.due_checks(cur_now)
        results = await asyncio.gather(*(self._run_one_check(ch, cur_now) for ch in due))
        return [r for r in results if r is not None]

    async def _run_one_check(self, ch: dict[str, Any], now: float) -> int | None:
        async with self._sem:
            cid = ch["id"]
            source = self._find_source(ch["kind"], ch["refs"])
            jitter = random.uniform(0.8, 1.2) if self.jitter else 1.0
            every_sec = max(MIN_EVERY * 60, ch["every_minutes"] * 60)
            next_at = now + every_sec * jitter

            if source is None:
                err_count = ch["consecutive_errors"] + 1
                detail = f"no source available for {ch['kind']}"
                self.store.save_reading(cid, now, False, {}, detail=detail)
                if err_count >= MAX_ERRORS:
                    self.store.update_check(cid, state="error", consecutive_errors=err_count, last_error=detail, last_at=now)
                    item = self.store.get_item(ch["item_key"])
                    title = item["title"] if item else ch["kind"]
                    self.notify("follow_problem", title, t("Stopped checking after repeated errors: {detail}", detail=detail))
                else:
                    self.store.update_check(cid, next_at=next_at, consecutive_errors=err_count, last_error=detail, last_at=now)
                return cid

            reading = await source.read(ch["refs"])
            if not reading.ok:
                err_count = ch["consecutive_errors"] + 1
                self.store.save_reading(cid, now, False, reading.fields, detail=reading.detail)
                if err_count >= MAX_ERRORS:
                    self.store.update_check(cid, state="error", consecutive_errors=err_count, last_error=reading.detail, last_at=now)
                    item = self.store.get_item(ch["item_key"])
                    title = item["title"] if item else ch["kind"]
                    self.notify("follow_problem", title, t("Stopped checking after repeated errors: {detail}", detail=reading.detail))
                else:
                    self.store.update_check(cid, next_at=next_at, consecutive_errors=err_count, last_error=reading.detail, last_at=now)
                return cid

            # Successful reading
            last_good = self.store.last_reading(cid, ok_only=True)
            self.store.save_reading(cid, now, True, reading.fields, detail=reading.detail)
            self.store.update_check(cid, next_at=next_at, consecutive_errors=0, last_error="", last_at=now)

            if last_good is not None:
                # Compare fields
                watch_fields = ch["fields"] or list(reading.fields.keys())
                changes: list[str] = []
                for f in watch_fields:
                    old_v = last_good["fields"].get(f)
                    new_v = reading.fields.get(f)
                    if old_v is not None and new_v is not None and str(old_v).strip() != str(new_v).strip():
                        changes.append(format_change_line(f, str(old_v).strip(), str(new_v).strip()))

                if changes:
                    change_detail = "\n".join(changes)
                    item = self.store.get_item(ch["item_key"])
                    title = item["title"] if item else ch["kind"]
                    starts_at = item["starts_at"] if item else now

                    # Quiet hours check
                    tell_now = (now >= starts_at - NOW_WINDOW) or self.is_awake(now)
                    if tell_now:
                        self.notify("follow_change", title, change_detail)
                    else:
                        self._pending_notices.append({
                            "type": "follow_change",
                            "title": title,
                            "detail": change_detail,
                            "starts_at": starts_at,
                        })

                    # If status is cancelled, update item
                    status_val = str(reading.fields.get("status") or "").lower()
                    if status_val in ("cancelled", "canceled", "결항", "취소"):
                        self.store.update_item_state(ch["item_key"], "cancelled")

            return cid
