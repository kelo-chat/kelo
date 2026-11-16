"""Gmail and Google Calendar with a sign-in button: the user signs in on Google's own page and comes back to their own Kelo. The mailbox
is then read and sent through IMAP/SMTP with the Google token (XOAUTH2) instead of an app password; the calendar is used through the
Calendar API (google_calendar.py).

Same shape as GitHub (see github_web.py): the operator registers ONE Google OAuth client (Web application, redirect = the shared relay
page). Its secret stays on the operator's server (the broker). Google tokens run out after an hour and renewing them needs the secret too,
so other copies ask the broker for a fresh one (POST /broker/google/refresh); the broker keeps nothing.

Until Google has verified the operator's OAuth client, NEW sign-ins stay closed (the operator flips OPEN_META on from 운영자 설정 when
the review is done): start() and the broker's exchange refuse with REVIEW_NOTE, the Connectors rows say "pending_review", and accounts that
are already connected keep working (refresh is never gated). A copy that leans on the broker follows the broker's switch.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from .i18n import N_, t
from .mcp_oauth import clean_origin, relay_state
from .netguard import NetGuardError, Reply, fetch
from .store import Store

Fetcher = Callable[..., Awaitable[Reply]]
AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "openid email https://mail.google.com/"
CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/calendar.events")
# What each connector asks Google for. Each sign-in asks only its own scopes (incremental authorization, include_granted_scopes), so one
# Google account can hold the Gmail and the Calendar grant side by side and connecting one never asks for the other's.
PRODUCTS = {"gmail": SCOPE, "calendar": "openid email " + " ".join(CALENDAR_SCOPES)}
VAULT_ITEM = {"gmail": "mail-gmail", "calendar": "google-calendar"}
SECRET = "oauth-app:google"
CLIENT_META = "oauth_client_id.google"
PENDING_SECONDS = 900.0
OPEN_META = "google_signin_open"     # "1": everyone may start a new Google sign-in (default: closed until Google's review is done)
SUPPORT_META = "support_url"         # a future "support Kelo" link shown under the note; nothing is shown while it is empty
REVIEW_NOTE = N_("This opens once Google's review is done. Supporting Kelo helps it open sooner.")


class GoogleWebError(Exception):
    """Shown to the user as it is."""


class GoogleReviewPending(GoogleWebError):
    """New Google sign-ins are closed until the operator opens them (HTTP 409)."""


def support_url(store: Store) -> str:
    """The support link for the review note, or "" (no link shown)."""
    url = store.meta(SUPPORT_META).strip()
    return url if url.startswith("https://") else ""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def email_of(id_token: str) -> str:
    """The address in an id_token that came straight from Google's token endpoint over TLS (so its payload is read, not re-verified)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return ""
    return str(claims.get("email") or "") if claims.get("email_verified", True) else ""


