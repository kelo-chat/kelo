"""Whether a tab has really left a sign-in flow (2026-11-10): a sign-in verification handed to the user ends as "solved" only on
positive evidence that the page is past signing in, never just because its address changed.

A Kakao-style sign-in moved from one verification page to another (a second factor, then "check your phone"), and the hand-over
ended at the first move: the app said "signed in" while the site still waited. The rule here is general (no site names): a page that
still shows a password box, a one-time-code box, verification words, or a sign-in / verification address or title is still signing
in. The reading is the browser tools' own page probe (tools/challenge.PROBE_JS).
"""

from __future__ import annotations

import re
from typing import Any

# a sign-in or verification step by its address path or title (never the host): login, sign-in, auth, verify, 2FA, OTP, ...
_SIGN_IN = re.compile(r"log[-_]?in|sign[-_]?in|signin|oauth|/auth|authori[sz]e|verif|2fa|mfa|otp|two[-_]?(?:step|factor)|"
                      r"challenge|password|로그인|인증|ログイン|認証|登录|登入|验证|驗證", re.I)
CLEAN_READINGS = 2   # past signing in on this many readings in a row (a page that is still loading is not trusted once)


def still_signing_in(probe: Any) -> bool:
    """True while the page reading shows any sign of a sign-in or verification step; also for a reading that failed."""
    if not isinstance(probe, dict) or not probe.get("kelo_check"):
        return True
    if probe.get("secret_box") or int(probe.get("otp") or 0) > 0:
        return True
    from .tools.verification import verification_step
    if verification_step(probe, signing_in=True) is not None:
        return True
    url = str(probe.get("url") or "")
    path = re.sub(r"^[a-z]+://[^/]+", "", url, flags=re.I)
    return bool(_SIGN_IN.search(path + " " + str(probe.get("title") or "")))


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
