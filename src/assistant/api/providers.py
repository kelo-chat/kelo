"""AI providers: the list the model picker shows, consent for unofficial sign-ins, API keys and endpoints, signing in and out from the phone, and keeping the claude / agy CLI sidecars current."""

from __future__ import annotations

import asyncio
import json
import logging
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
from ..providers.registry import normalize_base_url, set_stored_base_url
from ..providers import refused as refused_fields
from ..api.provider_policy import CONSENT_VERSION, UNOFFICIAL
from ..server import CLI_UPDATE_NOTE_DAYS
from fastapi import APIRouter
from .context import AppContext


class CliUpdateIn(BaseModel):
    version: str | None = Field(default=None, max_length=20)


class EndpointIn(BaseModel):
    base_url: Any = ""


class KeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


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
    provider_key = ctx.provider_key
    require_auth = ctx.require_auth
    require_owner = ctx.require_owner
    settings = ctx.settings
    store = ctx.store
    unofficial_name = ctx.unofficial_name
    unofficial_shown = ctx.unofficial_shown
    usage_cache = ctx.usage_cache

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
        for f in shown:
            p = f.default
            key = store.key(p.name) if p.name in stored else None
            cli = clis.get(p.name)
            if cli:
                cli = {**cli, **cli_release(p.name, cli)}
                # the app's 업데이트 button: an owner sign-in and a sidecar that can update itself (POST .../cli/update)
                cli["updatable"] = bool(owner and cli.get("remote") and cli.get("self_update") and cli.get("installed"))
            out.append({
                "cli": cli,
                "name": f.name, "display_name": f.display_name, "supported": True,
                "oauth": p.name in logins, "logged_in": logins.get(p.name, {}).get("logged_in"),
                # one answer for every sign-in provider (CLI or OAuth): True / False / None (unknown, or not a sign-in provider),
                # and the account's e-mail when the sign-in says it. DELETE /api/providers/{name}/login signs out.
                "signed_in": (cli.get("logged_in") if cli.get("installed") else False) if cli else logins.get(p.name, {}).get("logged_in"),
                "account": (cli or {}).get("account") or logins.get(p.name, {}).get("account"),
                "configured": (cli["installed"] and cli["logged_in"] is not False) if cli else is_configured(p, key),
                "effort_levels": list(effort_levels(p.name)), "effort_note": effort_note(p.name),
                "takes_key": p.auth_type == "api_key" and p.name not in logins and (bool(p.api_key_envs) or p.takes_url),
                "key_source": "app" if key else ("env" if p.resolve_api_key() else None),
                "key_hint": key[-4:] if key else None,  # last 4 chars only; the key itself is never returned
                "takes_url": p.takes_url,
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

    # The address of an OpenAI-compatible server the user runs or chose (Ollama, LM Studio, vLLM, a gateway). LAN and private
    # addresses are allowed here on purpose; see registry._STORED_BASE_URLS for why this one setting skips netguard.
    @router.put("/api/providers/{name}/endpoint", dependencies=[Depends(require_auth)])
    async def put_endpoint(name: str, body: EndpointIn) -> dict[str, Any]:
        prov = url_provider(name)
        try:
            url = normalize_base_url(body.base_url)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        store.set_meta(f"base_url:{prov.name}", url)
        set_stored_base_url(prov.name, url)
        refused_fields.forget(url)   # the address saved again: the server may take the thinking level now, so it is tried again
        forget(prov.name)
        return {"ok": True, "base_url": url}

    @router.delete("/api/providers/{name}/endpoint", dependencies=[Depends(require_auth)])
    async def remove_endpoint(name: str) -> dict[str, Any]:
        prov = url_provider(name)
        store.set_meta(f"base_url:{prov.name}", "")
        set_stored_base_url(prov.name, None)
        forget(prov.name)
        return {"ok": True, "base_url": prov.resolve_base_url()}   # what still applies: the environment's value, or ""

    @router.put("/api/providers/{name}/key", dependencies=[Depends(require_auth)])
    async def put_key(name: str, body: KeyIn) -> dict[str, bool]:
        key = body.api_key.strip()
        if not key.isprintable() or any(c.isspace() for c in key):  # also blocks header injection
            raise HTTPException(422, "API key must be a single line without spaces")
        prov = key_provider(name)
        store.set_key(prov.name, key)
        forget(prov.name)
        return {"ok": True}

    @router.delete("/api/providers/{name}/key", dependencies=[Depends(require_auth)])
    async def remove_key(name: str) -> dict[str, bool]:
        prov = key_provider(name)
        if not store.delete_key(prov.name):
            raise HTTPException(404, "no stored key")
        forget(prov.name)
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
