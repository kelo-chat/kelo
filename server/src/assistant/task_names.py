"""A task's name in the Work screen: a short title of what it does, made by the owner's background model from the request.

The first title of a task is the owner's message cut short (activity.title_from). For a longer request, or a short answer like
"너가", TaskNamer asks the model for a 2-6 word noun phrase in the background and renames the task; any failure keeps the first
title. The request goes only to the owner's own configured model (the background one when set, else the main one): nothing new
leaves the server.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from .activity import is_short_reply
from .providers import ChatRequest, TextDelta

log = logging.getLogger(__name__)

NAME_PROMPT = ("Name this task for a to-do list in 2 to 6 words, in the language of the request. A noun phrase of what will be "
               "done (e.g. 'Play Store sign-in screenshot', '쿠팡 리뷰 4개 등록'), no quotes, no ending punctuation, no polite "
               "words, no 'please'. Request: {request}")
EARLIER_NOTE = " Earlier request: {earlier}"
MAX_NAME = 40          # characters kept
MAX_ANSWER = 80        # a longer answer is not a name (the model answered the request instead)
SHORT_REQUEST = 24     # a request this short is its own title
TIMEOUT = 20.0
NAMED_SOURCES = ("chat", "api")   # jobs, schedules and goals already have their own titles
MAX_TOKENS = 30
_PREFIX = re.compile(r"^\s*(?:title|name|task|제목|이름|작업)\s*[:：]\s*", re.I)


def clean_name(text: str) -> str:
    """The model's answer as a task name: first line, no quotes / markdown / ending punctuation, at most MAX_NAME characters.
    "" when it is empty or too long to be a name."""
    line = next((ln for ln in (text or "").strip().splitlines() if ln.strip()), "")
    s = line.strip()
    for _ in range(3):
        s = _PREFIX.sub("", re.sub(r"^[#>*\-\s]+", "", s)).strip()
        s = s.strip("\"'`“”‘’「」『』*_ ")
    s = re.sub(r"\s+", " ", s).rstrip(" .,;:!?。…、\"'`“”‘’」』*_")
    if not s or len(s) > MAX_ANSWER:
        return ""
    if len(s) > MAX_NAME:
        head = s[:MAX_NAME]
        s = (head.rsplit(" ", 1)[0] if " " in head else head).rstrip(" .,;:!?。、") + "…"
    return s


def hidden_marks() -> tuple[str, ...]:
    from .goals import GOAL_MARK
    from .nest_drafts import NEST_SHARE_MARK
    from .recheck import RECHECK_MARK
    from .schedules import SCHEDULE_MARK
    from .watchers import WATCH_MARK
    from .workers import HANDOFF_MARK
    return (HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK)


class TaskNamer:
    """`stream_fn` is the background-model stream (server.job_stream("task_name")); `model()` -> (provider, model) or None;
    `rename(task_id, name)` stores the name."""

    def __init__(self, stream_fn: Callable[..., AsyncIterator[Any]], rename: Callable[[int, str], Any],
                 model: Callable[[], tuple[str, str] | None] = lambda: None,
                 key_args: Callable[[str], dict[str, Any]] = lambda _p: {}, clock: Callable[[], float] = time.monotonic,
                 marks: Callable[[], tuple[str, ...]] = hidden_marks) -> None:
        self.stream_fn, self.rename, self.model, self.key_args, self.clock, self.marks = stream_fn, rename, model, key_args, clock, marks
        self._running: set[asyncio.Task[None]] = set()
        self._named: set[int] = set()

    def wants(self, request: str, source: str = "chat") -> bool:
        text = (request or "").strip()
        if source not in NAMED_SOURCES or not text or text.startswith(self.marks()):
            return False
        return len(text) > SHORT_REQUEST or is_short_reply(text)

    def name_later(self, task_id: int, request: str, earlier: str = "", source: str = "chat") -> bool:
        """Start the background naming of the task; True when one was started (at most one per task)."""
        if task_id in self._named or not self.wants(request, source):
            return False
        try:
            task = asyncio.get_running_loop().create_task(self._name(task_id, request.strip(), (earlier or "").strip()))
        except RuntimeError:   # no event loop: nothing to run on
            return False
        self._named.add(task_id)
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return True

    async def _name(self, task_id: int, request: str, earlier: str) -> None:
        try:
            await asyncio.wait_for(self._ask(task_id, request, earlier), TIMEOUT)
        except Exception as e:  # noqa: BLE001 - the first title stays
            log.debug("task %s not named: %s: %s", task_id, type(e).__name__, str(e)[:160])

    async def _ask(self, task_id: int, request: str, earlier: str) -> None:
        chosen = self.model()
        if not chosen:
            return
        provider, model = chosen
        prompt = NAME_PROMPT.format(request=request[:600]) + (EARLIER_NOTE.format(earlier=earlier[:600]) if earlier else "")
        req = ChatRequest(model=model, messages=[{"role": "user", "content": prompt}], tools=[], max_tokens=MAX_TOKENS, extra={})
        parts: list[str] = []
        async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
        if name := clean_name("".join(parts)):
            self.rename(task_id, name)
