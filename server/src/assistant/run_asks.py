"""What an unattended run says and asks, for the owner to tell what it checks and why (approval cards in turn_engine.approve).
This is for runs the owner started (a schedule, a background task, a job). A run Kelo starts on its own (turn_engine.SELF_STARTED: a
correction check, a situation look, a checklist pass, an order check, the reply that tells a correction) never asks, announces or notifies:
a step that would need an OK is skipped and the owner hears only its result.

  * RunAsks       per run: the sites (rule kind, host) the owner allowed in this run are not asked again in it (a step whose kind can
                  never be remembered, a payment or a send, is never kept); and the run's one announcement, a chat line in the owner's
                  language before its first card: "<reason> 때문에 <what>을(를) 확인하려고 해요".
  * purpose_lines  the card's first line: "Why: <the run's own reason>" (its record, never page text).
  * purpose_body   the push text: "<title> · <what> · Why: <reason>".
"""

from __future__ import annotations

from typing import Any

from .activity import same_site
from .i18n import t

MAX_LINES = 4


class RunAsks:
    def __init__(self) -> None:
        self._allowed: set[tuple[str, str]] = set()
        self.announced = False

    def allowed_before(self, kind: str, host: str) -> bool:
        """The owner allowed this kind of step on this site (the host or a parent or subdomain of it: m.x.com is x.com) earlier in this run."""
        return bool(kind and host) and any(k == kind and same_site(h, host) for k, h in self._allowed)

    def allow(self, kind: str, host: str) -> None:
        if kind and host:
            self._allowed.add((kind, host))

    def announce(self, reason: str, what: str) -> str:
        """The run's one line before its first card ("" when it was said already, or there is no reason to give)."""
        if self.announced or not reason.strip():
            return ""
        self.announced = True
        return t("Because {reason}, I am going to check {what}.", reason=reason, what=what or "")


def purpose_lines(why: str, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The card's detail lines with the run's reason first."""
    if not why:
        return lines
    return [{"label": t("Why"), "value": why}, *lines][:MAX_LINES]


def purpose_body(why: str, title: str, what: str) -> str:
    """The push text of an unattended approval: the run's title, what is asked, and why."""
    if not why:
        return what
    return f"{title} · {what} · {t('Why')}: {why}"
