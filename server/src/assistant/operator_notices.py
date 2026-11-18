"""The words of the operator's notifications, in one place (server.py, testers.py, beta.py and operator_site.py use it).

`render(kind, data, tz=..., lang=...)` turns one event into (notification type, title, body). The msgids are the English words the
server has always sent, so the translations already in the locales apply without change. `KELO_*`, `REPORT_*` are the constants
server.py's Kelo address and report notices use (moved here unchanged)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import i18n
from .i18n import N_
from .beta import names as product_names
from .operator_events import EVENT_KINDS
from .schedules import zone as sched_zone

KELO_BUSY_TITLE = N_("Kelo addresses paused: too many new claims")
KELO_FULL_TITLE = N_("Kelo addresses are all taken ({used}/{max})")
KELO_FULL_BODY = N_("New copies are refused now. Move the Cloudflare zone to a bigger plan or raise the address limit.")
KELO_NEAR_TITLE = N_("Kelo addresses are almost all taken ({used}/{max})")
KELO_USED_TITLE = N_("Kelo addresses: {percent}% used ({used}/{max})")
KELO_NEAR_BODY = N_("New copies will be refused at {max}. Move the Cloudflare zone to a bigger plan or raise the address limit soon.")
KELO_BUSY_BODY = N_("{count} new addresses are waiting for their first connection. New claims are refused until fewer wait, at the latest until {time}.")
REPORT_TITLE = N_("New report: {reason}")
REPORT_BODY = N_("Someone reported a reply. Read it in Operator settings → Reports.")
REPORT_REASONS = {"harmful": N_("harmful or offensive"), "inaccurate": N_("not accurate"), "privacy": N_("private information"),
                  "other": N_("other")}
TESTER_TITLE = N_("New tester sign-up: {email}")
TESTER_BODY = N_("Phone: {model} · {count} signed up so far")
BETA_TITLE = N_("Google sign-in beta request")

Rendered = tuple[str, str, str]


def render(kind: str, data: dict[str, Any], *, tz: str = "UTC", lang: str | None = None) -> Rendered | None:
    """(notification type, title, body) for one operator event, in `lang` (the main copy's owner language by default); None for a
    kind this server does not know (the poller skips it)."""
    if kind not in EVENT_KINDS:
        return None
    if kind == "tester_signup":
        return ("beta_request", i18n.t(TESTER_TITLE, lang, email=data.get("email", "")),
                i18n.t(TESTER_BODY, lang, model=data.get("model") or "-", count=data.get("count", 0)))
    if kind == "beta_request":
        products = data.get("products") or []
        return ("beta_request", i18n.t(BETA_TITLE, lang), f"{data.get('email', '')} · {product_names(products)}")
    if kind == "report":
        reason = data.get("reason") or ""
        return ("operator_report", i18n.t(REPORT_TITLE, lang, reason=i18n.t(REPORT_REASONS.get(reason, REPORT_REASONS["other"]), lang)),
                i18n.t(REPORT_BODY, lang))
    if kind == "names_busy":
        until = datetime.fromtimestamp(float(data.get("until", 0)), _zone(tz)).strftime("%H:%M")
        return ("operator_busy", i18n.t(KELO_BUSY_TITLE, lang),
                i18n.t(KELO_BUSY_BODY, lang, count=data.get("count", 0), time=until))
    # names_pool: the same three words as before ("near50", "near70", "near", "full")
    used, cap, pool = data.get("used", 0), data.get("cap", 0), data.get("kind", "")
    if pool == "full":
        title, body = i18n.t(KELO_FULL_TITLE, lang, used=used, max=cap), i18n.t(KELO_FULL_BODY, lang, max=cap)
    elif pool == "near":
        title, body = i18n.t(KELO_NEAR_TITLE, lang, used=used, max=cap), i18n.t(KELO_NEAR_BODY, lang, max=cap)
    else:
        title = i18n.t(KELO_USED_TITLE, lang, percent=pool.removeprefix("near"), used=used, max=cap)
        body = i18n.t(KELO_NEAR_BODY, lang, max=cap)
    return ("operator_pool", title, body)


def _zone(tz: str):
    return sched_zone(tz)
