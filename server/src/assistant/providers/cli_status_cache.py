"""The claude / agy CLI status that GET /api/providers shows, kept so the list never waits on a sidecar.

Asking a sidecar (or the local CLI) runs `claude auth status` / `--version` there: 0.1 s when the machine is idle, many seconds when
it is busy. The server keeps the last answer per CLI and answers from it at once, with the time it was checked ("checked_at", epoch
seconds). An answer older than `ttl` is checked again in the background (the request that noticed does not wait). The last answer
is also saved (store meta "cli_status_last:<name>"), so a restart starts from it too.

Waiting happens only when nothing is known at all (the first start ever) or right after a change (sign-in, sign-out, update,
install: `invalidate`, also reached through cli_manage.forget_status): then a request waits up to `cold_wait` for the new check,
and gets the old answer (or a "checking" one) if it takes longer.

Hints are written per request: the check runs in English and the hint lines are put into the asking request's language when read
(sidecar statuses say which hints they carry with "hint_kind"; the local ones are fixed English sentences in the catalogue).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .. import i18n
from . import cli_manage, cli_remote

TTL = 45.0          # an answer this old (seconds) is checked again in the background
COLD_WAIT = 1.0     # the most a request waits, only when nothing is known or right after a change

Fetch = Callable[[str], Awaitable[dict[str, Any]]]


def _default_fetch(name: str) -> Awaitable[dict[str, Any]]:
    return cli_manage.cli_status(name)   # looked up at call time (tests replace it)


class CliStatusCache:
    def __init__(self, fetch: Fetch | None = None, *, ttl: float = TTL, cold_wait: float = COLD_WAIT,
                 load: Callable[[str], Any] | None = None, save: Callable[[str, dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self._fetch = fetch or _default_fetch
        self.ttl, self.cold_wait = ttl, cold_wait
        self._load, self._save = load, save
        self._clock, self._wall = clock, wall
        self._entries: dict[str, dict[str, Any]] = {}   # name -> {"st", "checked_at" (epoch), "at" (monotonic, -inf = loaded)}
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._dirty: set[str] = set()
        self._generation: dict[str, int] = {}          # bumped by invalidate: a check started before it does not count as new
        self._closed = False
        cli_manage.on_forget(self)

    # ---- reading

    async def get(self, name: str) -> dict[str, Any]:
        """The status for the app, in the request's language, plus "checked_at" (and "checking": true while none is known yet)."""
        entry = self._entry(name)
        if entry is None or name in self._dirty:
            task = self._start(name, fresh=name in self._dirty)   # after a change: not a check that began before it
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(task), self.cold_wait)
            entry = self._entry(name)
        elif self._clock() - entry["at"] >= self.ttl:
            self._start(name)
        if entry is None:
            return {"installed": False, "installable": False, "logged_in": None, "version": None, "hint": "",
                    "checking": True, "checked_at": None}
        return {**self._shown(name, entry["st"]), "checked_at": entry["checked_at"]}

    async def refresh(self, name: str) -> dict[str, Any]:
        """Check now and wait for it (the endpoints that change or need the real state: sign-in, sign-out). Returns the shown
        status (raises what the check raises)."""
        task = self._start(name, fresh=True)
        st = await asyncio.shield(task)
        return self._shown(name, st)

    def invalidate(self, name: str, *, now: bool = False) -> None:
        """The CLI's state changed (or may have): the next read waits briefly for a new check. now=True starts it right away."""
        if self._closed:
            return
        self._dirty.add(name)
        self._generation[name] = self._generation.get(name, 0) + 1
        if now:
            with contextlib.suppress(RuntimeError):   # no running loop: the next read starts it
                self._start(name, fresh=True)

    def warm(self, name: str) -> None:
        """At start-up: take the saved copy (if any) and check again in the background, without making anyone wait for it."""
        self._entry(name)
        self._start(name)

    def peek(self, name: str) -> dict[str, Any] | None:
        """The kept raw status (tests and diagnostics)."""
        e = self._entries.get(name)
        return dict(e["st"]) if e else None

    async def close(self) -> None:
        self._closed = True
        for t in list(self._tasks.values()):
            t.cancel()
        for t in list(self._tasks.values()):
            with contextlib.suppress(BaseException):
                await t
        self._tasks.clear()

    # ---- inside

    def _entry(self, name: str) -> dict[str, Any] | None:
        e = self._entries.get(name)
        if e is None and self._load is not None:
            try:
                saved = self._load(name)
            except Exception:  # noqa: BLE001 - a bad saved copy counts as none
                saved = None
            if isinstance(saved, dict) and isinstance(saved.get("st"), dict):
                e = self._entries[name] = {"st": saved["st"], "checked_at": saved.get("checked_at"), "at": float("-inf")}
        return e

    def _start(self, name: str, *, fresh: bool = False) -> asyncio.Task[dict[str, Any]]:
        """One check per CLI at a time. fresh: a check that started before the last invalidate does not do."""
        task = self._tasks.get(name)
        if task is not None and not task.done() and not (fresh and getattr(task, "kelo_gen", None) != self._generation.get(name, 0)):
            return task
        gen = self._generation.get(name, 0)
        task = asyncio.get_running_loop().create_task(self._check(name, gen))
        task.kelo_gen = gen   # type: ignore[attr-defined]
        task.add_done_callback(lambda t: t.cancelled() or t.exception())   # a failed background check is logged, not raised
        self._tasks[name] = task
        return task

    async def _check(self, name: str, gen: int) -> dict[str, Any]:
        tok = i18n.use("en")   # kept in English; _shown writes the hints in each request's language
        try:
            st = dict(await self._fetch(name))
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("uvicorn.error").warning("checking the %s CLI failed", name, exc_info=True)
            raise
        finally:
            i18n.reset(tok)
        entry = {"st": st, "checked_at": round(self._wall(), 3), "at": self._clock(), "gen": gen}
        kept = self._entries.get(name)
        if kept is not None and kept.get("gen", -1) > gen:
            return st   # a check started after a change already answered: this older one does not replace it
        self._entries[name] = entry
        if self._generation.get(name, 0) == gen:
            self._dirty.discard(name)
        if self._tasks.get(name) is asyncio.current_task():
            self._tasks.pop(name, None)
        if self._save is not None:
            with contextlib.suppress(Exception):
                self._save(name, {"st": st, "checked_at": entry["checked_at"]})
        return st

    @staticmethod
    def _shown(name: str, st: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in st.items() if k != "hint_kind"}
        kind = st.get("hint_kind")
        if kind:
            out.update(cli_remote.hints(name, kind))
        else:
            for k in ("hint", "update_hint"):
                if isinstance(out.get(k), str) and out[k]:
                    out[k] = i18n.t(out[k])
        return out
