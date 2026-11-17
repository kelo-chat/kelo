"""Token use per model call, by the job that made it, and the optional background model.

Every model call of the app goes through one stream function (server.create_app wraps the one it is given): each `Usage` event a
transport reports is kept as one record (store.add_usage_event) with the time, the kind of job, the task it belongs to (when it has
one), the provider and model that ran it, and its input / output / cache-read / cache-write tokens. The daily totals (store `usage`)
grow with every record, so GET /api/usage keeps its `today` / `total` numbers.

Kinds (KINDS): the user's own replies (chat), schedule runs, the recurring check, tasks handed over to run unattended, delegated jobs
and their reports (worker), and the jobs nobody asks for: the nightly look-back (reflection), memory upkeep (memory), the journal,
proactive notices (proactive), conversation summaries (compaction), task names (task_name), follow-up messages (followup), the reachability check (probe).

The background model (profile background_provider / background_model, empty = the main model) runs only BACKGROUND_KINDS: jobs that
read and write the app's own notes and never act for the user (no browser, no payment, nothing sent to anyone). When it fails before
answering, the job runs on the model it would have used, and why is kept (store meta LAST_FALLBACK, shown in GET /api/usage).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

from .providers.types import ChatRequest, Usage

log = logging.getLogger(__name__)
timing = logging.getLogger("uvicorn.error")   # the server's visible log (uvicorn sets it up)

KINDS = ("chat", "schedule", "checklist", "task", "worker", "reflection", "memory", "journal", "proactive", "compaction", "followup",
         "task_name", "probe", "follow_plan", "situation", "other")
# may run on the background model: they only read the conversation and write the app's own notes
BACKGROUND_KINDS = frozenset({"reflection", "memory", "journal", "proactive", "compaction", "task_name", "follow_plan"})
LAST_FALLBACK = "background_model_fallback"   # store meta: the last time the background model failed and the main model ran instead

StreamFn = Callable[..., AsyncIterator[Any]]


def kind_of_source(source: str | None) -> str:
    """The kind of a turn by its source (server.start_turn)."""
    s = source or ""
    if s in ("chat", "api"):
        return "chat"
    if s.startswith("schedule:"):
        return "schedule"
    if s == "checklist":
        return "checklist"
    if s in ("worker", "handoff"):
        return "worker"
    if s == "background":
        return "task"
    if s.startswith("situation"):
        return "situation"
    return "other"


def share(part: int, whole: int) -> float | None:
    """part / whole rounded to three places; None when there is nothing to divide."""
    return round(part / whole, 3) if whole > 0 else None


def _timing_note(clock: dict[str, float | None] | None) -> str:
    """"first 2.1s, done 6.8s": when the call's first event came and when its usage arrived (the end), kept with the usage record."""
    if not clock or clock.get("began") is None:
        return ""
    first = clock.get("first")
    done = time.monotonic() - float(clock["began"] or 0)
    return (f"first {first:.1f}s, " if first is not None else "") + f"done {done:.1f}s"


