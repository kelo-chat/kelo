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
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import local_login
from .credentials import apply_secret
from .i18n import t
from .netguard import NetGuardError, Reply, fetch, fetch_local, parse_local_address
from .presets import GOOGLE, GOOGLE_SCOPES, KEYS, KeyPreset
from .store import Store
from .vault import VaultError

if TYPE_CHECKING:
    from .packages import Registry
    from .vpn import VpnRoute

Fetcher = Callable[..., Awaitable[Reply]]
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"     # fixed: never the token_uri written in a key file
ZOOM_TOKEN = "https://zoom.us/oauth/token"
_minted: dict[str, tuple[str, float]] = {}


class KeyError_(Exception):
    """Shown to the user as it is."""


def is_minted(secret: str | None) -> bool:
    return bool(secret) and secret.startswith("{") and ('"mark": "google-sa-v1"' in secret or '"mark": "zoom-s2s-v1"' in secret or '"mark": "slack-v1"' in secret)


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
    if '"mark": "slack-v1"' in secret:
        token = str(json.loads(secret).get("user") or "")   # Slack: the owner's own token, nothing to exchange
        if not token.startswith("xoxp-"):
            raise KeyError_(t("The Slack user token is missing."))
        return token
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
    def __init__(self, store: Store, *, fetcher: Fetcher = fetch, registry: Registry | None = None, local_fetcher: Fetcher = fetch_local,
                 vpn: VpnRoute | None = None, keyring: Any = None):
        self.store, self.fetcher, self.registry, self.local_fetcher = store, fetcher, registry, local_fetcher
        self.vpn = vpn          # the VPN / Proxy connectors set the route of Kelo's web browsing (vpn.py)
        self.keyring = keyring

    async def connect_route(self, p: KeyPreset, values: dict[str, str], force: bool) -> dict[str, Any]:
        """"VPN (WireGuard)" and "Proxy": checked for form, sealed in the vault, and made the route of Kelo's web browsing. It is made at once,
        or when the browser is next quiet (`force` makes it at once); the answer says which."""
        from .vpn import VpnError
        if self.vpn is None or not self.vpn.available:
            raise KeyError_(t("This installation has no VPN support. Update it (the vpn container is part of the newer compose file)."))
        try:
            if p.route == "wireguard":
                res = await self.vpn.connect_wireguard(values.get("config", ""), force=force)
            else:
                res = await self.vpn.connect_proxy(values.get("address", ""), values.get("user", ""), values.get("password", ""), force=force)
        except VpnError as e:
            raise KeyError_(str(e)) from None
        cred = self.store.credential(p.item) or {}
        out: dict[str, Any] = {"item": p.item, "label": cred.get("label") or p.name, "checked": False, "account": res["account"],
                               "vpn": {"state": res["state"], "message": res["message"]}}
        if res["state"] == "applied":
            out["vpn"]["connected"] = await self.vpn.wait_connected()
        return out

    def build(self, p: KeyPreset, values: dict[str, str]) -> tuple[list[str], dict[str, Any], str, str]:
        """(hosts, placement, secret, what to show the user afterwards)"""
        hosts, placement, shown = list(p.hosts), dict(p.placement), ""
        if p.local:
            return self.build_local(p, values)
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
        if p.mint == "slack":
            user = _one_line(values.get("token", ""), t("key"))
            bot = _one_line(values.get("bot", ""), t("key"))
            app = _one_line(values.get("app", ""), t("key"))
            if not (user.startswith("xoxp-") and bot.startswith("xoxb-") and app.startswith("xapp-")):
                raise KeyError_(t("Paste the three Slack tokens in their own boxes: the user token starts with xoxp-, the bot token with xoxb-, the app-level token with xapp-."))
            return hosts, placement, json.dumps({"mark": "slack-v1", "user": user, "bot": bot, "app": app}), ""
        if p.store_host:
            store = re.sub(r"^https?://", "", (values.get("store") or "").strip().lower()).rstrip("/")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com", store):
                raise KeyError_(t("Enter the store address like mystore.myshopify.com."))
            hosts, shown = [store], store
        return hosts, placement, _one_line(values.get("token", ""), t("key")), shown

    def build_local(self, p: KeyPreset, values: dict[str, str]) -> tuple[list[str], dict[str, Any], str, str]:
        """An app on the owner's network: the address they typed becomes the origin kept in the placement (only this form writes it)."""
        placement = dict(p.placement)
        try:
            origin, base, _ = parse_local_address(values.get(p.endpoint_field or "address", ""))
            if p.endpoint_field:   # n8n: the whole MCP URL is typed; the origin is its address, the path is the endpoint
                parts = urlsplit(values[p.endpoint_field].strip() if "://" in values[p.endpoint_field] else "http://" + values[p.endpoint_field].strip())
                if not parts.path.strip("/"):
                    raise NetGuardError("the address must be the full URL, with its path")
                base = ""
        except NetGuardError as e:
            raise KeyError_(t("Please check the server address: {error}", error=e)) from None
        placement.update(origin=origin, **({"base": base} if base else {}), **({"device": True} if p.device else {}))
        shown = urlsplit(origin).netloc
        f = {k: (values.get(k) or "").strip() for k in ("username", "password", "client_id", "client_secret", "token")}
        if p.login:
            need = [x.id for x in p.fields if x.id != "address"]
            for k in need:   # passwords may hold spaces: one printable line is all that is asked
                if not (1 <= len(f[k]) <= 512) or not f[k].isprintable():
                    raise KeyError_(t("Please check the {what}. It must be one line with no spaces.", what=k.replace("_", " ")))
            return [shown], placement, local_login.bundle(p.login, {k: f[k] for k in need}), shown
        if placement["type"] == "basic_auth":
            if "username" in [x.id for x in p.fields]:
                if not f["username"] or ":" in f["username"] or not f["username"].isprintable() or len(f["username"]) > 100:
                    raise KeyError_(t("Please check the {what}. It must be one line with no spaces.", what=t("user name")))
                placement["user"] = f["username"]
            secret = f["password"] or f["token"]
            if not (1 <= len(secret) <= 512) or not secret.isprintable():
                raise KeyError_(t("Please check the {what}. It must be one line with no spaces.", what=t("key")))
            return [shown], placement, secret, shown
        if placement["type"] == "none":
            return [shown], placement, "no-key-needed", shown
        return [shown], placement, _one_line(f["token"], t("key")), shown

    async def verify_local(self, p: KeyPreset, placement: dict[str, Any], secret: str, endpoint: str) -> bool:
        """The check of an app on the network: sign in (when it takes a password), then the preset's GET path; an MCP-only preset
        is checked with tools/list. Raises KeyError_ when the app refuses. False when the preset has nothing to test."""
        origin, base = placement["origin"], placement.get("base", "")
        token = secret
        try:
            if p.login:
                token = await local_login.token_for(secret, origin, base, self.local_fetcher)
            if p.endpoint_field:
                from .tools.connect_tools import mcp_session_call   # here: connect_tools imports this module's neighbours
                async def via(method: str, url: str, **kw: Any) -> Reply:
                    return await self.local_fetcher(method, url, origin=origin, headers=kw.get("headers"), content=kw.get("content"))
                await mcp_session_call(via, {"endpoint": endpoint, "placement": placement, "secret": token, "hosts": []}, "tools/list", {})
                return True
            if not p.verify:
                return False
            plain = {k: v for k, v in placement.items() if k in ("type", "header", "prefix", "param", "user")}
            url, auth = apply_secret(f"{origin}{base}{p.verify}", plain, token)
            r = await self.local_fetcher("GET", url, origin=origin, headers={"Accept": "application/json", **placement.get("fixed_headers", {}), **auth})
        except (NetGuardError, local_login.LoginError) as e:
            raise KeyError_(t("Could not connect to {name}: {error}", name=p.name, error=e)) from None
        except Exception as e:   # noqa: BLE001 - an MCP answer that is not what was expected
            if type(e).__name__ != "McpError":
                raise
            raise KeyError_(t("Could not connect to {name}: {error}", name=p.name, error=e)) from None
        if r.status != 200:
            raise KeyError_(t("{name} did not accept this key (HTTP {status}). Nothing was saved. Please check the key and its permissions.", name=p.name, status=r.status))
        return True

    async def connect(self, pid: str, values: dict[str, str], force: bool = False) -> dict[str, Any]:
        p = preset(pid, self.registry)
        if p.route:
            return await self.connect_route(p, values, force)
        hosts, placement, secret, shown = self.build(p, values)
        if p.local:
            endpoint = ""
            if p.endpoint_field:
                o, _, _ = parse_local_address(values[p.endpoint_field])
                u = urlsplit(values[p.endpoint_field].strip() if "://" in values[p.endpoint_field] else "http://" + values[p.endpoint_field].strip())
                endpoint = f"{o}{u.path}"
            elif p.endpoint:
                endpoint = f"{placement['origin']}{placement.get('base', '')}{p.endpoint}"
            checked = await self.verify_local(p, placement, secret, endpoint)
            label = f"{p.name} · {shown}"
            old = self.store.credential(p.item)
            if old and old["kind"] != "api":
                raise KeyError_(t("The vault has another item named '{item}'. Delete that item and try again.", item=p.item))
            self.store.delete_credential(p.item)
            self.store.save_credential(p.item, label, hosts, placement, endpoint, kind="api")
            self.store.set_credential_secret(p.item, secret)
            if checked:
                self.store.set_credential_result(p.item, "ok", "checked when it was added")
            self.store.add_vault_log(p.item, "saved", f"{p.name} at {shown}, {'checked with a real request' if checked else 'not tried yet'}")
            from .cred_keyring import spawn_sync
            spawn_sync(self.store, self.keyring)
            return {"item": p.item, "label": label, "checked": checked, "account": shown}
        token = secret
        if p.mint:
            try:
                token = await mint(secret, self.fetcher)
            except (KeyError_, ValueError) as e:
                raise KeyError_(t("{name} did not accept this information. Nothing was saved. ({error})", name=p.name, error=e)) from None
        checked = False
        if p.verify:
            url, auth = apply_secret(p.verify.replace("{store}", hosts[0]), {k: v for k, v in placement.items() if k != "fixed_headers"}, token)
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
        from .cred_keyring import spawn_sync
        spawn_sync(self.store, self.keyring)
        return {"item": p.item, "label": label, "checked": checked or bool(p.mint), "account": shown}
