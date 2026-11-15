"""What the agent can do with the vault: see what is in it (never the values), ask the user for a login, and have a saved login typed
into the sign-in page it is looking at. The values themselves never pass through the model, in either direction."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from ..agent import Tool
from ..credentials import clean_name
from ..i18n import N_, t
from ..netguard import NetGuardError, valid_host
from ..secret_requests import ask
from ..store import Store
from ..vault import KEYCHAIN, Transient, VaultError
from .sites import registrable as site_key
from .browser import BrowserPolicyError, BrowserSession, browser_owner


def _when(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "never"


def site_host(site: str) -> str:
    """The host a login is for, from a URL or a bare host name. https only; a public DNS name only."""
    text = (site or "").strip()
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            raise NetGuardError("a login must be for an https:// site on the standard port, without a user name in the address")
        text = parts.hostname or ""
    return valid_host(text)


ASK_CODE = "A verification step is open (codes are never stored)."
ASK_CODE_NOW = "A verification step is open now (codes are never stored)."
# Mail named on the page: only then may a code be looked for in the user's mailbox (find_login_code)
_BY_MAIL = re.compile(r"e-?mail|\bmail\b|inbox|메일|이메일|メール|邮件|郵件|邮箱|correo|courriel|posta|почт", re.I)


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


def login_site_ok(page_host: str, hosts: list[str]) -> bool:
    """Whether a login saved for `hosts` may be typed on `page_host`: the same site, i.e. the same registrable domain
    (www.example.com's login works on member.example.com and example.com, never on example.net or another shop's
    subdomain of a shared host like github.io), like a password manager."""
    h = (page_host or "").lower()
    return bool(h) and any(h == x or (site_key(h) and site_key(h) == site_key(x)) for x in hosts)


def just_entered(store: Store, name: str, now: float | None = None) -> bool:
    """The user typed this login in the app a moment ago and it has not been used since: they just asked for this sign-in."""
    for row in store.vault_log(200):
        if row["item"] != name:
            continue
        return (row["action"] in ("saved", "replaced", "entered-once") and "website login" in (row.get("detail") or "")
                and (now or time.time()) - float(row["ts"]) <= JUST_ENTERED_SECONDS)
    return False


def vault_tools(store: Store, session: BrowserSession | None = None, transient: Transient | None = None,
                on_request: Callable[[str, str, str, Any], Any] | None = None, grants: Any = None, orders: Any = None) -> list[Tool]:
    """`on_request(name, label, host, deadline)`: told when the user is asked for a login that has no value yet (followups.py).
    `grants`: the standing permissions of conditional tasks (schedule_grants.py): a run of a task the user allowed at setup to use a
    login signs in with it on that site without a card."""
    transient = transient or Transient()

    async def vault_status(args: dict[str, Any]) -> str:
        items = store.credentials()
        head = "The user has LOCKED the keychain: nothing in it can be used until they unlock it.\n" if store.vault_locked() else ""
        if not items:
            return head + "The keychain is empty. Ask the user for a login with request_login, or for a service key with add_service_key."
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
            lines.append(f"- {c['name']} ({c['label']}): {kind} for {', '.join(c['hosts'])}; {have}; last used {_when(c['last_used'])}{seen}{ask}")
        return head + "\n".join(lines)

    async def request_login(args: dict[str, Any]) -> str:
        try:
            host = site_host(str(args.get("site", "")))
            name = clean_name(str(args.get("name") or "login-" + host.replace(".", "-")))
        except NetGuardError as e:
            return f"error: {e}"
        label = str(args.get("label") or host).strip()[:80] or host
        try:
            store.save_credential(name, label, [host], {}, "", kind="login")
        except VaultError as e:
            return f"error: {e}"
        store.add_vault_log(name, "requested", f"website login for {host}")
        item = store.credential(name)
        if item and item["secret"]:
            return f"{name} already has a saved login for {host}. Open the sign-in page in the browser and call browser_login(name={name!r})."
        req, state = await ask("login", name=name, label=label, hosts=[host], reason=str(args.get("reason") or "").strip()[:300])
        if state == "saved":
            mode = ((req or {}).get("result") or {}).get("mode") or "now"
            how = {"now": "saved it to the keychain", "after_success": "entered it; it is saved once a sign-in with it works",
                   "once": "entered it for this one sign-in (it is not stored)"}.get(mode, "entered it")
            return (f"The user {how} (you never see it). Go on now, without waiting for a message: open the sign-in page of {host} in the "
                    f"browser and call browser_login(name={name!r}).")
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
                "Once they enter it you are told and go on by yourself; then open the sign-in page of "
                f"{host} in the browser and call browser_login(name={name!r}).")

    tools = [
        Tool("vault_status", "List the keychain: saved website logins and service keys, whether each holds a value, when last used. "
             "Never shows a value, user name or password.",
             {"type": "object", "properties": {}}, vault_status, read_only=True),
        Tool("request_login", "Ask the user for a website's login when none is saved: a fill-in card (user name, password) appears "
             "below your message; they fill it there and you never see it. A service from the Connectors list goes through "
             "connect_service instead.",
             {"type": "object", "properties": {"site": {"type": "string", "description": "https address or host of the sign-in page"},
                                               "name": {"type": "string", "description": "short slug for this login"},
                                               "label": {"type": "string"},
                                               "reason": {"type": "string", "description": "one short line: why you need it now"},
                                               "deadline": {"type": "string", "description": "local date-time when the login is too late"}},
              "required": ["site"]},
             request_login),   # no approval card: filling the entry is the user's choice (they can just cancel it)
    ]
    if session is None:
        return tools

    def item_for(args: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return store.credential(clean_name(str(args.get("name", ""))))
        except NetGuardError:
            return None

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
        try:
            name = clean_name(str(args.get("name", "")))
            if store.vault_locked():
                store.add_vault_log(name, "denied", "browser_login: the vault is locked", False)
                raise VaultError("The user has locked the keychain, so no login can be used right now. They can unlock it in " + t(KEYCHAIN) + ".")
            item = store.credential(name)
            if not item or item["kind"] != "login":
                store.add_vault_log(name, "denied", "browser_login: no such login", False)
                raise VaultError(f"no website login named {name!r}. Logins: {', '.join(c['name'] for c in store.credentials() if c['kind'] == 'login') or 'none'}. "
                                 "Ask for one with request_login.")
            once = transient.peek(name)
            if not item["secret"] and not once:
                store.add_vault_log(name, "denied", "browser_login: nothing entered yet", False)
                raise VaultError(f"the user has not entered the login for {name!r} yet. Its fill-in card is waiting in the chat (and in "
                                 f"{t(KEYCHAIN)}); if they closed it, ask once with request_login.")
            if item["last_result"] == "failed" and not once:
                # the site turned this saved login down last time: trying again only risks locking the account
                store.add_vault_log(name, "denied", "browser_login: the last sign-in was rejected; waiting for the user", False)
                raise VaultError(f"the site rejected this saved login the last time ({item['last_result_detail'] or 'wrong user name or password'}). "
                                 f"Do NOT try again. Tell the user and ask them to check or re-enter it in {t(KEYCHAIN)} (there is also a 'try again' button).")
            username, password = once or (item["username"], item["secret"])
            page = await session.page()
            parts = urlsplit(page.url)
            if parts.scheme != "https" or not login_site_ok(parts.hostname or "", item["hosts"]):
                store.add_vault_log(name, "denied", f"browser_login: the browser was on {parts.hostname or 'a blank page'}", False)
                raise VaultError(f"the browser is on {parts.hostname or 'a blank page'}, but this login is only for the site {', '.join(item['hosts'])} "
                                 "(and its other addresses under the same domain) over https. "
                                 "Open that site's sign-in page first.")
            store.touch_credential(name)
            if submit:   # a sign-in the agent itself started on this site, in this task: a code that site mails for it may be typed
                from ..workers import current_turn

                session.sign_ins[browser_owner.get()] = {"site": site_key(parts.hostname or ""), "at": time.time(),
                                                         "task": (current_turn.get() or {}).get("task_id")}
            try:
                result = await session.fill_login(username, password, step=step, submit=submit)
            except BrowserPolicyError as e:
                store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: failed ({str(e)[:80]})", False)
                raise
            # logged once the outcome is known: ok only on signed-in evidence (a verification still open is code_needed, not ok)
            store.add_vault_log(name, "used", f"browser_login on {parts.hostname}: {result.outcome}", result.outcome == "success")
            note = settle(name, item, once, result, username, password, step, submit)
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
                            f"{typed}").replace("\n\n\n", "\n\n")
                if _BY_MAIL.search(result.text or "") and any(c["kind"] == "email" and c["has_secret"] for c in store.credentials()):
                    note += (f" The user's mailbox is connected: if the site emails the code, call find_login_code(site={parts.hostname!r}) — "
                             "it types a clear code from that site's own mail by itself when the user's setting allows it, else shows the "
                             "code to the user and types it only after they allow it.")
            if result.outcome == "code_needed":
                note = f"{not_signed_in(result.step)} {note}".strip()
            return f"[sign-in result: {shown_outcome(result)} — {result.detail}]\n{note}\n\n{result.text}"
        except (NetGuardError, BrowserPolicyError) as e:
            return f"error: {e}"

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

    async def describe(args: dict[str, Any]) -> str:
        item = item_for(args)
        host = await page_host()
        who = f" ({item['username_hint']})" if item and item.get("username_hint") else ""
        label = item["label"] if item else str(args.get("name", ""))
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
                      "unless they allowed that login. Works only on the site it was saved for; you never see the values. If the result "
                      "is failed, do not try again: tell the user. Signed in only when the result says success. On the user's own "
                      "account (never someone else's), accept an offer to keep this browser signed in or skip verification next time (any "
                      "wording, any language); not saving the password, marketing or cookie consent, a recovery phone or email, or security "
                      "settings.",
                      {"type": "object", "properties": {"name": {"type": "string"},
                                                        "step": {"type": "string", "enum": ["both", "username", "password"],
                                                                 "description": "username / password: a form split over two pages"},
                                                        "submit": {"type": "boolean"}}, "required": ["name"]},
                      browser_login, requires_approval=True, untrusted=True, source="page", auto_approve=auto, describe=describe))
    return tools