def metered(stream_fn: StreamFn, record: Callable[[dict[str, Any]], None], turn: Callable[[], dict[str, Any] | None],
            guard: Any = None) -> Callable[..., StreamFn]:
    """counted(kind=None, note="") -> a stream function that passes every event on and records each Usage event.

    kind None: the kind of the turn running (turn() -> {"source", "task_id"}), else "other".
    `guard` (model_limits.QuotaGuard): asked before every call (a provider whose usage is used up is not called for anything but
    chat), told of every limit error (it becomes QuotaExhausted with the reset time) and of every call that answered."""
    def counted(kind: str | None = None, note: str = "") -> StreamFn:
        async def run(provider: Any, request: ChatRequest, **kw: Any) -> AsyncIterator[Any]:
            if guard is None:
                async for ev in plain(provider, request, **kw):
                    yield ev
                return
            name = provider if isinstance(provider, str) else getattr(provider, "name", str(provider))
            k = kind or kind_of_source((turn() or {}).get("source"))
            await guard.before(name, request.model, k)
            attempt = 0
            while True:
                answered = False   # the stream gave something: from here on nothing is retried (there is partial output)
                try:
                    async for ev in plain(provider, request, **kw):
                        answered = True
                        yield ev
                except Exception as e:  # noqa: BLE001 - busy is retried; a used-up limit becomes QuotaExhausted; the rest goes on as it was
                    if not answered and (wait := await guard.busy_wait(name, request.model, e, attempt)) is not None:
                        attempt += 1
                        log.info("model busy (%s), retrying in %.0f s", getattr(e, "status", None) or "429", wait)
                        await guard.pause(wait)
                        continue
                    hit = await guard.classify(name, request.model, e)
                    if hit is None or hit is e:
                        raise
                    raise hit from e
                break
            guard.clear(name)

        async def plain(provider: Any, request: ChatRequest, **kw: Any) -> AsyncIterator[Any]:
            if not request.purpose:   # the agy session pool tells chat from one-off jobs by it
                request = replace(request, purpose=kind or kind_of_source((turn() or {}).get("source")))
            # one timing line per model call (2026-11-12, agy felt slow): when the first event came and how long the call took, so the
            # owner's logs show where a slow reply spends its time; never the text, only sizes and times
            began, first, tokens = time.monotonic(), None, [0, 0]
            clock: dict[str, float | None] = {"began": began, "first": None}   # also written with the usage record (it outlives the log)
            try:
                async for ev in _counted(stream_fn(provider, request, **kw), provider, request, clock):
                    if first is None and not isinstance(ev, Usage):
                        first = time.monotonic() - began
                        clock["first"] = first
                    if isinstance(ev, Usage):
                        tokens[0] += ev.input_tokens or 0
                        tokens[1] += ev.output_tokens or 0
                    yield ev
            finally:
                timing.info("model call %s %s (%s): first %s, total %.1fs, in %d, out %d tokens, %d messages",
                            provider if isinstance(provider, str) else getattr(provider, "name", str(provider)), request.model,
                            request.purpose or "-", f"{first:.1f}s" if first is not None else "-", time.monotonic() - began,
                            tokens[0], tokens[1], len(request.messages))

        async def _counted(events: AsyncIterator[Any], provider: Any, request: ChatRequest,
                           clock: dict[str, float | None] | None = None) -> AsyncIterator[Any]:
            async for ev in events:
                if isinstance(ev, Usage):
                    t = turn() or {}
                    try:
                        record({"kind": kind or kind_of_source(t.get("source")), "task_id": t.get("task_id"),
                                "provider": provider if isinstance(provider, str) else getattr(provider, "name", str(provider)),
                                "model": request.model, "input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens,
                                "cache_read_tokens": getattr(ev, "cache_read_tokens", 0) or 0,
                                "cache_write_tokens": getattr(ev, "cache_write_tokens", 0) or 0,
                                "note": "; ".join(x for x in (note, f"agy session {ev.session}, {_timing_note(clock)}"
                                                               if getattr(ev, "session", "") else "") if x)})
                    except Exception:  # noqa: BLE001 - counting never breaks a reply
                        log.exception("usage record failed")
                yield ev
        return run
    return counted


def background_stream(counted: Callable[..., StreamFn], kind: str, chosen: Callable[[], tuple[str, str] | None],
                      key_args: Callable[[str], dict[str, Any]], on_fallback: Callable[[str, str], None]) -> StreamFn:
    """The stream function of a background job of `kind`: on the background model when one is chosen (chosen() -> (provider, model)),
    else on the model the job asked for. A background model that fails before its first event hands the request back to that model,
    and on_fallback(kind, reason) is told why. A job outside BACKGROUND_KINDS always runs on the model it asked for."""
    plain = counted(kind)

    async def run(provider: Any, request: ChatRequest, **kw: Any) -> AsyncIterator[Any]:
        bg = chosen() if kind in BACKGROUND_KINDS else None
        if bg is None or (bg[0] == provider and bg[1] == request.model):
            async for ev in plain(provider, request, **kw):
                yield ev
            return
        started = False
        reason = ""
        try:
            async for ev in counted(kind)(bg[0], replace(request, model=bg[1]), **key_args(bg[0])):
                started = True
                yield ev
            return
        except Exception as e:  # noqa: BLE001 - any failure of the background model: the main model does the job
            if started:
                raise
            reason = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("background model %s/%s failed for %s, the main model runs it: %s", bg[0], bg[1], kind, reason)
        on_fallback(kind, reason)
        async for ev in counted(kind, note=f"background model failed: {reason}")(provider, request, **kw):
            yield ev
    return run


def fallback_record(store: Any, kind: str, reason: str, provider: str, model: str) -> None:
    store.set_meta(LAST_FALLBACK, json.dumps({"at": time.time(), "kind": kind, "reason": reason[:300], "provider": provider,
                                              "model": model}))


def last_fallback(store: Any) -> dict[str, Any] | None:
    try:
        v = json.loads(store.meta(LAST_FALLBACK) or "null")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None
