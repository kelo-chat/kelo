"""Signing in on the device (docs/android-device.md, "android_login"): where the sign-in fields are on a screen and what the
screen looks like after a submit (by structure: no button label or message is read). Pure: screens in, answers out; the typing is android_login's (tools/vault_tools.py).

A saved login may be typed only where its hosts name the app (`app:<package>`) or the service whose sign-in screen the app shows
(APP_SIGNIN_HOSTS: Google's account pages inside Google Play services and the Play Store)."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from .screen import Item, Screen

# The host form of a login for an app on the device: "app:" and the package name (lower case).
APP_HOST = re.compile(r"^app:[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
# Packages that show a service's own sign-in screens: a login saved for those web hosts may be typed there.
APP_SIGNIN_HOSTS: dict[str, tuple[str, ...]] = {
    "com.google.android.gms": ("accounts.google.com", "google.com"),
    "com.android.vending": ("accounts.google.com", "google.com"),
}

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


MAX_SUBMIT_CANDIDATES = 4


def submit_candidates(screen: Screen) -> list[Item]:
    """The controls that may send a sign-in on, by structure: clickable buttons that come after the lowest input field (below it), the
    nearest first. No label is read."""
    edits = [i for i in screen.items if i.kind == "edit"]
    if not edits:
        return []
    password = next((i for i in edits if i.password), None)
    floor = (password or max(edits, key=lambda i: i.bounds[3])).bounds[3]
    below = [i for i in screen.items if i.kind == "button" and i.clickable and i.bounds[1] >= floor]
    return sorted(below, key=lambda i: (i.bounds[1] - floor, i.bounds[0]))[:MAX_SUBMIT_CANDIDATES]


async def choose_submit(screen: Screen, judge: Any = None) -> Item | None:
    """The control that sends the sign-in on. One candidate by structure is it. Several: the step judge (step_judge.StepJudge, the main
    model) is asked what each does, nearest first, and the first one that signs nothing up, deletes nothing, sends nothing and changes no
    account security (a plain step) is it. No judge, or none plain: None (Enter alone)."""
    found = submit_candidates(screen)
    if len(found) <= 1:
        return found[0] if found else None
    if judge is None:
        return None
    nodes = [{"class": i.cls, "text": i.text[:60], "desc": i.desc[:60]} for i in screen.items[:60]]
    for item in found:
        verdict = await judge.phone(screen.package, screen.activity, {"class": item.cls, "text": item.text[:80], "desc": item.desc[:80],
                                                                       "hint": item.hint[:60]}, nodes)
        if verdict is not None and not verdict.kind:
            return item
    return None


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
    """Reads the screen after a sign-in was submitted, by structure only (no word of any language): (outcome, detail), outcome one of
    success | code_needed | unknown. The password field still shown, or nothing changed, says nothing certain, so it is unknown (the
    caller looks again until its wait is over). A reading, not a proof."""

    def read(self, before: Screen, after: Screen) -> tuple[str, str]:
        password = find_fields(after)[1] is not None
        if password or not after.items:
            return "unknown", "could not tell whether the app accepted the login"
        if after.app and after.app != before.app:
            return "success", "the sign-in screen was left"
        if self.step(after) == "code":   # no password field any more, but an input of its own: a code to type
            return "code_needed", "the app accepted the password and asks for a verification code"
        return "unknown", "could not tell whether the app accepted the login"

    @staticmethod
    def step(after: Screen) -> str:
        """The verification step a screen asks for: "code" when it shows an input field and no password field, else ""."""
        return "code" if any(i.kind == "edit" and not i.password for i in after.items) and find_fields(after)[1] is None else ""

    @staticmethod
    def left_sign_in(now: Screen, signin_app: str) -> bool:
        """Whether a screen read some time after the submit shows the sign-in is over: not the sign-in's own activity, and no password
        field (a held login is saved on this: vault_tools.confirm_later)."""
        return bool(now.items and now.app and now.app != signin_app and find_fields(now)[1] is None)
