"""A sign-in verification page (two-step / two-factor sign-in, "approve on your phone", a code sent by text, mail or an app), told
apart from other pages by structure and words in many languages, never by a list of sites.

`verification_step(probe, signing_in=...)` reads what challenge.PROBE_JS read from the page:
- "code": the page wants a one-time code typed (one-time-code boxes by structure, or a short visible input next to verification words);
- "approve": the page waits for the user to confirm somewhere else (a push or a message to their phone, an app, a number to tap)
  and has nothing to type;
- "": not a verification page.
A page with a visible password box is a sign-in form, not this. Without code boxes the words must stand on a short page that belongs to
a sign-in (a sign-in the agent started on this tab, or a sign-in / verify address or title), so an article or a settings page that only
talks about two-step verification is not taken for one.

What happens then (BrowserSession.snapshot): the user is told plainly what to do (the browser card's status line, and the app's hand-over
card with the live view, `handover` with kind "verification"), the step waits with the hand-over's time limit, and goes on when the page
moves on. A code the site mailed is first looked for in the user's mailbox (find_login_code) when one is connected."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..i18n import NAMES, N_, current, t

# Words of a page that verifies a sign-in, in many languages (lower-cased text)
_VERIFY = re.compile(
    r"two[-\s]?(?:step|factor)|\b2[-\s]?(?:step|fa)\b|multi[-\s]?factor|\bmfa\b|verify (?:it'?s|that it'?s) (?:you|really you)|"
    r"confirm (?:it'?s|that it'?s) you|verify your (?:identity|sign[-\s]?in|login|account|device)|"
    r"(?:approve|confirm|allow) (?:the |this |your )?(?:sign[-\s]?in|log[-\s]?in|login)(?: request)?|check your (?:phone|device|mobile)|"
    r"we(?:'ve| have| just)? sent (?:a |an |you a |you an )?(?:code|notification|push|request|text|sms|message|prompt)|"
    r"(?:enter|type) (?:the |your )?(?:\d[-\s]?digit )?(?:code|verification code|security code|one[-\s]?time)|"
    r"verification (?:code|required|request)|security code|one[-\s]?time (?:code|password|passcode|pin)|authenticator app|"
    r"tap (?:yes|approve|allow)|sign[-\s]?in request|login request|"
    r"2\s?단계\s?인증|2\s?차\s?인증|추가\s?인증|본인\s?확인|인증\s?요청|인증\s?번호|인증\s?코드|보안\s?코드|일회용\s?(?:비밀번호|코드)|"
    r"(?:휴대폰|휴대전화|앱|기기)(?:에서|으로|로)\s?(?:확인|승인|인증)|알림을\s?(?:보냈|확인)|로그인\s?(?:요청|승인)|"
    r"2\s?段階認証|二段階認証|[2二]\s?要素認証|認証コード|確認コード|ワンタイムパスワード|本人確認|ログインを承認|"
    r"两步验证|兩步驟驗證|兩步驗證|双重验证|雙重驗證|验证码|驗證碼|安全验证|安全驗證|确认登录|確認登入|"
    r"zwei[-\s]?(?:faktor|schritt)|bestätigungscode|sicherheitscode|anmeldung bestätigen|"
    r"(?:validation|vérification|authentification) en deux (?:étapes|facteurs)|code de (?:vérification|sécurité|confirmation)|"
    r"verificación en dos pasos|autenticación de dos factores|código de (?:verificación|seguridad|confirmación)|"
    r"verificação em (?:duas|2) etapas|autenticação de dois fatores|código de (?:verificação|segurança)|"
    r"verifica in due passaggi|autenticazione a due fattori|codice di (?:verifica|sicurezza)|"
    r"tweestapsverificatie|verificatiecode|weryfikacja dwuetapowa|kod weryfikacyjny|"
    r"двухэтапн|двухфакторн|код подтверждения|подтвердите вход|двоетапн|код підтвердження|"
    r"iki adımlı doğrulama|doğrulama kodu|xác minh 2 bước|mã xác minh|ยืนยันตัวตน|รหัสยืนยัน|รหัส otp|"
    r"verifikasi 2 langkah|kode verifikasi|التحقق بخطوتين|رمز التحقق|सत्यापन कोड|दो-चरणीय", re.I)
# A sign-in or verification address or title (path and title only, never the host)
_SIGN_IN_PAGE = re.compile(r"log[-_]?in|sign[-_]?in|signin|auth|verif|2fa|mfa|otp|challenge|two[-_]?step|second[-_]?factor|"
                           r"account|session|security|로그인|인증|ログイン|認証|登录|登入|验证|驗證", re.I)
MAX_TEXT = 6_000   # a verification page is short; longer pages are articles or settings
SIGN_IN_SECONDS = 20 * 60.0   # a sign-in the agent started on a tab counts this long

# The owner's decision (2026-11-10, no setting): on the owner's own account, an offer to keep this browser signed in or not to ask
# for verification again is accepted, whatever its wording or language. Same intent as content/prompts/system.md and browser_login.
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


def _path_and_title(probe: dict[str, Any]) -> str:
    url = str(probe.get("url") or "")
    return re.sub(r"^[a-z]+://[^/]+", "", url, flags=re.I) + " " + str(probe.get("title") or "")


def verification_step(probe: Any, *, signing_in: bool = False) -> Verification | None:
    """The sign-in verification this page asks for, or None (see the module's notes). `signing_in`: the agent started a sign-in on
    this tab a short while ago."""
    if not isinstance(probe, dict) or probe.get("secret_box"):
        return None
    text = str(probe.get("text") or "")
    hit = _VERIFY.search(text) or _VERIFY.search(str(probe.get("title") or ""))
    boxes = int(probe.get("otp") or 0)
    if boxes and (hit or signing_in or _SIGN_IN_PAGE.search(_path_and_title(probe))):
        return Verification("code", hit.group(0) if hit else "")
    if not hit or int(probe.get("text_len") or len(text)) > MAX_TEXT:
        return None
    if not (signing_in or _SIGN_IN_PAGE.search(_path_and_title(probe))):
        return None
    return Verification("code" if int(probe.get("short_inputs") or 0) else "approve", hit.group(0))


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
