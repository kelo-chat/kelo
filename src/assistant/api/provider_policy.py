"""Which AI providers may be listed and used here: the unofficial subscription sign-ins and the consent each needs.

A service (no routes): /api/providers, /api/profile and the review-mode stream filter read it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..providers import get_provider

# Providers reached by signing in to a consumer subscription/account, where the vendor has not confirmed that use in other apps is
# allowed. The app lists them under an "unofficial support" divider and asks once per provider (notice version) before use.
UNOFFICIAL = {"claude-subscription-directsdk-experimental": "Anthropic", "antigravity-oauth": "Google",
              "openai-codex": "OpenAI", "qwen-oauth": "Alibaba", "minimax-oauth": "MiniMax"}
CONSENT_VERSION = 1


class ProviderPolicy:
    def __init__(self, store: Any, settings: Any, provider_key: Callable[[str], str]):
        self.store = store
        self.settings = settings
        self.provider_key = provider_key

    @staticmethod
    def unofficial_name(name: str) -> str | None:
        try:
            real = get_provider(name).name
        except KeyError:
            return None
        return real if real in UNOFFICIAL else None

    # Off by default (`assistant enable oauth` turns them on): an unofficial provider is then left out of /api/providers and cannot be
    # newly chosen. One the profile already uses (main, fallback, or the server's default) stays listed and keeps working, so turning
    # the setting off never breaks a copy that runs on a sign-in.
    def unofficial_in_use(self) -> set[str]:
        prof = self.store.profile()
        return {self.provider_key(n) for n in (prof["provider"] or self.settings.provider, prof["fallback_provider"],
                                                prof["background_provider"]) if n}

    def unofficial_shown(self, name: str) -> bool:
        if self.settings.review_mode and name in UNOFFICIAL:   # review.py: never, not even one the profile still names
            return False
        return self.settings.unofficial_providers or name not in UNOFFICIAL or name in self.unofficial_in_use()

    def consent_of(self, name: str) -> dict[str, Any] | None:
        try:
            v = json.loads(self.store.meta(f"consent:{name}") or "null")
        except ValueError:
            return None
        return v if isinstance(v, dict) and v.get("version", 0) >= CONSENT_VERSION else None
