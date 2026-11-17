"""Provider-neutral request/response types.

Messages use the OpenAI chat shape because it is the lingua franca; each
transport converts to and from its own wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Message = dict[str, Any]  # {"role": "system|user|assistant|tool", "content": ..., ...}
ToolSpec = dict[str, Any]  # {"type":"function","function":{"name","description","parameters"}}


@dataclass(slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, exactly as the model produced it


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0    # input tokens read from the provider's prompt cache (where the transport reports them)
    cache_write_tokens: int = 0   # input tokens written to it
    reasoning_tokens: int = 0     # of output_tokens: spent on reasoning (where the transport reports them)
    session: str = ""              # agy: how the call used the session pool ("turn", "round", "new: <why>"); kept in usage_events.note


@dataclass(slots=True)
class Done:
    finish_reason: Literal["stop", "tool_calls", "length", "other"] = "stop"


@dataclass(slots=True)
class Thinking:
    """The model is reasoning. A status signal only: the reasoning text itself is never kept or shown.

    Emitted once per reasoning run (again only after text arrived in between), not per token.
    """


Event = TextDelta | ToolCall | Usage | Done | Thinking


# The machine-readable error codes providers put in their JSON error bodies (OpenAI "error.code", Anthropic "error.type", Google
# "error.status"), lower-cased. They are protocol identifiers, not words: what a failure is comes from the HTTP status and these codes,
# never from the words of its message (docs/no-word-lists.md).
CODES_BALANCE = frozenset({"insufficient_quota", "billing_error", "insufficient_credits", "payment_required"})
CODES_LIMIT = frozenset({"rate_limit_exceeded", "rate_limit_error", "resource_exhausted", "too_many_requests"}) | CODES_BALANCE
CODES_OUTAGE = frozenset({"overloaded_error", "api_error", "server_error", "service_unavailable", "unavailable", "internal",
                          "deadline_exceeded", "timeout", "overloaded"})
CODES_CONTEXT = frozenset({"context_length_exceeded", "string_above_max_length", "request_too_large"})


def error_code(body: Any) -> str:
    """The lower-cased code of a provider's error body (a dict, or JSON text): error.code, else error.type, else error.status."""
    if isinstance(body, (str, bytes)):
        import json
        try:
            body = json.loads(body)
        except ValueError:
            return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, list) and err:
        err = err[0]
    if not isinstance(err, dict):
        err = body if isinstance(body, dict) else {}
    for key in ("code", "type", "status"):
        v = err.get(key)
        if isinstance(v, str) and v:
            return v.lower()
    return ""


class ProviderError(Exception):
    """Raised for HTTP/protocol failures. Never includes credentials. `code`: the provider's own error code (see CODES_*), "" when none."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = (code or "").lower()
        self.retry_after = retry_after   # seconds the provider asked to wait (an HTTP Retry-After header), when it said


class QuotaExhausted(ProviderError):
    """The model's usage limit (a subscription window, a rate limit, the credit) is used up (model_limits.py). `reset_at`: when it
    can be used again, None when nobody said; `until`: when the next call may go out (the reset, or an estimate); `deferred`: not used
    up yet but nearly, and a background job steps aside for chat; `balance`: the provider's prepaid balance ran out (HTTP 402 and the
    like): no reset is coming, the owner has to top it up."""

    def __init__(self, message: str, *, provider: str = "", reset_at: float | None = None, until: float | None = None,
                 deferred: bool = False, balance: bool = False):
        super().__init__(message, status=429)
        self.provider, self.reset_at, self.until, self.deferred = provider, reset_at, until if until is not None else reset_at, deferred
        self.balance = balance


class ListUnavailable(ProviderError):
    """A model list could not be fetched for a reason that passes (timeout, network, 5xx, 429, a sidecar restarting): the last good
    list is shown instead (models.model_list)."""


class ProviderStalled(ProviderError):
    """Nothing at all came from the provider for `seconds` (no answer, no reasoning, not even a keep-alive line): the request is
    given up instead of waiting for ever (client.stall_seconds). Not an outage to wait for: the turn ends with its own line."""

    def __init__(self, seconds: float):
        super().__init__(f"no data from the provider for {int(seconds)} seconds")
        self.seconds = seconds


class UnsupportedProvider(ProviderError):
    """The provider's auth or wire protocol is not implemented here."""


# The system block that carries the rolling summary of the earlier conversation (compaction.py). Marked so the agy adapter can hand
# it over with the conversation instead of the rules file (cli_agents.fit_agy_rules).
CONVERSATION_SUMMARY_HEAD = "Earlier in this conversation (summary):"

# What the agent loop puts in place of an older tool result / an older picture to keep a request small (agent/loop.py). The agy session
# pool knows both: its live session still holds the full original, so a request that carries the short form goes on from the session.
DIGEST_PREFIX = "[earlier result of "
OLD_PICTURE = "[Not from the user: picture shown earlier ({name}), no longer attached.]"

# Heads the message with this turn's lines (clock, calendar, schedules, goals) that the server puts right before the user's message
# (server.py). It is rebuilt every turn and never stored, so the agy adapter knows the previous turn's copy is gone from the next
# request on purpose and can keep its CLI session going (agy_pool.continuation).
TURN_NOTE = "[Not from the user: the app's context for this turn.]\n"
# Ends the note: some providers merge the note and the user's message into one message, and without a line saying where the user's
# own words start a model once took a plain "order X on <site>" from the owner for text injected by a page (2026-11-15).
TURN_NOTE_END = "\n[End of the app's context. What follows is the user's own message, word for word.]"


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    max_tokens: int | None = None
    temperature: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # how many leading tools stay the same from request to request (the core set, tools on demand): a transport with explicit
    # prompt caching puts a breakpoint after them as well as after the last tool. 0 = not known.
    stable_tools: int = 0
    # what the call is for (the job kind: "chat", "worker", "memory" ...; "" = unknown). Only the agy session pool reads it; no wire sends it.
    purpose: str = ""


# A system/developer message after the conversation has started (the agent loop's "steps are running out" / "answer now" notes, the
# server's retry nudges) is a note for that point of the conversation, not a rule. Several wires refuse it there ("System/developer
# messages must precede conversation history": the Claude subscription plugin; many local chat templates) and the Responses wire would
# move it into the instructions at the top. So every request leaves stream_chat with system text only at the start; later ones become
# user-role text, tagged as the app's own words so the model never takes them for the user.
LATE_NOTE = "[Not from the user: a note from the app.]\n"
_NOT_USER = "[Not from the user"


def late_notes_as_user(messages: list[Message]) -> list[Message]:
    """`messages` with every system/developer message after the first other message turned into a tagged user message.
    The leading run of system messages (the system prompt, the conversation summary) stays as it is. Unchanged list when none."""
    talk = next((i for i, m in enumerate(messages) if m.get("role") not in ("system", "developer")), len(messages))
    if not any(m.get("role") in ("system", "developer") for m in messages[talk:]):
        return messages
    out = list(messages[:talk])
    for m in messages[talk:]:
        if m.get("role") not in ("system", "developer"):
            out.append(m)
            continue
        content = m.get("content")
        if isinstance(content, list):
            out.append({"role": "user", "content": [{"type": "text", "text": LATE_NOTE.rstrip("\n")}, *content]})
        elif content:
            text = str(content)
            out.append({"role": "user", "content": text if text.lstrip().startswith(_NOT_USER) else LATE_NOTE + text})
    return out
