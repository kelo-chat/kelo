"""Balance and spend reported by API-key providers, for GET /api/usage `accounts` (each entry has a `role`: main, background, or fallback).

Only providers with an official, documented endpoint that answers to the SAME key used for chat are asked (checked against
each provider's docs, 2026-11):

* OpenRouter  GET https://openrouter.ai/api/v1/key      data.{usage_daily, usage_weekly, usage_monthly, limit, limit_remaining}
              GET https://openrouter.ai/api/v1/credits  data.{total_credits, total_usage}  (management keys only; an ordinary
              key gets 403, which just leaves `balance` empty, and is not asked again for that key)
* DeepSeek    GET https://api.deepseek.com/user/balance  balance_infos[].{currency, total_balance}
* Novita      GET https://api.novita.ai/openapi/v1/billing/balance/detail  availableBalance (string, 1/10000 USD)
* Ollama      GET https://ollama.com/api/balance   included.{balance_usd, allowance_usd}, purchased.balance_usd
              GET https://ollama.com/api/usage?range=30d   buckets[].{from, usage_usd} (daily, UTC)
* Together    GET https://api.together.ai/v1/billing/usage?month=YYYY-MM&granularity=day   data[].{date, line_items[].cost}
              (beta, enabled per organization; 404 when it is not)
* Fireworks   GET https://api.fireworks.ai/v1/accounts   accounts[].name ("accounts/<id>")
              GET https://api.fireworks.ai/v1/accounts/<id>/billing/summary?startTime&endTime&granularity=DAILY
              lineItems[].totalCost, usageBuckets[]  (Money: currencyCode, units, nanos)
* DeepInfra   GET https://api.deepinfra.com/payment/usage?from=YYYY.MM  months[].{period, total_cost (cents)}
              GET https://api.deepinfra.com/payment/config  limit (USD, negative = none)
              (the key needs the `billing:read` scope, which new keys do not have; without it the row says so)

Every other API-key provider gets a row with `error` saying the service doesn't report its balance (some do report it, but only
to an admin / management key). An admin key the owner adds (ADMIN below, kept in the key container) is read the same way and
overlays the row. Requests go through netguard (https, the provider's fixed host, public
addresses, no redirects), never to a user-set base URL, so a key only ever reaches its own provider. The answer never contains a
key or a response body.

Row: {"provider", "display_name", "currency", "balance", "used_today", "used_week", "used_month", "limit", "limit_remaining",
      "error", "checked_at"}   numbers in `currency` or null; checked_at is ISO-8601 UTC.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from .i18n import t
from .netguard import NetGuardError, fetch
from .providers.registry import Provider

TIMEOUT = 10.0
_no_credits: set[str] = set()   # key ids OpenRouter refused /credits for (not a management key): not asked again
_fireworks_accounts: dict[str, str] = {}   # key id -> Fireworks account id (a key belongs to one account)
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class BalanceError(Exception):
    """A short, safe sentence for the app. `refused` is True when the provider turned the key away (HTTP 401 / 403)."""

    def __init__(self, message: str = "", *, refused: bool = False):
        super().__init__(message)
        self.refused = refused


def key_id(key: str | None) -> str:
    """A short fingerprint for caching per key; the key itself is never kept here."""
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def row(prov: Provider, **values: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"provider": prov.name, "display_name": prov.display_name, "currency": "USD", "balance": None,
                           "used_today": None, "used_week": None, "used_month": None, "limit": None, "limit_remaining": None,
                           "error": None, "checked_at": _now()}
    out.update(values)
    return out


Get = Callable[[str, str], Awaitable[tuple[int, Any]]]


def _getter(key: str, transport: httpx.AsyncBaseTransport | None, resolver: Callable[..., list] | None, style: str = "") -> Get:
    if style == "anthropic":
        auth = {"x-api-key": key, "anthropic-version": "2023-06-01", "accept": "application/json"}
    else:
        auth = {"authorization": f"Bearer {key}", "accept": "application/json", "content-type": "application/json"}

    async def get(host: str, path: str) -> tuple[int, Any]:
        try:
            r = await fetch("GET", f"https://{host}{path}", allowed_hosts=[host], timeout=TIMEOUT,
                            headers=auth, transport=transport, resolver=resolver)
        except NetGuardError:
            raise BalanceError(t("Could not reach the provider")) from None
        try:
            body = json.loads(r.body) if r.status == 200 else None
        except ValueError:
            body = None
        return r.status, body
    return get


def _http_error(status: int) -> BalanceError:
    if status in (401, 403):
        return BalanceError(t("The key was refused (HTTP {status}). Check the key", status=status), refused=True)
    if status == 429:
        return BalanceError(t("Too many requests (HTTP 429). Check again in a moment"))
    return BalanceError(t("Could not get the balance (HTTP {status})", status=status))


async def _openrouter(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    status, body = await get("openrouter.ai", "/api/v1/key")
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    d = body["data"]
    values: dict[str, Any] = {"used_today": _num(d.get("usage_daily")), "used_week": _num(d.get("usage_weekly")),
                              "used_month": _num(d.get("usage_monthly")), "limit": _num(d.get("limit")),
                              "limit_remaining": _num(d.get("limit_remaining"))}
    if kid not in _no_credits:
        try:
            cstatus, cbody = await get("openrouter.ai", "/api/v1/credits")
        except BalanceError:
            cstatus, cbody = 0, None
        if cstatus in (401, 403):
            _no_credits.add(kid)
        c = cbody.get("data") if isinstance(cbody, dict) else None
        if isinstance(c, dict):
            total, used = _num(c.get("total_credits")), _num(c.get("total_usage"))
            if total is not None and used is not None:
                values["balance"] = round(total - used, 6)
    return row(prov, **values)


async def _deepseek(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    status, body = await get("api.deepseek.com", "/user/balance")
    if status != 200:
        raise _http_error(status)
    infos = body.get("balance_infos") if isinstance(body, dict) else None
    infos = [i for i in infos if isinstance(i, dict)] if isinstance(infos, list) else []
    if not infos:
        raise BalanceError(t("The provider did not return the balance"))
    pick = next((i for i in infos if i.get("currency") == "USD"), infos[0])
    currency = pick.get("currency") if isinstance(pick.get("currency"), str) and len(pick["currency"]) <= 8 else None
    return row(prov, currency=currency, balance=_num(pick.get("total_balance")))


async def _opengateway(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    """Prepaid USD balance; any chat key reads its own team's (GET /v1/credits, free, not counted as usage)."""
    status, body = await get("apis.opengateway.ai", "/v1/credits")
    if status != 200 or not isinstance(body, dict):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    balance = _num(body.get("available_balance"))
    if balance is None:
        balance = _num(body.get("balance"))
    if balance is None:
        raise BalanceError(t("The provider did not return the balance"))
    currency = body.get("currency") if isinstance(body.get("currency"), str) and len(body["currency"]) <= 8 else "USD"
    return row(prov, currency=currency, balance=balance)