class GoogleWeb:
    def __init__(self, store: Store, *, relay: str = "", broker: str = "", fetcher: Fetcher = fetch):
        self.store, self.relay, self.fetcher = store, relay.strip(), fetcher
        self.broker = broker.strip().rstrip("/")
        self.pending: dict[str, dict[str, Any]] = {}
        self._remote_id: tuple[str, float] = ("", 0.0)
        self._remote_open = False   # the broker's switch, read with its client id
        self.beta_open: Callable[[], bool] = lambda: False   # this copy's beta request was approved (beta.py)
        self.beta_install: Callable[[], str] = lambda: ""   # its install secret for the broker's exchange, "" when not approved

    # ---- whether new sign-ins are open (see the module note)
    def open_to_all(self) -> bool:
        if not self.own_client_id() and self.broker:
            return self._remote_open
        return self.store.meta(OPEN_META) == "1"

    def open_here(self) -> bool:
        """New Google sign-ins may start on this copy: open to everyone, this copy's beta request was approved (beta.py), or this copy holds
        its own Google client (the operator's server, or a self-hoster's own client: its owner is that client's test user, and the review
        gate only protects copies that lean on the operator's client)."""
        return self.open_to_all() or self.beta_open() or bool(self.own_client_id())

    def set_open(self, on: bool) -> None:
        self.store.set_meta(OPEN_META, "1" if on else "")

    def review_note(self) -> str:
        return t(REVIEW_NOTE)

    # ---- the operator's side
    def own_client_id(self) -> str:
        return self.store.meta(CLIENT_META)

    def is_broker(self) -> bool:
        return bool(self.store.key(SECRET) and self.own_client_id())

    def set_app(self, client_id: str | None, client_secret: str | None) -> None:
        if client_id is not None:
            cid = client_id.strip()
            if cid and not (cid.endswith(".apps.googleusercontent.com") and len(cid) < 120 and " " not in cid):
                raise GoogleWebError(t("The Client ID ends with …apps.googleusercontent.com."))
            self.store.set_meta(CLIENT_META, cid)
        if client_secret is not None:
            sec = client_secret.strip()
            if sec and (len(sec) < 20 or len(sec) > 100 or any(c.isspace() for c in sec)):
                raise GoogleWebError(t("Please check the client secret again (it starts with GOCSPX-)."))
            if sec:
                self.store.set_key(SECRET, sec)
            else:
                self.store.delete_key(SECRET)

    async def token_here(self, form: dict[str, str]) -> dict[str, Any]:
        """The broker's job: add the client id and secret and ask Google. Nothing is kept."""
        secret, cid = self.store.key(SECRET), self.own_client_id()
        if not secret or not cid:
            raise GoogleWebError(t("This server has no Google app secret."))
        return await self._post(TOKEN, {**form, "client_id": cid, "client_secret": secret}, form_encoded=True)

    # ---- every copy's side
    async def client_id(self) -> str:
        own = self.own_client_id()
        if own or not self.broker:
            return own
        cid, at = self._remote_id
        if cid and time.time() - at < 3600:
            return cid
        try:
            r = await self.fetcher("GET", f"{self.broker}/broker/google/client", allowed_hosts=[urlsplit(self.broker).hostname or ""],
                                   headers={"Accept": "application/json"})
            body = json.loads(r.body) if r.status == 200 else {}
            cid = str(body.get("client_id") or "") if isinstance(body, dict) else ""
            self._remote_open = bool(cid and body.get("open") is True)
        except (NetGuardError, ValueError):
            cid = ""
        self._remote_id = (cid, time.time())
        return cid

    async def available(self) -> bool:
        return bool(self.relay and (self.is_broker() or self.broker) and await self.client_id())

    async def start(self, origin: str, product: str = "gmail") -> str:
        if product not in PRODUCTS:
            raise GoogleWebError(t("This Google service can't be connected here."))
        gmail = product == "gmail"
        if not self.relay:
            raise GoogleWebError(t("This server has no fixed address (sign-in relay) set up. Please connect with an app password.") if gmail
                                 else t("This server has no fixed address (sign-in relay) set up, so Google sign-in can't be used."))
        cid = await self.client_id()
        if not cid:
            raise GoogleWebError(t("Google sign-in is not ready yet. Please connect with an app password.") if gmail
                                 else t("The operator hasn't set up Google sign-in yet."))
        if not self.open_here():
            raise GoogleReviewPending(self.review_note())
        here = clean_origin(origin)
        verifier = _b64(secrets.token_bytes(32))
        state = relay_state(_b64(secrets.token_bytes(24)), here)
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v["created"] < PENDING_SECONDS}
        self.pending[state] = {"verifier": verifier, "origin": here, "created": now, "product": product}
        self.store.add_vault_log(VAULT_ITEM[product], "requested", "Google sign-in started")
        return AUTHORIZE + "?" + urlencode({"client_id": cid, "redirect_uri": self.relay, "response_type": "code", "scope": PRODUCTS[product],
                                             "state": state, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
                                             "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"})

    def owns(self, state: str) -> bool:
        return state in self.pending

    def product_of(self, state: str) -> str:
        """Which connector a pending sign-in is for ("gmail" or "calendar")."""
        return str(self.pending.get(state, {}).get("product") or "gmail")

    async def _token(self, form: dict[str, str], broker_path: str, broker_body: dict[str, str]) -> dict[str, Any]:
        if self.is_broker():
            return await self.token_here(form)
        return await self._post(f"{self.broker}{broker_path}", broker_body)

    async def exchange(self, state: str, code: str) -> tuple[dict[str, Any], str]:
        """(Google's token answer, the app address to go back to). Raises GoogleWebError."""
        p = self.pending.pop(state, None)
        if p is None:
            raise GoogleWebError(t("This connection request has expired or was already handled. Please connect again from the app."))
        beta = {} if self.open_to_all() or not (install := self.beta_install()) else {"install_id": install}   # an approved beta copy
        tok = await self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": self.relay, "code_verifier": p["verifier"]},
                                "/broker/google/exchange", {"code": code, "redirect_uri": self.relay, "code_verifier": p["verifier"], **beta})
        return tok, p["origin"]

    def forget(self, state: str) -> str:
        p = self.pending.pop(state, None)
        return p["origin"] if p else ""

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._token({"grant_type": "refresh_token", "refresh_token": refresh_token}, "/broker/google/refresh",
                                 {"refresh_token": refresh_token})

    async def _post(self, url: str, data: dict[str, str], *, form_encoded: bool = False) -> dict[str, Any]:
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" if form_encoded else "application/json"}
        content = (urlencode(data) if form_encoded else json.dumps(data)).encode()
        try:
            r = await self.fetcher("POST", url, allowed_hosts=[urlsplit(url).hostname or ""], headers=headers, content=content)
        except NetGuardError as e:
            raise GoogleWebError(t("Could not connect: {error}", error=e)) from None
        try:
            body = json.loads(r.body)
        except ValueError:
            raise GoogleWebError(t("Could not read the answer (HTTP {status}).", status=r.status)) from None
        if not isinstance(body, dict):
            raise GoogleWebError(t("The answer looks wrong."))
        if r.status >= 400 and "access_token" not in body:
            raise GoogleWebError(str(body.get("detail") or body.get("error_description") or body.get("error") or f"HTTP {r.status}")[:120])
        return body
