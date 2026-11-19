"""The owner's profile (names, main / fallback / background model, tone) and language for background jobs."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException, Request, Response
from .. import app_updates
from pydantic import BaseModel, Field
from .. import i18n
from ..effort import ORDER as EFFORT_ORDER
from ..providers import get_provider
from ..providers.registry import REMOVED_PROVIDERS
from fastapi import APIRouter
from .context import AppContext
from .about import SERVER_HEADER, server_update_for


class LanguageIn(BaseModel):
    language: str


class ProfileIn(BaseModel):
    user_name: str | None = Field(default=None, max_length=60)
    agent_name: str | None = Field(default=None, max_length=60)
    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    fallback_provider: str | None = Field(default=None, max_length=80)
    fallback_model: str | None = Field(default=None, max_length=120)
    effort: str | None = Field(default=None, max_length=10)  # "" = provider default
    # the model for background jobs (usage.BACKGROUND_KINDS); both empty = the main model. Any provider, the main one included.
    background_provider: str | None = Field(default=None, max_length=80)
    background_model: str | None = Field(default=None, max_length=120)
    tone: str | None = Field(default=None, pattern="^(friendly|polite)$")
    # payment approvals need the phone's signature even from a device without an approval key (docs/approval-signing.md); default off
    require_signed_payments: bool | None = None


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    avatar = ctx.avatar
    consent_of = ctx.consent_of
    provider_key = ctx.provider_key
    provider_identity = ctx.provider_identity   # the main/fallback rule: a custom endpoint is its host
    require_auth = ctx.require_auth
    settings = ctx.settings
    store = ctx.store
    unofficial_name = ctx.unofficial_name
    unofficial_shown = ctx.unofficial_shown

    def model_ready(prof: dict[str, Any]) -> bool:
        """A main model is chosen: in the app, or as the server's default (ASSISTANT_PROVIDER / ASSISTANT_MODEL). The app
        leads a new owner to the model settings while this is false (the setup in the terminal no longer asks for one)."""
        return bool((prof.get("provider") or settings.provider) and (prof.get("model") or settings.model))

    def language_view() -> dict[str, Any]:
        return {"language": store.meta("ui_lang") or i18n.DEFAULT, "source": store.meta("ui_lang_from") or ("app" if store.meta("ui_lang") else ""),
                "available": i18n.available()}

    @router.get("/api/language", dependencies=[Depends(require_auth)])
    async def get_language() -> dict[str, Any]:
        """The owner's language: what notifications, the briefing, heads-ups and scheduled runs speak (i18n.py has the rule)."""
        return language_view()

    @router.put("/api/language", dependencies=[Depends(require_auth)])
    async def put_language(body: LanguageIn) -> dict[str, Any]:
        """The language the owner chose in the app (My → Language; "follow the phone": the phone's language). Only this changes it."""
        lang = next((c for c in i18n.candidates(body.language.strip()) if c in i18n.available()), None) if body.language.strip() else None
        if lang is None:
            raise HTTPException(422, f"unknown language: {body.language[:20]!r}")
        store.set_meta("ui_lang", lang)
        store.set_meta("ui_lang_from", "picked")
        return language_view()

    def signing_view() -> dict[str, Any]:
        return {"require_signed_payments": store.meta("require_signed_payments") == "1", "first_chat_done": store.meta("first_chat_done") == "1"}

    @router.get("/api/profile", dependencies=[Depends(require_auth)])
    async def get_profile(request: Request, response: Response) -> dict[str, Any]:
        update = await ctx.app_updates.for_header(request.headers.get("x-kelo-app"), request.headers.get(app_updates.CHANNEL_HEADER))
        if update:
            response.headers[app_updates.HEADER] = update   # a newer released app that works with this server (app_updates.py)
        if server_update := server_update_for(ctx, request):
            response.headers[SERVER_HEADER] = server_update   # a newer server release the caller may install (api/about.py)
        prof = store.profile()
        return {**prof, **signing_view(), "avatar_version": avatar.version(), "model_ready": model_ready(prof),
                "min_app_version": dict(settings.min_app_version)}

    @router.put("/api/profile", dependencies=[Depends(require_auth)])
    async def put_profile(body: ProfileIn) -> dict[str, Any]:
        if body.effort not in (None, "", *EFFORT_ORDER):
            raise HTTPException(422, f"effort must be one of: {', '.join(EFFORT_ORDER)} (or empty)")
        fields = body.model_dump(exclude_none=True)
        if "require_signed_payments" in fields:
            store.set_meta("require_signed_payments", "1" if fields.pop("require_signed_payments") else "")
        cur = store.profile()
        if "background_provider" in fields or "background_model" in fields:
            bp = fields.get("background_provider", cur["background_provider"]).strip()
            bm = fields.get("background_model", cur["background_model"] if bp == cur["background_provider"] else "").strip()
            if bp and not bm:
                raise HTTPException(422, "the background model needs a model as well as a provider (or neither, for the main model)")
            if bm and not bp:
                raise HTTPException(422, "the background model needs a provider")
            if bp:
                try:
                    get_provider(bp)
                except KeyError:
                    raise HTTPException(422, f"unknown provider {bp!r} for the background model") from None
                if bp.strip().lower() in REMOVED_PROVIDERS:
                    raise HTTPException(422, f"{bp} is no longer offered")
            fields["background_provider"], fields["background_model"] = bp, bm
        for key in ("provider", "fallback_provider", "background_provider"):
            chosen = (fields.get(key) or "").strip()
            real = unofficial_name(chosen) if chosen else None
            if real and chosen != cur.get(key) and not unofficial_shown(real):
                raise HTTPException(403, f"unofficial_disabled:{real}: signing in with a subscription account is turned off on this server. "
                                         "The owner can turn it on with `assistant enable oauth`.")
            if real and chosen != cur.get(key) and consent_of(real) is None:
                raise HTTPException(428, f"consent_required:{real}")   # the app shows the notice, records consent, then retries
        if "provider" in fields or "fallback_provider" in fields:
            cur = store.profile()
            main = (fields.get("provider", cur["provider"]) or settings.provider).strip()   # empty = the server's default provider
            fb = fields.get("fallback_provider", cur["fallback_provider"]).strip()
            if main and fb and provider_identity(main) == provider_identity(fb):
                chosen = fields.get("fallback_provider", "").strip()
                if chosen and chosen != cur["fallback_provider"]:   # this very request picked the same provider for both
                    raise HTTPException(422, "the fallback model must come from a different provider than the main model")
                fields["fallback_provider"], fields["fallback_model"] = "", ""   # the main model moved onto the fallback's provider: empty the fallback
        prof = store.set_profile(**fields)
        return {**prof, **signing_view(), "avatar_version": avatar.version(), "model_ready": model_ready(prof),
                "min_app_version": dict(settings.min_app_version)}

    return router
