"""Keys and tokens the user creates on a service (and the two kinds that are exchanged for short tokens: a Zoom server-to-server app and
a Google service account). Each is tried with one real request before it is saved, sealed into the vault, and never returned.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .credentials import apply_secret
from .i18n import t
from .netguard import NetGuardError, Reply, fetch
from .presets import GOOGLE, GOOGLE_SCOPES, KEYS, KeyPreset
from .store import Store
from .vault import VaultError

if TYPE_CHECKING:
    from .packages import Registry

Fetcher = Callable[..., Awaitable[Reply]]
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"     # fixed: never the token_uri written in a key file
ZOOM_TOKEN = "https://zoom.us/oauth/token"
_minted: dict[str, tuple[str, float]] = {}


class KeyError_(Exception):
    """Shown to the user as it is."""


def is_minted(secret: str | None) -> bool:
    return bool(secret) and secret.startswith("{") and ('"mark": "google-sa-v1"' in secret or '"mark": "zoom-s2s-v1"' in secret)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def _token_post(fetcher: Fetcher, url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    host = url.split("/")[2]
    try:
        r = await fetcher("POST", url, allowed_hosts=[host], headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                                                                       **(headers or {})}, content=urlencode(form).encode())
    except NetGuardError as e:
        raise KeyError_(t("Could not get a token: {error}", error=e)) from None
    try:
        body = json.loads(r.body)
    except ValueError:
        body = {}
    return r.status, body if isinstance(body, dict) else {}


def google_assertion(client_email: str, private_key: str, scopes: list[str] | tuple[str, ...], now: int | None = None) -> str:
    """The signed JWT a Google service account trades for an access token (RFC 7523; aud is always Google's own token address)."""
    now = int(time.time()) if now is None else now
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": client_email, "scope": " ".join(scopes), "aud": GOOGLE_TOKEN, "iat": now, "exp": now + 3600}).encode())
    pem = serialization.load_pem_private_key(private_key.encode(), password=None)
    if not isinstance(pem, rsa.RSAPrivateKey):
        raise KeyError_(t("The service account key is not an RSA key."))
    sig = _b64(pem.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()))
    return f"{head}.{claims}.{sig}"


async def google_sa_grant(client_email: str, private_key: str, scopes: list[str] | tuple[str, ...], fetcher: Fetcher = fetch) -> tuple[int, dict[str, Any]]:
    """(HTTP status, token answer) for a service account and the given scopes. Shared with push.py (Firebase Cloud Messaging)."""
    return await _token_post(fetcher, GOOGLE_TOKEN, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                     "assertion": google_assertion(client_email, private_key, scopes)})


async def mint(secret: str, fetcher: Fetcher = fetch) -> str:
    """A short-lived access token for a minted secret (cached until a minute before it runs out)."""
    key = hashlib.sha256(secret.encode()).hexdigest()
    hit = _minted.get(key)
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    b = json.loads(secret)
    if b["mark"] == "zoom-s2s-v1":
        basic = base64.b64encode(f"{b['client_id']}:{b['client_secret']}".encode()).decode()
        status, tok = await _token_post(fetcher, f"{ZOOM_TOKEN}?{urlencode({'grant_type': 'account_credentials', 'account_id': b['account_id']})}",
                                        {}, {"Authorization": f"Basic {basic}"})
    else:
        status, tok = await google_sa_grant(b["client_email"], b["private_key"], GOOGLE_SCOPES, fetcher)
    if status != 200 or not tok.get("access_token"):
        raise KeyError_(t("The service did not give a token ({detail}).", detail=str(tok.get('error_description') or tok.get('reason') or tok.get('error') or status)[:80]))
    token = str(tok["access_token"])
    _minted[key] = (token, time.time() + float(tok.get("expires_in") or 3600))
    return token


async def open_minted(store: Store, cred: dict[str, Any], fetcher: Fetcher = fetch) -> dict[str, Any]:
    try:
        return {**cred, "secret": await mint(cred["secret"], fetcher)}
    except (KeyError_, ValueError) as e:
        store.set_credential_result(cred["name"], "rejected", "the service did not give a token")
        raise VaultError(f"{cred['label']} did not accept its stored credentials ({e}). Ask the user to connect it again in the Connectors tab.") from None


def preset(pid: str, registry: Registry | None = None) -> KeyPreset:
    """The key form of a connector: built-in presets first, then an installed key package (packages.py)."""
    if pid == GOOGLE.id:
        return GOOGLE
    if pid in KEYS:
        return KEYS[pid]
    got = registry.key_preset(pid) if registry is not None else None
    if got is None:
        raise KeyError_(t("This service cannot be connected with a key."))
    return got


