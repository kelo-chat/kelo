"""Signing in on the device (docs/android-device.md, "android_login"): where the sign-in fields are on a screen, which button submits,
and what the screen says after a submit. Pure: screens in, answers out; the typing is android_login's (tools/vault_tools.py).

A saved login may be typed only where its hosts name the app (`app:<package>`) or the service whose sign-in screen the app shows
(APP_SIGNIN_HOSTS: Google's account pages inside Google Play services and the Play Store)."""

from __future__ import annotations

import dataclasses
import re

from ..tools.browser import _CODE, _FAILED
from .screen import Item, Screen

# The host form of a login for an app on the device: "app:" and the package name (lower case).
APP_HOST = re.compile(r"^app:[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
# Packages that show a service's own sign-in screens: a login saved for those web hosts may be typed there.
APP_SIGNIN_HOSTS: dict[str, tuple[str, ...]] = {
    "com.google.android.gms": ("accounts.google.com", "google.com"),
    "com.android.vending": ("accounts.google.com", "google.com"),
}

SUBMIT_WORDS = ("next", "sign in", "log in", "login", "continue", "다음", "로그인", "계속")
_FAILED_MORE = ("wrong password", "couldn't find your google account", "couldn't sign you in", "enter a valid email",
                "잘못된 비밀번호", "비밀번호가 잘못", "계정을 찾을 수 없")
_APPROVE = ("check your phone", "tap yes", "open the gmail app", "approve the sign-in", "approve this sign-in", "notification to your phone", "휴대전화를 확인",
            "휴대폰을 확인", "알림을 보냈")
_CODE_MORE = ("2-step verification", "enter the 6-digit", "6-digit code", "get a verification code", "2단계 인증", "인증 코드")
FILLED = "(filled)"


def find_fields(screen: Screen) -> tuple[Item | None, Item | None]:
    """(user name field, password field) of a screen. The password field is the visible edit node with password="true". The user name
    field is the nearest edit node above it; on a screen with no password field (Google asks for the e-mail first) it is the focused
    edit node, else the first one. Either may be None."""
    edits = [i for i in screen.items if i.kind == "edit"]
    password = next((i for i in edits if i.password), None)
    others = [i for i in edits if not i.password]
    if password is not None:
        above = [i for i in others if i.bounds[1] < password.bounds[1]]
        return (max(above, key=lambda i: i.bounds[3]) if above else None), password
    if not others:
        return None, None
    return next((i for i in others if i.focused), others[0]), None


def find_submit(screen: Screen) -> Item | None:
    """The button that sends the sign-in on: one named Next / Sign in (or the like, in the owner's language). The lowest such button
    when there are several (the page's own heading is never a button)."""
    hits = [i for i in screen.items if i.kind == "button" and " ".join(i.words.casefold().split()) in SUBMIT_WORDS]
    return max(hits, key=lambda i: i.bounds[1], default=None)


def fingerprint(screen: Screen) -> tuple:
    """What a screen is, without what was typed in it: the app and the items' kind, words and places (a field's text is left out)."""
    return (screen.app, tuple((i.kind, "" if i.kind == "edit" else i.words, i.bounds) for i in screen.items))


def texts(screen: Screen) -> str:
    """The screen's words, lower case, for reading what it says. A field contributes its hint or name, never what is typed in it."""
    return " ".join((i.hint or i.desc) if i.kind == "edit" else i.words for i in screen.items).casefold()


def masked(screen: Screen, secrets: tuple[str, ...] = ()) -> str:
    """The screen as the model reads it, with what is typed in the fields replaced by "(filled)" and any of `secrets` (3+ characters)
    removed from every line."""
    items = [dataclasses.replace(i, text=FILLED) if i.kind == "edit" and i.text and i.text not in (i.hint, i.desc) and not i.password else i
             for i in screen.items]
    out = dataclasses.replace(screen, items=items).render()
    for secret in secrets:
        if len(secret) >= 3:
            out = out.replace(secret, "[hidden]")
    return out


class SignInReader:
    """Reads the screen after a sign-in was submitted: (outcome, detail), outcome one of success | failed | code_needed | unknown.
    A reading, not a proof: when nothing is clear it says unknown."""

    def read(self, before: Screen, after: Screen) -> tuple[str, str]:
        said = texts(after)
        password = find_fields(after)[1] is not None
        if not password and any(w in said for w in (*_CODE, *_CODE_MORE, *_APPROVE)):
            return "code_needed", ("the app accepted the password and waits for the user to approve the sign-in on their phone"
                                   if self.step(after) == "approve" else "the app accepted the password and asks for a verification code")
        failed = any(w in said for w in (*_FAILED, *_FAILED_MORE))
        if password and failed:
            return "failed", "the screen still shows the password field and says the user name or password is wrong"
        if not password and after.items and after.app and after.app != before.app and not failed:
            return "success", "the sign-in screen was left"
        return "unknown", "could not tell whether the app accepted the login"

    @staticmethod
    def step(after: Screen) -> str:
        """The verification step a screen asks for: "approve" (something to approve on the phone), "code" (a code to type), or ""."""
        said = texts(after)
        if any(w in said for w in _APPROVE):
            return "approve"
        return "code" if any(w in said for w in (*_CODE, *_CODE_MORE)) else ""

    @staticmethod
    def left_sign_in(now: Screen, signin_app: str) -> bool:
        """Whether a screen read some time after the submit shows the sign-in is over: not the sign-in's own activity, and no password
        field (a held login is saved on this: vault_tools.confirm_later)."""
        return bool(now.items and now.app and now.app != signin_app and find_fields(now)[1] is None)
