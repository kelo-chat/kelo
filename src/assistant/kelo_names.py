"""Kelo addresses (`<name>.kelo.chat`): the operator's side.

The operator's Kelo server (the one at kelo.chat, the same one that brokers GitHub/Google sign-ins) holds a Cloudflare API token
for the kelo.chat zone. Any copy of Kelo can ask it for a name. For each name the broker makes one Cloudflare Tunnel whose only
route is name.kelo.chat -> http://server:8080, points a proxied CNAME at it, and hands back the tunnel's run token. The copy runs
`cloudflared tunnel run` with that token next to its server, so the operator never carries anyone's traffic on its own machine;
the traffic goes through Cloudflare (TLS ends there), which is why Tailscale and own-domain options exist as well.

Ownership: every copy makes a long random `install_id` and keeps it to itself. The broker stores only its SHA-256, so the
database alone cannot be used to take a name over, and a name can be re-issued or released only by whoever holds that secret.
One name per copy. A name that had connected but has had no connector for PARK_DAYS is PARKED by `reclaim_idle`: its tunnel and
CNAME go (the slot is free and it does not count toward max_names) but the name stays reserved for its copy: nobody else can claim
it, and the same install_id asking again gets a new tunnel, CNAME and token under the same name (connect.kelo_refresh does that when
a copy starts and every few hours; owner re-claims never count against the daily cap or the breaker). Names unused for IDLE_DAYS
(no connector, counted from the last time one was seen) are released fully by `reclaim_idle`; a name whose tunnel never had a
connector within UNCONNECTED_SECONDS of its claim is released sooner by the same sweep (the server runs it every TICK_SECONDS
while such claims exist), so made-up install ids cannot hold the pool.

Abuse limits on new names (re-issuing a copy's own name never counts): at most PER_IP_DAY per client address in 24 h (429
{"code": "daily_cap"}; an IPv6 address counts by its /64), and at most PENDING_MAX names waiting for their first connector (inside their
UNCONNECTED_SECONDS grace) at a time: what threatens the pool. Past that every new claim is refused with 503 {"code": "busy"} and a
Retry-After of the real time until the oldest waiting name's grace ends; it lifts by itself as soon as fewer wait (released, or
connected: a refusal first asks Cloudflare again, at most every REFRESH_SECONDS). Owners who connect promptly never count against
anyone. The operator is told once per trip (`on_busy`).

A full pool (max_names, or a Cloudflare tunnel / DNS record limit) answers 503 {"code": "full"}; the operator is told once when the
pool reaches each share in WARN_AT (50, 70, 90%) of max_names and once when it is full, again only after it went below and filled again (`on_pool`). App attestation for claims
(Play Integrity / App Attest) is planned before launch. A hard cap (default 900) keeps the
operator under the 1,000 free tunnels per Cloudflare account; free DNS zones created after 2024-09-01 hold only 200 records, so on a
free zone set the cap to about 190 (or move the zone to a paid plan, 3,500 records).

Cloudflare API calls used (https://api.cloudflare.com/client/v4, Bearer token; scopes: Account > Cloudflare Tunnel > Edit and
Zone > DNS > Edit for kelo.chat):
  POST   /accounts/{a}/cfd_tunnel                       {name, config_src: "cloudflare"} -> result {id, token, ...}
  GET    /accounts/{a}/cfd_tunnel?name=&is_deleted=false / ?include_prefix=kelo-&per_page=1000&page=
  PUT    /accounts/{a}/cfd_tunnel/{id}/configurations    {config: {ingress: [...]}}
  GET    /accounts/{a}/cfd_tunnel/{id}/token            -> result "<token>"
  DELETE /accounts/{a}/cfd_tunnel/{id}/connections      (a tunnel with live connectors cannot be deleted)
  DELETE /accounts/{a}/cfd_tunnel/{id}
  GET    /zones/{z}/dns_records?type=CNAME&name.exact=   POST /zones/{z}/dns_records   PATCH/DELETE /zones/{z}/dns_records/{id}
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from typing import Any

import httpx

from .store import Store

API = "https://api.cloudflare.com/client/v4"
TOKEN_KEY = "operator:cloudflare"            # the API token, encrypted like the AI service keys; never returned
META = {"account_id": "kelo:cf_account", "zone_id": "kelo:cf_zone", "zone_name": "kelo:cf_zone_name", "max_names": "kelo:max_names"}
DEFAULT_MAX = 900
IDLE_DAYS = 30
PARK_DAYS = 5                   # a name that connected before but has had no connector this long is parked (slot freed, name kept)
UNCONNECTED_SECONDS = 3600      # a claimed name whose tunnel never had a connector this long after the claim is released
TICK_SECONDS = 600              # the server's sweep while such claims exist (tick)
PER_IP_DAY = 3                  # new names per client address in 24 h
PENDING_MAX = 30                # names waiting for their first connector at a time; at that many the broker is "busy"
NEAR_FULL = 0.9                 # the last warning before anyone is refused
WARN_AT = (0.5, 0.7, NEAR_FULL)  # the operator is warned once at each of these shares of max_names (owner, 2026-11-11)
REFRESH_SECONDS = 60            # a busy refusal asks Cloudflare which waiting names connected, at most this often
TUNNEL_PREFIX = "kelo-"
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28})[a-z0-9]$")    # 3-30 characters
RESERVED = frozenset("""
www api app apps admin administrator root mail email smtp imap pop pop3 mx ns ns1 ns2 dns ftp sftp ssh vpn kelo broker relay oauth
auth login signin signup account accounts billing pay payment payments support help docs doc status blog news shop store static cdn
assets media img images files download downloads update updates dev staging stage test beta prod production demo internal intranet
dashboard console portal cloud server servers host localhost tunnel tunnels connect webhook webhooks security abuse postmaster
hostmaster webmaster noreply no-reply info contact team official system null undefined anonymous
""".split())
SCHEMA = """
CREATE TABLE IF NOT EXISTS kelo_names (
    name TEXT PRIMARY KEY, owner TEXT NOT NULL UNIQUE, tunnel_id TEXT NOT NULL, dns_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, last_claim REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kelo_claims (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, ip TEXT NOT NULL, name TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kelo_claims_at ON kelo_claims(at);
"""


class KeloNameError(Exception):
    """Shown to the caller as it is; `status` is the HTTP status the broker answers with."""

    def __init__(self, message: str, status: int = 422, code: str = "", retry_after: int = 0):
        super().__init__(message)
        self.status, self.code, self.retry_after = status, code, retry_after


def owner_of(install_id: str) -> str:
    return hashlib.sha256(install_id.encode()).hexdigest()


def name_problem(name: str) -> str | None:
    if not NAME.match(name or ""):
        return "a name is 3-30 characters: lower-case letters, digits and '-', not starting or ending with '-'"
    if "--" in name:
        return "a name cannot contain '--'"
    if name in RESERVED:
        return f"{name!r} is reserved; pick another"
    return None


def _when(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Cloudflare:
    """A thin client for the few Cloudflare endpoints this needs. `transport` lets tests stand in a fake Cloudflare."""

    def __init__(self, token: str, account_id: str, zone_id: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.token, self.account, self.zone, self.transport = token, account_id, zone_id, transport

    async def call(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None,
                   ok_missing: bool = False) -> Any:
        async with httpx.AsyncClient(transport=self.transport, timeout=30) as c:
            try:
                r = await c.request(method, API + path, params=params, json=body, headers={"Authorization": f"Bearer {self.token}"})
            except httpx.HTTPError as e:
                raise KeloNameError(f"Cloudflare did not answer ({type(e).__name__})", 502) from None
        if ok_missing and r.status_code == 404:
            return None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or not data.get("success", False):
            errs = data.get("errors") or []
            msg = "; ".join(f"{e.get('code', '')} {e.get('message', '')}".strip() for e in errs if isinstance(e, dict)) or f"HTTP {r.status_code}"
            raise KeloNameError(f"Cloudflare refused: {msg}", 502)
        return data.get("result")

    # tunnels
    def _t(self, rest: str = "") -> str:
        return f"/accounts/{self.account}/cfd_tunnel{rest}"

    async def create_tunnel(self, name: str) -> dict[str, Any]:
        return await self.call("POST", self._t(), body={"name": name, "config_src": "cloudflare"})

    async def find_tunnel(self, name: str) -> dict[str, Any] | None:
        found = await self.call("GET", self._t(), params={"name": name, "is_deleted": "false"}) or []
        return next((t for t in found if t.get("name") == name and not t.get("deleted_at")), None)

    async def tunnels(self, prefix: str) -> list[dict[str, Any]]:
        out, page = [], 1
        while True:
            batch = await self.call("GET", self._t(), params={"include_prefix": prefix, "is_deleted": "false", "per_page": 1000, "page": page}) or []
            out += batch
            if len(batch) < 1000:
                return out
            page += 1

    async def route(self, tunnel_id: str, hostname: str) -> None:
        ingress = [{"hostname": hostname, "service": "http://server:8080"},
                   {"service": "http_status:404"}]
        await self.call("PUT", self._t(f"/{tunnel_id}/configurations"), body={"config": {"ingress": ingress}})

    async def tunnel_token(self, tunnel_id: str) -> str:
        return str(await self.call("GET", self._t(f"/{tunnel_id}/token")))

    async def delete_tunnel(self, tunnel_id: str) -> None:
        await self.call("DELETE", self._t(f"/{tunnel_id}/connections"), ok_missing=True)
        await self.call("DELETE", self._t(f"/{tunnel_id}"), ok_missing=True)

    # DNS
    async def find_record(self, hostname: str) -> dict[str, Any] | None:
        found = await self.call("GET", f"/zones/{self.zone}/dns_records", params={"type": "CNAME", "name.exact": hostname}) or []
        return next((r for r in found if r.get("name") == hostname), None)

    async def point(self, hostname: str, tunnel_id: str, label: str) -> str:
        body = {"type": "CNAME", "name": hostname, "content": f"{tunnel_id}.cfargotunnel.com", "proxied": True, "ttl": 1, "comment": label}
        old = await self.find_record(hostname)
        if old:
            return str((await self.call("PATCH", f"/zones/{self.zone}/dns_records/{old['id']}", body=body))["id"])
        return str((await self.call("POST", f"/zones/{self.zone}/dns_records", body=body))["id"])

    async def delete_record(self, record_id: str) -> None:
        if record_id:
            await self.call("DELETE", f"/zones/{self.zone}/dns_records/{record_id}", ok_missing=True)

    async def verify(self) -> None:
        """Both scopes work: list one tunnel, list one DNS record."""
        await self.call("GET", self._t(), params={"per_page": 1})
        await self.call("GET", f"/zones/{self.zone}/dns_records", params={"per_page": 1})


class KeloNames:
    def __init__(self, store: Store, *, transport: httpx.AsyncBaseTransport | None = None, clock: Any = time.time):
        self.store, self.transport, self.clock = store, transport, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(kelo_names)")}
            if "connected_at" not in have:   # when a connector was first seen (0: never): such a name is not released early
                store._db.execute("ALTER TABLE kelo_names ADD COLUMN connected_at REAL NOT NULL DEFAULT 0")
            if "parked_at" not in have:      # parked (no tunnel, no CNAME, name kept for its copy) since; 0: holds a slot
                store._db.execute("ALTER TABLE kelo_names ADD COLUMN parked_at REAL NOT NULL DEFAULT 0")
            if "idle_since" not in have:     # a parked name: when its connector was last seen (the IDLE_DAYS clock)
                store._db.execute("ALTER TABLE kelo_names ADD COLUMN idle_since REAL NOT NULL DEFAULT 0")
            store._db.commit()
        self.last_reclaim = 0.0
        self.last_parked: list[str] = []   # names the last sweep parked
        self.on_busy: Any = None   # (names waiting, until) -> None: the operator is told once per trip
        self._refreshed = -1e18
        self.on_pool: Any = None     # ("near" | "full", used, cap) -> None: the operator hears once per fill (_pool_watch)      # when a busy refusal last asked Cloudflare which waiting names connected

    # ---- operator settings
    def config(self) -> dict[str, Any]:
        cfg = {k: self.store.meta(v) for k, v in META.items() if k != "max_names"}
        return {**cfg, "has_token": bool(self.store.key(TOKEN_KEY)), "max_names": self.max_names(), "used": self.count(),
                "enabled": self.enabled(), "idle_days": IDLE_DAYS, "park_days": PARK_DAYS, "parked": self.parked()}

    def max_names(self) -> int:
        try:
            return int(self.store.meta(META["max_names"]) or DEFAULT_MAX)
        except ValueError:
            return DEFAULT_MAX

    def enabled(self) -> bool:
        return bool(self.store.key(TOKEN_KEY) and all(self.store.meta(META[k]) for k in ("account_id", "zone_id", "zone_name")))

    def client(self, token: str | None = None, account_id: str | None = None, zone_id: str | None = None) -> Cloudflare:
        return Cloudflare(token or self.store.key(TOKEN_KEY) or "", account_id or self.store.meta(META["account_id"]),
                          zone_id or self.store.meta(META["zone_id"]), transport=self.transport)

    async def configure(self, *, api_token: str | None, account_id: str | None, zone_id: str | None, zone_name: str | None,
                        max_names: int | None) -> dict[str, Any]:
        """Verify, then save. An empty api_token removes the token (turns the service off)."""
        if max_names is not None:
            if not 1 <= max_names <= 100_000:
                raise KeloNameError("max_names must be between 1 and 100000")
        hexid = re.compile(r"^[0-9a-f]{32}$")
        for label, v in (("account_id", account_id), ("zone_id", zone_id)):
            if v is not None and v and not hexid.match(v):
                raise KeloNameError(f"{label} is the 32-character id shown in the Cloudflare dashboard")
        if zone_name is not None and zone_name and not re.match(r"^([a-z0-9-]+\.)+[a-z]{2,63}$", zone_name.lower()):
            raise KeloNameError("zone_name is a domain such as kelo.chat")
        if api_token == "":
            self.store.delete_key(TOKEN_KEY)
        new = {"account_id": account_id, "zone_id": zone_id, "zone_name": zone_name.lower() if zone_name else zone_name}
        token = api_token or self.store.key(TOKEN_KEY)
        acc = new["account_id"] or self.store.meta(META["account_id"])
        zone = new["zone_id"] or self.store.meta(META["zone_id"])
        if api_token and (len(api_token) < 20 or len(api_token) > 200 or not re.match(r"^[A-Za-z0-9_-]+$", api_token)):
            raise KeloNameError("that does not look like a Cloudflare API token")
        if api_token and acc and zone:
            await self.client(token, acc, zone).verify()        # raises with Cloudflare's own message (never the token)
        if api_token:
            self.store.set_key(TOKEN_KEY, api_token)
        for k, v in new.items():
            if v is not None:
                self.store.set_meta(META[k], v)
        if max_names is not None:
            self.store.set_meta(META["max_names"], str(max_names))
            if self.enabled():
                self._pool_watch()   # a raised cap is below the marks again: the next fill is told again
        return self.config()

    # ---- the table
    def count(self) -> int:
        """Names that hold a slot (a tunnel and a CNAME); parked names do not."""
        return int(self.store._all("SELECT COUNT(*) AS n FROM kelo_names WHERE parked_at = 0")[0]["n"])

    def parked(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM kelo_names WHERE parked_at > 0")[0]["n"])

    def by_name(self, name: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM kelo_names WHERE name = ?", (name,))
        return rows[0] if rows else None

    def by_owner(self, owner: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM kelo_names WHERE owner = ?", (owner,))
        return rows[0] if rows else None

    def hostname(self, name: str) -> str:
        return f"{name}.{self.store.meta(META['zone_name'])}"

    def _need(self) -> None:
        if not self.enabled():
            raise KeloNameError("this server does not hand out Kelo addresses", 404)

    @staticmethod
    def _install(install_id: str) -> str:
        if not isinstance(install_id, str) or not re.match(r"^[A-Za-z0-9_-]{32,128}$", install_id):
            raise KeloNameError("install_id must be the copy's own random secret (32-128 letters, digits, '-' or '_')")
        return owner_of(install_id)

    # ---- the broker's three calls
    def check(self, name: str) -> dict[str, Any]:
        self._need()
        name = (name or "").strip().lower()
        if problem := name_problem(name):
            return {"name": name, "available": False, "reason": problem}
        if self.by_name(name):
            return {"name": name, "available": False, "reason": "taken"}
        if self.count() >= self.max_names():
            return {"name": name, "available": False, "reason": "full"}
        return {"name": name, "available": True, "hostname": self.hostname(name)}

    def pending(self) -> list[float]:
        """Claim times of the names still waiting for their first connector inside their grace, oldest first."""
        rows = self.store._all("SELECT created_at FROM kelo_names WHERE connected_at = 0 AND created_at > ? ORDER BY created_at",
                               (self.clock() - UNCONNECTED_SECONDS,))
        return [float(r["created_at"]) for r in rows]

    def busy_until(self) -> float:
        """When new claims are taken again at the latest (0: they are now): the oldest waiting name's grace ends then."""
        waiting = self.pending()
        if len(waiting) < PENDING_MAX:
            return 0.0
        return waiting[len(waiting) - PENDING_MAX] + UNCONNECTED_SECONDS

    def _mark_connected(self, tunnels: list[dict[str, Any]]) -> None:
        by = {t.get("name"): t for t in tunnels}
        for row in self.store._all("SELECT name, tunnel_id FROM kelo_names WHERE connected_at = 0"):
            t = by.get(TUNNEL_PREFIX + row["name"])
            if t and t.get("id") == row["tunnel_id"] and self._ever_connected(t):
                self.store._run("UPDATE kelo_names SET connected_at = ? WHERE name = ?", (self.clock(), row["name"]))

    async def _limits(self, ip: str) -> None:
        """Before a NEW name is made: the per-address daily cap and the breaker on names waiting for their first connector."""
        now = self.clock()
        self.store._run("DELETE FROM kelo_claims WHERE at < ?", (now - 86400,))
        if self.busy_until() and now - self._refreshed >= REFRESH_SECONDS:
            self._refreshed = now   # some may have connected since the last sweep: ask Cloudflare before refusing
            try:
                self._mark_connected(await self.client().tunnels(TUNNEL_PREFIX))
            except KeloNameError:
                pass
        until = self.busy_until()
        if until:
            wait = max(1, int(until - now + 0.999))
            if self.store.meta("kelo:busy") != "1":   # a new trip: the operator hears once
                self.store.set_meta("kelo:busy", "1")
                if self.on_busy is not None:
                    try:
                        self.on_busy(len(self.pending()), until)
                    except Exception:  # noqa: BLE001 - the refusal stands either way
                        pass
            raise KeloNameError(f"the Kelo address service is busy right now; try again in about {max(1, round(wait / 60))} "
                                "minute(s)", 503, "busy", wait)
        if self.store.meta("kelo:busy") == "1":
            self.store.set_meta("kelo:busy", "")      # lifted: the next trip is told again
        if ip:
            n = int(self.store._all("SELECT COUNT(*) AS n FROM kelo_claims WHERE ip = ? AND at > ?", (ip, now - 86400))[0]["n"])
            if n >= PER_IP_DAY:
                raise KeloNameError(f"at most {PER_IP_DAY} new Kelo addresses a day from one network; try again tomorrow", 429,
                                    "daily_cap", int(86400 - (now - float(self.store._all(
                                        "SELECT MIN(at) AS m FROM kelo_claims WHERE ip = ? AND at > ?", (ip, now - 86400))[0]["m"]))))

    async def claim(self, name: str, install_id: str, ip: str = "") -> dict[str, str]:
        """`ip`: the client's address as the broker sees it (client_ip); "" skips the per-address cap (a call from this server)."""
        self._need()
        name = (name or "").strip().lower()
        owner = self._install(install_id)
        if problem := name_problem(name):
            raise KeloNameError(problem)
        cf, host, now = self.client(), self.hostname(name), self.clock()
        mine = self.by_owner(owner)
        if mine and mine["name"] != name:
            raise KeloNameError(f"this copy already has {mine['name']}.{self.store.meta(META['zone_name'])}; release it first", 409)
        current = (await cf.call("GET", cf._t(f"/{mine['tunnel_id']}"), ok_missing=True)
                   if mine and mine["tunnel_id"] and not mine["parked_at"] else None)
        if mine and (not current or current.get("deleted_at")):
            # parked, or its tunnel is gone (deleted in Cloudflare by hand): the same name, a new tunnel, CNAME and token; this owner's
            # own name, so neither the daily cap nor the breaker counts it, but it needs a free slot like any name
            if mine["parked_at"] and self.count() >= self.max_names():
                raise self._full("no Kelo addresses are available right now. Use Tailscale or your own domain instead "
                                 "(assistant connect tailscale | assistant connect domain).")
            await cf.delete_record(mine["dns_id"])
            tid, dns_id, token = await self._make(cf, name, host)
            self.store._run("UPDATE kelo_names SET tunnel_id = ?, dns_id = ?, parked_at = 0, idle_since = 0, last_claim = ? WHERE name = ?",
                            (tid, dns_id, now, name))
            self._pool_watch()
            return {"name": name, "hostname": host, "tunnel_token": token}
        if mine:                                  # the same copy again (new computer, lost token): hand out its token again
            await cf.route(mine["tunnel_id"], host)
            dns_id = await cf.point(host, mine["tunnel_id"], f"kelo:{name}")
            token = await cf.tunnel_token(mine["tunnel_id"])
            self.store._run("UPDATE kelo_names SET last_claim = ?, dns_id = ? WHERE name = ?", (now, dns_id, name))
            return {"name": name, "hostname": host, "tunnel_token": token}
        if self.by_name(name):
            raise KeloNameError(f"{host} is taken; pick another name", 409)
        if self.count() >= self.max_names():
            raise self._full("no Kelo addresses are available right now. Use Tailscale or your own domain instead "
                             "(assistant connect tailscale | assistant connect domain).")
        await self._limits(ip)
        tid, dns_id, token = await self._make(cf, name, host)
        self.store._run("INSERT INTO kelo_names(name, owner, tunnel_id, dns_id, created_at, last_claim) VALUES(?, ?, ?, ?, ?, ?)",
                        (name, owner, tid, dns_id, now, now))
        self.store._run("INSERT INTO kelo_claims(at, ip, name) VALUES(?, ?, ?)", (now, ip, name))
        self._pool_watch()
        return {"name": name, "hostname": host, "tunnel_token": token}

    async def _make(self, cf: Cloudflare, name: str, host: str) -> tuple[str, str, str]:
        """A tunnel routed to the copy's server, its proxied CNAME and its run token (a stale tunnel of the name is removed first)."""
        tname = TUNNEL_PREFIX + name
        stale = await cf.find_tunnel(tname)       # left over from an earlier holder: its old token must stop working
        if stale:
            await cf.delete_tunnel(stale["id"])
        try:
            made = await cf.create_tunnel(tname)
        except KeloNameError as e:
            if "limit" in str(e).lower() or "quota" in str(e).lower():
                raise self._full("no Kelo addresses are available right now (the operator's Cloudflare tunnel limit). "
                                 "Use Tailscale or your own domain instead.") from None
            raise
        tid = str(made["id"])
        try:
            await cf.route(tid, host)
            dns_id = await cf.point(host, tid, f"kelo:{name}")
            token = str(made.get("token") or await cf.tunnel_token(tid))
        except KeloNameError as e:
            await cf.delete_tunnel(tid)
            if "quota" in str(e).lower() or "limit" in str(e).lower():
                raise self._full("no Kelo addresses are available right now (the kelo.chat DNS record limit). "
                                 "Use Tailscale or your own domain instead.") from None
            raise
        return tid, dns_id, token

    def _tell(self, kind: str) -> None:
        if self.on_pool is not None:
            try:
                self.on_pool(kind, self.count(), self.max_names())
            except Exception:  # noqa: BLE001 - never breaks a claim
                pass

    def _pool_watch(self) -> None:
        """The operator hears once at each share in WARN_AT ("near50", "near70", "near" = NEAR_FULL) and once when it is full; a level
        is told again only after the pool went below it and filled again."""
        used, cap = self.count(), self.max_names()
        levels = [(f"near{int(share * 100)}" if share != NEAR_FULL else "near", share * cap) for share in WARN_AT]
        due = []
        for kind, level in (*levels, ("full", cap)):
            key = f"kelo:told_{kind}"
            if used >= level and self.store.meta(key) != "1":
                self.store.set_meta(key, "1")
                due.append(kind)
            elif used < level and self.store.meta(key) == "1":
                self.store.set_meta(key, "")
        if due:   # several levels passed at once: only the highest one is told (at full straight away: only "full")
            self._tell(due[-1])

    def _full(self, message: str) -> KeloNameError:
        """The pool is full (the cap, or a Cloudflare limit): 503 {"code": "full"}, and the operator hears once per fill."""
        if self.store.meta("kelo:told_full") != "1":
            for kind in ("full", "near", *(f"near{int(x * 100)}" for x in WARN_AT if x != NEAR_FULL)):
                self.store.set_meta(f"kelo:told_{kind}", "1")
            self._tell("full")
        return KeloNameError(message, 503, "full")

    async def _drop(self, row: dict[str, Any]) -> None:
        cf = self.client()
        await cf.delete_record(row["dns_id"])
        if row["tunnel_id"]:
            await cf.delete_tunnel(row["tunnel_id"])
        self.store._run("DELETE FROM kelo_names WHERE name = ?", (row["name"],))
        self._pool_watch()   # below the marks again: the next fill is told again

    async def release(self, install_id: str) -> str | None:
        self._need()
        row = self.by_owner(self._install(install_id))
        if not row:
            return None
        await self._drop(row)
        return str(row["name"])

    def unconnected(self) -> int:
        """Names whose tunnel has not been seen with a connector yet."""
        return int(self.store._all("SELECT COUNT(*) AS n FROM kelo_names WHERE connected_at = 0")[0]["n"])

    async def tick(self) -> list[str]:
        """The server's sweep (every TICK_SECONDS): while names wait for their first connector, and at least every 6 hours."""
        if not self.enabled() or not (self.unconnected() or self.clock() - self.last_reclaim >= 6 * 3600):
            return []
        return await self.reclaim_idle()

    @staticmethod
    def _ever_connected(t: dict[str, Any]) -> bool:
        return bool(t.get("conns_active_at") or t.get("conns_inactive_at") or t.get("connections")
                    or t.get("status") in ("healthy", "degraded"))

    async def reclaim_idle(self) -> list[str]:
        """Release every name whose tunnel has had no connector for IDLE_DAYS (counting from the last claim at the earliest), and
        every name whose tunnel never had one within UNCONNECTED_SECONDS of its claim; park a name that connected before but has
        had no connector for PARK_DAYS (last_parked lists them). Returns the names released."""
        self._need()
        self.last_reclaim = self.clock()
        cutoff = self.clock() - IDLE_DAYS * 86400
        live = {t.get("name"): t for t in await self.client().tunnels(TUNNEL_PREFIX)}
        gone = []
        park_cutoff = self.clock() - PARK_DAYS * 86400
        self.last_parked = []
        for row in self.store._all("SELECT * FROM kelo_names"):
            if row["parked_at"]:                  # parked: released fully IDLE_DAYS after its connector was last seen
                if row["idle_since"] <= cutoff:
                    await self._drop(row)
                    gone.append(str(row["name"]))
                continue
            t = live.get(TUNNEL_PREFIX + row["name"])
            connected = bool(row["connected_at"])
            if t and t.get("id") == row["tunnel_id"]:
                if not row["connected_at"] and self._ever_connected(t):
                    connected = True
                    self.store._run("UPDATE kelo_names SET connected_at = ? WHERE name = ?", (self.clock(), row["name"]))
                elif not row["connected_at"] and self.clock() - row["created_at"] > UNCONNECTED_SECONDS:
                    await self._drop(row)   # never connected within an hour of its claim: not a copy that runs
                    gone.append(str(row["name"]))
                    continue
                if t.get("status") not in ("inactive", "down"):
                    continue
                ref = max(_when(t.get("conns_inactive_at")) or 0.0, _when(t.get("created_at")) or 0.0, row["last_claim"])
            else:                                 # not listed (deleted by hand, or not yet): from its last claim
                ref = row["last_claim"]
            if ref > park_cutoff:
                continue
            if ref <= cutoff or not connected:
                await self._drop(row)
                gone.append(str(row["name"]))
            else:                                 # connected before, idle PARK_DAYS: free the slot, keep the name for its copy
                await self._park(row, ref)
                self.last_parked.append(str(row["name"]))
        return gone

    async def _park(self, row: dict[str, Any], idle_since: float) -> None:
        cf = self.client()
        await cf.delete_record(row["dns_id"])
        if row["tunnel_id"]:
            await cf.delete_tunnel(row["tunnel_id"])
        self.store._run("UPDATE kelo_names SET parked_at = ?, idle_since = ?, tunnel_id = '', dns_id = '' WHERE name = ?",
                        (self.clock(), idle_since, row["name"]))
        self._pool_watch()
