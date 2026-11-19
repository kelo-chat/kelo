"""Compaction: keep each model request within the model's context window, the way a coding agent does.

Before every model request the whole request is estimated: system prompt + tool schemas + the summary + the history + this turn
(the user's message, and inside a turn every tool call and result so far). While that stays under COMPACT_AT (75 %, owner setting
ASSISTANT_COMPACT_AT) of the model's context window, everything is sent as it is. At or above it the conversation part is compacted to
at most TARGET (10 %) of the window, of which the newest messages kept word for word take RECENT_SHARE (5 %):
  - inside a turn, first the oldest tool results become one-line digests (agent/loop.py, no model call);
  - then the oldest messages of the conversation's middle are folded into the rolling summary (one or a few calls to the main
    model). The head (the first HEAD_MESSAGES messages since the chat was last cleared, capped at HEAD_TOKENS) and the newest messages
    that fit RECENT_SHARE of the window (at least MIN_KEEP) stay word for word.
Before a fold, one silent memory step (when the agent has its files) lets the model save durable facts, preferences and
commitments from the messages about to be folded to MEMORY.md / user.md / memory/ (only those file tools, no chat output; at most
once per compaction).
The summary is stored (store.conversation_summary: text + up_to_message_id), written to a fixed set of sections in the
conversation's language, and goes in as a system block after the stable prompt and the head, right before the recent history. After a chat turn, a background fold runs when the next request would likely cross the threshold, so the
next reply is not delayed; if the background fold did not happen or failed, the next turn folds before it starts. A fold that
fails keeps the previous summary; the messages it would have folded are left out of that request anyway (it cannot be sent
otherwise) and the next request tries again. Only one fold runs at a time.
Tokens are estimated as UTF-8 bytes / 3 (fair for mixed Korean and English; Korean is 3 bytes a character).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .providers import ChatRequest, TextDelta
from .secret_guard import scrub_rows
from .providers.types import CODES_CONTEXT, CONVERSATION_SUMMARY_HEAD as SUMMARY_HEAD
from .windows import Window

log = logging.getLogger("assistant.compaction")

BYTES_PER_TOKEN = 3
MESSAGE_OVERHEAD = 4          # role and framing of one message
COMPACT_AT = 0.75             # of the context window: compact when a request reaches this (ASSISTANT_COMPACT_AT)
TARGET = 0.10                 # ...then the conversation part (head + summary + recent messages) is at most this share
RECENT_SHARE = 0.05           # the newest messages kept word for word: this share of the context window
MIN_KEEP = 2                  # at least this many newest messages are always kept (on a tiny model: shortened, but present)
HEAD_MESSAGES, HEAD_TOKENS = 4, 2_000   # the conversation's first two exchanges stay too (long ones shortened once it is compacted)
FLUSH_INPUT_TOKENS = 16_000   # the memory step reads at most this much of what is about to be folded
NEXT_TURN_MARGIN = 1_000      # tokens a next user message is assumed to add, for the after-turn check
SUMMARY_TOKENS, SMALL_SUMMARY_TOKENS, SMALL_CONTEXT = 1_200, 400, 16_000
FETCH_LIMIT = 2_000           # newest messages after the summary looked at; older ones never summarized are left to the journal
FOLD_INPUT_TOKENS = 8_000     # one summary call reads at most this much of the conversation
MAX_FOLD_CALLS = 4            # summary calls in one compaction at most; what is left waits for the next one
FOLD_MESSAGE_CHARS = 2_000
SHORTENED = " …[shortened]"
# a "[Fri 2026-11-06 10:42:07 KST] [client_timezone=…]" line that older versions put before each user message; still removed
# from messages saved back then
TIME_TAG = re.compile(r"^\s*\[[^\]]*\]\s*\[client_timezone=[^\]]*\]\s*")

SUMMARY_PROMPT = (
    "You keep the running summary of the middle of a long conversation between a user and their personal assistant. You get the "
    "previous summary (it may be empty) and the messages that came after it. Write the new summary that replaces both, in the language "
    "the conversation is in (translate the section names below into it), with exactly these sections, each a few short lines or "
    "'-' when empty:\n"
    "## Goals and requests\n## Standing rules (what the user told you to always or never do)\n## Commitments (what you promised)\n"
    "## Decisions\n## Decided against (declined or ruled out; do not offer again)\n## Facts and numbers\n## People and places\n"
    "## Open tasks and next steps\n## User preferences\n"
    "Keep names, numbers, dates and promises exactly; drop greetings, small talk and finished business. Never invent anything. "
    "Each message starts with the date and time it was sent in [YYYY-MM-DD HH:MM]. Write every date and time as an absolute date "
    "and time (for example 2026-11-17 15:00), never 'today', 'tomorrow' or 'next week'; work them out from the time each message "
    "was sent. "
    "At most about {tokens} tokens (about {chars} characters). Reply with the summary only.")
NO_REPLY = "NO_REPLY"


def est_tokens(text: Any) -> int:
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return -(-len(text.encode("utf-8")) // BYTES_PER_TOKEN)


def message_tokens(m: dict[str, Any]) -> int:
    content = m.get("content")
    if isinstance(content, list):   # pictures etc.: the text parts are counted
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return est_tokens(content) + (est_tokens(str(m["tool_calls"])) if m.get("tool_calls") else 0) + MESSAGE_OVERHEAD


def request_tokens(messages: list[dict[str, Any]], extra: int = 0) -> int:
    return sum(message_tokens(m) for m in messages) + extra


def summary_target(window: Window) -> int:
    return SMALL_SUMMARY_TOKENS if window.context < SMALL_CONTEXT else SUMMARY_TOKENS


def summary_message(text: str) -> dict[str, Any]:
    return {"role": "system", "content": f"{SUMMARY_HEAD} {text.strip()}"}


def clip_tokens(text: str, tokens: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= tokens * BYTES_PER_TOKEN:
        return text
    limit = max(0, tokens * BYTES_PER_TOKEN - len(SHORTENED.encode()))
    return raw[:limit].decode("utf-8", "ignore").rstrip() + SHORTENED


@dataclass(slots=True)
class Selection:
    summary: str                       # as it goes in (maybe shortened)
    kept: list[dict[str, Any]]         # oldest first, as they go in (maybe shortened)
    dropped: list[dict[str, Any]]      # after the summary but left out, oldest first
    tokens: int
    shortened: bool = False


def select_history(candidates: list[dict[str, Any]], summary: str, budget: int, min_keep: int = MIN_KEEP) -> Selection:
    """`candidates`: the messages after the summary, oldest first. Keeps the newest that fit `budget` tokens together with the
    summary, at least `min_keep`; if even those do not fit, shortens the summary, then the oldest kept messages' long contents."""
    used = message_tokens(summary_message(summary)) if summary.strip() else 0
    kept: list[dict[str, Any]] = []
    for m in reversed(candidates):
        t = message_tokens(m)
        if len(kept) >= min_keep and used + t > budget:
            break
        kept.append(m)
        used += t
    kept.reverse()
    dropped = candidates[:len(candidates) - len(kept)]
    shortened = False
    if used > budget:
        shortened = True
        if summary.strip():
            allowed = max(60, budget // 4)
            if est_tokens(summary) > allowed:
                before = message_tokens(summary_message(summary))
                summary = clip_tokens(summary, allowed)
                used -= before - message_tokens(summary_message(summary))
        out = []
        for m in kept:
            over, t = used - budget, message_tokens(m)
            if over > 0 and t > 150 and isinstance(m.get("content"), str):
                m = {**m, "content": clip_tokens(m["content"], max(120, t - MESSAGE_OVERHEAD - over))}
                used -= t - message_tokens(m)
            out.append(m)
        kept = out
    return Selection(summary, kept, dropped, used, shortened)


@dataclass(slots=True)
class Assembled:
    """The conversation part of one request: `messages` = [summary block?] + history, ready to go between the system prompt and
    the user's message (the head, the summary block, then the recent history). `rows`: the stored messages behind `history` (same
    order), so a fold during the turn can find them; the head is never folded."""
    summary_msgs: list[dict[str, Any]]
    history: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    head: list[dict[str, Any]]
    tokens: int                 # the whole request, estimated (fixed part + this)
    over: bool                  # everything would have reached the threshold: this is cut down to TARGET
    left_out: int = 0
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [*self.head, *self.summary_msgs, *self.history]


def zone_of(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - an unknown name: UTC, as elsewhere in the server
        return ZoneInfo("UTC")


def stamp(created_at: Any, zone: ZoneInfo) -> str:
    """"[YYYY-MM-DD HH:MM]" of a message's send time (epoch seconds) in the profile's timezone; "" when it has none."""
    try:
        return datetime.fromtimestamp(float(created_at), zone).strftime("[%Y-%m-%d %H:%M]")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _with_steps(content: str, m: dict[str, Any]) -> str:
    """An assistant reply's content with its tool steps after it, for the model (the stored text does not change)."""
    if m.get("role") != "assistant" or not m.get("steps"):
        return content
    return f"{content}\n\n[Not from the user: steps of this reply: {m['steps']}]"


def _line(m: dict[str, Any], zone: ZoneInfo | None = None) -> str:
    who = "User" if m.get("role") == "user" else "Assistant"
    text = TIME_TAG.sub("", str(m.get("content") or "")).strip()
    if len(text) > FOLD_MESSAGE_CHARS:
        text = text[:FOLD_MESSAGE_CHARS] + SHORTENED
    when = stamp(m.get("created_at"), zone or zone_of(None))
    return f"{when} {who}: {text}" if when else f"{who}: {text}"


class Compactor:
    """The stored conversation, fitted to a model's window, and the folds into its summary (with the user's main model or the model
    of the turn). `threshold`: the owner's COMPACT_AT."""

    def __init__(self, store: Any, stream_fn: Callable[..., AsyncIterator[Any]],
                 key_args: Callable[[str], dict[str, Any]] = lambda _p: {}, threshold: float = COMPACT_AT, target: float = TARGET,
                 flush_fn: Callable[[str, str, str], Awaitable[Any]] | None = None):
        """`flush_fn(provider, model, transcript)`: the silent memory step (None = the agent has no files: skipped)."""
        self.store, self.stream_fn, self.key_args, self.flush_fn = store, stream_fn, key_args, flush_fn
        self.threshold, self.target = threshold, min(target, threshold * 0.9)
        self.forgotten: Callable[[], str] = lambda: ""   # forget.Tombstones.block: topics the owner asked to forget (docs/forget.md)
        self.lock = asyncio.Lock()
        self.last_error = ""
        self.last_request_tokens = 0

    def _zone(self) -> ZoneInfo:
        """The profile's timezone: the send times a fold reads are written in it."""
        try:
            return zone_of(self.store.profile().get("timezone"))
        except Exception:  # noqa: BLE001 - a store without a profile: UTC
            return zone_of(None)

    def limit(self, window: Window) -> int:
        return int(window.context * self.threshold)

    def _head(self, before_id: int | None, capped: bool) -> tuple[list[dict[str, Any]], int]:
        """(the head as it goes in, the id of its last message). Shortened to HEAD_TOKENS once the conversation is compacted."""
        since = self.store.session_start() if hasattr(self.store, "session_start") else 0   # /new: the conversation starts after it
        rows = self.store.first_messages(HEAD_MESSAGES, before_id=before_id, after_id=since)
        each = HEAD_TOKENS // max(1, len(rows))
        msgs = [{"role": r["role"], "content": _with_steps(clip_tokens(r["content"], each) if capped else r["content"], r)} for r in rows]
        return msgs, (rows[-1]["id"] if rows else since)

    def _candidates(self, before_id: int | None) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
        cur = self.store.conversation_summary()
        _, head_end = self._head(before_id, False)
        start = max(cur["up_to_message_id"], head_end)
        rows = self.store.messages_after(start, before_id=before_id, limit=FETCH_LIMIT)
        return cur, start, [{"id": r["id"], "role": r["role"], "content": r["content"], "steps": r.get("steps") or ""} for r in rows]

    def assemble(self, window: Window, fixed_tokens: int, before_id: int | None = None) -> Assembled:
        """Head + summary + everything after it when that stays under the threshold; otherwise the head (shortened), the summary and
        the newest messages that fit TARGET (`over` set)."""
        cur, _, cands = self._candidates(before_id)
        summary = cur["summary"]
        compacted = bool(summary.strip()) or cur["up_to_message_id"] > 0
        head, _ = self._head(before_id, compacted)
        sum_tokens = message_tokens(summary_message(summary)) if summary.strip() else 0
        whole = fixed_tokens + request_tokens(head) + sum_tokens + request_tokens(cands)
        if whole < self.limit(window):
            sel = Selection(summary, cands, [], sum_tokens + request_tokens(cands))
            over = False
        else:
            if not compacted:
                head, _ = self._head(before_id, True)
            budget = min(int(window.context * RECENT_SHARE) + sum_tokens, int(window.context * self.target) - request_tokens(head))
            sel = select_history(cands, summary, budget)
            over = True
        # the tool steps of each reply, for the model only (reply_text.py): what it did, not what the outside said. Kept on every
        # reply (not only the last few): a note that moved along with the window would change the history each turn and restart
        # the provider's session (providers/agy_pool.py continuation)
        history = [{"role": m["role"], "content": _with_steps(m["content"], m)} for m in sel.kept]
        return Assembled([summary_message(sel.summary)] if sel.summary.strip() else [], history, sel.kept, head,
                         fixed_tokens + request_tokens(head) + sel.tokens, over, len(sel.dropped), {"shortened": sel.shortened})

    def needs_fold(self, window: Window, fixed_tokens: int, before_id: int | None = None) -> bool:
        return self.assemble(window, fixed_tokens, before_id).over

    def plan(self, window: Window, fixed_tokens: int, before_id: int | None = None) -> int | None:
        """The id of the newest message to fold so the rest (with a new summary) fits TARGET; None when nothing needs to go."""
        _, _, cands = self._candidates(before_id)
        head, _ = self._head(before_id, True)
        room = min(int(window.context * RECENT_SHARE),
                   int(window.context * self.target) - request_tokens(head) - summary_target(window)) - MESSAGE_OVERHEAD
        sel = select_history(cands, "", room)
        return sel.dropped[-1]["id"] if sel.dropped else None

    async def compact(self, provider: str, model: str, window: Window, fixed_tokens: int, before_id: int | None = None,
                      *, only_if_over: bool = True) -> dict[str, Any]:
        """Fold what must go so the request fits TARGET. Waits for a fold already running, then looks again."""
        async with self.lock:
            if only_if_over and not self.needs_fold(window, fixed_tokens, before_id):
                return {"status": "nothing", "folded": 0, "reason": ""}
            through = self.plan(window, fixed_tokens, before_id)
            if through is None:
                return {"status": "nothing", "folded": 0, "reason": ""}
            return await self._fold_through(provider, model, window, through)

    async def fold_through(self, provider: str, model: str, window: Window, through_id: int) -> dict[str, Any]:
        async with self.lock:
            return await self._fold_through(provider, model, window, through_id)

    async def _fold_through(self, provider: str, model: str, window: Window, through_id: int) -> dict[str, Any]:
        """Fold every message after the summary up to `through_id` into it, in at most MAX_FOLD_CALLS summary calls.
        {"status": done|partial|failed|stale|nothing, "folded": n, "reason"}."""
        folded = 0
        cap = min(FOLD_INPUT_TOKENS, max(1_000, window.context // 3))
        target = summary_target(window)
        _, head_end = self._head(None, False)
        cur = self.store.conversation_summary()
        about = scrub_rows(self.store, self.store.messages_after(max(cur["up_to_message_id"], head_end), before_id=through_id + 1, limit=FETCH_LIMIT))
        if about and self.flush_fn is not None:   # once per compaction: save what lasts before the details leave the context
            await self._flush(provider, model, about)
        for _ in range(MAX_FOLD_CALLS):
            cur = self.store.conversation_summary()
            start = max(cur["up_to_message_id"], head_end)
            if start >= through_id:
                break
            rows = scrub_rows(self.store, self.store.messages_after(start, before_id=through_id + 1, limit=FETCH_LIMIT))
            if not rows:
                break
            lines, size, batch = [], 0, []
            zone = self._zone()
            for m in rows:
                line = _line(m, zone)
                if batch and size + est_tokens(line) > cap:
                    break
                lines.append(line)
                batch.append(m)
                size += est_tokens(line)
            prompt = SUMMARY_PROMPT.format(tokens=target, chars=target * 2)
            if forgotten := self.forgotten():   # the summary never refers to a forgotten topic, even when the messages still do
                prompt += f"\n\n{forgotten}\nDo not make any reference to the forgotten topics in the summary, and do not note that they were left out."
            body = f"Previous summary:\n{cur['summary'].strip() or '(none)'}\n\nNew messages:\n" + "\n".join(lines)
            req = ChatRequest(model=model, messages=[{"role": "system", "content": prompt}, {"role": "user", "content": body}],
                              tools=[], extra={})
            parts: list[str] = []
            try:
                async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
                    if isinstance(ev, TextDelta):
                        parts.append(ev.text)
                text = "".join(parts).strip()
                if not text:
                    raise RuntimeError("the model gave no summary")
            except Exception as e:  # noqa: BLE001 - keep the old summary; the next request tries again
                self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
                log.warning("conversation summary not updated: %s", self.last_error)
                return {"status": "failed", "folded": folded, "reason": self.last_error}
            if not self.store.save_conversation_summary(clip_tokens(text, int(target * 1.5)), batch[-1]["id"], len(batch),
                                                        expect_up_to=cur["up_to_message_id"]):
                return {"status": "stale", "folded": folded, "reason": "the conversation changed meanwhile (cleared?)"}
            folded += len(batch)
        self.last_error = ""
        if not folded:
            return {"status": "nothing", "folded": 0, "reason": ""}
        done = self.store.conversation_summary()["up_to_message_id"] >= through_id
        return {"status": "done" if done else "partial", "folded": folded, "reason": ""}

    async def _flush(self, provider: str, model: str, rows: list[dict[str, Any]]) -> None:
        lines, size = [], 0
        zone = self._zone()
        for m in rows:
            line = _line(m, zone)
            if lines and size + est_tokens(line) > FLUSH_INPUT_TOKENS:
                break
            lines.append(line)
            size += est_tokens(line)
        try:
            transcript = "\n".join(lines)
            if forgotten := self.forgotten():
                transcript += f"\n\n{forgotten}\nDo not write anything about the forgotten topics to memory."
            await self.flush_fn(provider, model, transcript)   # type: ignore[misc]
        except Exception as e:  # noqa: BLE001 - the fold goes on without it
            log.warning("memory step before compaction failed: %s: %s", type(e).__name__, str(e)[:160])
        self.store.note_memory_flush()


# ---- context-length errors: the request was larger than the model takes
CONTEXT_REJECT_SHARE = 0.9   # a request this near the model's window that the provider rejects (4xx) was rejected for its size


def is_context_error(e: BaseException, size: int = 0, window: int = 0) -> bool:
    """Whether the provider rejected a request for being too large, by structure: HTTP 413, the provider's own code (CODES_CONTEXT), or
    a client-error rejection (400 / 422) of a request whose estimated `size` is within CONTEXT_REJECT_SHARE of the model's `window`."""
    status = getattr(e, "status", None) or getattr(e, "status_code", None)
    if status == 413 or getattr(e, "code", "") in CODES_CONTEXT:
        return True
    return bool(size and window and status in (400, 422) and size >= window * CONTEXT_REJECT_SHARE)


class ContextOverflow(Exception):
    """A model request was too large for the model. `tokens`: the estimated size of that request."""

    def __init__(self, tokens: int, message: str):
        super().__init__(message)
        self.tokens = tokens
