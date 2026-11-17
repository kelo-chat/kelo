"""Going on after a turn was cut off: by a server restart, or because the AI model could not be reached.

Every turn that can be started again keeps a small resume record on its task (activity.py, column `resume`):
  {"kind": "chat", "message_id": <the user's message>}                         a reply to the user's own chat message
  {"kind": "handoff", "message_id": <hidden message>, "title", "head", "quiet", "parent_task_id", "worker_id"}
                                                                               the main agent answering a job's report, a goal kickoff
                                                                               or goal work, a new schedule's check
  {"kind": "background", "prompt", "title", "source", "owner", "note", "saved_as"}   a task handed over from the agent screen
  {"kind": "schedule", "schedule_id"}                                          an automatic run

After a restart (server.py, at start-up):
- anything the system started (handoff, background, schedule) goes on by itself: the same turn again from the saved conversation, told
  that the server restarted mid-turn and not to repeat finished steps; an automatic run only within the catch-up window. The old task
  is "interrupted" and points at the new one (`resumed_as`).
- the user's own chat turn is not run again behind their back: the chat gets the "stopped" line with a "Continue" button (a quick
  reply), and tapping it goes on.
- a task resumed after restarts more than `MAX_RESTART_RESUMES` times ends as failed, so a crash loop cannot run it for ever.

When the main AND the fallback model fail for a reason of the provider (network, timeout, overload, 5xx), not of the content:
- a background turn is not failed: its task waits ("waiting_model"), a light check of the model runs after 1, 5, 15, then every 60
  minutes, and the turn goes on once the model answers. The user hears about it only when it is still down after 6 hours.
- a chat turn ends with the "the AI model connection dropped" line and a "Continue" button, and goes on by itself once the model is
  back, unless the user has written something newer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .i18n import N_
from .providers import ProviderError
from .providers.types import CODES_LIMIT, CODES_OUTAGE, ProviderStalled

MAX_RESTART_RESUMES = 3
MODEL_WAIT_BACKOFF = (60.0, 300.0, 900.0, 3600.0)   # seconds before each check; the last one repeats
MODEL_WAIT_NOTIFY_AFTER = 6 * 3600.0                 # tell the user once when the model is still down after this long
MODEL_WAIT_GIVE_UP = 48 * 3600.0                     # then the task ends as failed
RESUMABLE_ON_RESTART = ("handoff", "background", "schedule")   # kinds that go on by themselves after a restart

CONTINUE_LABEL = N_("Continue")
MODEL_DOWN_LINE = N_("The AI model connection dropped, so I stopped")
RESTART_LINE = N_("Stopped because the server restarted")   # the same words as the task's result (activity.RESTART_RESULT)

RESTART_NOTE = ("[Not from the user: the server restarted in the middle of this turn and cut it off. Continue from where you left off. "
                "What you already said is in the conversation above. Steps that already finished (saved facts or plans, messages sent, "
                "jobs started, bookings) are done: check their state instead of doing them again, and do not repeat what you already "
                "told the user. Do not apologize at length; just go on.]")
MODEL_NOTE = ("[Not from the user: the AI model could not be reached for a while and this turn was cut off. It is back now. Continue "
              "from where you left off. What you already said is in the conversation above. Steps that already finished are done: "
              "check their state instead of doing them again, and do not repeat what you already told the user.]")

def with_language(note: str, lang: str) -> str:
    """A resume note for the model, with the language to answer in. The note is English and, after a restart, may be the whole of the
    message the model sees (or most of it), so a model mirrors it: an English, clipped answer to a Korean user. Saying the language
    and the usual length keeps the answer the owner's (2026-11-10, a sign-in cut off by two restarts came back in English)."""
    from .i18n import NAMES
    name = NAMES.get(lang, NAMES.get((lang or "").split("-")[0], "the user's language"))
    return (f"{note}\n[Not from the user: answer in {name}, the user's language, as fully as this task needs; this note and the one "
            "above are Kelo's, not the user's words, so never answer in their language or style.]")


def is_outage(e: BaseException) -> bool:
    """Whether a turn's failure came from the provider being unreachable or overloaded (worth waiting for), not from what was asked
    (a content or context error, a sign-in or a setting the user must fix, a model that answered with nothing)."""
    if isinstance(e, ProviderStalled):   # it went silent mid-request: the turn ends with its own line (client.stall_seconds)
        return False
    if isinstance(e, (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.TransportError)):
        return True
    if isinstance(e, ProviderError):
        status = getattr(e, "status", None)
        if isinstance(status, int):
            return status in (408, 409, 425, 429, 529) or status >= 500
        return getattr(e, "code", "") in CODES_OUTAGE | CODES_LIMIT   # a stream error event: no status, the provider's own code
    cause = e.__cause__ or e.__context__
    return cause is not None and cause is not e and isinstance(cause, (asyncio.TimeoutError, TimeoutError, ConnectionError,
                                                                       httpx.TransportError))


def next_wait(waits: int) -> float:
    """Seconds until the next model check after `waits` checks so far (0 = the first)."""
    return MODEL_WAIT_BACKOFF[min(max(waits, 0), len(MODEL_WAIT_BACKOFF) - 1)]


def continue_buttons(label: str) -> list[dict[str, Any]]:
    """The quick-reply set with one "Continue" button (goals.attach_replies; the app sends the label as the user's message)."""
    return [{"question": "", "options": [label]}]