def _today() -> datetime:
    return datetime.now(timezone.utc)


def _sum(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    return round(sum(nums), 6) if nums else None


async def _novita(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    status, body = await get("api.novita.ai", "/openapi/v1/billing/balance/detail")
    if status != 200:
        raise _http_error(status)
    v = _num(body.get("availableBalance")) if isinstance(body, dict) else None
    if v is None:
        raise BalanceError(t("The provider did not return the balance"))
    return row(prov, balance=round(v / 10000, 4))   # 1/10000 USD


async def _ollama(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    status, body = await get("ollama.com", "/api/balance")
    if status != 200 or not isinstance(body, dict):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    inc = body.get("included") if isinstance(body.get("included"), dict) else {}
    bought = body.get("purchased") if isinstance(body.get("purchased"), dict) else {}
    left, allowance, paid = _num(inc.get("balance_usd")), _num(inc.get("allowance_usd")), _num(bought.get("balance_usd"))
    values: dict[str, Any] = {"balance": _sum([left, paid]), "limit": allowance, "limit_remaining": left}
    try:
        ustatus, ubody = await get("ollama.com", "/api/usage?range=30d")
    except BalanceError:
        ustatus, ubody = 0, None
    buckets = ubody.get("buckets") if ustatus == 200 and isinstance(ubody, dict) else None
    if isinstance(buckets, list):
        now = _today()
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        rows = [(b["from"][:10], _num(b.get("usage_usd"))) for b in buckets
                if isinstance(b, dict) and isinstance(b.get("from"), str)]
        values["used_today"] = _sum([u for d, u in rows if d == day]) or (0.0 if rows else None)
        values["used_week"] = _sum([u for d, u in rows if d >= monday]) or (0.0 if rows else None)
        values["used_month"] = _sum([u for d, u in rows if d.startswith(month)]) or (0.0 if rows else None)
    return row(prov, **values)


async def _together(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    now = _today()
    q = urlencode({"month": now.strftime("%Y-%m"), "granularity": "day", "limit": 100})
    status, body = await get("api.together.ai", f"/v1/billing/usage?{q}")
    if status == 404:
        raise BalanceError(t("This service doesn't report its balance."))   # the usage report is a beta that Together turns on per organization
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    day = now.strftime("%Y-%m-%d")
    today: list[float | None] = []
    month: list[float | None] = []
    for w in body["data"]:
        if not isinstance(w, dict) or not isinstance(w.get("line_items"), list):
            continue
        costs = [_num(i.get("cost")) for i in w["line_items"] if isinstance(i, dict)]
        month += costs
        if w.get("date") == day:
            today += costs
    currency = body.get("currency") if isinstance(body.get("currency"), str) and len(body["currency"]) <= 8 else "USD"
    return row(prov, currency=currency, used_today=_sum(today) or 0.0, used_month=_sum(month) or 0.0)


def _money(m: Any) -> tuple[float | None, str | None]:
    if not isinstance(m, dict):
        return None, None
    units, nanos = _num(m.get("units")) or 0.0, _num(m.get("nanos")) or 0.0
    code = m.get("currencyCode") if isinstance(m.get("currencyCode"), str) and len(m["currencyCode"]) <= 8 else None
    return units + nanos / 1e9, code


async def _fireworks(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    acct = _fireworks_accounts.get(kid)
    if not acct:
        status, body = await get("api.fireworks.ai", "/v1/accounts?pageSize=1")
        accounts = body.get("accounts") if status == 200 and isinstance(body, dict) else None
        if status != 200:
            raise _http_error(status)
        name = accounts[0].get("name") if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict) else None
        acct = name.removeprefix("accounts/") if isinstance(name, str) else ""
        if not _ACCOUNT_ID.fullmatch(acct):
            raise BalanceError(t("The provider answered in an unknown format"))
        _fireworks_accounts[kid] = acct
    now = _today()
    start = now.strftime("%Y-%m-01T00:00:00Z")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")   # exclusive
    q = urlencode({"startTime": start, "endTime": end, "granularity": "DAILY"})
    status, body = await get("api.fireworks.ai", f"/v1/accounts/{acct}/billing/summary?{q}")
    if status != 200 or not isinstance(body, dict):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    currency = "USD"
    month: list[float | None] = []
    for item in body.get("lineItems") or []:
        if isinstance(item, dict):
            v, code = _money(item.get("totalCost"))
            month.append(v)
            currency = code or currency
    day = now.strftime("%Y-%m-%d")
    today: list[float | None] = []
    for b in body.get("usageBuckets") or []:
        if isinstance(b, dict) and isinstance(b.get("startTime"), str) and b["startTime"][:10] == day:
            today += [_money(i.get("totalCost"))[0] for i in b.get("lineItems") or [] if isinstance(i, dict)]
    return row(prov, currency=currency, used_today=_sum(today) or 0.0, used_month=_sum(month) or 0.0)


async def _deepinfra(prov: Provider, get: Get, kid: str) -> dict[str, Any]:
    period = _today().strftime("%Y.%m")
    status, body = await get("api.deepinfra.com", f"/payment/usage?{urlencode({'from': period})}")
    if status == 403:
        raise BalanceError(t("This key may not read the balance. Allow billing access for the key on the provider's site"))
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("months"), list):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    cents = [_num(m.get("total_cost")) for m in body["months"] if isinstance(m, dict) and m.get("period") == period]
    used = _sum(cents)
    values: dict[str, Any] = {"used_month": round(used / 100, 6) if used is not None else 0.0}
    try:
        cstatus, cbody = await get("api.deepinfra.com", "/payment/config")
    except BalanceError:
        cstatus, cbody = 0, None
    limit = _num(cbody.get("limit")) if cstatus == 200 and isinstance(cbody, dict) else None
    if limit is not None and limit >= 0:   # negative means no limit
        values["limit"] = limit
    return row(prov, **values)


FETCHERS: dict[str, Callable[[Provider, Get, str], Awaitable[dict[str, Any]]]] = {
    "openrouter": _openrouter,
    "deepseek": _deepseek,
    "novita": _novita,
    "ollama-cloud": _ollama,
    "together": _together,
    "fireworks": _fireworks,
    "deepinfra": _deepinfra,
    "opengateway": _opengateway,
}

# Admin (billing) keys (backlog 61b): kept in the key container as "admin:<provider>", which lets them through only as a GET on these
# paths of this host. They report what the chat key cannot: spend (OpenAI, Anthropic) or the balance (OpenRouter).
ADMIN: dict[str, dict[str, Any]] = {
    "openai": {"host": "api.openai.com", "paths": ["/v1/organization/costs"]},
    "anthropic": {"host": "api.anthropic.com", "paths": ["/v1/organizations/cost_report"]},
    "openrouter": {"host": "openrouter.ai", "paths": ["/api/v1/credits"]},
}


def admin_name(provider: str) -> str:
    return f"admin:{provider}"


def _month_start() -> datetime:
    now = _today()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _admin_openai(get: Get) -> dict[str, Any]:
    start = _month_start()
    status, body = await get("api.openai.com", f"/v1/organization/costs?start_time={int(start.timestamp())}&bucket_width=1d&limit=31")
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    today_start = int(_today().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    month: list[float | None] = []
    today: list[float | None] = []
    for b in body["data"]:
        if not isinstance(b, dict):
            continue
        vals = [_num((r.get("amount") or {}).get("value")) for r in (b.get("results") or []) if isinstance(r, dict)]
        month += vals
        if isinstance(b.get("start_time"), (int, float)) and b["start_time"] >= today_start:
            today += vals
    return {"used_month": _sum(month) or 0.0, "used_today": _sum(today) or 0.0}


async def _admin_anthropic(get: Get) -> dict[str, Any]:
    start = _month_start().strftime("%Y-%m-%dT00:00:00Z")
    status, body = await get("api.anthropic.com", f"/v1/organizations/cost_report?starting_at={start}&bucket_width=1d&limit=31")
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    day = _today().strftime("%Y-%m-%d")
    month: list[float | None] = []
    today: list[float | None] = []
    for b in body["data"]:
        if not isinstance(b, dict):
            continue
        vals = [(_num(r.get("amount")) or 0.0) / 100 for r in (b.get("results") or []) if isinstance(r, dict)]   # cents
        month += vals
        if str(b.get("starting_at") or "").startswith(day):
            today += vals
    return {"used_month": round(_sum(month) or 0.0, 4), "used_today": round(_sum(today) or 0.0, 4)}


async def _admin_openrouter(get: Get) -> dict[str, Any]:
    status, body = await get("openrouter.ai", "/api/v1/credits")
    c = body.get("data") if isinstance(body, dict) else None
    if status != 200 or not isinstance(c, dict):
        raise _http_error(status) if status != 200 else BalanceError(t("The provider answered in an unknown format"))
    total, used = _num(c.get("total_credits")), _num(c.get("total_usage"))
    if total is None or used is None:
        raise BalanceError(t("The provider answered in an unknown format"))
    return {"balance": round(total - used, 6)}


ADMIN_FETCHERS = {"openai": _admin_openai, "anthropic": _admin_anthropic, "openrouter": _admin_openrouter}


async def admin_values(prov: Provider, *, transport: httpx.AsyncBaseTransport | None = None,
                       resolver: Callable[..., list] | None = None, placeholder: str = "KELO_KEYRING_SECRET") -> dict[str, Any]:
    """What the admin key reports for this provider (balance / used_today / used_month), through the key container's transport.
    Raises BalanceError."""
    fn = ADMIN_FETCHERS.get(prov.name)
    if fn is None:
        raise BalanceError(t("This service doesn't report its balance."))
    try:
        return await fn(_getter(placeholder, transport, resolver, style=prov.name))
    except BalanceError:
        raise
    except Exception:  # noqa: BLE001 - a surprise in the answer's shape must not break /api/usage or the key check
        raise BalanceError(t("The provider answered in an unknown format")) from None


async def account(prov: Provider, key: str | None, *, transport: httpx.AsyncBaseTransport | None = None,
                  resolver: Callable[..., list] | None = None) -> dict[str, Any]:
    """One row; never raises."""
    fn = FETCHERS.get(prov.name)
    if fn is None:
        return row(prov, currency=None, error=t("This service doesn't report its balance."))
    if not key:
        return row(prov, error=t("There is no API key"))
    try:
        return await fn(prov, _getter(key, transport, resolver), key_id(key))
    except BalanceError as e:
        return row(prov, error=str(e))
    except Exception:  # noqa: BLE001 - a surprise in one provider's answer must not break /api/usage
        return row(prov, error=t("Could not read the balance"))
