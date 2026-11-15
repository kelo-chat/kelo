"""Apps on the owner's network that take a user name and password and give back a session token (Pi-hole v6, Beszel / PocketBase,
FreshRSS' Google Reader API, Wallabag's OAuth password grant). The password is kept sealed in the vault as a small JSON bundle
(MARK); each use trades it for a token (kept in memory until shortly before it runs out) and the token goes where the preset's
placement says. A 401 from the app forgets the token so the next call signs in again.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from .netguard import NetGuardError, Reply

MARK = "local-login-v1"
LocalFetcher = Callable[..., Awaitable[Reply]]
_sessions: dict[str, tuple[str, float]] = {}


class LoginError(Exception):
    """Shown to the user as it is."""


def bundle(kind: str, fields: dict[str, str]) -> str:
    return json.dumps({"mark": MARK, "kind": kind, "fields": fields})


def is_login(secret: str | None) -> bool:
    return bool(secret) and secret.startswith("{") and f'"mark": "{MARK}"' in secret


def forget(secret: str | None) -> None:
    if secret:
        _sessions.pop(hashlib.sha256(secret.encode()).hexdigest(), None)


def _json(r: Reply) -> dict[str, Any]:
    try:
        body = json.loads(r.body)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _post(fetcher: LocalFetcher, origin: str, base: str, path: str, *, form: dict[str, str] | None = None,
                body: dict[str, Any] | None = None) -> Reply:
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" if form is not None else "application/json"}
    content = urlencode(form).encode() if form is not None else json.dumps(body).encode()
    return await fetcher("POST", f"{origin}{base}{path}", origin=origin, headers=headers, content=content)


async def sign_in(kind: str, f: dict[str, str], origin: str, base: str, fetcher: LocalFetcher) -> tuple[str, float]:
    """(token, seconds it lasts) for one app, or LoginError."""
    try:
        if kind == "pihole":
            r = await _post(fetcher, origin, base, "/api/auth", body={"password": f["password"]})
            sess = _json(r).get("session") or {}
            if r.status == 200 and sess.get("valid", True) and sess.get("sid"):
                return str(sess["sid"]), float(sess.get("validity") or 300)
        elif kind == "beszel":
            r = await _post(fetcher, origin, base, "/api/collections/users/auth-with-password",
                            body={"identity": f["username"], "password": f["password"]})
            if r.status == 200 and _json(r).get("token"):
                return str(_json(r)["token"]), 3600.0
        elif kind == "freshrss":
            r = await _post(fetcher, origin, base, "/api/greader.php/accounts/ClientLogin", form={"Email": f["username"], "Passwd": f["password"]})
            for line in r.body.decode("utf-8", "replace").splitlines():
                if r.status == 200 and line.startswith("Auth="):
                    return line[5:].strip(), 6 * 3600.0
        elif kind == "wallabag":
            r = await _post(fetcher, origin, base, "/oauth/v2/token", form={
                "grant_type": "password", "client_id": f["client_id"], "client_secret": f["client_secret"],
                "username": f["username"], "password": f["password"]})
            tok = _json(r)
            if r.status == 200 and tok.get("access_token"):
                return str(tok["access_token"]), float(tok.get("expires_in") or 3600)
        else:
            raise LoginError("unknown sign-in kind")
    except NetGuardError as e:
        raise LoginError(str(e)) from None
    raise LoginError(f"the app did not accept the sign-in (HTTP {r.status})")


async def token_for(secret: str, origin: str, base: str, fetcher: LocalFetcher) -> str:
    key = hashlib.sha256(secret.encode()).hexdigest()
    hit = _sessions.get(key)
    if hit and hit[1] > time.time() + 30:
        return hit[0]
    b = json.loads(secret)
    token, ttl = await sign_in(b["kind"], b["fields"], origin, base, fetcher)
    _sessions[key] = (token, time.time() + ttl)
    return token
