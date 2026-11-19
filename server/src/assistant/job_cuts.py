"""A job cut off by a restart or a lost browser, and the note it is picked up with. Pure: the callers read the transcript and the
ledgers and pass them in. The checkpoint is the finished tool call; the one call running at the cut is answered by `cut_result`.

What a cut call could have done decides how it is answered: a read or a move is done again if still needed; a send, change or delete
is checked first (its ledger record is unknown); a pay press is never pressed again for the same order (the shop's own page decides).
At most MAX_PICKUPS pick-ups come in a row without a finished tool call in between; then the job stops and the owner decides."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

PICKUP_WAITS = (0.0, 60.0, 300.0)   # seconds before the 1st, 2nd, 3rd pick-up in a row without progress; then the job stops
MAX_PICKUPS = len(PICKUP_WAITS)

CUT_READ = ("[This step was cut off (the Kelo server or the browser restarted) before it gave a result. It only read or moved around, "
            "so do it again if you still need it.]")
CUT_ACT = ("[This step was cut off (the Kelo server or the browser restarted) before it gave a result, and it may already have happened "
           "outside (record {id}). Do not do it again before you check: look where it would show (for mail: the sent folder; on a site: "
           "its history or order page), then call action_result({id}, done|not_done).]")
CUT_PAY = ("[The pay press was cut off (the Kelo server or the browser restarted). Nobody saw whether the order went through. Nothing can "
           "be pressed again for this order: read the shop's own order or confirmation page and call purchase_result with what it "
           "shows (placed with the order number, not_placed with what shows nothing was ordered, or unknown). If you cannot tell, report "
           "needs_input and say so.]")

PICKUP_HEAD = "The {what} restarted while you were working; you are picked up where you stopped."   # what: "Kelo server" | "browser"
TAB_KEPT = "Your browser tab is still open on the page you were on; read it again (browser_read) before you act."
TAB_NEW = ("Your browser tab was closed; a new blank tab will open. Your last page was {url}: if you need it, open that site's home page "
           "and get back to it through the site's own links (sign-ins are kept); a form you were filling may be empty again.")
TAB_HELD = ("The owner holds your browser tab (they took it over before the restart). Do not act on it; your steps wait until they hand "
            "it back.")
CHECKS = "Before anything that would repeat them, check these: {items}."
PAY_CHECK = "a payment press on {site} whose outcome nobody saw (purchase_result first)"
REC_CHECK = "record {id}: {what} (action_result after you check)"
PICKUP_TAIL = ("Do not redo steps that finished above. A payment or an approval you had not used yet is not carried over: ask again. "
               "Go on from where you stopped and finish with report.")


@dataclass(frozen=True, slots=True)
class OpenCall:
    call_id: str
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Cut:
    at: float
    why: str                      # "restart" | "browser"
    step: OpenCall | None         # the call that was running, if any
    records: tuple[int, ...]      # DoneOnce records of the cut run still "unknown"
    pay: str | None               # id of a pay attempt of this tab still reserved / pressed / unknown
    tab: str                      # "kept" (same page re-adopted) | "new" (the page is gone) | "held" (the owner holds it) | "none"
    url: str                      # the job's last_url
    texts: tuple[str, ...] = ()   # what each record is (aligned with records), for the pick-up note

    def to_json(self) -> str:
        return json.dumps({
            "at": self.at, "why": self.why, "records": list(self.records), "pay": self.pay, "tab": self.tab, "url": self.url,
            "texts": list(self.texts),
            "step": None if self.step is None else {"call_id": self.step.call_id, "tool": self.step.tool, "args": self.step.args},
        }, ensure_ascii=False)

    @classmethod
    def of(cls, raw: str) -> Cut | None:
        if not raw or not raw.strip():
            return None
        try:
            d = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None
        step = d.get("step")
        return cls(
            at=float(d.get("at") or 0.0), why=str(d.get("why") or "restart"),
            step=None if not step else OpenCall(str(step.get("call_id") or ""), str(step.get("tool") or ""), dict(step.get("args") or {})),
            records=tuple(int(x) for x in d.get("records") or ()), pay=d.get("pay") or None, tab=str(d.get("tab") or "none"),
            url=str(d.get("url") or ""), texts=tuple(str(x) for x in d.get("texts") or ()))


def open_calls(transcript: list[dict[str, Any]]) -> list[OpenCall]:
    """Assistant tool calls with no tool message after them (a hard kill), in order."""
    pending: dict[str, OpenCall] = {}
    for msg in transcript:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                cid = str(tc.get("id") or "")
                pending[cid] = OpenCall(cid, str(fn.get("name") or ""), args if isinstance(args, dict) else {})
        elif msg.get("role") == "tool":
            pending.pop(str(msg.get("tool_call_id") or ""), None)
    return list(pending.values())


def cut_result(call: OpenCall, records: dict[str, int], pay: str | None) -> str:
    """The tool message written for a cut call. `records`: tool name -> unknown record id of the cut run for that tool."""
    if pay and call.tool == "checkout_submit":
        return CUT_PAY
    if call.tool in records:
        return CUT_ACT.format(id=records[call.tool])
    return CUT_READ


def _site(url: str) -> str:
    host = urlparse(url).hostname if url else None
    return host or "the shop"


def pickup_note(cut: Cut, *, describe: Callable[[int], str] | None = None) -> str:
    """The English note the picked-up run starts with (the caller puts it inside "[Not from the user: ...]")."""
    what = "browser" if cut.why == "browser" else "Kelo server"
    parts = [PICKUP_HEAD.format(what=what)]
    if cut.tab == "kept":
        parts.append(TAB_KEPT)
    elif cut.tab == "new" and cut.url:
        parts.append(TAB_NEW.format(url=cut.url))
    elif cut.tab == "held":
        parts.append(TAB_HELD)
    checks = []
    if cut.pay:
        checks.append(PAY_CHECK.format(site=_site(cut.url)))
    for i, rid in enumerate(cut.records):
        what_it = cut.texts[i] if i < len(cut.texts) else (describe(rid) if describe is not None else "a step")
        checks.append(REC_CHECK.format(id=rid, what=what_it))
    if checks:
        parts.append(CHECKS.format(items="; ".join(checks)))
    parts.append(PICKUP_TAIL)
    return " ".join(parts)


def next_pickup(cuts_in_a_row: int) -> float | None:
    """Seconds to wait before this pick-up (`cuts_in_a_row` counts this cut too, 1 = first), or None when the job must stop."""
    n = max(1, int(cuts_in_a_row))
    if n > MAX_PICKUPS:
        return None
    return PICKUP_WAITS[n - 1]
