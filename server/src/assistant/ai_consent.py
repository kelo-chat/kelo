"""Asking once before chats go to an AI service (App Store Review Guideline 5.1.2(i): "You must clearly disclose where personal data
will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so").

A reply is written by the AI service behind the model the owner picked. Before the first message from this server ever goes to one,
the apps say so (naming the service chosen now, with the privacy policy) and ask: Agree / Not now. "Not now" sends nothing. The answer
is kept once for the whole server (which device agreed, and when), so another phone does not ask again. Changing the model later does
not ask again: picking a service is itself the owner's choice of where chats go, and the model picker and the Model page always say
"Your chats go to <service>". A model on the owner's own computer or network (LM Studio, Ollama, a custom endpoint at a private
address) sends nothing to a company, so it needs no answer until a service on the internet is chosen.

The check runs only for requests that ask for it (`"ai_consent": "check"` on POST /api/talk and /api/turns, sent by the Android, iPhone
and desktop apps): a held-back message gets 428 `{"code": "ai_consent_required", "company": ..., "privacy_url": ...}` before anything
is stored or sent. API clients and older apps are not asked.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import socket
import time
from collections.abc import Callable
from typing import Any

from .tools.sites import in_public_dns
from urllib.parse import urlsplit

from .providers.registry import get_provider

VERSION = 1
PRIVACY_URL = "https://kelo.chat/privacy"
CODE = "ai_consent_required"

# provider (its canonical name in the registry) -> the company that receives the conversation
COMPANIES: dict[str, str] = {
    "anthropic": "Anthropic", "claude-subscription-directsdk-experimental": "Anthropic",
    "openai": "OpenAI", "openai-codex": "OpenAI",
    "gemini": "Google", "vertex": "Google", "antigravity-oauth": "Google",
    "alibaba": "Alibaba Cloud", "alibaba-coding-plan": "Alibaba Cloud", "alibaba-token-plan": "Alibaba Cloud", "qwen-oauth": "Alibaba Cloud",
    "minimax": "MiniMax", "minimax-oauth": "MiniMax",
    "azure-foundry": "Microsoft Azure", "bedrock": "Amazon Web Services",
    "deepinfra": "DeepInfra", "deepseek": "DeepSeek", "fireworks": "Fireworks AI", "huggingface": "Hugging Face",
    "nebius-token-factory": "Nebius", "novita": "Novita AI", "opengateway": "OpenGateway", "nvidia": "NVIDIA", "ollama-cloud": "Ollama",
    "openrouter": "OpenRouter", "upstage": "Upstage", "xai": "xAI", "zai": "Z.ai", "mistral": "Mistral AI",
    "groq": "Groq", "cerebras": "Cerebras", "together": "Together AI",
}

ROLES = (("provider", "main"), ("fallback_provider", "fallback"), ("background_provider", "background"))


def is_private_host(host: str) -> bool:
    """An address on the owner's own computer or network: localhost, private / Tailscale (100.64/10) ranges, a name without a dot
    (a Docker service such as `freellmapi`), a name outside the public DNS, or a public name that resolves into those ranges."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or not in_public_dns(host) and not _is_ip(host):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return _resolves_private(host)
    return _private_ip(ip)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10")


@functools.lru_cache(maxsize=256)
def _resolves_private(host: str) -> bool:
    """A name in the public DNS that points into the owner's own network (a tailnet name resolves to 100.64.0.0/10)."""
    try:
        found = {i[4][0] for i in socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)}
    except OSError:
        return False
    return any(_private_ip(ipaddress.ip_address(a.split("%")[0])) for a in found)


def company_of(provider: str) -> str | None:
    """The company a provider sends the conversation to, or None when it stays on the owner's own hardware or network."""
    try:
        p = get_provider(provider)
    except KeyError:
        return provider.strip() or None
    if p.name in COMPANIES:
        return COMPANIES[p.name]
    if p.takes_url or p.name in ("custom", "lmstudio"):   # an address the owner typed: a company only when it is out on the internet
        host = urlsplit(p.resolve_base_url()).hostname or ""
        return None if is_private_host(host) else host
    return p.display_name or p.name


META_KEY = "ai_consent"


class ConsentBook:
    """The one answer, kept in the server's small settings table (`meta`)."""

    def __init__(self, meta: Callable[[str], str], set_meta: Callable[[str, str], None]) -> None:
        self._meta, self._set_meta = meta, set_meta

    def get(self) -> dict[str, Any] | None:
        try:
            v = json.loads(self._meta(META_KEY) or "null")
        except ValueError:
            return None
        return v if isinstance(v, dict) and int(v.get("version") or 0) >= VERSION else None

    def accept(self, device: str, company: str | None, now: float | None = None) -> dict[str, Any]:
        """Records the agreement with the device that gave it and the service named on its screen."""
        rec = {"accepted_at": time.time() if now is None else now, "device": device, "company": company or "", "version": VERSION}
        self._set_meta(META_KEY, json.dumps(rec))
        return rec

    def withdraw(self) -> bool:
        had = bool(self._meta(META_KEY))
        self._set_meta(META_KEY, "")
        return had

    @staticmethod
    def in_use(profile: dict[str, Any], default_provider: str = "") -> list[dict[str, Any]]:
        """The companies the chosen models send to, main first: [{"company", "providers", "roles"}]."""
        out: dict[str, dict[str, Any]] = {}
        for field, role in ROLES:
            name = (profile.get(field) or (default_provider if field == "provider" else "") or "").strip()
            company = company_of(name) if name else None
            if not company:
                continue
            row = out.setdefault(company.lower(), {"company": company, "providers": [], "roles": []})
            if name not in row["providers"]:
                row["providers"].append(name)
            row["roles"].append(role)
        return list(out.values())

    def status(self, profile: dict[str, Any], default_provider: str = "") -> dict[str, Any]:
        rows = self.in_use(profile, default_provider)
        rec = self.get()
        return {"version": VERSION, "privacy_url": PRIVACY_URL, "accepted": rec is not None, "record": rec,
                "company": rows[0]["company"] if rows else None, "companies": rows,
                "needed": rec is None and bool(rows)}

    def required(self, profile: dict[str, Any], default_provider: str = "") -> dict[str, Any] | None:
        """The 428 detail while nothing is agreed and a chosen model would send to a company, else None."""
        st = self.status(profile, default_provider)
        if not st["needed"]:
            return None
        return {"code": CODE, "company": st["company"], "privacy_url": PRIVACY_URL, "version": VERSION}