def _one_line(value: str, what: str, *, lo: int = 8, hi: int = 4096) -> str:
    v = (value or "").strip()
    if not (lo <= len(v) <= hi) or any(c.isspace() for c in v) or not v.isprintable():
        raise KeyError_(t("Please check the {what}. It must be one line with no spaces.", what=what))
    return v


class KeyConnections:
    def __init__(self, store: Store, *, fetcher: Fetcher = fetch, registry: Registry | None = None):
        self.store, self.fetcher, self.registry = store, fetcher, registry

    def build(self, p: KeyPreset, values: dict[str, str]) -> tuple[list[str], dict[str, Any], str, str]:
        """(hosts, placement, secret, what to show the user afterwards)"""
        hosts, placement, shown = list(p.hosts), dict(p.placement), ""
        if p.mint == "google":
            try:
                key = json.loads(values.get("key", ""))
            except ValueError:
                raise KeyError_(t("Please paste the contents of the JSON file as they are.")) from None
            if not isinstance(key, dict) or key.get("type") != "service_account" or not key.get("client_email") or "PRIVATE KEY" not in str(key.get("private_key", "")):
                raise KeyError_(t("This is not a service account key (JSON). It must be a file with 'type': 'service_account'."))
            email = str(key["client_email"])
            if not re.fullmatch(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", email):
                raise KeyError_(t("The service account address looks wrong. Please check that the file came from Google Cloud."))
            return hosts, placement, json.dumps({"mark": "google-sa-v1", "client_email": email, "private_key": str(key["private_key"])}), email
        if p.mint == "zoom":
            acct = _one_line(values.get("account_id", ""), "Account ID", lo=3, hi=100)
            cid = _one_line(values.get("client_id", ""), "Client ID", lo=3, hi=200)
            sec = _one_line(values.get("client_secret", ""), "Client secret")
            return hosts, placement, json.dumps({"mark": "zoom-s2s-v1", "account_id": acct, "client_id": cid, "client_secret": sec}), ""
        if p.store_host:
            store = re.sub(r"^https?://", "", (values.get("store") or "").strip().lower()).rstrip("/")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com", store):
                raise KeyError_(t("Enter the store address like mystore.myshopify.com."))
            hosts, shown = [store], store
        return hosts, placement, _one_line(values.get("token", ""), t("key")), shown

    async def connect(self, pid: str, values: dict[str, str]) -> dict[str, Any]:
        p = preset(pid, self.registry)
        hosts, placement, secret, shown = self.build(p, values)
        token = secret
        if p.mint:
            try:
                token = await mint(secret, self.fetcher)
            except (KeyError_, ValueError) as e:
                raise KeyError_(t("{name} did not accept this information. Nothing was saved. ({error})", name=p.name, error=e)) from None
        checked = False
        if p.verify:
            url, auth = apply_secret(p.verify.format(store=hosts[0]), {k: v for k, v in placement.items() if k != "fixed_headers"}, token)
            try:
                r = await self.fetcher("GET", url, allowed_hosts=hosts, headers={"Accept": "application/json", **placement.get("fixed_headers", {}), **auth})
            except NetGuardError as e:
                raise KeyError_(t("Could not connect to {name}: {error}", name=p.name, error=e)) from None
            ok = r.status == 200
            if ok and p.verify_json_ok:
                try:
                    ok = bool(json.loads(r.body).get("ok"))
                except ValueError:
                    ok = False
            if not ok:
                raise KeyError_(t("{name} did not accept this key (HTTP {status}). Nothing was saved. Please check the key and its permissions.", name=p.name, status=r.status))
            checked = True
        label = f"{p.name} · {shown}" if shown else p.name
        old = self.store.credential(p.item)
        if old and old["kind"] != "api":
            raise KeyError_(t("The vault has another item named '{item}'. Delete that item and try again.", item=p.item))
        self.store.delete_credential(p.item)
        self.store.save_credential(p.item, label, hosts, placement, p.endpoint, kind="api")
        self.store.set_credential_secret(p.item, secret)
        if checked or p.mint:
            self.store.set_credential_result(p.item, "ok", "checked when it was added")
        self.store.add_vault_log(p.item, "saved", f"{p.name}, {'checked with a real request' if checked or p.mint else 'not tried yet'}")
        return {"item": p.item, "label": label, "checked": checked or bool(p.mint), "account": shown}
