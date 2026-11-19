"""The hand-over of a final checkout step, by structure (docs/browser-workers.md).

With the payment split on, the chat and the jobs that find things have no paying tool. A run that reaches a final checkout step
(tools/checkout.py: the page's structure says so, or a click on the pay control got the REFUSAL) hands over by itself: the app starts
ONE checkout job on that page through `Workers.delegate(kind="checkout")`, with a brief built from what the app knows (the shop, the
cart lines and total as the page shows them, the order's terms, the owner's last request), and the run's tool result says so with the
job's id. The model is not asked to remember to delegate. Nothing here reads words: the page's structure and the app's own records
decide. One open checkout job per shop (Workers); the approvals stay where they are (checkout_review).
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from ..agent import ToolOutput
from .checkout import REFUSAL, Probe, read_page

# The tools whose result may land on a final checkout step: the page's own readings and the steps that move through it.
WATCHED = frozenset({"browser_open", "browser_read", "browser_click", "browser_type", "browser_click_at", "browser_drag"})
GUARDED = frozenset({"browser_click", "browser_type", "browser_click_at", "browser_drag"})   # these have the click guard (hold) as well
PAGE_CHARS = 1500
JOB_ID = re.compile(r"\bjob (w[0-9a-f]+)")


class CheckoutHandover:
    """`start(args)`: Workers.delegate called by the app. `open_job(host)`: the id of the open checkout job on a shop or None. `order()`: the
    open order of this tab (tools/order.py) or None. `request()`: the owner's last request text. `active()`: False in the specialist's
    own run (it pays itself)."""

    def __init__(self, session: Any, start: Callable[[dict[str, Any]], Awaitable[str]], open_job: Callable[[str], str | None], *,
                 order: Callable[[], Any] = lambda: None, request: Callable[[], str] = lambda: "",
                 active: Callable[[], bool] = lambda: True):
        self.session, self.start, self.open_job, self.order, self.request, self.active = session, start, open_job, order, request, active
        self._asked: dict[str, str] = {}      # shop -> the owner's request the last job on it was started for

    async def check(self, refused: bool = False) -> str:
        """The note to add to a run's result ("" = nothing to say). `refused`: the click guard just answered with REFUSAL."""
        if not self.active():
            return ""
        probe = await read_page(self.session)
        if probe is None or not (probe.is_checkout or refused):
            return ""
        host = (urlsplit(probe.url).hostname or "").removeprefix("www.")
        if not host:
            return ""
        if (job := self.open_job(host)) is not None:
            return f"[Checkout job {job} is already on this shop; it pays, not you. Tell the user it is underway.]"
        request = self.request()
        if host in self._asked and self._asked[host] == request:
            return ""                     # a job for this shop and this request was started and has ended: never a second order from a re-read
        got = await self.start({"kind": "checkout", "url": probe.url, "task": self.brief(probe, host, request),
                                "title": f"Pay at {host}"[:80]}, by_app=True)
        if not (m := JOB_ID.search(got)):
            return f"[The app tried to hand this checkout to a payment specialist and could not: {got}]"
        self._asked[host] = request
        return (f"[The app handed this checkout step over: checkout job {m.group(1)} is started on {probe.url} and will check the cart and "
                "pay only after the user approves. You cannot pay and need not do anything on this page: do not read the cart again or stop the job, "
                "it checks the cart itself and a difference comes back to you as a question. Tell the user it is underway.]")

    def brief(self, probe: Probe, host: str, request: str) -> str:
        lines = [f"Shop: {host}", f"Checkout page (reach it by clicking from the shop's home page; do not open this address): {probe.url}"]
        if probe.totals:
            lines.append("Total shown: " + "; ".join(probe.totals[:3]) + (f" (currency {probe.currency})" if probe.currency else ""))
        if probe.text.strip():
            lines.append("The page as the app read it (cart lines and total; check it against the cart yourself):\n" + probe.text.strip()[:PAGE_CHARS])
        if (o := self.order()) is not None:
            lines.append(f"The order's terms: {o.what or 'no description'} (on {o.site})")
            if o.profile:
                lines.append("Ship to the profile: " + str(o.profile.get("label") or o.profile.get("one_line") or ""))
            if o.grant:
                lines.append("The owner's standing permission for it: " + json.dumps(
                    {k: v for k, v in o.grant.items() if isinstance(v, (str, int, float, bool))}, ensure_ascii=False)[:600])
        if request.strip():
            lines.append("The owner's last request, in their words:\n" + request.strip()[:800])
        lines.append("Complete this one checkout: check that the cart is what was asked, fill what is missing, pay only through "
                     "checkout_review and checkout_submit; a difference goes back to the user as a question.")
        return "\n".join(lines)


def wrapped(tools: Iterable[Any], hand: CheckoutHandover) -> list[Any]:
    """The tools with the hand-over built into the page tools: a result that lands on a final checkout step, or a click the guard
    refused there, carries the note that the checkout job was started."""
    out: list[Any] = []
    for t in tools:
        if t.name not in WATCHED:
            out.append(t)
            continue
        out.append(dataclasses.replace(t, handler=_after(t.handler, hand, t.untrusted), hold=_held(t.hold, hand) if t.hold is not None and t.name in GUARDED else t.hold))
    return out


def _after(handler: Callable[[dict[str, Any]], Awaitable[Any]], hand: CheckoutHandover, untrusted: bool) -> Callable[[dict[str, Any]], Awaitable[Any]]:
    async def run(args: dict[str, Any]) -> Any:
        res = await handler(args)
        note = await hand.check()
        if not note:
            return res
        if isinstance(res, str):   # a page tool's text is fenced as the page's own: our note goes outside it, in the tail
            return ToolOutput(text=res, tail=note) if untrusted else f"{res}\n\n{note}"
        if hasattr(res, "tail"):
            res.tail = f"{res.tail}\n{note}".strip()
        return res
    return run


def _held(hold: Callable[[dict[str, Any]], Awaitable[str | None]], hand: CheckoutHandover) -> Callable[[dict[str, Any]], Awaitable[str | None]]:
    async def run(args: dict[str, Any]) -> str | None:
        said = await hold(args)
        if said != REFUSAL:
            return said
        note = await hand.check(refused=True)
        return f"{said}\n\n{note}" if note else said
    return run
