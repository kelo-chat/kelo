"""AI providers: the list the model picker shows, consent for unofficial sign-ins, API keys and endpoints, signing in and out from the phone, and keeping the claude / agy CLI sidecars current."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .. import i18n
from ..providers.oauth import logout as oauth_logout, status as oauth_status
from ..providers.login_flows import SIGNIN_PROVIDERS, LoginFlows
from .. import ai_consent
from ..providers import cli_manage, cli_remote, releases
from ..providers.cli_status_cache import CliStatusCache
from ..effort import levels as effort_levels, note as effort_note
from ..models import forget, is_configured
from ..providers import ProviderError, get_provider, list_families
from ..providers.registry import (
    USER_ENDPOINT_PREFIX,
    normalize_base_url,
    set_stored_base_url,
    set_user_endpoints,
    stored_base_url,
)
from ..providers import refused as refused_fields
from ..api.provider_policy import CONSENT_VERSION, UNOFFICIAL
from ..server import CLI_UPDATE_NOTE_DAYS
from ..keyring_client import PLACEHOLDER, KeyringError, KeyringTransport, host_entry
from .. import balances
from fastapi import APIRouter
from .context import AppContext


class CliUpdateIn(BaseModel):
    version: str | None = Field(default=None, max_length=20)


class EndpointIn(BaseModel):
    base_url: Any = ""


class EndpointNew(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    base_url: str
    transport: str = "chat_completions"


class EndpointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    base_url: str | None = None


class CustomNameIn(BaseModel):
    name: str = Field(default="", max_length=40)


class KeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


class AdminKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)


class LoginFinishIn(BaseModel):
    session: str = Field(min_length=1, max_length=100)
    code: str = Field(default="", max_length=4096)          # the code the page showed, or the address it ended on (url_code)
    redirect_url: str = Field(default="", max_length=4096)


class LoginStartIn(BaseModel):
    region: str = Field(default="global", max_length=10)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    app = ctx.app
    consent_of = ctx.consent_of
    login_flows = ctx.login_flows
    meta_json = ctx.meta_json
    keyring = ctx.keyring
    provider_key = ctx.provider_key
    require_auth = ctx.require_auth
    require_owner = ctx.require_owner
    settings = ctx.settings
    store = ctx.store
    unofficial_name = ctx.unofficial_name
    unofficial_shown = ctx.unofficial_shown
    usage_cache = ctx.usage_cache
    account_cache = ctx.account_cache   # balances of the API-key providers (api/models_usage.py)
    balance_resolver = ctx.balance_resolver

    @router.put("/api/providers/{name}/consent", dependencies=[Depends(require_auth)])
    async def put_provider_consent(name: str, body: dict[str, Any]) -> dict[str, Any]:
        real = unofficial_name(name)
        if real is None:
            raise HTTPException(404, "this provider needs no consent")
        if not isinstance(body, dict) or body.get("accepted") is not True:
            raise HTTPException(422, "accepted must be true")
        rec = {"accepted_at": time.time(), "version": int(body.get("version") or CONSENT_VERSION)}
        if rec["version"] < CONSENT_VERSION:
            raise HTTPException(409, f"the notice is now version {CONSENT_VERSION}; show it again")
        store.set_meta(f"consent:{real}", json.dumps(rec))
        return {"name": real, "consent": rec}

    # The claude / agy status is kept (providers/cli_status_cache.py): GET /api/providers answers from it at once, with "checked_at"
    # (epoch seconds) in each CLI's status, and an answer older than 45 s is checked again in the background. Sign-in, sign-out,
    # update and install make the next look wait briefly (at most 1 s) for a new check. The last answer survives a restart.
    def save_cli_status(name: str, value: dict[str, Any]) -> None:
        store.set_meta(f"cli_status_last:{name}", json.dumps(value))

    cli_statuses = CliStatusCache(load=lambda name: meta_json(f"cli_status_last:{name}"), save=save_cli_status)
    app.state.cli_statuses = cli_statuses   # tests

    def prime_cli_statuses() -> None:
        for name in (cli_manage.CLAUDE, cli_manage.AGY):
            if unofficial_shown(name):
                cli_statuses.warm(name)

    @router.get("/api/providers", dependencies=[Depends(require_auth)])
    async def get_providers(request: Request) -> list[dict[str, Any]]:
        logins = oauth_status()
        owner = request.state.device_id is None or bool(request.state.device_owner)
        stored = store.key_providers()
        shown = [f for f in list_families() if f.supported and unofficial_shown(f.default.name)]
        cli_names = [f.default.name for f in shown if f.default.name in (cli_manage.CLAUDE, cli_manage.AGY)]
        clis = dict(zip(cli_names, await asyncio.gather(*(cli_statuses.get(n) for n in cli_names))))
        out = []
        custom_name = store.meta("custom_name") or ""
        for f in shown:
            p = f.default
            is_user = p.name.startswith(USER_ENDPOINT_PREFIX)
            disp_name = (custom_name or "Custom") if f.name == "custom" else f.display_name
            in_ring = keyring.has(p.name)   # the key is in the key container: set, but the server holds no copy
            adm = balances.admin_name(p.name)
            key = PLACEHOLDER if in_ring else (store.key(p.name) if p.name in stored else None)
            cli = clis.get(p.name)
            if cli:
                cli = {**cli, **cli_release(p.name, cli)}
                # the app's 업데이트 button: an owner sign-in and a sidecar that can update itself (POST .../cli/update)
                cli["updatable"] = bool(owner and cli.get("remote") and cli.get("self_update") and cli.get("installed"))
            out.append({
                "cli": cli,
                "name": f.name, "display_name": disp_name, "supported": True,
                "user_endpoint": is_user,
                "oauth": p.name in logins, "logged_in": logins.get(p.name, {}).get("logged_in"),
                # one answer for every sign-in provider (CLI or OAuth): True / False / None (unknown, or not a sign-in provider),
                # and the account's e-mail when the sign-in says it. DELETE /api/providers/{name}/login signs out.
                "signed_in": (cli.get("logged_in") if cli.get("installed") else False) if cli else logins.get(p.name, {}).get("logged_in"),
                "account": (cli or {}).get("account") or logins.get(p.name, {}).get("account"),
                "configured": (cli["installed"] and cli["logged_in"] is not False) if cli else is_configured(p, key),
                "effort_levels": list(effort_levels(p.name)), "effort_note": effort_note(p.name),
                "takes_key": p.auth_type == "api_key" and p.name not in logins and (bool(p.api_key_envs) or p.takes_url),
                "key_source": "app" if key else ("env" if p.resolve_api_key() else None),
                "key_hint": (keyring.hints.get(p.name) or None) if in_ring else (key[-4:] if key else None),  # last 4 chars only; the key itself is never returned
                "takes_url": p.takes_url,
                # an optional admin (billing) key for spend or balance (PUT .../admin-key); it lives only in the key container
                "admin_key": {"supported": p.name in balances.ADMIN and keyring.enabled, "set": keyring.has(adm),
                              "hint": keyring.hints.get(adm) or None},
                **({"base_url": p.resolve_base_url()} if p.takes_url else {}),   # stored in the app, else the environment, else ""
                "unofficial": p.name in UNOFFICIAL,
                # where chats go with this provider (ai_consent.py): a company, or null for the owner's own computer or network
                "company": ai_consent.company_of(p.name),
                "signin": p.name in SIGNIN_PROVIDERS,   # can be signed in from the app (POST /api/providers/{name}/login/start)
                **({"vendor": UNOFFICIAL[p.name], "consent": consent_of(p.name), "consent_version": CONSENT_VERSION}
                   if p.name in UNOFFICIAL else {}),
            })
        return out

    # ---- CLI sidecars going stale: the claude / agy CLI is installed when its image is built, so it ages until `assistant update`.
    # The server compares the installed version with the newest published one (releases.py, cached a day) and says so in the CLI
    # status ("latest_version", "update_available"; the app shows it next to the version). The server has no Docker socket and
    # never controls a container: the owner presses 업데이트 and the sidecar installs the newer CLI into its own home volume
    # (POST /api/providers/{name}/cli/update -> cli_runner POST /update; providers/cli_update.py). A newer image from
    # `assistant update` wins again by itself (the runner uses the newer of the two). When an update has been out for
    # CLI_UPDATE_NOTE_DAYS, one notification (type cli_update) tells the owner, once per new version; it opens My → Model.
    cli_release_names = {cli_manage.CLAUDE: "claude", cli_manage.AGY: "agy"}
    cli_product = {cli_manage.CLAUDE: "Claude Code", cli_manage.AGY: "Antigravity CLI"}

    def cli_release(name: str, st: dict[str, Any]) -> dict[str, Any]:
        if not st.get("installed") or name not in cli_release_names:
            return {"latest_version": None, "update_available": False}
        latest = releases.latest_cached(cli_release_names[name])   # never waits: looked up in the background when old
        return {"latest_version": latest, "update_available": releases.newer(latest, st.get("version"))}

    async def check_cli_updates(now: float | None = None) -> list[str]:
        """One look at each CLI; returns the names a notification went out for."""
        now = time.time() if now is None else now
        sent = []
        for name, what in cli_release_names.items():
            key = f"cli_update:{name}"
            try:
                st = await cli_statuses.refresh(name)
            except Exception:  # noqa: BLE001 - a status problem only skips this look
                continue
            latest = await releases.latest(what) if st.get("installed") else None
            if not latest or not releases.newer(latest, st.get("version")):
                if store.meta(key):
                    store.set_meta(key, "")
                continue
            rec = meta_json(key) or {}
            if rec.get("version") != latest:
                rec = {"version": latest, "since": now, "noted": False}
                store.set_meta(key, json.dumps(rec))
            if not rec.get("noted") and now - float(rec.get("since") or now) >= CLI_UPDATE_NOTE_DAYS * 86400:
                product = cli_product[name]
                activity.notify("cli_update", i18n.t("A newer {cli} is out", cli=product),
                                i18n.t("{cli} {installed} is installed and {latest} is out. To update: {how}", cli=product,
                                       installed=releases.version_of(st.get("version")) or "?", latest=latest,
                                       how=i18n.t("My → Model → Update") if st.get("self_update")
                                       else st.get("update_hint") or "assistant update"), provider=name)
                store.set_meta(key, json.dumps({**rec, "noted": True}))
                sent.append(name)
        return sent

    async def cli_update_watch() -> None:
        await asyncio.sleep(600)   # not right at start-up
        while True:
            try:
                await check_cli_updates()
            except Exception:  # noqa: BLE001 - the next look tries again
                logging.getLogger("uvicorn.error").warning("cli update check failed", exc_info=True)
            await asyncio.sleep(6 * 3600)

    app.state.check_cli_updates = check_cli_updates   # tests

    def cli_update_provider(name: str) -> str:
        try:
            prov = get_provider(name)
        except KeyError:
            raise HTTPException(404, f"unknown provider {name!r}") from None
        if prov.name not in cli_release_names:
            raise HTTPException(400, f"{prov.name} is not a CLI provider")
        return prov.name

    @router.post("/api/providers/{name}/cli/update", dependencies=[Depends(require_owner)])
    async def cli_update_start(name: str, body: CliUpdateIn | None = None) -> dict[str, Any]:
        prov = cli_update_provider(name)
        try:
            res = await cli_remote.update_start(prov, (body.version if body else None) or None)
        except ProviderError as e:
            raise signin_error(e) from None
        return {**res, "product": cli_product[prov]}

    @router.get("/api/providers/{name}/cli/update", dependencies=[Depends(require_owner)])
    async def cli_update_status(name: str) -> dict[str, Any]:
        prov = cli_update_provider(name)
        try:
            res = await cli_remote.update_status(prov)
        except ProviderError as e:
            raise signin_error(e) from None
        if res.get("state") == "done":
            cli_manage.forget_status(prov)
        return {**res, "product": cli_product[prov]}

    def key_provider(name: str) -> Any:
        try:
            prov = get_provider(name)
        except KeyError:
            raise HTTPException(404, f"unknown provider {name!r}") from None
        if prov.auth_type != "api_key" or not (prov.api_key_envs or prov.takes_url) or prov.name in oauth_status():
            raise HTTPException(400, f"{prov.name} does not use an API key")
        return prov

    def url_provider(name: str) -> Any:
        try:
            prov = get_provider(name)
        except KeyError:
            raise HTTPException(404, f"unknown provider {name!r}") from None
        if not prov.takes_url:
            raise HTTPException(400, f"{prov.name} has a fixed address; it does not take a base URL")
        return prov

    def load_custom_endpoints() -> list[dict[str, Any]]:
        try:
            raw = json.loads(store.meta("custom_endpoints") or "[]")
            return raw if isinstance(raw, list) else []
        except ValueError:
            return []

    def save_custom_endpoints(items: list[dict[str, Any]]) -> None:
        store.set_meta("custom_endpoints", json.dumps(items))

    def check_name_available(new_name: str, exclude_id: str | None = None) -> None:
        target = new_name.strip().lower()
        custom_disp = (store.meta("custom_name") or "Custom").strip().lower()
        if target == custom_disp or target == "custom":
            if exclude_id != "custom":
                raise HTTPException(409, {"code": "name_taken"})
        endpoints = load_custom_endpoints()
        for it in endpoints:
            if exclude_id is not None and it.get("id") == exclude_id:
                continue
            if str(it.get("name") or "").strip().lower() == target:
                raise HTTPException(409, {"code": "name_taken"})

    def generate_endpoint_id(name: str, existing_ids: set[str]) -> str:
        has_ascii_letter = any("a" <= c <= "z" or "A" <= c <= "Z" for c in name)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24].strip("-")
        candidate = f"{USER_ENDPOINT_PREFIX}{slug}" if (has_ascii_letter and slug) else ""
        if candidate and candidate not in existing_ids:
            return candidate
        while True:
            candidate = f"{USER_ENDPOINT_PREFIX}{secrets.token_hex(3)}"
            if candidate not in existing_ids:
                return candidate

    def validate_endpoint_name(name: str) -> str:
        cleaned = name.strip()
        if not cleaned or len(cleaned) > 40 or not cleaned.isprintable() or "\n" in cleaned or "\r" in cleaned:
            raise HTTPException(422, "name must be a single printable line of 1 to 40 characters")
        return cleaned

    def validate_endpoint_url(url_raw: Any) -> str:
        try:
            return normalize_base_url(url_raw)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @router.post("/api/providers/custom-endpoints", dependencies=[Depends(require_auth)], status_code=201)
    async def create_custom_endpoint(body: EndpointNew) -> dict[str, Any]:
        endpoints = load_custom_endpoints()
        if len(endpoints) >= 20:
            raise HTTPException(409, {"code": "too_many"})
        url = validate_endpoint_url(body.base_url)
        transport = str(body.transport or "chat_completions").strip()
        if transport not in ("chat_completions", "responses"):
            raise HTTPException(422, "transport must be 'chat_completions' or 'responses'")
        name = validate_endpoint_name(body.name)
        check_name_available(name)
        existing_ids = {it["id"] for it in endpoints if "id" in it}
        endpoint_id = generate_endpoint_id(name, existing_ids)
        new_item = {"id": endpoint_id, "name": name, "base_url": url, "transport": transport}
        endpoints.append(new_item)
        save_custom_endpoints(endpoints)
        store.set_meta(f"base_url:{endpoint_id}", url)
        set_stored_base_url(endpoint_id, url)
        set_user_endpoints(endpoints)
        refused_fields.forget(url)
        forget(endpoint_id)
        return new_item

    @router.patch("/api/providers/custom-endpoints/{id}", dependencies=[Depends(require_auth)])
    async def update_custom_endpoint(id: str, body: EndpointUpdate) -> dict[str, Any]:
        endpoints = load_custom_endpoints()
        target = next((it for it in endpoints if it.get("id") == id), None)
        if target is None:
            raise HTTPException(404, f"unknown endpoint {id!r}")
        changed = False
        if body.name is not None:
            new_name = validate_endpoint_name(body.name)
            check_name_available(new_name, exclude_id=id)
            target["name"] = new_name
            changed = True
        if body.base_url is not None:
            new_url = validate_endpoint_url(body.base_url)
            target["base_url"] = new_url
            store.set_meta(f"base_url:{id}", new_url)
            set_stored_base_url(id, new_url)
            await drop_entry_if_moved(id, new_url)   # a key saved for the old host does not follow the endpoint to a new one
            refused_fields.forget(new_url)
            changed = True
        if changed:
            save_custom_endpoints(endpoints)
            set_user_endpoints(endpoints)
            forget(id)
        return {
            "id": id,
            "name": target["name"],
            "base_url": stored_base_url(id) or target.get("base_url", ""),
            "transport": target.get("transport", "chat_completions"),
        }

    @router.delete("/api/providers/custom-endpoints/{id}", dependencies=[Depends(require_auth)])
    async def delete_custom_endpoint(id: str) -> dict[str, Any]:
        endpoints = load_custom_endpoints()
        target = next((it for it in endpoints if it.get("id") == id), None)
        if target is None:
            raise HTTPException(404, f"unknown endpoint {id!r}")
        prof = store.profile()
        main_p = (prof.get("provider") or settings.provider or "").strip()
        fallback_p = (prof.get("fallback_provider") or "").strip()
        if main_p == id or fallback_p == id:
            raise HTTPException(409, {"code": "in_use"})
        endpoints = [it for it in endpoints if it.get("id") != id]
        save_custom_endpoints(endpoints)
        store.set_meta(f"base_url:{id}", "")
        set_stored_base_url(id, None)
        store.delete_key(id)
        if keyring.has(id):   # its key in the key container goes with it
            try:
                await keyring.delete(id)
            except KeyringError:
                raise HTTPException(503, "the key store is not reachable") from None
        forget(id)
        set_user_endpoints(endpoints)
        return {"ok": True}

    @router.put("/api/providers/custom/name", dependencies=[Depends(require_auth)])
    async def put_custom_name(body: CustomNameIn) -> dict[str, Any]:
        raw_name = (body.name or "").strip()
        if raw_name:
            if len(raw_name) > 40 or not raw_name.isprintable() or "\n" in raw_name or "\r" in raw_name:
                raise HTTPException(422, "name must be a single printable line of at most 40 characters")
            check_name_available(raw_name, exclude_id="custom")
            store.set_meta("custom_name", raw_name)
        else:
            store.set_meta("custom_name", "")
        return {"ok": True, "name": store.meta("custom_name") or "Custom"}

    # The address of an OpenAI-compatible server the user runs or chose (Ollama, LM Studio, vLLM, a gateway). LAN and private
    # addresses are allowed here on purpose; see registry._STORED_BASE_URLS for why this one setting skips netguard.
    async def drop_entry_if_moved(name: str, url: str) -> None:
        """The key in the key container was saved for the old address's host only: a new host drops it (the app asks for it again)."""
        if not keyring.has(name):
            return
        entry = await asyncio.to_thread(host_entry, url) if url else None
        if entry is not None and keyring.hosts.get(name) == entry[0]:
            return
        try:
            await keyring.delete(name)
        except KeyringError:
            raise HTTPException(503, "the key store is not reachable") from None

    @router.put("/api/providers/{name}/endpoint", dependencies=[Depends(require_auth)])
    async def put_endpoint(name: str, body: EndpointIn) -> dict[str, Any]:
        prov = url_provider(name)
        try:
            url = normalize_base_url(body.base_url)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        store.set_meta(f"base_url:{prov.name}", url)
        set_stored_base_url(prov.name, url)
        await drop_entry_if_moved(prov.name, url)
        refused_fields.forget(url)   # the address saved again: the server may take the thinking level now, so it is tried again
        forget(prov.name)
        return {"ok": True, "base_url": url}

    @router.delete("/api/providers/{name}/endpoint", dependencies=[Depends(require_auth)])
    async def remove_endpoint(name: str) -> dict[str, Any]:
        prov = url_provider(name)
        store.set_meta(f"base_url:{prov.name}", "")
        set_stored_base_url(prov.name, None)
        await drop_entry_if_moved(prov.name, prov.resolve_base_url())
        forget(prov.name)
        return {"ok": True, "base_url": prov.resolve_base_url()}   # what still applies: the environment's value, or ""

    @router.put("/api/providers/{name}/key", dependencies=[Depends(require_auth)])
    async def put_key(name: str, body: KeyIn) -> dict[str, bool]:
        key = body.api_key.strip()
        if not key.isprintable() or any(c.isspace() for c in key):  # also blocks header injection
            raise HTTPException(422, "API key must be a single line without spaces")
        prov = key_provider(name)
        if keyring.enabled:   # the key goes to the key container for this provider's own host only; the server keeps no copy
            entry = await asyncio.to_thread(host_entry, prov.resolve_base_url() or "")
            if entry is None:
                raise HTTPException(422, "set the address first")
            try:
                await keyring.put(prov.name, key, entry[0], entry[1], hint=key[-4:])
            except KeyringError:
                raise HTTPException(503, "the key store is not reachable") from None
            store.delete_key(prov.name)   # a leftover from before
        else:
            store.set_key(prov.name, key)
        forget(prov.name)
        return {"ok": True}

    @router.delete("/api/providers/{name}/key", dependencies=[Depends(require_auth)])
    async def remove_key(name: str) -> dict[str, bool]:
        prov = key_provider(name)
        if keyring.has(prov.name):
            try:
                await keyring.delete(prov.name)
            except KeyringError:
                raise HTTPException(503, "the key store is not reachable") from None
            store.delete_key(prov.name)
        elif not store.delete_key(prov.name):
            raise HTTPException(404, "no stored key")
        forget(prov.name)
        return {"ok": True}

    # The optional admin (billing) key of a provider (balances.ADMIN): it reads spend or the balance and nothing else. It lives only in
    # the key container. The check call decides: a key the provider refuses is removed again (422 key_refused); any other failure keeps
    # it, since the provider may just be down.
    def clear_account_cache(name: str) -> None:
        for ck in [ck for ck in account_cache if ck[0] == name]:
            account_cache.pop(ck, None)

    @router.put("/api/providers/{name}/admin-key", dependencies=[Depends(require_auth)])
    async def put_admin_key(name: str, body: AdminKeyIn) -> dict[str, Any]:
        prov = key_provider(name)
        if prov.name not in balances.ADMIN:
            raise HTTPException(404, f"{prov.name} has no admin key")
        if not keyring.enabled:   # admin keys live only in the key container
            raise HTTPException(409, {"code": "keyring_off"})
        key = body.api_key.strip()
        if not key.isprintable() or any(c.isspace() for c in key):  # also blocks header injection
            raise HTTPException(422, "admin key must be a single line without spaces")
        spec, adm = balances.ADMIN[prov.name], balances.admin_name(prov.name)
        try:
            await keyring.put(adm, key, [spec["host"]], False, hint=key[-4:], kind="admin", paths=spec["paths"])
        except KeyringError:
            raise HTTPException(503, "the key store is not reachable") from None
        checked = True
        try:
            await balances.admin_values(prov, transport=KeyringTransport(keyring, adm), resolver=balance_resolver)
        except balances.BalanceError as e:
            if e.refused:
                try:
                    await keyring.delete(adm)
                except KeyringError:
                    raise HTTPException(503, "the key store is not reachable") from None
                forget(prov.name)
                clear_account_cache(prov.name)
                raise HTTPException(422, {"code": "key_refused"})
            checked = False
        forget(prov.name)
        clear_account_cache(prov.name)
        return {"ok": True, "checked": checked}

    @router.delete("/api/providers/{name}/admin-key", dependencies=[Depends(require_auth)])
    async def remove_admin_key(name: str) -> dict[str, bool]:
        prov = key_provider(name)
        adm = balances.admin_name(prov.name)
        if prov.name not in balances.ADMIN or not keyring.has(adm):
            raise HTTPException(404, "no admin key")
        try:
            await keyring.delete(adm)
        except KeyringError:
            raise HTTPException(503, "the key store is not reachable") from None
        clear_account_cache(prov.name)
        return {"ok": True}

    # Sign out of a provider reached by signing in (Claude / agy CLI, Codex, Qwen, MiniMax): the stored login really goes (the CLI's own
    # sign-out in its sidecar, or the token file entry); the consent record stays. Refused while it is the main model; a fallback on it
    # is emptied, as the fallback rules do elsewhere.
    @router.delete("/api/providers/{name}/login", dependencies=[Depends(require_auth)])
    async def remove_login(name: str) -> dict[str, Any]:
        try:
            prov = get_provider(name)
        except KeyError:
            raise HTTPException(404, f"unknown provider {name!r}") from None
        is_cli = prov.name in (cli_manage.CLAUDE, cli_manage.AGY)
        if not is_cli and prov.name not in oauth_status():
            raise HTTPException(400, f"{prov.name} does not use a sign-in")
        if is_cli:
            cli = await cli_statuses.refresh(prov.name)
            signed_in = bool(cli.get("installed")) and cli.get("logged_in") is not False
        else:
            signed_in = bool(oauth_status()[prov.name].get("logged_in"))
        if not signed_in:
            raise HTTPException(404, "not signed in")
        prof = store.profile()
        if provider_key(prof["provider"] or settings.provider or "") == prov.name:
            raise HTTPException(409, "main_model_uses_login: this sign-in runs the main model; choose another main model first")
        try:
            if is_cli:
                res = await cli_manage.cli_logout(prov.name)
            else:
                res = {"removed": oauth_logout(prov.name), "still_signed_in": False}
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None
        if is_cli:
            cli_statuses.invalidate(prov.name, now=True)   # the next GET /api/providers shows it signed out
        fallback_cleared = bool(prof["fallback_provider"]) and provider_key(prof["fallback_provider"]) == prov.name
        if fallback_cleared:
            store.set_profile(fallback_provider="", fallback_model="")
        background_cleared = bool(prof["background_provider"]) and provider_key(prof["background_provider"]) == prov.name
        if background_cleared:   # background jobs go back to the main model
            store.set_profile(background_provider="", background_model="")
        forget(prov.name)
        usage_cache.pop(prov.name, None)
        return {"ok": True, "name": prov.name, "removed": res["removed"], "still_signed_in": res["still_signed_in"],
                "fallback_cleared": fallback_cleared, "background_cleared": background_cleared}

    # Sign in from the phone (providers/login_flows.py): start -> {kind: "device_code"|"url_code", url, user_code?, expires_in,
    # session}; the app opens the address, then either polls GET .../login/status (device_code) or sends the code the page showed
    # to POST .../login/finish (url_code). Needs the consent first (428 consent_required:<name>, as for choosing the model), and is
    # refused while already signed in (sign out first). Codes and tokens are never logged or returned.
    signins = login_flows or LoginFlows()

    def signin_provider(name: str) -> Any:
        try:
            prov = get_provider(name)
        except KeyError:
            raise HTTPException(404, f"unknown provider {name!r}") from None
        if prov.name not in SIGNIN_PROVIDERS:
            raise HTTPException(400, f"{prov.name} does not use a sign-in")
        return prov

    def signin_error(e: ProviderError) -> HTTPException:
        return HTTPException(e.status if isinstance(e.status, int) and 400 <= e.status < 600 else 502, str(e))

    async def after_signin(name: str, res: dict[str, Any]) -> dict[str, Any]:
        if res.get("state") == "done":
            forget(name)
            usage_cache.pop(name, None)
            cli_manage.forget_status(name)
            if name in (cli_manage.CLAUDE, cli_manage.AGY):
                res["account"] = (await cli_statuses.refresh(name)).get("account")
            else:
                res["account"] = oauth_status().get(name, {}).get("account")
        return res

    @router.post("/api/providers/{name}/login/start", dependencies=[Depends(require_auth)])
    async def start_login(name: str, body: LoginStartIn | None = None) -> dict[str, Any]:
        prov = signin_provider(name)
        if not unofficial_shown(prov.name):
            raise HTTPException(403, "Subscription sign-ins are off on this server. The owner can turn it on with `assistant enable oauth`.")
        if consent_of(prov.name) is None:
            raise HTTPException(428, f"consent_required:{prov.name}")
        if prov.name in (cli_manage.CLAUDE, cli_manage.AGY):
            cli = await cli_statuses.refresh(prov.name)
            signed_in = bool(cli.get("installed")) and cli.get("logged_in") is True
        else:
            signed_in = bool(oauth_status()[prov.name].get("logged_in"))
        if signed_in:
            raise HTTPException(409, "already_signed_in: " + i18n.t("Already signed in. Sign out first to use another account"))
        try:
            return await signins.start(prov.name, region=(body.region if body else "global"))
        except ProviderError as e:
            raise signin_error(e) from None

    @router.get("/api/providers/{name}/login/status", dependencies=[Depends(require_auth)])
    async def login_status(name: str, session: str = "") -> dict[str, Any]:
        prov = signin_provider(name)
        try:
            return await after_signin(prov.name, await signins.status(prov.name, session))
        except ProviderError as e:
            raise signin_error(e) from None

    @router.post("/api/providers/{name}/login/finish", dependencies=[Depends(require_auth)])
    async def finish_login(name: str, body: LoginFinishIn) -> dict[str, Any]:
        prov = signin_provider(name)
        try:
            return await after_signin(prov.name, await signins.finish(prov.name, body.session, body.code or body.redirect_url))
        except ProviderError as e:
            raise signin_error(e) from None

    @router.post("/api/providers/{name}/login/cancel", dependencies=[Depends(require_auth)])
    async def cancel_login(name: str, body: LoginFinishIn) -> dict[str, Any]:
        prov = signin_provider(name)
        return await signins.cancel(prov.name, body.session)

    ctx.add(cli_statuses=cli_statuses, cli_update_watch=cli_update_watch, prime_cli_statuses=prime_cli_statuses)
    return router
