"""The model lists of each provider (with their labels and context windows), installing the agy CLI, token use and subscription windows (usage.py)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
import httpx
from fastapi import Depends, HTTPException
from .. import windows
from ..providers.oauth import status as oauth_status
from .. import usage as usage_meter
from ..providers import cli_manage, cli_remote
from ..providers.vision import supports_images
from ..effort import listed as effort_listed
from ..effort import refused_for as effort_refused, refused_note as effort_refused_note
from ..models import label_all, model_list
from ..providers import ProviderError, get_provider
from .. import balances
from ..keyring_client import PLACEHOLDER, KeyringTransport
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    background_model = ctx.background_model
    balance_resolver = ctx.balance_resolver
    balance_transport = ctx.balance_transport
    keyring = ctx.keyring
    limit_guard = ctx.limit_guard
    meta_json = ctx.meta_json
    models_client = ctx.models_client
    provider_key = ctx.provider_key
    require_auth = ctx.require_auth
    settings = ctx.settings
    store = ctx.store

    @router.post("/api/providers/{name}/install", dependencies=[Depends(require_auth)])
    async def install_provider(name: str) -> dict[str, Any]:
        """Only the fixed agy installer; nothing from the request reaches the command line."""
        try:
            prov = get_provider(name)
        except KeyError:
            raise HTTPException(404, f"unknown provider {name!r}") from None
        if prov.name != "antigravity-oauth":
            raise HTTPException(400, f"{prov.name} cannot be installed from the app")
        try:
            return {"ok": True, "output": await cli_manage.install_agy()}
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None

    def model_info(provider: str, found: list[str]) -> dict[str, dict[str, Any]]:
        """What the app's model list can say about each model without guessing: whether it can be shown pictures (per provider,
        providers/vision.py) and its context window when the provider's own list or the published table says (never the default)."""
        try:
            pname = get_provider(provider).name
        except KeyError:
            pname = provider
        stored = meta_json(f"model_windows:{pname}")
        images = supports_images(pname)
        out: dict[str, dict[str, Any]] = {}
        stated = effort_listed(pname)
        for m in found:
            w = windows.lookup(pname, m, overrides=settings.context_overrides, stored=stored)
            out[m] = {"images": images, "context": w.context if w.source in ("override", "provider", "table") else None}
            if m in stated:   # the thinking levels the provider's list states for this model ([] = none); else the provider's
                out[m]["effort_levels"] = list(stated[m])
        try:
            base = get_provider(pname).resolve_base_url(None, None)
        except KeyError:
            base = ""
        prof = store.profile()
        mine = [prof["model"]] if provider_key(prof["provider"] or "") == provider_key(pname) and prof["model"] else []
        for m in dict.fromkeys([*found, *mine]):
            if effort_refused(pname, base, m):   # the server turned the thinking level away: no levels, a quiet note instead
                out.setdefault(m, {"images": images, "context": None})
                out[m]["effort_levels"] = []
                out[m]["effort_note"] = effort_refused_note()
        return out

    # GET /api/models?provider=&refresh= -> {"provider", "models": [id], "labels": {id: display name}, "info": {id: {"images",
    #   "context", "effort_levels"?, "effort_note"?}}, "error", "stale", "fetched_at", "retry_in"}
    #   effort_levels: the thinking levels the provider's own list states for that model ([] = none), absent when it does not say
    #   (then the provider's "effort_levels" from /api/providers apply). effort_note (with effort_levels []): this server answered
    #   400 to the thinking level and is sent none (providers/refused.py); the app shows the line quietly on the Model page. stale: true = the list could not be fetched just now (timeout,
    #   network, 5xx) and this is the last good one, fetched at fetched_at (epoch seconds); asked again after retry_in seconds or on
    #   refresh=true. error is set only when there is no list at all. labels has every model of the list: the provider's own name,
    #   else models.model_label() (the rule the apps copy for before their first list). cli status (in /api/providers) also has "latest_version" and
    #   "update_available" (a newer claude / agy CLI is published; the update command is in "update_hint").
    # One client for every model-list fetch, made on first use: making one loads the CA certificates, which takes seconds on a busy
    # machine and holds up every other request meanwhile (it used to be made on each look, even when the list came from the cache).
    shared_models_client: list[httpx.AsyncClient] = []

    async def models_http() -> httpx.AsyncClient:
        if models_client is not None:
            return models_client
        if not shared_models_client or shared_models_client[0].is_closed:
            made = await cli_remote.new_client(httpx.Timeout(10.0, connect=5.0))   # on a worker thread: never holds up the loop
            if not shared_models_client or shared_models_client[0].is_closed:
                shared_models_client[:] = [made]
            else:
                await made.aclose()
        return shared_models_client[0]

    app.state.close_models_http = lambda: shared_models_client[0].aclose() if shared_models_client else asyncio.sleep(0)

    @router.get("/api/models", dependencies=[Depends(require_auth)])
    async def get_models(provider: str, refresh: bool = False) -> dict[str, Any]:
        try:
            prov = get_provider(provider)
        except KeyError:
            raise HTTPException(404, f"unknown provider {provider!r}") from None
        in_ring = keyring.has(prov.name)   # the key is in the key container: the list is asked through it, with a placeholder
        client = keyring.client_for(prov.name, httpx.Timeout(10.0, connect=5.0)) if in_ring else await models_http()
        try:
            res = await model_list(prov, client, refresh=refresh, api_key=PLACEHOLDER if in_ring else store.key(prov.name),
                                   load=lambda: meta_json(f"models_last:{prov.name}"),
                                   save=lambda good: store.set_meta(f"models_last:{prov.name}", json.dumps(good)))
            if not res["stale"] and res["models"] and (seen := windows.provider_windows(prov.name)):
                # the context windows the list gave: kept for after a restart
                store.set_meta(f"model_windows:{prov.name}", json.dumps({k: list(v) for k, v in seen.items()}))
            return {"provider": prov.name, "models": res["models"], "labels": label_all(res["models"], res["labels"]),
                    "info": model_info(prov.name, res["models"]), "error": res["error"],
                    "stale": res["stale"], "fetched_at": res["fetched_at"], "retry_in": res["retry_in"]}
        except ProviderError as e:  # never raises 5xx for "can't list": the UI falls back to manual entry
            return {"provider": prov.name, "models": [], "labels": {}, "info": model_info(prov.name, []), "error": str(e), "stale": False, "fetched_at": None,
                    "retry_in": None}

    usage_cache = ctx.usage_cache   # provider -> (when, answer); a sign-in or sign-out empties its entry (api/providers.py)

    async def one_subscription(name: str, refresh: bool) -> dict[str, Any]:
        hit = usage_cache.get(name)
        if hit and not refresh and time.monotonic() - hit[0] < 60:
            return hit[1]
        try:
            res: dict[str, Any] = {"provider": name, "windows": await cli_manage.cli_usage(name), "error": None}
            limit_guard.remember(name, res["windows"])   # the background jobs' "nearly used up" check reads the same numbers
        except ProviderError as e:
            res = {"provider": name, "windows": [], "error": str(e)}
        usage_cache[name] = (time.monotonic(), res)
        return res

    async def subscriptions_usage(refresh: bool) -> list[dict[str, Any]]:
        """Per-role subscription usage for each distinct CLI provider (CLAUDE or AGY) among main, background, and fallback."""
        prof = store.profile()
        candidates: list[tuple[str, str]] = []  # (name, role)
        seen_names: set[str] = set()

        def collect(raw_name: str | None, role: str) -> None:
            if not raw_name:
                return
            try:
                name = get_provider(raw_name).name
            except KeyError:
                return
            if name in seen_names:
                return
            if name not in (cli_manage.CLAUDE, cli_manage.AGY):
                return
            seen_names.add(name)
            candidates.append((name, role))

        collect(prof["provider"] or settings.provider, "main")
        bg = background_model()
        if bg:
            collect(bg[0], "background")
        fp = prof.get("fallback_provider") or ""
        if fp:
            collect(fp, "fallback")

        results: list[dict[str, Any]] = []
        for name, role in candidates:
            entry = await one_subscription(name, refresh)
            results.append({**entry, "role": role})
        return results

    async def subscription_usage(refresh: bool) -> dict[str, Any] | None:
        """What the selected CLI provider's own `/usage` reports (session/week limits), if it has one."""
        provider = store.profile()["provider"] or settings.provider
        try:
            name = get_provider(provider).name if provider else ""
        except KeyError:
            return None
        if name not in (cli_manage.CLAUDE, cli_manage.AGY):
            return None
        return await one_subscription(name, refresh)

    account_cache = ctx.account_cache   # (provider, key id or "keyring", admin key set) -> (when, row); api/providers.py clears it

    async def provider_accounts(refresh: bool) -> list[dict[str, Any]]:
        """Balance and spend of the main model's provider, background provider, and fallback provider (only API-key providers).
        Other providers with a stored key stay out of the usage screen. Cached a minute per provider and key; a key is never
        part of the answer. Each entry includes a `role` field (\"main\", \"background\", or \"fallback\"); with an admin key
        (balances.ADMIN) it also has `admin: true` and this month's and today's spend (or the balance), and a refused or failed
        admin read is shown as `admin_error`."""
        prof = store.profile()
        logins = oauth_status()
        wanted: list[tuple[str, str]] = []  # (name, role)

        def add(name: str, only_with_balance: bool, role: str) -> None:
            try:
                prov = get_provider(name)
            except KeyError:
                return
            if any(prov.name == n for n, _ in wanted) or prov.auth_type != "api_key" or prov.name in logins:
                return   # CLI and OAuth providers report through `subscription`, or not at all
            if only_with_balance and prov.name not in balances.FETCHERS:
                return   # an extra stored key is only worth a row when there is something to show for it
            wanted.append((prov.name, role))

        main = prof["provider"] or settings.provider
        if main:
            add(main, False, "main")
        bg = background_model()
        if bg:
            add(bg[0], True, "background")
        fp = prof.get("fallback_provider") or ""
        if fp:
            add(fp, True, "fallback")

        async def one(name: str) -> dict[str, Any]:
            prov = get_provider(name)
            in_ring = keyring.has(name)
            adm = balances.admin_name(prov.name)
            has_admin = keyring.has(adm)   # the admin key reports what the chat key cannot (balances.ADMIN)
            key = PLACEHOLDER if in_ring else prov.resolve_api_key(store.key(name))
            ck = (name, "keyring" if in_ring else balances.key_id(key), has_admin)
            hit = account_cache.get(ck)
            if hit and not refresh and time.monotonic() - hit[0] < 60:
                return hit[1]
            res = await balances.account(prov, key, transport=KeyringTransport(keyring, name) if in_ring else balance_transport,
                                         resolver=balance_resolver)
            if has_admin:
                try:
                    res = {**res, **await balances.admin_values(prov, transport=KeyringTransport(keyring, adm), resolver=balance_resolver),
                           "currency": "USD", "error": None, "admin": True}
                except balances.BalanceError as e:
                    res = {**res, "admin_error": str(e)}
            account_cache[ck] = (time.monotonic(), res)
            return res

        results = await asyncio.gather(*(one(name) for name, role in wanted))
        return [{**res, "role": role} for res, (_, role) in zip(results, wanted)]

    @router.get("/api/usage", dependencies=[Depends(require_auth)])
    async def get_usage(refresh: bool = False) -> dict[str, Any]:
        """`subscription`: the CLI's /usage windows for the main provider (null for API-key providers);
        `subscriptions`: per-role list of CLI subscription windows (main/background/fallback, CLAUDE or AGY providers only);
        today/total: this app's own tokens;
        `accounts`: balance / spend reported by the API-key providers in use (each entry has a `role` field;
        see balances.py for the shape; an entry may carry `admin: true` when an admin key reported it, or `admin_error`)."""
        subs = await subscriptions_usage(refresh)
        main_sub = next((s for s in subs if s["role"] == "main"), None)
        sub = {"provider": main_sub["provider"], "windows": main_sub["windows"], "error": main_sub["error"]} if main_sub else None
        return {**store.usage(), "subscription": sub,
                "subscriptions": subs,
                "accounts": await provider_accounts(refresh),
                "jobs": {"today": usage_part(time.strftime("%Y-%m-%d")), "month": usage_part(time.strftime("%Y-%m-01"))},
                "background": background_state()}

    def usage_row(r: dict[str, Any]) -> dict[str, Any]:
        """A summed row with its cached share: cache_share = cache-read tokens / input tokens (null when there was no input)."""
        return {**r, "cache_share": usage_meter.share(int(r["cache_read_tokens"]), int(r["input_tokens"]))}

    def usage_part(since: str) -> dict[str, Any]:
        """The records since a day: the sums, and the same by kind of job (the largest first)."""
        return {"since": since, **usage_row(store.usage_sum(since)), "kinds": [usage_row(r) for r in store.usage_by("kind", since)]}

    def background_state() -> dict[str, Any]:
        """The background model as set ("" = the main model) and the last time it failed and the main model ran instead (only while
        that model is still the one set)."""
        bg = background_model()
        last = usage_meter.last_fallback(store)
        if not bg or not last or (last.get("provider"), last.get("model")) != bg:
            last = None
        return {"provider": bg[0] if bg else "", "model": bg[1] if bg else "", "kinds": sorted(usage_meter.BACKGROUND_KINDS),
                "last_fallback": last}

    @router.get("/api/usage/summary", dependencies=[Depends(require_auth)])
    async def get_usage_summary(days: int = 30) -> dict[str, Any]:
        """The last `days` days (1-400, today included): {"since", "days": [{day, input_tokens, output_tokens, cache_read_tokens,
        cache_write_tokens, cache_share}] (the daily totals, oldest first), "kinds" / "models": the records summed by kind of job /
        by provider and model (with calls and cache_share), the largest first, "total": their sums, "background"}."""
        n = max(1, min(int(days), store.USAGE_KEEP_DAYS))
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (n - 1) * 86400))
        return {"since": since, "days": [usage_row(r) for r in store.usage_daily(since)],
                "kinds": [usage_row(r) for r in store.usage_by("kind", since)],
                "models": [usage_row(r) for r in store.usage_by("model", since)],
                "total": usage_row(store.usage_sum(since)), "background": background_state()}

    return router
