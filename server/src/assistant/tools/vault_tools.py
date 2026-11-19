"""What the agent can do with the vault: see what is in it (never the values), ask the user for a login, and have a saved login typed
into the sign-in page it is looking at. The values themselves never pass through the model, in either direction."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from ..agent import Tool, emit_ui
from ..agent.fence import data
from ..android.browser_signin import refusal as browser_refusal
from ..android.device import DeviceError
from ..android.signin import APP_HOST, APP_SIGNIN_HOSTS, SignInReader, choose_submit, find_fields, fingerprint, masked
from ..credentials import clean_name
from ..i18n import N_, t
from ..netguard import NetGuardError, valid_host
from ..secret_requests import SecretRequests, entry_context
from ..store import Store
from ..vault import KEYCHAIN, Transient, VaultError
from ..workers import current_turn, current_worker
from .sites import registrable as site_key
from .browser import BrowserPolicyError, BrowserSession, LoginOptions, NoSignInForm, SignIn, browser_owner


def _when(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "never"


USERNAME_HINT_MAX = 200


def clean_username_hint(value: Any) -> str:
    """The account name the card is pre-filled with (not a secret): at most 200 characters, no control characters; anything else is
    dropped (an empty pre-fill, never an error that blocks the request)."""
    text = value.strip() if isinstance(value, str) else ""
    if len(text) > USERNAME_HINT_MAX or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return ""
    return text


def site_host(site: str) -> str:
    """The host a login is for, from a URL or a bare host name. https only; a public DNS name only. An app on the Android device is
    app:<package name> (lower case, like app:com.example.shop)."""
    text = (site or "").strip()
    if text.lower().startswith("app:"):
        if not APP_HOST.match(text.lower()):
            raise NetGuardError("an app login is for app:<package name>, for example app:com.example.shop")
        return text.lower()
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            raise NetGuardError("a login must be for an https:// site on the standard port, without a user name in the address")
        text = parts.hostname or ""
    return valid_host(text)


ASK_CODE = "A verification step is open (codes are never stored)."
ASK_CODE_NOW = "A verification step is open now (codes are never stored)."
REMEMBER_NOTE = ("If the site now asks whether to remember this device or keep you signed in, choose the option that keeps the owner "
                 "signed in (it goes on without a card); never choose the 'don't register / not now' option on your own.")


def no_form_note(name: str, hosts: list[str], page_host: str, links: list[str]) -> str:
    """browser_login on a page without a sign-in form: nothing typed; the form is reached by a click on the site's own sign-in link,
    never by opening an address. `links`: words of the page's links to the login's own hosts (page text, fenced)."""
    web = [h for h in hosts if not h.startswith("app:")]
    seen = (f" Links on this page that go to this login's own address ({', '.join(web)}): " + ", ".join(data(w) for w in links) + "."
            if links else "")
    return (f"[No sign-in form on this page ({page_host or 'a blank page'}); nothing was typed. Reach the sign-in form like a person: "
            "find the site's own sign-in link or button on this page by its words (any language) and call "
            f"browser_login(name={name!r}, via=<its ref from your latest reading>); Kelo clicks it and fills the form it opens.{seen} "
            "If this page has none, click the site's home link (its logo or home menu), read that page and do the same there. Do not "
            "open a sign-in address yourself.]")


def keep_note(name: str, options: list[tuple[str, str]]) -> str:
    """browser_login on a form with unticked boxes or switches: nothing typed yet; the agent picks the stay-signed-in one by its words."""
    listed = "\n".join(f"- `{sel}`: {data(label)}" for sel, label in options)
    return (f"[The sign-in form shows unticked options; nothing was typed yet:\n{listed}\nCall browser_login(name={name!r}) again now "
            "with keep=<the selector of the one option whose effect is that this browser stays signed in or remembers this device (any "
            "wording, any language)>, or keep=\"none\" when no option means that. Never choose one that agrees to terms, marketing or "
            "newsletters, saves the password, or changes security settings. The second call is part of this sign-in.]")


# A sign-in whose verification is still open is not a sign-in: said plainly, so the model never tells the user they are signed in.
# The wording follows the page: an approval on the phone is never called a code (2026-11-10, 0.9.0+6: "ask for a verification code"
# and a find_login_code hint on a KakaoTalk approval sent the model through the user's mail for a minute).
def not_signed_in(step: str = "") -> str:
    from .verification import NO_CODE, in_language
    return ("The sign-in is NOT finished: a verification step is open. Do not tell the user they are signed in. If a note on the "
            "page below asks you to tick an option first, do that first. Read the page below and follow what it says; tell the user "
            f"exactly what the page asks of them, {in_language()}." + (" " + NO_CODE if step == "approve" else ""))


def shown_outcome(result: Any) -> str:
    """The outcome as the model reads it: a pending verification is named neutrally, with its kind (never "code" for an approval)."""
    return f"verification_pending ({result.step or 'code'})" if result.outcome == "code_needed" else result.outcome
JUST_ENTERED_SECONDS = 15 * 60   # a login the user typed in this long ago and has not been used yet signs in without a second card


# One service under two registrable domains (one moved and redirects): a login for one is the login for the other. Kept short and
# exact on purpose; a login never crosses to a domain that is not listed here (owner 2026-11-14: Threads asked for its login twice,
# once for threads.net and again after the redirect to threads.com).
SAME_SERVICE: tuple[frozenset[str], ...] = (frozenset({"threads.net", "threads.com"}), frozenset({"twitter.com", "x.com"}))


def service_sites(host: str) -> frozenset[str]:
    """The registrable domains of the service `host` belongs to (itself alone, or its SAME_SERVICE group)."""
    key = site_key((host or "").lower())
    return next((g for g in SAME_SERVICE if key in g), frozenset({key} if key else set()))


def login_site_ok(page_host: str, hosts: list[str]) -> bool:
    """Whether a login saved for `hosts` may be typed on `page_host`: the same site, i.e. the same registrable domain
    (www.example.com's login works on member.example.com and example.com, never on example.net or another shop's
    subdomain of a shared host like github.io), like a password manager; or the same service (SAME_SERVICE)."""
    h = (page_host or "").lower()
    if not h:
        return False
    if h.startswith("app:"):   # an app on the device: a login for that app, or for the web host of the service whose sign-in it shows
        return h in hosts or any(login_site_ok(w, hosts) for w in APP_SIGNIN_HOSTS.get(h[4:], ()))
    hosts = [x for x in hosts if not x.startswith("app:")]
    mine = service_sites(h)
    return any(h == x or (site_key(h) and site_key(h) == site_key(x)) or (site_key(x) in mine) for x in hosts)


def just_entered(store: Store, name: str, now: float | None = None) -> bool:
    """The user typed this login in the app a moment ago and it has not been used since: they just asked for this sign-in."""
    for row in store.vault_log(200):
        if row["item"] != name:
            continue
        return (row["action"] in ("saved", "replaced", "entered-once") and "website login" in (row.get("detail") or "")
                and (now or time.time()) - float(row["ts"]) <= JUST_ENTERED_SECONDS)
    return False


ANDROID_TYPE_PAUSE = 0.5     # seconds after a tap on a field, before typing (the keyboard comes up)
ANDROID_SUBMIT_WAIT = 2.0    # seconds Enter gets to move the screen before the Next / Sign in button is tapped
ANDROID_SETTLE = 0.8         # seconds before the screen is read for the answer
ANDROID_SIGN_IN_WAIT = 12.0  # how long the device's sign-in is watched for a clear answer (the browser's sign_in_wait)
ANDROID_POLL = 0.5
CONFIRM_DELAYS = (5.0, 15.0, 30.0, 60.0, 120.0, 180.0)   # seconds between looks at a sign-in whose result was unknown (within the 10-minute hold)


def vault_tools(store: Store, session: BrowserSession | None = None, transient: Transient | None = None,
                on_request: Callable[[str, str, str, Any], Any] | None = None, grants: Any = None, orders: Any = None,
                android: Any = None) -> list[Tool]:
    """`on_request(name, label, host, deadline)`: told when the user is asked for a login that has no value yet (followups.py).
    `grants`: the standing permissions of conditional tasks (schedule_grants.py): a run of a task the user allowed at setup to use a
    login signs in with it on that site without a card. `android`: the Android device's AndroidSession (android/tools.py), for
    android_login; None = no device."""
    transient = transient or Transient()
    checks: set[asyncio.Task[Any]] = set()   # sign-ins whose result was unknown, looked at again for a while (confirm_later)

    async def confirm_later(name: str, username: str, password: str, signin: tuple[str, str]) -> None:
        """A login typed for 'save if it works' whose sign-in could not be read at once (the site took its time, or went on to another
        page): while it is held, look again; once the browser is on that site but off the sign-in page (`signin`: its host and path)
        with no password field, the sign-in worked and the login is saved. Nothing is saved when that never shows."""
        site = site_key(signin[0])
        for delay in CONFIRM_DELAYS:
            await asyncio.sleep(delay)
            if not transient.promotes(name) or session is None:
                return
            try:
                page = await session.page()
                host = (urlsplit(page.url).hostname or "").lower()
                if not host or site_key(host) != site or (host, urlsplit(page.url).path.rstrip("/")) == signin:
                    continue
                if await page.locator("input[type=password]").count():
                    continue
            except Exception:  # noqa: BLE001 - a page that cannot be read now is looked at again later
                continue
            store.set_login(name, username, password)
            store.set_credential_result(name, "ok", "signed in (seen afterwards on the site)")
            store.add_vault_log(name, "saved", "kept because the browser was signed in on the site afterwards")
            transient.discard(name)
            return

    async def vault_status(args: dict[str, Any]) -> str:
        items = store.credentials()
        head = "The user has LOCKED the keychain: nothing in it can be used until they unlock it.\n" if store.vault_locked() else ""
        if not items:
            return head + "The keychain is empty. Ask the user for a login with request_login, or for a service key with add_service_key."
        def where(c: dict[str, Any]) -> str:
            """The login's hosts, and its whole site when a host is only one address of it (a login works on every address of its site)."""
            hosts = ", ".join(c["hosts"])
            sites = sorted({site_key(h) for h in c["hosts"] if not h.startswith("app:")} - set(c["hosts"]) - {""})
            return hosts + (f" (every address of {', '.join(sites)})" if c["kind"] == "login" and sites else "")

        lines = []
        for c in items:
            kind = "website login" if c["kind"] == "login" else "service key"
            have = ("stored" if c["has_secret"] else "entered, and will be saved once a sign-in with it works" if transient.promotes(c["name"])
                    else "entered for one use" if transient.has(c["name"]) else "waiting for the user to enter it")
            ask = ""
            if c["kind"] == "login":
                ask = "; signs in without asking" if c["auto_allow"] else "; asks the user each time"
                mine = [g for g in grants.list(login=c["name"])] if grants is not None else []
                if mine:
                    ask += "; signs in without asking in the runs of " + ", ".join(f"{g['scope']} #{g['scope_ref']}" for g in mine)
            seen = f"; last result: {c['last_result']}{' (' + c['last_result_detail'] + ')' if c['last_result_detail'] else ''}" if c["last_result"] else ""
            lines.append(f"- {c['name']} ({c['label']}): {kind} for {where(c)}; {have}; last used {_when(c['last_used'])}{seen}{ask}")
        return head + "\n".join(lines)

    async def request_login(args: dict[str, Any]) -> str:
        try:
            host = site_host(str(args.get("site", "")))
            name = clean_name(str(args.get("name") or "login-" + host.replace(":", "-").replace(".", "-")))
        except NetGuardError as e:
            return f"error: {e}"
        label = str(args.get("label") or host).strip()[:80] or host
        hint = clean_username_hint(args.get("username"))
        def long_for(login: str) -> str:
            if host.startswith("app:"):   # an app on the Android device: its sign-in screen and android_login, not a page and browser_login
                return f"open the sign-in screen of {host[4:]} on the phone (android_open) and call android_login(name={login!r})"
            return f"get to the sign-in form of {host} by the site's own sign-in link and call browser_login(name={login!r})"
        def short_for(login: str) -> str:
            if host.startswith("app:"):
                return f"Open the sign-in screen of the app on the phone (android_open) and call android_login(name={login!r})."
            return (f"Get to the site's sign-in form by its own sign-in link (or call browser_login with via=<the ref of that link>) "
                    f"and call browser_login(name={login!r}).")
        # A login saved (or asked for) for any address of the same site or service is THE login of the whole site: no second card,
        # whatever name was asked for (owner 2026-11-19: a second login was asked for three times for one address of a site whose
        # login was saved). Two accounts on one site only on purpose: another_account=true, with its own name.
        clash = store.credential(name)   # a service key of that name is a conflict (save_credential below), not a login to reuse
        if not args.get("another_account") and not (clash and clash.get("kind") != "login"):
            same = [c for c in store.credentials() if c["kind"] == "login" and any(login_site_ok(host, [h]) for h in c["hosts"])]
            if same:
                saved = [c for c in same if c["has_secret"]]
                pick = next((c for c in same if c["name"] == name), None) or (saved[0] if saved else same[0])
                name = pick["name"]
                if not pick["has_secret"] and host not in pick["hosts"]:   # nothing sealed yet: its hosts may grow
                    store.save_credential(name, pick["label"], sorted({*pick["hosts"], host}), {}, "", kind="login")
                if pick["has_secret"]:
                    others = [c["name"] for c in saved if c["name"] != name]
                    more = f" Other saved logins for this site (another account): {', '.join(others)}." if others else ""
                    site = host if host.startswith("app:") else (site_key(host) or host)
                    return f"{name} already has a saved login for {site}.{more} {short_for(name)}"
        item = store.credential(name)
        if item and item["secret"] and item.get("kind") == "login":   # a service key of that name is a conflict (save_credential), not a login
            return f"{name} already has a saved login for {host}. {short_for(name)}"

        ctx = entry_context.get()
        entries: SecretRequests = (ctx[0] if ctx else None) or getattr(store, "secret_requests", None) or SecretRequests(store)
        waiter = ctx[1] if ctx else None
        turn = current_turn.get() or {}
        task_id = turn.get("task_id")
        turn_id = str(turn.get("turn_id") or "")

        with store._lock:
            existing = entries.find_open_matching("login", name=name, hosts=[host], task_id=task_id, turn_id=turn_id, max_age=1800.0)
            req = None
            if not existing:
                try:
                    store.save_credential(name, label, store.credential(name)["hosts"] if store.credential(name) else [host], {}, "", kind="login")
                except VaultError as e:
                    return f"error: {e}"
                store.add_vault_log(name, "requested", f"website login for {host}")
                item = store.credential(name)
                if item and item["secret"]:
                    return f"{name} already has a saved login for {host}. {short_for(name)}"
                req = entries.create("login", name=name, label=label, hosts=[host],
                                     reason=str(args.get("reason") or "").strip()[:300], username_hint=hint,
                                     turn=turn, worker_id=current_worker.get() or "", owner=browser_owner.get() or "")

        def saved_reply(rid: str, state: str) -> str:
            """The login the user entered: a saved request, or a request covered by a saved login of the same account (its own name
            has no value, so the model is pointed at the saved one)."""
            now = entries.get(rid) or {}
            if state == "covered":
                info = entries.covered_by(rid)
                login, mode = info.get("covered_by") or name, info.get("mode") or "now"
            else:
                login, mode = now.get("name") or name, ((now.get("result") or {}).get("mode") or "now")
            how = {"now": "saved it to the keychain", "after_success": "entered it; it is saved once a sign-in with it works",
                   "once": "entered it for this one sign-in (it is not stored)"}.get(mode, "entered it")
            return f"The user {how} (you never see it). Go on now, without waiting for a message: {long_for(login)}."

        if existing:
            ex_label = existing.get("label") or label
            state = "open"
            if waiter is not None:
                state = await waiter(existing)
            if state in ("saved", "covered"):
                return saved_reply(existing["id"], state)
            if state == "cancelled":
                return ("The user closed the fill-in card without entering the login. Do not ask for it again unless they bring it up; say "
                        "in one short line what this means for the task, if anything.")
            if state in ("replaced", "expired"):
                return "This entry was replaced by a newer one or ran out; nothing was entered."
            return f"Already asked the user for this sign-in ({ex_label}); wait for it — do not ask again."

        await emit_ui({"type": "secret_request", "request": req})
        state = "open"
        if waiter is not None:
            state = await waiter(req)
        now = entries.get(req["id"]) or req
        if state != "open":
            await emit_ui({"type": "secret_request", "request": now})
        if state in ("saved", "covered"):
            return saved_reply(req["id"], state)
        if state == "cancelled":
            return ("The user closed the fill-in card without entering the login. Do not ask for it again unless they bring it up; say "
                    "in one short line what this means for the task, if anything.")
        if state in ("replaced", "expired"):
            return "This entry was replaced by a newer one or ran out; nothing was entered."
        if on_request is not None:
            try:
                on_request(name, label, host, args.get("deadline"))
            except Exception:  # noqa: BLE001 - a reminder that cannot be set up never stops the request
                pass
        return (f"A fill-in card for the {label} login ({host}) is now shown in the chat, right below your message. Tell the user in one "
                "short sentence to fill it in below; NEVER ask them to type the password in chat, and do not send them to another page. "
                f"Once they enter it you are told and go on by yourself; then {long_for(name)}.")

    tools = [
        Tool("vault_status", "List the keychain: saved website logins and service keys, whether each holds a value, when last used. "
             "Never shows a value, user name or password.",
             {"type": "object", "properties": {}}, vault_status, read_only=True),   # names, sites and dates only (no secret values: those are guarded by the data trail's secret check); a label made every search or open of a saved site ask (2026-11-16)
        Tool("request_login", "Ask the user for a website's login when none is saved: a fill-in card (user name, password) appears "
             "below your message; they fill it there and you never see it. A saved login works on every address of its site, so it is "
             "never asked for again. A service from the Connectors list goes through connect_service instead.",
             {"type": "object", "properties": {"site": {"type": "string", "description": "https address or host of the sign-in page; app:<package> for an app on the Android device"},
                                               "name": {"type": "string", "description": "short slug for this login"},
                                               "label": {"type": "string"},
                                               "reason": {"type": "string", "description": "one short line: why you need it now"},
                                               "username": {"type": "string", "description": "the account name or email the sign-in will use, only when the owner gave it or it is one of the owner's connected account addresses; never guess"},
                                               "another_account": {"type": "boolean", "description": "true only when the user asked to save a second, different account for a site that already has a saved login; give it its own name"},
                                               "deadline": {"type": "string", "description": "local date-time when the login is too late"}},
              "required": ["site"]},
             request_login),   # no approval card: filling the entry is the user's choice (they can just cancel it)
    ]
    def item_for(args: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return store.credential(clean_name(str(args.get("name", ""))))
        except NetGuardError:
            return None

    def usable_login(name: str, tool: str) -> tuple[dict[str, Any], tuple[str, str] | None, str, str]:
        """The checks every typing of a saved login passes (browser_login, android_login): the vault is not locked, the login exists and
        holds a value (or one for this use), and the site did not reject it last time. (item, the login held for one use or until it
        works, user name, password); a VaultError says what is wrong."""
        if store.vault_locked():
            store.add_vault_log(name, "denied", f"{tool}: the vault is locked", False)
            raise VaultError("The user has locked the keychain, so no login can be used right now. They can unlock it in " + t(KEYCHAIN) + ".")
        item = store.credential(name)
        if not item or item["kind"] != "login":
            store.add_vault_log(name, "denied", f"{tool}: no such login", False)
            raise VaultError(f"no website login named {name!r}. Logins: {', '.join(f"{c['name']} ({', '.join(c['hosts'])})" for c in store.credentials() if c['kind'] == 'login') or 'none'}. "
                             "Ask for one with request_login.")
        once = transient.peek(name)
        if not item["secret"] and not once:
            store.add_vault_log(name, "denied", f"{tool}: nothing entered yet", False)
            raise VaultError(f"the user has not entered the login for {name!r} yet. Its fill-in card is waiting in the chat (and in "
                             f"{t(KEYCHAIN)}); if they closed it, ask once with request_login.")
        if item["last_result"] == "failed" and not once:
            # the site turned this saved login down last time: trying again only risks locking the account
            store.add_vault_log(name, "denied", f"{tool}: the last sign-in was rejected; waiting for the user", False)
            raise VaultError(f"the site rejected this saved login the last time ({item['last_result_detail'] or 'wrong user name or password'}). "
                             f"Do NOT try again. Tell the user and ask them to check or re-enter it in {t(KEYCHAIN)} (there is also a 'try again' button).")
        return item, once, *(once or (item["username"], item["secret"]))

    def settle(name: str, item: dict[str, Any], once: tuple[str, str] | None, result: Any, username: str, password: str, step: str, submit: bool) -> str:
        """Remember what the sign-in showed. A login typed for 'save if it works' is saved only now; one the site rejected is not kept."""
        final = submit and step in ("both", "password")          # a username-only step says nothing about the password
        o = result.outcome
        if once is not None:
            if final and o in ("success", "code_needed") and transient.promotes(name):
                store.set_login(name, username, password)
                store.set_credential_result(name, "ok" if o == "success" else o, result.detail)
                store.add_vault_log(name, "saved", "kept because the sign-in worked")
                transient.discard(name)
                return ("The login worked, so it was saved to the keychain." if o == "success" else
                        "The site accepted the password, so the login was saved to the keychain. "
                        + ("A verification step is open now." if result.step == "approve" else ASK_CODE_NOW))
            if final and o == "failed":
                transient.discard(name)
                store.add_vault_log(name, "failed", "the typed login was rejected; it was not saved", False)
                return ("The site rejected the login that was just typed, and it was NOT saved. Do NOT try again. Tell the user it did not work "
                        f"and ask them to check the user name and password and enter them again in {t(KEYCHAIN)}.")
            if final and not transient.promotes(name):
                transient.discard(name)                             # a login for one use is gone after its password step
            if o == "success":
                return "Signed in (the login was for one use and is gone)." if final and not transient.promotes(name) else "Signed in."
        elif final:
            store.set_credential_result(name, "ok" if o == "success" else o, result.detail)
            store.add_vault_log(name, "result", f"sign-in {o}", o == "success")   # ok only when signed in (not a pending verification)
            if o == "failed":
                return (f"The site rejected the saved login. It is now marked as failing and will not be tried again until the user checks it. "
                        f"Tell the user and ask them to check the password in {t(KEYCHAIN)}.")
        return {"success": "Signed in.", "code_needed": "A verification step is open." if result.step == "approve" else ASK_CODE,
                "blocked": "A human check is in the way: follow the note on the page below (you may try it yourself, at most twice; then it goes to the user).",
                "failed": "", "unknown": "Look at the page below to see whether it worked."}.get(o, "")

    # ---- android_login: the same saved login typed into the sign-in screen of an app on the Android device (android/signin.py)
    if android is not None:
        reader = SignInReader()

        async def confirm_android_later(name: str, username: str, password: str, signin_app: str) -> None:
            """confirm_later for the device: a login held for 'save if it works' is saved once the device shows the sign-in is over (off
            the sign-in's own activity, no password field). Looks with `peek`, so the model's refs stay good."""
            for delay in CONFIRM_DELAYS:
                await asyncio.sleep(delay)
                if not transient.promotes(name):
                    return
                try:
                    if not reader.left_sign_in(await android.peek(), signin_app):
                        continue
                except Exception:  # noqa: BLE001 - a screen that cannot be read now is looked at again later
                    continue
                store.set_login(name, username, password)
                store.set_credential_result(name, "ok", "signed in (seen afterwards on the device)")
                store.add_vault_log(name, "saved", "kept because the device was signed in afterwards")
                transient.discard(name)
                return

        async def android_login(args: dict[str, Any]) -> str:
            step = str(args.get("step") or "both")
            if step not in ("both", "username", "password"):
                return "error: step must be both, username or password"
            submit = bool(args.get("submit", True))
            secrets: tuple[str, ...] = ()
            try:
                name = clean_name(str(args.get("name", "")))
                item, once, username, password = usable_login(name, "android_login")
                secrets = (username, password)
                try:
                    screen = (await android.look())[0]   # the screen as it is now: the fields and the app come from it
                except DeviceError as e:
                    return f"error: {e}"
                package = screen.package
                if not package or not login_site_ok("app:" + package.lower(), item["hosts"]):
                    # an app may hand its sign-in to a browser: that page is the app's sign-in when the device says so (android/browser_signin.py)
                    why = await browser_refusal(android.client, screen, [h[4:] for h in item["hosts"] if h.startswith("app:")]) if package else "no app"
                    if why:
                        store.add_vault_log(name, "denied", f"android_login: the device was on {package or 'no app'} ({why})", False)
                        raise VaultError(f"the phone is on {package or 'no app'}, but this login is only for {', '.join(item['hosts'])}. "
                                         "Open that app's sign-in screen first (android_open).")
                    store.add_vault_log(name, "used", f"android_login on {package} for {', '.join(item['hosts'])}: app sign-in page")
                user_box, pass_box = find_fields(screen)
                want_user, want_pass = step in ("both", "username"), step in ("both", "password")
                if not ((want_user and user_box is not None) or (want_pass and pass_box is not None)):
                    raise VaultError("no sign-in field of the expected kind was found on this screen; look at the screen (android_look) "
                                     "and open the app's sign-in screen first")
                store.touch_credential(name)
                try:
                    if want_user and user_box is not None:
                        await android.client.tap(*user_box.center)   # the field first, so the text goes there
                        await asyncio.sleep(ANDROID_TYPE_PAUSE)
                        await android.client.text(username)
                        if want_pass and pass_box is not None:   # the keyboard may have moved the screen: the password field again
                            await asyncio.sleep(ANDROID_TYPE_PAUSE)
                            pass_box = find_fields(await android.peek())[1] or pass_box
                    if want_pass and pass_box is not None:
                        await android.client.tap(*pass_box.center)
                        await asyncio.sleep(ANDROID_TYPE_PAUSE)
                        await android.client.text(password)
                    if submit:
                        typed = await android.peek()
                        await android.client.key("enter")
                        await asyncio.sleep(ANDROID_SUBMIT_WAIT)
                        now = await android.peek()
                        if fingerprint(now) == fingerprint(typed):   # Enter moved nothing: the submit control by structure, then the step judge
                            button = await choose_submit(now, getattr(android, "step_judge", None))
                            if button is not None:
                                await android.client.tap(*button.center)
                except DeviceError as e:   # a message could quote what was typed: only its kind, cleaned of the values
                    msg = str(e)
                    for secret in secrets:
                        if len(secret) >= 1:
                            msg = msg.replace(secret, "[hidden]")
                    store.add_vault_log(name, "used", f"android_login on {package}: failed ({msg[:80]})", False)
                    raise VaultError(f"could not type into the screen: {msg}") from None
                typed_pass = submit and want_pass and pass_box is not None
                outcome, detail, verify = "unknown", "no sign-in was submitted", ""
                if typed_pass:
                    await asyncio.sleep(ANDROID_SETTLE)
                    deadline = time.monotonic() + ANDROID_SIGN_IN_WAIT
                    while True:   # looked at again until the screen answers clearly (signed in, rejected, a code) or the wait is over
                        try:
                            now = await android.peek()
                            outcome, detail = reader.read(screen, now)
                        except DeviceError:
                            outcome, detail = "unknown", "could not read the screen"
                        if outcome != "unknown":
                            verify = reader.step(now) if outcome == "code_needed" else ""
                            break
                        if time.monotonic() >= deadline:
                            break
                        await asyncio.sleep(ANDROID_POLL)
                try:
                    shown = masked((await android.look())[0], secrets)
                except DeviceError as e:
                    shown = f"(the screen could not be read afterwards: {e})"
                result = SignIn(shown, outcome, detail, verify)
                effective = step if (pass_box is not None or step == "username") else "username"   # Google asks for the e-mail first
                store.add_vault_log(name, "used", f"android_login on {package}: {outcome}", outcome == "success")
                note = settle(name, item, once, result, username, password, effective, submit)
                if once is not None and submit and effective in ("both", "password") and outcome == "unknown" and transient.promotes(name):
                    job = asyncio.get_running_loop().create_task(confirm_android_later(name, username, password, screen.app))
                    checks.add(job)
                    job.add_done_callback(checks.discard)
                if outcome == "code_needed":
                    note = (f"{not_signed_in(verify)} {note} If a code is needed and you cannot get it, call android_show so the user can "
                            "type it on the phone's screen themselves.").strip()
                return f"[sign-in result: {shown_outcome(result)} — {detail}]\n{note}\n\n{shown}"
            except (NetGuardError, VaultError) as e:
                return f"error: {e}"

        def android_auto(args: dict[str, Any]) -> bool:
            item = item_for(args)
            if not item or item["kind"] != "login" or store.vault_locked():
                return False
            # no card when the user allowed this login to sign in on its own, or typed it in a moment ago for this very sign-in
            return bool(item["auto_allow"] or just_entered(store, item["name"]) or transient.has(item["name"]))

        async def android_describe(args: dict[str, Any]) -> str:
            item = item_for(args)
            label = item["label"] if item else str(args.get("name", ""))
            try:
                app = (await android.peek()).package   # what is in front now: the card names the app the values would go into
            except Exception:  # noqa: BLE001 - the card still asks, naming no app
                app = ""
            return t("Type your {label} login into {app}.", label=label, app=app or t("an app on the phone"))

        tools.append(Tool("android_login", "Type a saved login into the sign-in screen of the phone app in front on the Android device "
                          "and submit it; the user approves unless they allowed that login. Works only in the app (or the service's sign-in "
                          "screen, e.g. Google in Play) the login was saved for: request_login with site app:<package>. If the app opens its sign-in "
                          "in a browser on the phone, call android_login there: the phone says whether that page belongs to the app. You never see the "
                          "values. Google asks for the e-mail first: call it with step username, then again with step password on the next "
                          "screen. If the result is failed, do not try again: tell the user. Signed in only when the result says success.",
                          {"type": "object", "properties": {"name": {"type": "string"},
                                                            "step": {"type": "string", "enum": ["both", "username", "password"],
                                                                     "description": "username / password: a form split over two screens"},
                                                            "submit": {"type": "boolean"}}, "required": ["name"]},
                          android_login, requires_approval=True, untrusted=True, source="page", auto_approve=android_auto,
                          describe=android_describe))
    if session is None:
        return tools

    async def page_host() -> str:
        try:
            return urlsplit((await session.page()).url).hostname or ""
        except Exception:  # noqa: BLE001
            return ""

    async def browser_login(args: dict[str, Any]) -> str:
        step = str(args.get("step") or "both")
        if step not in ("both", "username", "password"):
            return "error: step must be both, username or password"
        submit = bool(args.get("submit", True))
        via = str(args.get("via") or "").strip()
        keep = None if args.get("keep") is None else (str(args.get("keep")).strip() or "none")
        try:
            name = clean_name(str(args.get("name", "")))
            if keep is not None:
                session.end_keep_choice(name)   # the choice asked for by the call before is answered now
            item, once, username, password = usable_login(name, "browser_login")
            page = await session.page()
            parts = urlsplit(page.url)
            came_from = str(page.url or "")
            if parts.scheme != "https" or not login_site_ok(parts.hostname or "", item["hosts"]):
                store.add_vault_log(name, "denied", f"browser_login: the browser was on {parts.hostname or 'a blank page'}", False)
                raise VaultError(f"the browser is on {parts.hostname or 'a blank page'}, but this login is only for the site {', '.join(item['hosts'])} "
                                 "(and its other addresses under the same domain) over https. "
                                 "Go to that site first (its home page), then use its own sign-in link.")
            store.touch_credential(name)

            def mark() -> None:
                if submit:   # a sign-in the agent itself started on this site, in this task: a code that site mails for it may be typed
                    from ..workers import current_turn

                    session.sign_ins[browser_owner.get()] = {"site": site_key(parts.hostname or ""), "at": time.time(),
                                                             "task": (current_turn.get() or {}).get("task_id")}

            async def fill() -> SignIn:
                return await session.fill_login(
                    username, password, step=step, submit=submit, ask_options=submit and keep is None, keep=keep,
                    origin_ok=lambda url: urlsplit(url).scheme == "https" and login_site_ok(urlsplit(url).hostname or "", item["hosts"]))

            mark()
            try:
                try:
                    result = await fill()
                except NoSignInForm:
                    if not via:
                        store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: no sign-in form on the page", False)
                        return no_form_note(name, item["hosts"], parts.hostname or "", await session.sign_in_links(item["hosts"]))
                    await session.open_sign_in(via)
                    parts = urlsplit((await session.page()).url)
                    if parts.scheme != "https" or not login_site_ok(parts.hostname or "", item["hosts"]):
                        store.add_vault_log(name, "denied", f"browser_login: the sign-in link led to {parts.hostname or 'a blank page'}", False)
                        raise VaultError(f"the link led to {parts.hostname or 'a blank page'}, which is not the site of this login "
                                         f"({', '.join(item['hosts'])}); nothing was typed.") from None
                    mark()
                    try:
                        result = await fill()
                    except NoSignInForm:
                        store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: no sign-in form after the click", False)
                        return (f"error: after the click the page ({parts.hostname}) still shows no sign-in form; nothing was typed. Read "
                                "the page: if it asks something first, do that, then call browser_login again on the page with the form.")
            except LoginOptions as e:
                session.wait_keep_choice(name)
                store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: the form's options were listed, nothing typed", True)
                return keep_note(name, e.options)
            except BrowserPolicyError as e:
                store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: failed ({str(e)[:80]})", False)
                raise
            back = ""
            if via and str((await session.page()).url or "").split("#", 1)[0] != came_from.split("#", 1)[0]:
                back = (f"\n[Before signing in you were on {came_from[:200]}. If the site did not bring you back, get there again through "
                        "the site's own links.]")
            # logged once the outcome is known: ok only on signed-in evidence (a verification still open is code_needed, not ok)
            store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: {result.outcome}", result.outcome == "success")
            if result.outcome == "success":   # Kelo's browser now holds this site's sign-in (browser_sign_ins.py, the Keychain card)
                store.note_browser_sign_in(site_key(parts.hostname or ""), "login", session.profile_of(browser_owner.get() or ""))
                session.clear_pause(site_key(parts.hostname or ""))   # the sign-in pass worked: the site loads again (brief B2)
            if result.outcome in ("success", "failed", "blocked"):
                session.end_sign_in_pass(site_key(parts.hostname or ""))   # a sign-in pass ends with an outcome; code_needed goes on
            note = settle(name, item, once, result, username, password, step, submit)
            if once is not None and submit and step in ("both", "password") and result.outcome == "unknown" and transient.promotes(name):
                job = asyncio.get_running_loop().create_task(
                    confirm_later(name, username, password, ((parts.hostname or "").lower(), parts.path.rstrip("/"))))
                checks.add(job)
                job.add_done_callback(checks.discard)
            pending = N_("Approve the sign-in on your phone") if result.step == "approve" else N_("A verification code is needed")
            await session.card(t({"success": N_("Signed in"), "code_needed": pending,
                                  "blocked": N_("A human check is needed"), "failed": N_("The sign-in was refused")}.get(result.outcome, N_("Signing in"))),
                               force=True)
            if result.outcome == "code_needed" and result.step != "approve":   # a code (never for an approval on the phone)
                typed = await session.auto_code(parts.hostname or "")   # the site's own mail, the owner's setting (browser_more.auto_code)
                if typed:
                    for ask in (ASK_CODE, ASK_CODE_NOW):
                        note = note.replace(ask, "").strip()
                    return (f"[sign-in result: {shown_outcome(result)} — {result.detail}]\n{not_signed_in(result.step)} {note}\n"
                            f"{typed}{back}").replace("\n\n\n", "\n\n")
                # a code step and a connected mailbox: find_login_code decides by the mail itself (code_judge, the user's grant for that
                # site's login), not by a word of the page; an approval step has no code (NO_CODE)
                if any(c["kind"] == "email" and c["has_secret"] for c in store.credentials()):
                    note += (f" The user's mailbox is connected: if the site emails the code, call find_login_code(site={parts.hostname!r}) — "
                             "it types a clear code from that site's own mail by itself when the user's setting allows it, else shows the "
                             "code to the user and types it only after they allow it.")
            if result.outcome == "code_needed":
                note = f"{not_signed_in(result.step)} {note}".strip()
            elif submit and result.outcome in ("success", "unknown"):
                note = f"{note} {REMEMBER_NOTE}".strip()
            return f"[sign-in result: {shown_outcome(result)} — {result.detail}]\n{note}{back}\n\n{result.text}"
        except (NetGuardError, BrowserPolicyError) as e:
            return f"error: {e}"

    async def describe(args: dict[str, Any]) -> str:
        item = item_for(args)
        host = await page_host()
        who = f" ({item['username_hint']})" if item and item.get("username_hint") else ""
        label = item["label"] if item else str(args.get("name", ""))
        via = str(args.get("via") or "").strip()
        if via and (link := session.ref_label(via)):
            line = t("Opens the sign-in page through “{link}” on {host}, then types the {label}{who} login there. It is registered for this site only.",
                     link=link, label=label, who=who, host=host or t("a blank page"))
        elif via:
            line = t("Opens the site's sign-in page from {host}, then types the {label}{who} login there. It is registered for this site only.",
                     label=label, who=who, host=host or t("a blank page"))
        else:
            line = t("Types the {label}{who} login into the page that is open now ({host}). It is registered for this site only.",
                     label=label, who=who, host=host or t("a blank page"))
        order = orders.current() if orders is not None else None
        if order is not None and not order.paid:   # one of at most two cards of the order: say so, so the user knows what comes
            line += " " + t("This is for your order of {what}; the payment is confirmed once more at the end.", what=order.what)
        return line

    def auto(args: dict[str, Any]) -> Any:
        item = item_for(args)
        if not item or item["kind"] != "login" or store.vault_locked():
            return False
        if args.get("keep") is not None and session.keep_choice_open(item["name"]):
            return True   # the second call of a sign-in the user just approved: it only adds the stay-signed-in choice (S6)
        # no card when the user allowed this login to sign in on its own, or typed it in a moment ago for this very sign-in
        if item["auto_allow"] or just_entered(store, item["name"]) or transient.has(item["name"]):
            return True
        if grants is None or not grants.list(login=item["name"]):
            return False
        return scheduled(item)

    async def scheduled(item: dict[str, Any]) -> bool:
        """A run of a task the user allowed, at setup, to use this login on this site (logged as allowed by that permission)."""
        host = await page_host()
        if host and login_site_ok(host, item["hosts"]) and (g := grants.login_cover(item["name"], host)) is not None:
            grants.use_login(g, host)
            return True
        return False

    tools.append(Tool("browser_login", "Type a saved login into the sign-in form of the page you are on and submit it; the user approves "
                      "unless they allowed that login. No sign-in form on the page: pass via=<the ref of the site's own sign-in link or button on "
                      "this page>; Kelo clicks it like a person and fills the form it opens (never open a sign-in address yourself). When "
                      "the form has unticked options, nothing is typed at first: you get them and call again with keep=<the selector "
                      "of the one that keeps this browser signed in> or keep=\"none\". Works only on the site it was saved for (every "
                      "address of that site); you never see the values. If the result is failed, do not try again: tell the user. "
                      "Signed in only when the result says success. On the user's own account (never someone else's), accept an offer "
                      "to keep this browser signed in or skip verification next time (any wording, any language); not saving the "
                      "password, marketing or cookie consent, a recovery phone or email, or security settings.",
                      {"type": "object", "properties": {"name": {"type": "string"},
                                                        "step": {"type": "string", "enum": ["both", "username", "password"],
                                                                 "description": "username / password: a form split over two pages"},
                                                        "submit": {"type": "boolean"},
                                                        "via": {"type": "string", "description": "only when the page has no sign-in form: ref (e12) of the site's own sign-in link or button on this page, from your latest reading"},
                                                        "keep": {"type": "string", "description": "after browser_login listed the form's unticked options: the selector of the one that keeps this browser signed in, or \"none\""}},
                       "required": ["name"]},
                      browser_login, requires_approval=True, untrusted=True, source="page", auto_approve=auto, describe=describe))
    return tools
