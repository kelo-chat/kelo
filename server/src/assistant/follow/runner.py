"""FollowRunner: executes due checks, compares readings, and notifies on changes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from ..credentials import clean_name
from ..home import NOTIFY_PREFS
from ..i18n import N_, t
from .sources.parcel import parcel_status_name
from ..notify_block import has_rules
from ..tools.vault_tools import login_site_ok, site_host
from .checks import Reading, Source
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


# What the weather source's codes are called when told (the source stores codes; the words come from here, in the owner's language)
WEATHER_WORDS = {"rain": N_("rain"), "snow": N_("snow"), "sleet": N_("sleet"), "thunder": N_("thunderstorm"), "fog": N_("fog"),
                 "cloudy": N_("cloudy"), "partly": N_("partly cloudy"), "clear": N_("clear"),
                 "wind": N_("strong wind"), "heat": N_("heat"), "cold": N_("severe cold")}


def weather_words(codes: str) -> str:
    """"wind,heat" -> "strong wind, heat" in the owner's language; "" -> "none"."""
    parts = [c.strip() for c in str(codes).split(",") if c.strip()]
    return ", ".join(t(WEATHER_WORDS[c]) if c in WEATHER_WORDS else c for c in parts) or t("none")


def format_change_line(field: str, old: str, new: str) -> str:
    """Template line per field: e.g. Gate A11 -> A6."""
    f = field.lower().strip()
    if f == "gate":
        return t("Gate {old} -> {new}", old=old, new=new)
    if f in ("departure_time", "time", "departure"):
        return t("Departure {old} -> {new}", old=old, new=new)
    if f == "status":
        return t("Status: {status}", status=new)
    if f == "parcel":
        return t("Status: {status}", status=parcel_status_name(new))
    if f == "rain_chance":
        return t("Chance of rain {old}% -> {new}%", old=old, new=new)
    if f == "temperature":
        return t("Temperature {old}° -> {new}°", old=old, new=new)
    if f == "condition":
        return t("Weather: {old} -> {new}", old=weather_words(old), new=weather_words(new))
    if f == "warning":
        return t("Weather warning: {new}", new=weather_words(new))
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
                 clock: Callable[[], float] = time.time,
                 secret_requests: Any = None,
                 vault: Any = None) -> None:
        self.store = store
        self.notify_block: Any = None   # notify_block.NotifyBlock (server.py); None: the rules cannot be read
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
        self.secret_requests = secret_requests
        if self.secret_requests is None:
            if hasattr(self.store, "secret_requests"):
                self.secret_requests = self.store.secret_requests
            elif hasattr(self.store, "_store") and hasattr(self.store._store, "secret_requests"):
                self.secret_requests = self.store._store.secret_requests
        self.vault = vault
        self._sem = asyncio.Semaphore(PARALLEL)
        self._running_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._found_at: float | None = None
        self._pending_notices: list[dict[str, Any]] = []

    @staticmethod
    def _extract_prep_site(entry: dict[str, Any]) -> str:
        needs = entry.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for n in needs:
            n_str = str(n).strip()
            if n_str.lower().startswith("login:"):
                return n_str[6:].strip()
            if n_str.lower().startswith("site:"):
                return n_str[5:].strip()
            if "://" in n_str:
                return n_str
            if "." in n_str and not n_str.startswith("app:"):
                return n_str
            if n_str.startswith("app:"):
                return n_str
        return ""

    def _has_saved_login(self, host: str) -> bool:
        if self.vault is not None and hasattr(self.vault, "has_login"):
            return bool(self.vault.has_login(host))

        creds: list[dict[str, Any]] = []
        if isinstance(self.vault, list):
            creds = self.vault
        elif self.vault is not None and hasattr(self.vault, "credentials"):
            creds = self.vault.credentials()
        elif hasattr(self.store, "credentials"):
            creds = self.store.credentials()
        elif hasattr(self.store, "_store") and hasattr(self.store._store, "credentials"):
            creds = self.store._store.credentials()

        for c in creds:
            if c.get("kind") == "login":
                hosts = c.get("hosts") or []
                if login_site_ok(host, hosts):
                    if c.get("has_secret"):
                        return True
        return False

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
        """The owner's notification rules: their saved copy (owner_copies.py), never the file the agent may have written."""
        if self.home is not None and hasattr(self.home, "policy"):
            return self.home.policy(NOTIFY_PREFS)
        return ""

    async def _is_suppressed(self, item: UpcomingItem) -> bool | None:
        """On the owner's do-not-tell list (read by the main model, notify_block.py)? None: not known now (no reader or no answer)."""
        text = self._get_notify_prefs_text()
        if not has_rules(text):
            return False
        nb = self.notify_block
        return await nb.one(text, f"{item.kind} {item.title} {item.place}") if nb is not None else None

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
                        title = it_dict["title"]
                        what = entry.get("what") or ""
                        detail = t("{title}: {what} done ahead", title=title, what=what)
                        starts_at = float(it_dict["starts_at"])
                        tell_now = (cur_now >= starts_at - NOW_WINDOW) or self.is_awake(cur_now)
                        if tell_now:
                            self.notify("follow_done", title, detail)
                        else:
                            self._pending_notices.append({
                                "type": "follow_done",
                                "title": title,
                                "detail": detail,
                                "starts_at": starts_at,
                            })
                    elif w_status in ("failed", "stopped"):
                        entry["state"] = "failed"
                        changed = True
                        title = it_dict["title"]
                        what = entry.get("what") or ""
                        detail = t("{title}: {what} preparation failed", title=title, what=what)
                        self.notify("follow_problem", title, detail)
                continue

            # Advance notice for missing login (1 day before prep time)
            if state == "later":
                when_str = entry.get("when") or ""
                offset_sec = parse_offset(when_str)
                fire_at = float(it_dict["starts_at"]) + offset_sec
                notice_at = fire_at - 86400

                site = self._extract_prep_site(entry)
                if site and cur_now >= notice_at:
                    try:
                        host = site_host(site)
                    except Exception:
                        host = site.lower().strip()

                    if not self._has_saved_login(host) and not entry.get("login_requested"):
                        entry["login_requested"] = True
                        changed = True

                        title = it_dict["title"]
                        detail = t("I need your {site} login to prepare for {title}", site=host, title=title)
                        name = clean_name("login-" + host.replace(":", "-").replace(".", "-"))
                        label = host

                        entries = self.secret_requests
                        if entries is None and hasattr(self.store, "secret_requests"):
                            entries = self.store.secret_requests
                        elif entries is None and hasattr(self.store, "_store") and hasattr(self.store._store, "secret_requests"):
                            entries = self.store._store.secret_requests

                        if entries is not None:
                            existing = entries.find_open_matching("login", name=name, hosts=[host]) if hasattr(entries, "find_open_matching") else None
                            if not existing:
                                raw_store = getattr(self.store, "_store", self.store)
                                if hasattr(raw_store, "save_credential"):
                                    try:
                                        raw_store.save_credential(name, label, [host], {}, "", kind="login")
                                    except Exception:
                                        pass
                                if hasattr(raw_store, "add_vault_log"):
                                    try:
                                        raw_store.add_vault_log(name, "requested", f"website login for {host}")
                                    except Exception:
                                        pass
                                entries.create("login", name=name, label=label, hosts=[host], reason=detail)

                        starts_at = float(it_dict["starts_at"])
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

    def _candidates(self, kind: str, refs: dict[str, str]) -> list[Source]:
        """The sources of a kind that can read this check now, in order: the first that answers is used, the next ones are the fallback.
        A source stopped for the month (`paused`) is left out."""
        return [s for s in self.sources if s.kind == kind and s.available() and s.covers(refs) and not s.paused(refs)]

    def _paused_reason(self, kind: str, refs: dict[str, str]) -> str:
        """Why no source can read this check right now when one would cover it but is stopped for the month, else ""."""
        for s in self.sources:
            if s.kind == kind and s.available() and s.covers(refs):
                reason = s.paused(refs)
                if reason:
                    return reason
        return ""

    def usage(self) -> list[dict[str, Any]]:
        """This month's calls per counted source, for GET /api/follow."""
        return [u for u in (s.usage() for s in self.sources) if u]

    async def tick(self, now: float | None = None) -> list[int]:
        """One check pass: find new items, plan them, run due checks, report changes."""
        cur_now = self.clock() if now is None else now

        # 1. Discover items and plan new ones: the calendar is read at most every FIND_EVERY_SECONDS (the checks below run every
        #    minute; reading the calendars each minute would cost a calendar API call per minute for nothing)
        looked = False
        if self._found_at is not None and cur_now - self._found_at < FIND_EVERY_SECONDS:
            new_items = []
        else:
            looked = True
            self._found_at = cur_now
            new_items = await self.finder.find(cur_now)
        for it in new_items:
            if any(self.store.is_removed(k, it.starts_at) for k in [it.key, *it.aliases]):
                continue   # the owner took it off the list: the calendar still shows it, but it is not put back
            same = self.store.find_same(it)   # the same real-world thing, found through another source or under another key
            if same is not None and same["key"] != it.key:
                self.store.add_aliases(same["key"], [it.key, *it.aliases])
                it = replace(it, key=same["key"], aliases=())   # the first key wins
            elif same is not None and it.aliases:
                self.store.add_aliases(same["key"], list(it.aliases))
            existing = self.store.get_item(it.key)
            if existing is not None:
                # Same key and same start time: keep existing plan
                if existing.get("starts_at") == it.starts_at:
                    continue
            stop = await self._is_suppressed(it)
            if stop is None:
                continue   # the rules could not be read: the item is found again at the next look
            if stop:
                self.store.save_item(it, state="stopped")
                continue
            await self._plan_new(it, cur_now)

        # 1a. An event gone from every calendar source it came from is no longer something to follow (only when the calendar read
        #     worked: a failed read looks like an empty calendar)
        if looked and getattr(self.finder, "calendar_ok", False):
            seen = getattr(self.finder, "seen_keys", set())
            for it_dict in self.store.list_items():
                keys = [it_dict["key"], *it_dict.get("aliases", [])]
                if (all(k.startswith("cal:") for k in keys) and not (set(keys) & seen)
                        and cur_now < float(it_dict["starts_at"]) <= cur_now + LOOK_AHEAD_DAYS * 86400):
                    self.store.delete_item(it_dict["key"])

        # 1b. Things said in chat (kept "later" by upcoming.py) are planned once they enter the look-ahead window
        for it_dict in self.store.list_items(state="later"):
            if cur_now >= item_end_time(it_dict):
                self.store.update_item_state(it_dict["key"], "done")
                continue
            if float(it_dict["starts_at"]) - cur_now > LOOK_AHEAD_DAYS * 86400:
                continue
            item = item_from_dict(it_dict)
            stop = await self._is_suppressed(item)
            if stop is None:
                continue
            if stop:
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
            jitter = random.uniform(0.8, 1.2) if self.jitter else 1.0
            every_sec = max(MIN_EVERY * 60, ch["every_minutes"] * 60)
            next_at = now + every_sec * jitter

            # The first source of the kind that answers is used; a source that fails hands over to the next one that covers the check
            source: Source | None = None
            reading: Reading | None = None
            failed: Reading | None = None
            for candidate in self._candidates(ch["kind"], ch["refs"]):
                tried = await candidate.read(ch["refs"])
                if tried.ok:
                    source, reading = candidate, tried
                    break
                failed = tried

            if reading is None and failed is None:
                paused = self._paused_reason(ch["kind"], ch["refs"])
                if paused:   # a monthly allowance is used up and no other source covers this: wait, nothing is an error
                    self.store.update_check(cid, next_at=next_at, consecutive_errors=0, last_error=paused, last_at=now)
                    return cid
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

            if reading is None or source is None:
                failure = failed or Reading(ok=False)
                err_count = ch["consecutive_errors"] + 1
                self.store.save_reading(cid, now, False, failure.fields, detail=failure.detail)
                if err_count >= MAX_ERRORS:
                    self.store.update_check(cid, state="error", consecutive_errors=err_count, last_error=failure.detail, last_at=now)
                    item = self.store.get_item(ch["item_key"])
                    title = item["title"] if item else ch["kind"]
                    self.notify("follow_problem", title, t("Stopped checking after repeated errors: {detail}", detail=failure.detail))
                else:
                    self.store.update_check(cid, next_at=next_at, consecutive_errors=err_count, last_error=failure.detail, last_at=now)
                return cid

            # Successful reading
            last_good = self.store.last_reading(cid, ok_only=True)
            watch_fields = ch["fields"] or list(reading.fields.keys())
            stored = dict(reading.fields)
            changes: list[str] = []
            if last_good is not None:
                for f in watch_fields:
                    old_v = last_good["fields"].get(f)
                    new_v = reading.fields.get(f)
                    if old_v is None or new_v is None or str(old_v).strip() == str(new_v).strip():
                        continue
                    if not source.significant(f, old_v, new_v):
                        stored[f] = old_v   # a small move (a forecast) is neither told nor kept: drift adds up against the told value
                        continue
                    changes.append(format_change_line(f, str(old_v).strip(), str(new_v).strip()))
            self.store.save_reading(cid, now, True, stored, detail=reading.detail)
            self.store.update_check(cid, next_at=next_at, consecutive_errors=0, last_error="", last_at=now)

            if reading.done:
                # the source says the item is over (a parcel delivered): it is done and its checks end
                self.store.update_item_state(ch["item_key"], "done")
                for other in self.store.list_checks(item_key=ch["item_key"], state="active"):
                    self.store.update_check(other["id"], state="done")

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
                if reading.fields.get("cancelled") is True:   # the source says so in structure (a boolean it set from its API's own field)
                    self.store.update_item_state(ch["item_key"], "cancelled")

            return cid
