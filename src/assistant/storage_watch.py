"""Early warnings before storage runs out, and a clear answer when the server machine's disk is already full.

Two things can fill up (quota.py keeps the first, nothing here ever blocks chat):
- the assistant's own share (the storage cap, ASSISTANT_DISK_LIMIT): uploads, generated images, notes, results, history AND the
  database. The database is counted in what the app shows and in these warnings, but writes to it are never refused at the cap, so
  chat keeps working; only new uploads, images, note writes and results are refused there (Quota.check).
- the server machine's disk itself: the database, Docker images and anything else on that disk can fill it even while the cap is far
  away. When it is full, SQLite cannot write at all and chat stops too.

Warnings (one notification of type "storage" per crossing; the apps' [Clean up] action opens the cleanup screen):
  cap 80%   WARN, re-armed once use drops under WARN_CLEAR (75%)
  cap 95%   CRITICAL, re-armed once use drops under CRITICAL_CLEAR (90%) (it then counts as an 80% crossing already told)
  host disk  HOST_LOW: free under HOST_MIN_FREE (1 GB), or under HOST_MIN_SHARE (5%) of the disk while that is less than
             HOST_SHARE_CAP (5 GB) free (5% of a 4 TB disk is not "low"); re-armed once free is back over 1.5x those numbers.
Quiet hours: the cap warnings (80% and 95%) wait for the owner's waking hours (proactive.py's contact hours, learned in
active_hours.py) or a chat message in the last 30 minutes: at the cap chat still works and nothing is lost overnight, so they are
not worth waking anyone for. HOST_LOW goes out at once, day or night: when the disk fills, the database stops taking writes and the
whole assistant (chat included) stops working, which is the one case worth a late push. A warning that waited is sent at the
first waking check if it still holds (the latest level only: 80% then 95% overnight is one 95% message).

Cost: `tick` runs every minute. The host's free space is one statfs call; the cap's numbers come from quota.py's cached walk, done
again only after a write (Quota.wrote marks it dirty) or every FULL_EVERY seconds.

Disk full: `disk_full(exc)` recognizes SQLite's "database or disk is full" and OSError ENOSPC/EDQUOT. The server turns those into
507 {"detail": DISK_FULL_TEXT (localized), "code": "disk_full"} instead of a bare 500, the chat's error event carries the same text
with "code": "disk_full", and the next tick looks at the disk at once.
"""

from __future__ import annotations

import errno
import json
import logging
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .i18n import N_, t
from .quota import Quota, fmt_size

log = logging.getLogger("uvicorn.error")

WARN, WARN_CLEAR = 0.80, 0.75
CRITICAL, CRITICAL_CLEAR = 0.95, 0.90
HOST_MIN_FREE = 1024**3
HOST_MIN_SHARE = 0.05
HOST_SHARE_CAP = 5 * 1024**3
HOST_CLEAR = 1.5                  # back over 1.5x the low marks: the host warning is armed again
# a low disk that is not yet critical (under HOST_MIN_FREE) must stay low this long before anyone is told: building Kelo's own update
# (a few GB of images for some minutes, pruned right after) dipped under the 5% mark and pushed a warning at every update (2026-11-11)
HOST_LOW_HOLD = 15 * 60
FULL_EVERY = 600.0                # seconds between full looks at the cap's numbers when nothing was written
META = "storage:alert"            # {"level": 0|80|95, "told": 0|80|95, "host_low": bool, "host_told": bool}
NOTE_TYPE = "storage"
DISK_FULL_CODE = "disk_full"
DISK_FULL_TEXT = N_("The Kelo server's disk is full, so nothing can be saved right now. Free up space (My → Kelo server "
                    "resources → Clean up, or on the computer the server runs on), then try again.")

# the same words the apps use for the parts (database: the chat history it mostly holds; history: earlier versions of notes)
PART_NAMES = {"database": N_("chat history"), "uploads": N_("uploaded documents"), "images": N_("images"), "notes": N_("notes"),
              "history": N_("backups"), "other": N_("other files")}


