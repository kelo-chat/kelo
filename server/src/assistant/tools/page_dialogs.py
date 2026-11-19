"""A page's own pop-ups (JavaScript alert, confirm, prompt, and the question a page asks before it is left): how Kelo answers them, what
the model is told about them and what the owner's live view shows (docs/browser.md "Page pop-ups"). Owner report 2026-11-19: a job's
click got a question that was answered Cancel at once, the model only read "dismissed" and clicked the same element five times, and the
owner watched a page that seemed to do nothing, since a native pop-up is never in the picture or on the X screen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..agent.fence import data
from ..i18n import t

KEEP = 5                # pop-ups of a tab kept for its next page reading (the newest ones)
SHOW_SECONDS = 60.0     # how long the live view shows a tab's newest pop-up while the tab stays on the address it came from

DIALOG_ONLY_NOTE = ("[Nothing on the page changed: same address, same page content and form values, no new window. The click only "
                    "brought up the page's own question, which Kelo answered Cancel (see the note on that pop-up in this reading), so "
                    "the click did nothing. The same click again brings the same question and the same answer: do not click it again; "
                    "do what that note says.]")


@dataclass
class PageDialog:
    kind: str       # alert | confirm | prompt | beforeunload, as the browser names it ("" when it did not say)
    message: str    # the page's own words (at most 300 characters)
    answer: str     # "ok" | "cancel" | "closed" (an alert: it had only OK)
    why: str        # with "ok": "user" | "purchase" | "order"; else ""
    url: str        # the address of the page that showed it
    at: float       # wall time (time.time())
    count: int = 1  # the same pop-up (address, kind, words, answer) in a row, this one included


def answer_for(kind: str, *, by_user: bool, in_purchase: bool, in_order: bool) -> tuple[str, str]:
    """How Kelo answers a page's own pop-up: (answer, why). OK only for the user: they hold the tab (the pop-up came from their own
    tap), a purchase or cancel window they allowed on that site is open, or it is a step of the order they asked for (Orders.dialog_ok).
    Otherwise an alert (it has only OK, closing it chooses nothing) is closed and a question (confirm, prompt, a page asking before it
    is left, a kind the browser did not name) is answered Cancel: its words come from the site, only words would tell a harmless
    question from one that deletes, pays, sends or leaves, Kelo judges nothing by word lists, and a wrong yes cannot be taken back."""
    if by_user:
        return "ok", "user"
    if in_purchase:
        return "ok", "purchase"
    if in_order:
        return "ok", "order"
    return ("closed", "") if kind == "alert" else ("cancel", "")


class TabDialogs:
    """One tab's pop-ups: the ones its next page reading tells the model, the newest one (the live view), and running counts that a
    step compares before and after (trimming the unread ones never hides a pop-up from that comparison)."""

    def __init__(self) -> None:
        self.unread: list[PageDialog] = []
        self.last: PageDialog | None = None
        self.seen = 0
        self.cancelled = 0

    def add(self, kind: str, message: str, answer: str, why: str, url: str, at: float) -> PageDialog:
        prev = self.last
        same = prev is not None and (prev.url, prev.kind, prev.message, prev.answer) == (url, kind, message, answer)
        d = PageDialog(kind, message, answer, why, url, at, prev.count + 1 if same and prev is not None else 1)
        self.unread.append(d)
        del self.unread[:-KEEP]
        self.last = d
        self.seen += 1
        self.cancelled += answer == "cancel"
        return d

    def mark(self) -> tuple[int, int]:
        """(pop-ups so far, of them answered Cancel): taken before a step, given to `only_cancelled` after it."""
        return self.seen, self.cancelled

    def only_cancelled(self, mark: tuple[int, int]) -> bool | None:
        """Since `mark`: None when no pop-up came; True when every one was a question answered Cancel (through them the step did
        nothing); False when one got OK or was a message that was closed (the page may have acted)."""
        new = self.seen - mark[0]
        if new <= 0:
            return None
        return self.cancelled - mark[1] == new

    def take(self) -> list[PageDialog]:
        """The pop-ups not told yet, oldest first: each is told once."""
        out, self.unread = self.unread, []
        return out

    def shown(self, url: str, now: float) -> PageDialog | None:
        """The newest pop-up while the tab still shows the address it came from, for SHOW_SECONDS after it (the live view)."""
        d = self.last
        return d if d is not None and d.url == url and 0 <= now - d.at <= SHOW_SECONDS else None


def dialog_note(d: PageDialog, *, site: str, login: str, job: bool) -> str:
    """What the model is told about one pop-up in its next page reading: the kind and the page's full words, how Kelo answered and what
    that means for the step, a repeat of the same pop-up, and the general next step (never a list of words: the model reads the
    question). `site`: the registrable site it came from; `login`: the name of a saved login for it ("" = none); `job`: a background job."""
    what = f" ({d.kind})" if d.kind else ""
    again = f" The page has shown this same pop-up {d.count} times on this page now." if d.count > 1 else ""
    if d.answer == "ok":
        how = ("accepted (the user was controlling the browser)" if d.why == "user" else "accepted (a step of the order the user asked for)"
               if d.why == "order" else "accepted (inside the purchase the user allowed)")
        return f"[The page showed its own pop-up{what}: {data(d.message)} Kelo answered OK: {how}.{again}]"
    if d.answer == "closed":
        return (f"[The page showed a message in its own pop-up{what}: {data(d.message)} It had only OK, so it was closed and nothing was "
                f"chosen.{again}]")
    site = site or "this site"
    if login:
        sign_in = (f"a saved login exists for {site}: open {site}'s own sign-in page (its sign-in link or address; this pop-up cannot "
                   f"take you there) and call browser_login(name={login!r})")
    elif job:
        sign_in = (f"no login is saved for {site} and nobody can sign in during this job: report needs_input and say that Kelo's browser "
                   f"needs to be signed in to {site} once")
    else:
        sign_in = (f"no login is saved for {site}: ask the user to save one (request_login) or to sign in once themselves by taking over "
                   "the browser")
    ask = ("report needs_input with the page's question in its own words" if job
           else "ask the user, with the page's question in its own words")
    return (f"[The page asked a question in its own pop-up{what}: {data(d.message)} Kelo answered Cancel for you, because only the user "
            "says yes to a page's question (Kelo may inside a purchase or an order they allowed): the step that opened it was cancelled "
            f"and did nothing.{again} Doing the same step again brings the same question and the same answer, so do not repeat it. Read "
            f"the question. When it asks you to sign in first, sign in the usual way: {sign_in}. When it asks for something else, do "
            f"that another way on the page if there is one; otherwise {ask}: the user can answer it themselves by taking over the "
            "browser (a pop-up that comes from their own tap is answered OK).]")


def owner_line(d: PageDialog) -> str:
    """The line the live view shows the owner about one pop-up, in their language, with the page's own words."""
    if d.answer == "cancel":
        return t("The page asked: “{message}” Kelo answered Cancel.", message=d.message)
    if d.answer == "ok":
        return t("The page asked: “{message}” It was answered OK.", message=d.message)
    return t("The page said: “{message}”", message=d.message)


def live_view(d: PageDialog) -> dict[str, Any]:
    """The live status's `dialog` (server.py LIVE VIEW API): what the page showed, how Kelo answered, and the line to show."""
    return {"kind": d.kind, "message": d.message, "answer": d.answer, "count": d.count, "at": round(d.at, 3), "text": owner_line(d)}
