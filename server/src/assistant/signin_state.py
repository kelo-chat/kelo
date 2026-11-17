"""Whether a tab has really left a sign-in flow (2026-11-10): a sign-in verification handed to the user ends as "solved" only on
positive evidence that the page is past signing in, never just because its address changed.

A Kakao-style sign-in moved from one verification page to another (a second factor, then "check your phone"), and the hand-over
ended at the first move: the app said "signed in" while the site still waited. The rule here is general (no site names): a page that
still shows a password box, a one-time-code box, verification words, or a sign-in / verification address or title is still signing
in. The reading is the browser tools' own page probe (tools/challenge.PROBE_JS).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

CLEAN_READINGS = 2   # past signing in on this many readings in a row (a page that is still loading is not trusted once)
OAUTH_PARAMS = ("client_id", "response_type")   # the parameters an OAuth authorization request carries (RFC 6749), never words of a language


def sign_in_address(url: str) -> bool:
    """The address is a sign-in step by its STRUCTURE (docs/no-word-lists.md): an OAuth authorization request (client_id and
    response_type), or a query that carries a whole web address back (the page to return to after signing in). Path and title words
    decide nothing. A page that asked for a sign-in instead of the page that was asked for (a watcher's fetch redirected) also shows a
    password box: watchers.py reads that from the body."""
    parts = urlsplit(str(url or ""))
    pairs = parse_qsl(parts.query + "&" + parts.fragment, keep_blank_values=True)
    keys = {k.lower() for k, _ in pairs}
    if all(k in keys for k in OAUTH_PARAMS):
        return True
    return any(unquote(v).lower().startswith(("http://", "https://")) for _k, v in pairs)


def under_sign_in(url: str, origin: str) -> bool:
    """The address is on the site and in the first path folder of the page the agent's sign-in started from ("/login" -> "/login/verify"):
    the flow's own pages. Structure only; a sign-in at the site's root has no folder, so nothing is under it."""
    a, b = urlsplit(str(url or "")), urlsplit(str(origin or ""))
    folder = [p for p in b.path.split("/") if p][:1]
    return bool(folder) and a.hostname == b.hostname and [p for p in a.path.split("/") if p][:1] == folder


def still_signing_in(probe: Any, origin: str = "") -> bool:
    """True while the page reading shows any sign of a sign-in or verification step; also for a reading that failed. `origin`: the address
    the agent's sign-in started from (its own folder of pages counts as the flow)."""
    if not isinstance(probe, dict) or not probe.get("kelo_check"):
        return True
    if probe.get("secret_box") or int(probe.get("otp") or 0) > 0:
        return True
    from .tools.verification import verification_step
    if verification_step(probe, signing_in=True) is not None:
        return True
    return sign_in_address(str(probe.get("url") or "")) or under_sign_in(str(probe.get("url") or ""), origin)


def login_verified(store: Any, site: str) -> list[str]:
    """A sign-in verification handed to the user ended solved (signed-in evidence): every saved login of that site whose last
    sign-in stopped at a verification (last_result code_needed) gets its result as ok, and the vault log says so. The login's
    fill-in card then reads sign_in "signed_in" (SecretRequests.login_checked). Returns the logins updated."""
    from .tools.sites import same_site
    done = []
    for c in store.credentials():
        if c.get("kind") != "login" or c.get("last_result") != "code_needed":
            continue
        if not any(same_site(h, site) for h in c.get("hosts") or []):
            continue
        store.set_credential_result(c["name"], "ok", "signed in after the verification")
        store.add_vault_log(c["name"], "used", f"signed in on {site} after the verification step")
        done.append(c["name"])
    return done