def next_level(prev: int, ratio: float | None) -> int:
    """The cap's warning level (0, 80 or 95) after `prev`, with hysteresis: a level holds until use drops under its clear mark."""
    if ratio is None:
        return 0
    if ratio >= CRITICAL:
        return 95
    if prev == 95 and ratio >= CRITICAL_CLEAR:
        return 95
    if ratio >= WARN:
        return 80
    if prev >= 80 and ratio >= WARN_CLEAR:
        return 80
    return 0


def host_low(free: int | None, total: int | None, was_low: bool = False) -> bool:
    """Whether the server machine's disk is critically low (see the module text), with hysteresis."""
    if free is None or not total:
        return False
    k = HOST_CLEAR if was_low else 1.0
    if free < HOST_MIN_FREE * k:
        return True
    return free < total * HOST_MIN_SHARE * k and free < HOST_SHARE_CAP * k


def display_level(ratio: float | None) -> str:
    """What the app shows right now (no hysteresis: the plain numbers): ok | warn | critical."""
    if ratio is None:
        return "ok"
    return "critical" if ratio >= CRITICAL else "warn" if ratio >= WARN else "ok"


def disk_full(exc: BaseException | None) -> bool:
    """A write failed because the disk (or the database's file system) is full."""
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, sqlite3.OperationalError) and ("disk is full" in str(exc).lower() or "disk full" in str(exc).lower()):
            return True
        if isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def disk_full_text() -> str:
    return t(DISK_FULL_TEXT)


