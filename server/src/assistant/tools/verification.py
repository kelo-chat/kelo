"""A sign-in verification page (two-step / two-factor sign-in, "approve on your phone", a code sent by text, mail or an app), told
apart from other pages by STRUCTURE and, when that is silent, by the page judge (page_judge.py), never by words or a list of sites
(docs/no-word-lists.md, hc-pagefields).

`verification_step(probe, signing_in=...)` reads what challenge.PROBE_JS read from the page:
- "code": one-time-code boxes (autocomplete one-time-code, or a row of one-character boxes) stand on the page, or the agent's own
  sign-in left a short page with a short input and the judge did not say otherwise;
- "approve": the agent's own sign-in left a short page with nothing to type and the judge did not say otherwise: the page may wait for
  the user to confirm somewhere else;
- "": not a verification page (a visible password box means a sign-in form; a page the judge read and called none).
Fail closed: with no judge (or no answer) a short page right after a sign-in the agent started is taken for a verification step, so the
user is asked and the agent never says "signed in" on a guess. `probe["judged_step"]` is the judge's answer (BrowserSession sets it).

What happens then (BrowserSession.snapshot): the user is told plainly what to do (the browser card's status line, and the app's hand-over
card with the live view, `handover` with kind "verification"), the step waits with the hand-over's time limit, and goes on when the page
moves on. A code the site mailed is first looked for in the user's mailbox (find_login_code) when one is connected."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..i18n import NAMES, N_, current, t

MAX_TEXT = 1_500   # a verification page is short; longer pages are articles, settings or a signed-in page
SIGN_IN_SECONDS = 20 * 60.0   # a sign-in the agent started on a tab counts this long

# The owner's decision (2026-11-10, no setting): on the owner's own account, an offer to keep this browser signed in or not to ask
# for verification again is accepted, whatever its wording or language. Same intent as server/content/prompts/system.md and browser_login.
REMEMBER = ("On the user's own account only: if the page offers to keep this browser or device signed in or not to verify it again "
            "(any wording: a checkbox, a trust button, a step after verifying), choose it; never saving the password in the browser, "
            "marketing or cookie consent, recovery phone or email, or security settings.")

def in_language() -> str:
    """'in Korean, the user's language': these notes are English, and a model mirrors them unless told (2026-11-10, a Kakao sign-in's
    chat went English)."""
    lang = current()
    return f"in {NAMES.get(lang, NAMES.get((lang or '').split('-')[0], 'their own language'))}, the user's language"


NO_CODE = ("There is NO code for this step: the user approves it on their phone or in the site's app. Do NOT search mail or SMS "
           "for a code and do not ask for one.")

APPROVE_SAY = N_("Approve the sign-in on your phone")
CODE_SAY = N_("Enter the sign-in code on the page")


@dataclass(slots=True)
class Verification:
    step: str        # "code" | "approve"
    words: str       # the words that showed it (for the agent's note; page text, fenced by the caller)

    @property
    def say(self) -> str:
        """What the user is asked to do, in their language."""
        return t(APPROVE_SAY if self.step == "approve" else CODE_SAY)


def unconfirmed(probe: Any, *, signing_in: bool = False) -> bool:
    """True when `verification_step` would rest on the page being short right after a sign-in alone (no code box, no judge answer yet):
    the case the page judge is asked about."""
    if not isinstance(probe, dict) or probe.get("secret_box") or int(probe.get("otp") or 0) or not signing_in:
        return False
    return probe.get("judged_step") is None and int(probe.get("text_len") or len(str(probe.get("text") or ""))) <= MAX_TEXT


def verification_step(probe: Any, *, signing_in: bool = False) -> Verification | None:
    """The sign-in verification this page asks for, or None (see the module's notes). `signing_in`: the agent started a sign-in on
    this tab a short while ago."""
    if not isinstance(probe, dict) or probe.get("secret_box"):
        return None
    judged = probe.get("judged_step")
    if judged == "none":
        return None
    if judged in ("code", "approve"):
        return Verification(judged, "")
    if int(probe.get("otp") or 0):
        return Verification("code", "")
    if not signing_in or int(probe.get("text_len") or len(str(probe.get("text") or ""))) > MAX_TEXT:
        return None
    return Verification("code" if int(probe.get("short_inputs") or 0) else "approve", "")


def waiting_note(v: Verification, host: str) -> str:
    """The agent's note when nobody can be asked from here (no app turn): tell the user plainly and wait for them."""
    site = host or "the site"
    what = ("approve the sign-in on their phone (the site sent them a request or notification)" if v.step == "approve"
            else "type the code the site sent them into the page")
    where = ("on their phone or in the app the request went to (no live browser needed)" if v.step == "approve"
             else "in the live browser (Browser, then Take control)")
    return (f"[Sign-in verification on {site}: the page waits for the user to {what}. The sign-in is NOT finished. Do not retry or "
            f"sign in again, and click nothing else on it but the option below. Tell the user plainly now, {in_language()}, exactly "
            f"what to do ({where}) and to tell you when it is done; then read the page once with browser_read and go on. "
            f"{NO_CODE + ' ' if v.step == 'approve' else ''}{REMEMBER}]")


def options_note(v: Verification, host: str, options: str) -> str:
    """The user was just asked (card and notification) and the verification page shows unticked options: the agent chooses first,
    in one step, because the page moves on as soon as the user approves. `options`: each option's selector and words (fenced)."""
    site = host or "the site"
    what = "approve the sign-in on their phone or in the site's app" if v.step == "approve" else "enter the sign-in code on the page"
    return (f"[Sign-in verification on {site}: the user has just been asked (card and notification) to {what}. FIRST, before "
            f"anything else, this page has unticked options:\n{options}\nIf one's effect is that this browser or device stays signed "
            "in or is not asked to verify again (any wording, any language), tick it NOW with browser_click on its selector: the user "
            "may approve any moment. If none fits, call browser_read now. Either way the step then waits for the user. Click nothing "
            "else; never saving the password, marketing or cookie consent, recovery contacts or security settings. Read the page below "
            f"and follow what it says; tell the user exactly what the page asks of them, {in_language()}."
            f"{' ' + NO_CODE if v.step == 'approve' else ''}]")


def verification_note(outcome: str, v: Verification, host: str, minutes: int = 0) -> str:
    """What the agent is told after the verification was handed to the user (the browser step waited for it)."""
    site = host or "the site"
    what = "approve the sign-in on their phone" if v.step == "approve" else "enter the sign-in code"
    if outcome in ("solved", "done"):
        return (f"[The sign-in verification on {site} is done (the page is past signing in). The page below is how it looks now: go on "
                f"from here. {REMEMBER}]")
    if outcome == "progressed":
        return (f"[The user did the verification step on {site}, but the sign-in is NOT finished yet: the page went on to another step "
                "(below). If it needs no one (a continue button, or the option below), do it yourself, then check the page shows you "
                "are signed in; a step only the user can do is handed to them. Do not say they are signed in before a page shows it; "
                f"anything you tell the user, tell it {in_language()}. {NO_CODE + ' ' if v.step == 'approve' else ''}{REMEMBER}]")
    stop = ("Do not retry the sign-in, click around or open the site again for it in this task. Tell the user plainly, "
            f"{in_language()}, that {site} is waiting for them to {what}, and that you will carry on when they tell you it is done."
            + (" " + NO_CODE if v.step == "approve" else ""))
    if outcome == "pending":
        return f"[The sign-in on {site} is NOT finished: the page still shows a sign-in or verification step. {stop}]"
    if outcome == "expired":
        return f"[Nobody completed the sign-in verification on {site} within {max(1, minutes)} minutes. {stop}]"
    if outcome in ("skipped", "repeat"):
        return f"[The user chose not to complete the sign-in verification on {site} now. {stop}]"
    if outcome == "closed":
        return f"[The tab with the sign-in verification on {site} was closed. {stop}]"
    return f"[The sign-in verification on {site} was not completed (the task is stopping). {stop}]"
