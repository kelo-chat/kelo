"""Push notifications to the Kelo phone app through Firebase Cloud Messaging (FCM HTTP v1).

Who holds what:
  - Only the operator's server (the broker, the same one that brokers sign-ins and Kelo addresses) holds the Firebase
    service-account key of the Kelo project. It is uploaded once with any signed-in device (PUT /api/operator/firebase), checked by
    minting one OAuth token, stored encrypted (store.set_key) and never returned.
  - Every copy of Kelo keeps the FCM token of each of its paired phones (POST /api/push/register from the phone). When the copy
    makes a notification that nobody saw arrive (activity.py), it asks the broker to wake its phones: POST /broker/push/send.
  - The operator's own copy, which holds the key, sends straight to FCM (no HTTP hop to itself).

Privacy: a push carries only ids, never words: {"type", "id", "task_id"?, "approval_id"?, "schedule_id"?} (FCM data message, all
values strings, no notification title or body). The phone wakes up, asks its own server GET /api/notifications for the details,
and shows them. The broker therefore never learns what a copy's assistant did, only that something happened. FCM tokens and data
payloads never reach a log line (only notification types and counts do) and no answer ever echoes a token back.

Live Activities (live_activity.py): while a reply runs, the iPhone's lock screen and Dynamic Island show its step. These updates are
the one place words travel, and they are sealed for the phone: the words are encrypted with a key only the phone holds (given to its
own server once, with the activity's push token), so Apple and the broker see ciphertext and a step number only (check_live,
apns_live_payload, PushBroker.handle_live).

A copy proves who it is with its own random push install secret (made on first use, kept encrypted in its own database); the
broker keeps only its SHA-256 (the same scheme as kelo_names.py) and registers it on first use. Rate limits on the broker: per
install PER_INSTALL_HOUR requests an hour, per address PER_IP_HOUR, at most NEW_INSTALLS_PER_IP_DAY new installs per address a
day, and at most MAX_TOKENS tokens per request.

FCM (checked against firebase.google.com/docs/reference/fcm/rest/v1 and /docs/cloud-messaging/error-codes, 2026-11):
  OAuth scope https://www.googleapis.com/auth/firebase.messaging (a service-account JWT traded at oauth2.googleapis.com/token)
  POST https://fcm.googleapis.com/v1/projects/{project_id}/messages:send
       {"message": {"token": "...", "data": {"k": "v", ...}, "android": {"priority": "HIGH"}}}   -> 200 Message {"name": ...}
  error -> {"error": {"code", "message", "status", "details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
            "errorCode": UNREGISTERED|INVALID_ARGUMENT|SENDER_ID_MISMATCH|QUOTA_EXCEEDED|UNAVAILABLE|INTERNAL|...}]}}
  UNREGISTERED (404) and INVALID_ARGUMENT (400) mean the token is no good: reported as {"invalid": true} so the copy drops it.
  UNAVAILABLE (503) and INTERNAL (500) are retried with backoff (Retry-After honoured up to a few seconds); QUOTA_EXCEEDED (429)
  needs a minute or more and is not retried here. A 401 means the cached OAuth token was refused: minted again once.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .keyconnect import KeyError_, google_sa_grant
from .netguard import NetGuardError, Reply, fetch
from .store import Store

log = logging.getLogger("uvicorn.error")
Fetcher = Callable[..., Awaitable[Reply]]

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_HOST = "fcm.googleapis.com"
SA_KEY = "operator:firebase"            # the whole service-account JSON, encrypted like the AI service keys; never returned
INSTALL_KEY = "push:install_id"         # this copy's own secret towards the broker (only its hash leaves this server)
NOTE_TYPES = ("task_done", "task_failed", "approval_waiting", "payment_auto", "schedule_disabled", "schedule_paused", "watch_fired",
              "watch_disabled", "proactive", "followup", "device_task", "storage",
              "beta_request", "cli_update", "server_update", "keyguard_locked",
              "follow_change", "follow_done", "follow_problem", "follow_reminder", "nest_shared")   # beta_request: a beta or tester sign-up for the operator
# (beta.py, testers.py). Every type a notification is made with must be here or in WIRE_TYPES, or its push is refused
# (test_every_notification_type_can_be_pushed); the apps then show it with the server's own title.   # device_task: a phone has work waiting (phone_data.py)
DATA_KEYS = ("type", "id", "task_id", "approval_id", "schedule_id", "ids")
SETTLED = "settled"                      # a push of its own kind: these waiting items (approval ids) do not wait any more; the phones take
SETTLED_MAX = 10                         # their notifications down. `ids` = up to this many item ids, comma-separated; `id` = the first one
SETTLED_DELAY = 1.0                      # seconds settles are collected, so several that settle together go out as one push
WIRE_TYPES = {"briefing": "proactive",   # the morning briefing (briefing.py) wakes the phones like a proactive message
              "operator_busy": "storage", "operator_pool": "storage"}   # the Kelo address breaker tripped (kelo_names.py): a notice about the server, like storage
PLATFORMS = ("android", "ios-apns")      # ios-apns: the iPhone app's own APNs device token (hex), no Firebase SDK in the app
APNS_HOST = "api.push.apple.com"
APNS_SANDBOX_HOST = "api.sandbox.push.apple.com"
APNS_ENVIRONMENTS = ("production", "development")   # the app's aps-environment: TestFlight and the App Store are production
APNS_TOKEN_RE = re.compile(r"^[0-9a-f]{64,200}$")
APNS_JWT_SECONDS = 40 * 60               # Apple wants a provider token refreshed between every 20 and 60 minutes
APNS_LOC_KEY = "kelo_push_update"        # the app's own Localizable.strings entry ("Kelo has an update", in the phone's language)
LIVE_STALE_SECONDS = 300                 # a Live Activity update is stale after 5 minutes (the phone then shows it as old)
LIVE_DISMISS_SECONDS = 600               # an end is dismissed after 10 minutes
LIVE_SEALED_MAX = 2048                   # the base64 of the sealed words (live_activity.py): a few hundred characters of text
LIVE_PER_INSTALL_HOUR = 4000             # Live Activity updates per copy an hour (one per 5 s per reply, a few replies at once)
MAX_TOKENS = 10                          # tokens per broker request
PER_INSTALL_HOUR = 120
PER_IP_HOUR = 600
NEW_INSTALLS_PER_IP_DAY = 20
MAX_INSTALLS = 100_000
TOKEN_RE = re.compile(r"^[A-Za-z0-9_:.\-]{20,4096}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
INSTALL_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
INVALID_CODES = frozenset({"UNREGISTERED", "INVALID_ARGUMENT"})
RETRY_CODES = frozenset({"UNAVAILABLE", "INTERNAL", "UNREACHABLE"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS push_tokens (
    device_id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, platform TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    environment TEXT NOT NULL DEFAULT 'production');
CREATE TABLE IF NOT EXISTS push_installs (
    owner TEXT PRIMARY KEY, created_at REAL NOT NULL, last_used REAL NOT NULL, requests INTEGER NOT NULL DEFAULT 0);
"""