class StorageWatch:
    def __init__(self, store: Any, quota: Quota | None, notify: Callable[..., Any], *, awake: Callable[[float], bool] = lambda ts: True,
                 clock: Callable[[], float] = time.time, disk_usage: Callable[[Path], Any] = shutil.disk_usage):
        self.store, self.quota, self.notify, self.awake, self.clock, self._usage = store, quota, notify, awake, clock, disk_usage
        self.dirty = True
        self._last_full = 0.0
        self._numbers: dict[str, Any] | None = None

    # ------------------------------------------------------------------ numbers
    def mark_dirty(self) -> None:
        """Something was written or deleted (Quota.wrote): the next tick looks at the cap's numbers again."""
        self.dirty = True

    def host(self) -> tuple[int | None, int | None]:
        if self.quota is None:
            return None, None
        try:
            du = self._usage(self.quota.root)
            return int(du.free), int(du.total)
        except OSError:
            return None, None

    def numbers(self, *, fresh: bool = False) -> dict[str, Any]:
        """{"used", "limit" (None = no cap), "ratio" (None without a cap), "parts", "top", "free", "total"}."""
        now = self.clock()
        if self.quota is not None and (fresh or self.dirty or self._numbers is None or now - self._last_full >= FULL_EVERY):
            if fresh:
                self.quota.invalidate()
            used, parts = self.quota.used(), self.quota.parts()
            self.dirty, self._last_full = False, now
            self._numbers = {"used": used, "parts": parts}
        base = self._numbers or {"used": 0, "parts": {}}
        limit = (self.quota.limit or None) if self.quota is not None else None
        free, total = self.host()
        parts = {k: v for k, v in base["parts"].items() if v > 0}
        top = max(parts, key=lambda k: parts[k]) if parts else None
        return {"used": base["used"], "limit": limit, "ratio": (base["used"] / limit) if limit else None, "parts": parts, "top": top,
                "free": free, "total": total}

    def _state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.store.meta(META) or "{}")
        except ValueError:
            raw = {}
        return {"level": int(raw.get("level") or 0), "told": int(raw.get("told") or 0), "host_low": bool(raw.get("host_low")),
                "host_told": bool(raw.get("host_told")), "host_since": float(raw.get("host_since") or 0)}

    def public(self, nums: dict[str, Any] | None = None) -> dict[str, Any]:
        """For GET /api/resources: {"level": ok|warn|critical, "percent": int | null, "host_low": bool, "top": part | null}."""
        n = nums or self.numbers()
        st = self._state()
        return {"level": display_level(n["ratio"]), "percent": None if n["ratio"] is None else int(n["ratio"] * 100),
                "host_low": host_low(n["free"], n["total"], st["host_low"]), "top": n["top"]}

    # ------------------------------------------------------------------ the minute check
    def check(self, now: float | None = None, *, fresh: bool = False) -> list[str]:
        """Looks at the numbers and sends what is due. Returns what went out: "warn", "critical", "host"."""
        now = self.clock() if now is None else now
        n = self.numbers(fresh=fresh)
        st = self._state()
        level = next_level(st["level"], n["ratio"])
        told = min(st["told"], level)               # dropped under a clear mark: that crossing may be told again
        low = host_low(n["free"], n["total"], st["host_low"])
        host_told = st["host_told"] and low
        since = (st["host_since"] or now) if low else 0.0
        critical = low and (n["free"] or 0) < HOST_MIN_FREE
        sent: list[str] = []
        if low and not host_told and (critical or now - since >= HOST_LOW_HOLD):
            if self._send("host", n):
                host_told = True
                sent.append("host")
        if level > told and self.awake(now):
            kind = "critical" if level == 95 else "warn"
            if self._send(kind, n):
                told = level
                sent.append(kind)
        new = {"level": level, "told": told, "host_low": low, "host_told": host_told, "host_since": since}
        if new != st:
            try:
                self.store.set_meta(META, json.dumps(new))
            except Exception:  # noqa: BLE001 - a full disk can refuse even this; the next tick tries again
                log.warning("storage: could not save the warning state")
        return sent

    async def tick(self) -> list[str]:
        return self.check()

    def _send(self, kind: str, n: dict[str, Any]) -> bool:
        top = n["top"]
        biggest = (t("Most of it: {part} ({size}).", part=t(PART_NAMES.get(top, PART_NAMES["other"])), size=fmt_size(n["parts"][top]))
                   if top else "")
        if kind == "host":
            title = t("The Kelo server's disk is almost full")
            body = t("Only {free} is left on the disk of the computer the Kelo server runs on. When it runs out, Kelo stops "
                     "working, chat included.",
                     free=fmt_size(n["free"] or 0))
        else:
            title = (t("Kelo storage is almost full ({percent}%)", percent=int((n["ratio"] or 0) * 100)) if kind == "critical"
                     else t("Kelo storage is {percent}% full", percent=int((n["ratio"] or 0) * 100)))
            body = t("{used} of {limit} used.", used=fmt_size(n["used"]), limit=fmt_size(n["limit"] or 0))
        if kind == "host" and (n.get("used") or 0) < HOST_MIN_FREE:
            # Kelo's own files are not what fills that disk: cleaning them up would not help, so say what does
            biggest = t("Kelo's own data is only {used}; the rest is other files on that computer, for example Docker images "
                        "(`docker image prune` on that computer removes unused ones).", used=fmt_size(n.get("used") or 0))
            body = " ".join(x for x in (body, biggest) if x)
        else:
            body = " ".join(x for x in (body, biggest, t("Clean up shows what can be removed.")) if x)
        try:
            self.notify(NOTE_TYPE, title, body)
            return True
        except Exception:  # noqa: BLE001 - the notification itself may fail on a full disk; tried again next tick
            log.warning("storage: could not add the %s warning", kind)
            return False


__all__ = ["CRITICAL", "CRITICAL_CLEAR", "DISK_FULL_CODE", "HOST_MIN_FREE", "NOTE_TYPE", "StorageWatch", "WARN", "WARN_CLEAR",
           "disk_full", "disk_full_text", "display_level", "host_low", "next_level"]