class PushError(Exception):
    """Shown to the caller as it is (never contains a key or a token); `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def _schema(store: Store) -> None:
    with store._lock:
        store._db.executescript(SCHEMA)
        if "environment" not in {r[1] for r in store._db.execute("PRAGMA table_info(push_tokens)")}:   # a database from before the iPhone
            store._db.execute("ALTER TABLE push_tokens ADD COLUMN environment TEXT NOT NULL DEFAULT 'production'")
        store._db.commit()


# ---------------------------------------------------------------------------------------------------------------- the key file
def parse_service_account(raw: bytes | str) -> dict[str, Any]:
    """The Firebase service-account key file, checked. Accepts the file itself, or {"service_account": <the file or its text>}.
    Error messages name the field that is wrong, never its value."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise PushError("send the service-account key file itself (JSON), as downloaded from the Firebase console") from None
    if isinstance(data, dict) and "service_account" in data and data.get("type") != "service_account":
        inner = data["service_account"]
        try:
            data = json.loads(inner) if isinstance(inner, str) else inner
        except ValueError:
            raise PushError("service_account must be the key file's JSON") from None
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise PushError('this is not a service-account key: "type" must be "service_account"')
    project = data.get("project_id")
    if not isinstance(project, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise PushError("project_id is missing or not a Firebase project id")
    email = data.get("client_email")
    if not isinstance(email, str) or not re.fullmatch(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", email):
        raise PushError("client_email is missing or not a service-account address (…@….iam.gserviceaccount.com)")
    pem = data.get("private_key")
    if not isinstance(pem, str) or "PRIVATE KEY-----" not in pem:
        raise PushError("private_key is missing")
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        raise PushError("private_key could not be read; download a new key file") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise PushError("private_key is not an RSA key")
    return data


def check_data(data: Any) -> dict[str, str]:
    """The only payload a push may carry: ids, never text. Anything else is refused (on the copy and again on the broker)."""
    if not isinstance(data, dict):
        raise PushError("data must be an object")
    out: dict[str, str] = {}
    for k, v in data.items():
        if k not in DATA_KEYS:
            raise PushError(f"data may hold only {', '.join(DATA_KEYS)}")
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise PushError(f"data.{k} must be a string")
        out[k] = str(v)
    if out.get("type") not in NOTE_TYPES + (SETTLED,):
        raise PushError(f"data.type must be one of {', '.join(NOTE_TYPES + (SETTLED,))}")
    if out["type"] == SETTLED:                              # item ids only, never more than SETTLED_MAX
        ids = out.get("ids", "").split(",")
        if len(ids) > SETTLED_MAX or not all(ID_RE.match(i) for i in ids):
            raise PushError(f"data.ids must be 1 to {SETTLED_MAX} ids (letters, digits, '-', '_') separated by commas")
        out.setdefault("id", ids[0])
    elif "ids" in out:
        raise PushError("data.ids is only for settled pushes")
    if "id" not in out:
        raise PushError("data.id is required")
    for k, v in out.items():
        if k not in ("type", "ids") and not ID_RE.match(v):
            raise PushError(f"data.{k} must be an id (letters, digits, '-', '_')")
    return out


def note_data(note: dict[str, Any]) -> dict[str, str]:
    """The push for one notification row (activity.Activity.notify)."""
    # a type newer than the push brokers out there goes as one they know (the phone reads the real type from /api/notifications)
    out = {"type": WIRE_TYPES.get(str(note["type"]), str(note["type"])), "id": str(note["id"])}
    for k in ("task_id", "approval_id", "schedule_id"):
        if note.get(k) is not None:
            out[k] = str(note[k])
    return out


def _fcm_code(r: Reply) -> str:
    try:
        err = (json.loads(r.body) or {}).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    if isinstance(err, dict):
        for d in err.get("details") or []:
            if isinstance(d, dict) and str(d.get("@type", "")).endswith("google.firebase.fcm.v1.FcmError") and d.get("errorCode"):
                return str(d["errorCode"])[:40]
        status = str(err.get("status") or "")
        if status == "NOT_FOUND" or (not status and r.status == 404):
            return "UNREGISTERED"
        if status:
            return status[:40]
    return {400: "INVALID_ARGUMENT", 429: "QUOTA_EXCEEDED", 500: "INTERNAL", 503: "UNAVAILABLE"}.get(r.status, f"HTTP_{r.status}")


def _retry_after(r: Reply) -> float | None:
    v = {k.lower(): v for k, v in (r.headers or {}).items()}.get("retry-after", "")
    try:
        return max(0.0, float(v))
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------- FCM (operator)
class Fcm:
    """Sends data messages with the operator's service account. `fetcher` is netguard.fetch (tests pass a fake FCM + OAuth)."""

    RETRIES = 2

    def __init__(self, store: Store, *, fetcher: Fetcher = fetch, backoff: float = 0.5, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time):
        self.store, self.fetcher, self.backoff, self.sleep, self.clock = store, fetcher, backoff, sleep, clock
        self._token: tuple[str, float, str] | None = None      # (OAuth access token, when to mint again, which key it belongs to)
        self._minting = asyncio.Lock()

    def account(self) -> dict[str, Any] | None:
        raw = self.store.key(SA_KEY)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def enabled(self) -> bool:
        return self.account() is not None

    def config(self) -> dict[str, Any]:
        sa = self.account() or {}
        return {"enabled": bool(sa), "project_id": sa.get("project_id", ""), "client_email": sa.get("client_email", "")}

    async def configure(self, raw: bytes | str) -> dict[str, Any]:
        """Check the key file, prove it works by minting one OAuth token (no message is sent), then save it."""
        sa = parse_service_account(raw)
        await self._mint(sa)
        self.store.set_key(SA_KEY, json.dumps(sa))
        return self.config()

    def disable(self) -> dict[str, Any]:
        self.store.delete_key(SA_KEY)
        self._token = None
        return self.config()

    @staticmethod
    def _which(sa: dict[str, Any]) -> str:
        return hashlib.sha256(f"{sa['client_email']}\n{sa['private_key']}".encode()).hexdigest()

    async def _mint(self, sa: dict[str, Any]) -> tuple[str, float]:
        try:
            status, tok = await google_sa_grant(sa["client_email"], sa["private_key"], [FCM_SCOPE], self.fetcher)
        except (KeyError_, ValueError) as e:
            raise PushError(f"Google did not give an access token for this key ({str(e)[:80]})", 502) from None
        if status != 200 or not tok.get("access_token"):
            why = str(tok.get("error_description") or tok.get("error") or f"HTTP {status}")[:80]
            raise PushError(f"Google did not accept this service-account key ({why})", 422 if status in (400, 401, 403) else 502)
        life = float(tok.get("expires_in") or 3600)
        return str(tok["access_token"]), self.clock() + max(60.0, min(life, 3600.0) - 300.0)   # ~55 minutes for a 1-hour token

    async def access_token(self, sa: dict[str, Any], *, fresh: bool = False) -> str:
        which = self._which(sa)
        async with self._minting:
            hit = self._token
            if not fresh and hit and hit[2] == which and hit[1] > self.clock():
                return hit[0]
            token, until = await self._mint(sa)
            self._token = (token, until, which)
            return token

    async def send(self, tokens: list[str], data: dict[str, str]) -> list[dict[str, Any]]:
        """One data message per token; results in the same order: {"ok", "invalid", "error"?}."""
        sa = self.account()
        if not sa:
            raise PushError("this server does not send push notifications", 404)
        bearer = await self.access_token(sa)
        url = f"https://{FCM_HOST}/v1/projects/{sa['project_id']}/messages:send"
        state = {"bearer": bearer, "refreshed": False}
        return list(await asyncio.gather(*(self._one(url, t, data, sa, state) for t in tokens)))

    async def _one(self, url: str, token: str, data: dict[str, str], sa: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            return {"ok": False, "invalid": True, "error": "INVALID_ARGUMENT"}
        body = json.dumps({"message": {"token": token, "data": data, "android": {"priority": "HIGH"}}}).encode()
        code, attempt, reauth = "UNREACHABLE", 0, False
        while True:
            wait: float | None = None
            try:
                r = await self.fetcher("POST", url, allowed_hosts=[FCM_HOST], content=body,
                                       headers={"Authorization": f"Bearer {state['bearer']}", "Content-Type": "application/json; charset=UTF-8"})
            except NetGuardError:
                code = "UNREACHABLE"
            else:
                if r.status == 200:
                    return {"ok": True, "invalid": False}
                code = _fcm_code(r)
                if r.status == 401 and not reauth:        # the OAuth token was refused (revoked, clock): mint once more, then try again
                    reauth = True
                    if not state["refreshed"]:
                        state["refreshed"] = True
                        state["bearer"] = await self.access_token(sa, fresh=True)
                    continue
                if code in INVALID_CODES:
                    return {"ok": False, "invalid": True, "error": code}
                if code not in RETRY_CODES:
                    return {"ok": False, "invalid": False, "error": code}
                wait = _retry_after(r)
            if attempt >= self.RETRIES:
                return {"ok": False, "invalid": False, "error": code}
            await self.sleep(min(wait if wait is not None else self.backoff * 2 ** attempt, 10.0))
            attempt += 1


# ---------------------------------------------------------------------------------------------------------------- APNs (operator)
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _apns_reason(r: Reply) -> str:
    try:
        reason = (json.loads(r.body) or {}).get("reason")
    except (ValueError, AttributeError):
        reason = None
    return re.sub(r"[^A-Za-z0-9]", "", str(reason))[:40] if reason else f"HTTP_{r.status}"


# An approval push names the Deny-only category: the push carries ids only, so it cannot know whether Allow may be offered (a payment's
# Allow needs the phone's signature in the app). The app, woken by content-available, fetches the pending item and re-posts the
# notification with "kelo.approval" (Allow + Deny) when its notify_actions offer "allow" (fail closed: never an Allow by default).
APNS_CATEGORIES = {"approval_waiting": "kelo.approval.deny",   # the app registers these (KeloUI NotificationTaps)
                   "task_done": "kelo.reply", "proactive": "kelo.reply"}   # an assistant's answer: a Reply field (Android replyable types)


def apns_payload(data: dict[str, str]) -> bytes:
    """What the iPhone gets: a generic alert the phone words itself (loc-key, so no text of ours or of the user's travels through
    Apple), content-available (the app may fetch the details from its own server when iOS lets it) and the ids. Nothing personal.
    An approval or an answer also names its notification category (the buttons under it) and is mutable (the app may rewrite it)."""
    body: dict[str, Any] = {"aps": {"alert": {"loc-key": APNS_LOC_KEY}, "sound": "default", "content-available": 1}}
    if data.get("type") == SETTLED:   # nothing to show: a silent background push that only lets the app take notifications down
        body = {"aps": {"content-available": 1}}
    elif data.get("type") in APNS_CATEGORIES:
        body["aps"].update({"category": APNS_CATEGORIES[data["type"]], "mutable-content": 1})
    body.update(data)
    return json.dumps(body, separators=(",", ":")).encode()


def check_live(content: Any, event: Any) -> tuple[dict[str, Any], str]:
    """A Live Activity update may carry only {"v": 1, "n": int, "sealed": base64} (the words are sealed for the phone)."""
    if event not in ("update", "end"):
        raise PushError("event must be update or end")
    if not isinstance(content, dict) or set(content) != {"v", "n", "sealed"} or content.get("v") != 1:
        raise PushError("content must be {v: 1, n, sealed}")
    n, sealed = content.get("n"), content.get("sealed")
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 100_000:
        raise PushError("n must be a whole number")
    if not isinstance(sealed, str) or not sealed or len(sealed) > LIVE_SEALED_MAX or not re.fullmatch(r"[A-Za-z0-9+/=]+", sealed):
        raise PushError("sealed must be base64")
    return {"v": 1, "n": n, "sealed": sealed}, event


def apns_live_payload(content: dict[str, Any], event: str, ts: int) -> bytes:
    aps: dict[str, Any] = {"timestamp": ts, "event": event, "content-state": content}
    if event == "end":
        aps["dismissal-date"] = ts + LIVE_DISMISS_SECONDS
    else:
        aps["stale-date"] = ts + LIVE_STALE_SECONDS
    return json.dumps({"aps": aps}, separators=(",", ":")).encode()


class Apns:
    """Sends pushes straight to Apple's push service (HTTP/2) with the operator's APNs auth key (.p8). The key never enters the
    database or an answer: only its FILE PATH, key id, team id and topic (the app's bundle id) are read from the environment
    (KELO_APNS_KEY_FILE, KELO_APNS_KEY_ID, KELO_APNS_TEAM_ID, KELO_APNS_TOPIC; the topic defaults to chat.kelo.app). Without them
    `enabled()` is False and iPhones simply keep their polling path. `fetcher` is netguard.fetch (tests pass a fake APNs)."""

    RETRIES = 2
    DEFAULT_TOPIC = "chat.kelo.app"

    def __init__(self, *, fetcher: Fetcher = fetch, env: Callable[[], Any] = lambda: os.environ, backoff: float = 0.5,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, clock: Callable[[], float] = time.time):
        self.fetcher, self.env, self.backoff, self.sleep, self.clock = fetcher, env, backoff, sleep, clock
        self._jwt: tuple[str, float, str] | None = None          # (provider token, mint again after, which key/team it belongs to)

    def settings(self) -> dict[str, str] | None:
        e = self.env()
        key_file, key_id, team_id = (str(e.get(k, "")).strip() for k in ("KELO_APNS_KEY_FILE", "KELO_APNS_KEY_ID", "KELO_APNS_TEAM_ID"))
        if not (key_file and key_id and team_id):
            return None
        topic = str(e.get("KELO_APNS_TOPIC", "")).strip() or self.DEFAULT_TOPIC
        return {"key_file": key_file, "key_id": key_id, "team_id": team_id, "topic": topic}

    def enabled(self) -> bool:
        s = self.settings()
        return s is not None and Path(s["key_file"]).is_file()

    def _mint(self, s: dict[str, str]) -> str:
        try:
            key = serialization.load_pem_private_key(Path(s["key_file"]).read_bytes(), password=None)
        except (OSError, ValueError, TypeError):
            raise PushError("the APNs key file could not be read", 502) from None
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise PushError("the APNs key file is not an ES256 key", 502)
        head = _b64(json.dumps({"alg": "ES256", "kid": s["key_id"]}, separators=(",", ":")).encode())
        claims = _b64(json.dumps({"iss": s["team_id"], "iat": int(self.clock())}, separators=(",", ":")).encode())
        r, sig_s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
        return f"{head}.{claims}.{_b64(r.to_bytes(32, 'big') + sig_s.to_bytes(32, 'big'))}"

    def provider_token(self, s: dict[str, str], *, fresh: bool = False) -> str:
        which = f"{s['key_file']}\n{s['key_id']}\n{s['team_id']}"
        hit = self._jwt
        if not fresh and hit and hit[2] == which and hit[1] > self.clock():
            return hit[0]
        token = self._mint(s)
        self._jwt = (token, self.clock() + APNS_JWT_SECONDS, which)
        return token

    async def send(self, items: list[tuple[str, str]], data: dict[str, str]) -> list[dict[str, Any]]:
        """One push per (device token, environment); results in the same order: {"ok", "invalid", "error"?}."""
        s = self.settings()
        if s is None or not Path(s["key_file"]).is_file():
            raise PushError("this server does not send iPhone push notifications", 404)
        state = {"jwt": self.provider_token(s), "refreshed": False}
        body = apns_payload(data)
        if data["type"] == SETTLED:   # silent: push type background, priority 5, never collapsed with a visible notification
            return list(await asyncio.gather(*(self._one(s, t, env, body, state, push_type="background", priority="5") for t, env in items)))
        collapse = f"kelo-{data['id']}"[:64]
        return list(await asyncio.gather(*(self._one(s, t, env, body, state, collapse=collapse) for t, env in items)))

    async def send_live(self, token: str, env: str, content: dict[str, Any], event: str, ts: int) -> dict[str, Any]:
        """One Live Activity push (topic <bundle id>.push-type.liveactivity; an end at priority 10, updates at 5)."""
        s = self.settings()
        if s is None or not Path(s["key_file"]).is_file():
            raise PushError("this server does not send iPhone push notifications", 404)
        state = {"jwt": self.provider_token(s), "refreshed": False}
        return await self._one(s, token.lower(), env, apns_live_payload(content, event, ts), state, push_type="liveactivity",
                               topic=f"{s['topic']}.push-type.liveactivity", priority="10" if event == "end" else "5")

    async def _one(self, s: dict[str, str], token: str, env: str, body: bytes, state: dict[str, Any], *, push_type: str = "alert",
                   topic: str | None = None, priority: str = "10", collapse: str | None = None) -> dict[str, Any]:
        if not isinstance(token, str) or not APNS_TOKEN_RE.match(token):
            return {"ok": False, "invalid": True, "error": "BadDeviceToken"}
        host = APNS_SANDBOX_HOST if env == "development" else APNS_HOST
        url = f"https://{host}/3/device/{token}"
        code, attempt, reauth = "UNREACHABLE", 0, False
        while True:
            wait: float | None = None
            headers = {"authorization": f"bearer {state['jwt']}", "apns-topic": topic or s["topic"], "apns-push-type": push_type,
                       "apns-priority": priority, "apns-expiration": str(int(self.clock()) + 86400), "content-type": "application/json"}
            if collapse is not None:
                headers["apns-collapse-id"] = collapse
            try:
                r = await self.fetcher("POST", url, allowed_hosts=[host], content=body, headers=headers, http2=True)
            except NetGuardError:
                code = "UNREACHABLE"
            else:
                if r.status == 200:
                    return {"ok": True, "invalid": False}
                code = _apns_reason(r)
                if r.status == 403 and code == "ExpiredProviderToken" and not reauth:   # the clock or the cache: mint once more
                    reauth = True
                    if not state["refreshed"]:
                        state["refreshed"] = True
                        state["jwt"] = self.provider_token(s, fresh=True)
                    continue
                if r.status == 410 or (r.status == 400 and code == "BadDeviceToken"):    # the token is gone (app removed) or not for this environment
                    return {"ok": False, "invalid": True, "error": code}
                if r.status not in (429, 500, 503):
                    return {"ok": False, "invalid": False, "error": code}
                wait = _retry_after(r)
            if attempt >= self.RETRIES:
                return {"ok": False, "invalid": False, "error": code}
            await self.sleep(min(wait if wait is not None else self.backoff * 2 ** attempt, 10.0))
            attempt += 1


# ---------------------------------------------------------------------------------------------------------------- the broker
class PushBroker:
    """POST /broker/push/send on the operator's server: who may ask, how often, and what a push may carry."""

    def __init__(self, store: Store, fcm: Fcm, apns: Apns | None = None, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        _schema(store)
        self.store, self.fcm, self.apns, self.clock, self.wall = store, fcm, apns, clock, wall
        self.hits: dict[str, deque[float]] = {}

    def _gate(self, key: str, limit: int, window: float, what: str) -> None:
        now = self.clock()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise PushError(f"too many {what}; try again later", 429, int(window - (now - q[0])) + 1)
        q.append(now)
        if len(self.hits) > 20000:
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 86400]:
                self.hits.pop(k, None)

    def _install(self, install_id: Any, ip: str) -> str:
        if not isinstance(install_id, str) or not INSTALL_RE.match(install_id):
            raise PushError("install_id must be the copy's own random secret (32-128 letters, digits, '-' or '_')", 403)
        owner = hashlib.sha256(install_id.encode()).hexdigest()
        rows = self.store._all("SELECT owner FROM push_installs WHERE owner = ?", (owner,))
        if not rows:                                         # first use: register it, a few per address a day
            if int(self.store._all("SELECT COUNT(*) AS n FROM push_installs")[0]["n"]) >= MAX_INSTALLS:
                raise PushError("this push service is full", 503)
            self._gate(f"new:{ip}", NEW_INSTALLS_PER_IP_DAY, 86400, "new copies from one address")
            self.store._run("INSERT OR IGNORE INTO push_installs(owner, created_at, last_used) VALUES(?, ?, ?)", (owner, self.wall(), self.wall()))
        return owner

    def enabled(self) -> bool:
        return self.fcm.enabled() or (self.apns is not None and self.apns.enabled())

    async def handle(self, body: Any, ip: str) -> dict[str, Any]:
        if not self.enabled():
            raise PushError("this server does not send push notifications", 404)
        self._gate(f"ip:{ip}", PER_IP_HOUR, 3600, "push requests from one address")
        if not isinstance(body, dict):
            raise PushError("send {install_id, tokens, apns, data}")
        tokens, phones = body.get("tokens", []), body.get("apns", [])
        if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
            raise PushError("tokens must be a list of strings")
        if not isinstance(phones, list) or not all(isinstance(p, dict) and isinstance(p.get("token"), str) for p in phones):
            raise PushError("apns must be a list of {token, environment}")
        if not tokens and not phones:
            raise PushError("tokens must be a non-empty list of strings")
        if len(tokens) > MAX_TOKENS or len(phones) > MAX_TOKENS:
            raise PushError(f"at most {MAX_TOKENS} tokens per request")
        data = check_data(body.get("data"))
        owner = self._install(body.get("install_id"), ip)
        self._gate(f"install:{owner}", PER_INSTALL_HOUR, 3600, "push requests from this copy")
        self.store._run("UPDATE push_installs SET last_used = ?, requests = requests + 1 WHERE owner = ?", (self.wall(), owner))
        off = {"ok": False, "invalid": False, "error": "DISABLED"}      # this broker has no key for that service: nothing is dropped
        results = (await self.fcm.send(tokens, data) if self.fcm.enabled() else [dict(off) for _ in tokens]) if tokens else []
        out: dict[str, Any] = {"results": results}
        if phones:
            items = [(str(p["token"]).lower(), "development" if p.get("environment") == "development" else "production") for p in phones]
            out["apns_results"] = [dict(off) for _ in items]
            if self.apns is not None and self.apns.enabled():
                try:
                    out["apns_results"] = await self.apns.send(items, data)
                except PushError as e:                   # an unreadable key: the Android pushes of this request still go out
                    log.warning("push broker: APNs not used (%s)", e)
        everyone = results + out.get("apns_results", [])
        log.info("push broker: %s to %d device(s), %d delivered, %d invalid", data["type"], len(everyone),
                 sum(1 for r in everyone if r["ok"]), sum(1 for r in everyone if r["invalid"]))
        return out

    async def handle_live(self, body: Any, ip: str) -> dict[str, Any]:
        """One Live Activity update for one iPhone (the words inside are sealed: only the phone can open them)."""
        if not self.enabled():
            raise PushError("this server does not send push notifications", 404)
        # its own budget per address: a long reply's updates never use up the one the notifications need
        self._gate(f"live-ip:{ip}", LIVE_PER_INSTALL_HOUR, 3600, "live activity updates from one address")
        if not isinstance(body, dict):
            raise PushError("send {install_id, token, environment, content, event, timestamp}")
        token, env = body.get("token"), body.get("environment")
        if not isinstance(token, str) or not APNS_TOKEN_RE.match(token):
            raise PushError("token must be an iPhone push token (hex)")
        if env not in APNS_ENVIRONMENTS:
            raise PushError(f"environment must be one of {', '.join(APNS_ENVIRONMENTS)}")
        content, event = check_live(body.get("content"), body.get("event"))
        ts = body.get("timestamp")
        if not isinstance(ts, int) or isinstance(ts, bool) or abs(ts - self.wall()) > 86400:
            raise PushError("timestamp must be a whole number of seconds within a day of now")
        if self.apns is None or not self.apns.enabled():
            raise PushError("this server does not send iPhone push notifications", 404)
        owner = self._install(body.get("install_id"), ip)
        self._gate(f"live:{owner}", LIVE_PER_INSTALL_HOUR, 3600, "live activity updates from this copy")
        result = await self.apns.send_live(token, env, content, event, ts)
        log.info("push broker: live activity %s %s", event, "delivered" if result.get("ok") else ("invalid" if result.get("invalid") else "not sent"))
        return result


# ---------------------------------------------------------------------------------------------------------------- each copy
class PushClient:
    """This copy's phones: their FCM tokens, and waking them when a notification is made that nobody saw arrive."""

    QUEUE_MAX = 200

    def __init__(self, store: Store, fcm: Fcm, apns: Apns | None = None, *, broker_url: str = "", fetcher: Fetcher = fetch,
                 backoff: tuple[float, ...] = (2.0, 10.0, 30.0), sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time, settle_delay: float = SETTLED_DELAY):
        _schema(store)
        self.settle_delay = settle_delay
        self._settling: list[str] = []
        self._settle_timer: asyncio.TimerHandle | None = None
        self._settle_loop: asyncio.AbstractEventLoop | None = None
        self.store, self.fcm, self.apns, self.fetcher, self.backoff, self.sleep, self.clock = store, fcm, apns, fetcher, backoff, sleep, clock
        self.broker = broker_url.strip().rstrip("/")
        self._queue: asyncio.Queue[dict[str, str]] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._probe: tuple[float, bool, bool] | None = None   # (when, reachable, push enabled there)

    # ---- where pushes go
    def mode(self) -> str:
        """local: this server holds the Firebase key or the APNs key (the operator); broker: through the broker; "": push is off."""
        if self.fcm.enabled() or (self.apns is not None and self.apns.enabled()):
            return "local"
        return "broker" if self.broker else ""

    # ---- tokens (one per device; a newer one replaces it)
    def register(self, device_id: int, token: str, platform: str, environment: str = "") -> None:
        if platform not in PLATFORMS:
            raise PushError(f"platform must be one of {', '.join(PLATFORMS)}")
        env = "production"
        if platform == "ios-apns":
            token = token.strip().lower() if isinstance(token, str) else token
            env = (environment or "production").strip().lower()
            if env not in APNS_ENVIRONMENTS:
                raise PushError(f"environment must be one of {', '.join(APNS_ENVIRONMENTS)}")
            if not isinstance(token, str) or not APNS_TOKEN_RE.match(token):
                raise PushError("that does not look like an iPhone push token")
        elif not isinstance(token, str) or not TOKEN_RE.match(token):
            raise PushError("that does not look like an FCM registration token")
        now = self.clock()
        with self.store._lock:
            self.store._db.execute("DELETE FROM push_tokens WHERE token = ? AND device_id != ?", (token, device_id))   # moved to another pairing
            self.store._db.execute("INSERT INTO push_tokens(device_id, token, platform, created_at, updated_at, environment) VALUES(?,?,?,?,?,?) "
                                   "ON CONFLICT(device_id) DO UPDATE SET token = excluded.token, platform = excluded.platform, "
                                   "updated_at = excluded.updated_at, environment = excluded.environment", (device_id, token, platform, now, now, env))
            self.store._db.commit()

    def unregister(self, device_id: int) -> bool:
        return self.store._run("DELETE FROM push_tokens WHERE device_id = ?", (device_id,)).rowcount > 0

    def registered(self, device_id: int | None) -> bool:
        return device_id is not None and bool(self.store._all("SELECT 1 FROM push_tokens WHERE device_id = ?", (device_id,)))

    def tokens(self) -> list[str]:
        """Tokens of devices that are still paired (a removed device's token never gets a push)."""
        return [r["token"] for r in self.store._all(
            "SELECT token FROM push_tokens WHERE platform = 'android' AND device_id IN (SELECT id FROM devices) ORDER BY device_id")]

    def apns_tokens(self) -> list[tuple[str, str]]:
        """(token, environment) of the paired iPhones that registered their own APNs token."""
        return [(r["token"], r["environment"]) for r in self.store._all(
            "SELECT token, environment FROM push_tokens WHERE platform = 'ios-apns' AND device_id IN (SELECT id FROM devices) ORDER BY device_id")]

    def drop_tokens(self, tokens: list[str]) -> int:
        n = 0
        for t in tokens:
            n += self.store._run("DELETE FROM push_tokens WHERE token = ?", (t,)).rowcount
        return n

    def install_id(self) -> str:
        have = self.store.key(INSTALL_KEY)
        if have and INSTALL_RE.match(have):
            return have
        made = secrets.token_urlsafe(32)
        self.store.set_key(INSTALL_KEY, made)
        return made

    # ---- status
    async def broker_alive(self) -> tuple[bool, bool]:
        """(the broker answered, it sends pushes), asked at most every 5 minutes."""
        if self._probe and self.clock() - self._probe[0] < 300:
            return self._probe[1], self._probe[2]
        reachable = enabled = False
        try:
            r = await self.fetcher("GET", f"{self.broker}/broker/push/status", allowed_hosts=[urlsplit(self.broker).hostname or ""],
                                   headers={"Accept": "application/json"})
            reachable = r.status < 500
            enabled = r.status == 200 and bool((json.loads(r.body) or {}).get("push"))
        except (NetGuardError, ValueError, AttributeError):
            pass
        self._probe = (self.clock(), reachable, enabled)
        return reachable, enabled

    async def status(self, device_id: int | None) -> dict[str, Any]:
        mode = self.mode()
        out: dict[str, Any] = {"enabled": bool(mode), "registered": self.registered(device_id), "mode": mode or "off"}
        if mode == "broker":
            out["broker_reachable"], out["broker_push"] = await self.broker_alive()
        return out

    # ---- sending
    async def live(self, token: str, env: str, content: dict[str, Any], event: str, ts: int) -> dict[str, Any]:
        """One Live Activity update to one iPhone (live_activity.py): straight to Apple here, or through the broker. Never raises:
        a failure answers {"ok": False, "invalid": False} and the next update comes in a few seconds anyway."""
        off = {"ok": False, "invalid": False}
        mode = self.mode()
        if mode == "local":
            if self.apns is None or not self.apns.enabled():
                return off
            try:
                return await self.apns.send_live(token, env, content, event, ts)
            except PushError as e:
                log.warning("push: live activity not sent (%s)", e)
                return off
        if mode != "broker":
            return off
        body = json.dumps({"install_id": self.install_id(), "token": token, "environment": env, "content": content, "event": event,
                           "timestamp": ts}, separators=(",", ":")).encode()
        host = urlsplit(self.broker).hostname or ""
        try:
            r = await self.fetcher("POST", f"{self.broker}/broker/push/live", allowed_hosts=[host], content=body,
                                   headers={"Content-Type": "application/json", "Accept": "application/json"})
        except NetGuardError as e:
            log.warning("push: live activity: broker not reachable (%s)", str(e)[:60])
            return off
        if r.status != 200:
            return off
        try:
            answer = json.loads(r.body)
        except (ValueError, TypeError):
            return off
        if not isinstance(answer, dict) or not isinstance(answer.get("invalid"), bool):
            return off
        return answer

    def notify(self, note: dict[str, Any]) -> None:
        """Called (synchronously) for each new notification nobody saw arrive; the push goes out in the background."""
        if not self.mode():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                                           # no event loop (a script): no push
        if self._loop is not loop or self._queue is None or self._worker is None or self._worker.done():
            self._loop, self._queue = loop, asyncio.Queue(self.QUEUE_MAX)
            self._worker = loop.create_task(self._run())
        try:
            self._queue.put_nowait(note_data(note))
        except asyncio.QueueFull:
            log.warning("push: queue full, %s not pushed", note.get("type"))

    def settle(self, ids: list[str]) -> None:
        """Waiting items stopped waiting (answered anywhere, ran out, cancelled): tell the phones once, ids only. Items that settle within
        SETTLED_DELAY of each other go out as one push (at most SETTLED_MAX ids each)."""
        if not self.mode() or not ids:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._settle_loop is not loop:                   # another event loop (tests): start over
            self._settle_loop, self._settling, self._settle_timer = loop, [], None
        self._settling.extend(i for i in ids if ID_RE.match(i) and i not in self._settling)
        if self._settle_timer is None:
            self._settle_timer = loop.call_later(self.settle_delay, self._flush_settled)

    def _flush_settled(self) -> None:
        batch, self._settling, self._settle_timer = self._settling, [], None
        for i in range(0, len(batch), SETTLED_MAX):
            ids = batch[i:i + SETTLED_MAX]
            self._enqueue({"type": SETTLED, "id": ids[0], "ids": ",".join(ids)})

    def _enqueue(self, data: dict[str, str]) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._queue is None or self._worker is None or self._worker.done():
            self._loop, self._queue = loop, asyncio.Queue(self.QUEUE_MAX)
            self._worker = loop.create_task(self._run())
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            log.warning("push: queue full, %s not pushed", data.get("type"))

    def wake(self, task_id: int) -> None:
        """A phone has a task waiting (a text to send, the dialer to open, a calendar change): wake the phones with the id only."""
        if not self.mode():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is not loop or self._queue is None or self._worker is None or self._worker.done():
            self._loop, self._queue = loop, asyncio.Queue(self.QUEUE_MAX)
            self._worker = loop.create_task(self._run())
        try:
            self._queue.put_nowait({"type": "device_task", "id": str(task_id)})
        except asyncio.QueueFull:
            log.warning("push: queue full, device_task not pushed")

    async def _run(self) -> None:
        q = self._queue
        assert q is not None
        while True:
            data = await q.get()
            try:
                await self.deliver(data)
            except Exception:  # noqa: BLE001 - one failed push never stops the next
                log.warning("push: sending %s failed", data.get("type"), exc_info=False)
            finally:
                q.task_done()

    async def drain(self) -> None:
        """Wait until every queued push was handled (tests, shutdown)."""
        if self._queue is not None and self._loop is asyncio.get_running_loop():
            await self._queue.join()

    async def close(self) -> None:
        if self._settle_timer is not None:
            self._settle_timer.cancel()
            self._settle_timer = None
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._worker = None

    async def deliver(self, data: dict[str, str]) -> dict[str, int]:
        """One push to every registered phone, in batches of MAX_TOKENS; tokens reported invalid are dropped."""
        data = check_data(data)
        tokens, phones, mode = self.tokens(), self.apns_tokens(), self.mode()
        sent = dropped = 0
        count = len(tokens) + len(phones)
        if not count or not mode:
            return {"devices": count, "delivered": 0, "dropped": 0}
        for i in range(0, max(len(tokens), len(phones)), MAX_TOKENS):
            chunk, ios = tokens[i:i + MAX_TOKENS], phones[i:i + MAX_TOKENS]
            got = await (self._local(chunk, ios, data) if mode == "local" else self._via_broker(chunk, ios, data))
            if got is None:
                continue
            results, ios_results = got
            sent += sum(1 for r in results + ios_results if r.get("ok"))
            dropped += self.drop_tokens([t for t, r in zip(chunk, results) if r.get("invalid")]
                                        + [t for (t, _), r in zip(ios, ios_results) if r.get("invalid")])
        log.info("push: %s to %d device(s) (%s), %d delivered, %d stale token(s) dropped", data["type"], count, mode, sent, dropped)
        return {"devices": count, "delivered": sent, "dropped": dropped}

    async def _local(self, chunk: list[str], ios: list[tuple[str, str]], data: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        try:
            results = await self.fcm.send(chunk, data) if chunk and self.fcm.enabled() else []
            if len(results) != len(chunk):                    # no Firebase key here: those phones are skipped, nothing is dropped
                results = [{"ok": False, "invalid": False} for _ in chunk]
            ios_results = await self.apns.send(ios, data) if ios and self.apns is not None and self.apns.enabled() else []
            if len(ios_results) != len(ios):
                ios_results = [{"ok": False, "invalid": False} for _ in ios]
            return results, ios_results
        except PushError as e:
            log.warning("push: could not send (%s)", e)
            return None

    async def _via_broker(self, chunk: list[str], ios: list[tuple[str, str]],
                          data: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        url = f"{self.broker}/broker/push/send"
        payload: dict[str, Any] = {"install_id": self.install_id(), "tokens": chunk, "data": data}
        if ios:                                              # iPhones: their APNs token and which Apple host it belongs to
            payload["apns"] = [{"token": t, "environment": env} for t, env in ios]
        body = json.dumps(payload).encode()
        host = urlsplit(self.broker).hostname or ""
        for attempt in range(len(self.backoff) + 1):
            wait: float | None = None
            try:
                r = await self.fetcher("POST", url, allowed_hosts=[host], content=body,
                                       headers={"Content-Type": "application/json", "Accept": "application/json"})
            except NetGuardError as e:
                why = f"broker not reachable ({str(e)[:60]})"
            else:
                if r.status == 200:
                    try:
                        answer = json.loads(r.body) or {}
                        results, ios_results = answer.get("results"), answer.get("apns_results", [])
                    except (ValueError, AttributeError):
                        results = ios_results = None
                    if isinstance(results, list) and len(results) == len(chunk) and all(isinstance(x, dict) for x in results):
                        if not (isinstance(ios_results, list) and len(ios_results) == len(ios) and all(isinstance(x, dict) for x in ios_results)):
                            ios_results = [{"ok": False, "invalid": False} for _ in ios]   # an older broker: iPhones keep polling, nothing dropped
                        return results, ios_results
                    log.warning("push: the broker's answer did not fit; nothing dropped")
                    return None
                if r.status == 404:
                    log.info("push: the broker does not send push notifications (yet)")
                    return None
                if r.status != 429 and r.status < 500:
                    log.warning("push: the broker refused (HTTP %d)", r.status)
                    return None
                why, wait = f"HTTP {r.status}", _retry_after(r)
            if attempt >= len(self.backoff):
                log.warning("push: gave up after %d tries (%s)", attempt + 1, why)
                return None
            delay = self.backoff[attempt] if wait is None else wait
            if delay > 600:
                log.warning("push: the broker asks to wait %.0f s; not retried", delay)
                return None
            await self.sleep(delay)
        return None
