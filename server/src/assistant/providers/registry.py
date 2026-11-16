"""Provider registry loaded from providers.json (+ user overrides)."""

from __future__ import annotations

import json
import re
import os
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

from .oauth import OAUTH_PROVIDERS

# Wire protocol -> implemented?
IMPLEMENTED_TRANSPORTS = {"chat_completions", "anthropic_messages", "codex_responses", "responses",
                          "bedrock_converse", "cli_claude", "cli_agy"}
# Auth kinds we deliberately do or do not support.
SUPPORTED_AUTH = {"api_key", "none", "aws_sdk", "vertex", "cli"}

# Taken out of the catalog (2026-11). A profile that still names one is reset to the server default at start-up.
REMOVED_PROVIDERS = frozenset({
    "actual", "arcee", "solstice", "commandcode", "commandcode-anthropic", "gmi", "nous", "stepfun", "tencent-tokenhub",
    "router", "meta-ai", "alibaba-cn", "alibaba-coding-plan-cn", "alibaba-token-plan-cn", "minimax-cn", "xiaomi",
    "ai-gateway", "kilocode", "opencode-zen", "opencode-go",
    # their family / variant spellings and aliases
    "commandcode:openai", "commandcode:anthropic", "opencode", "opencode:zen", "opencode:go", "alibaba:cn",
    "alibaba:coding-plan-cn", "alibaba:token-plan-cn", "minimax:cn", "minimax:global", "ramp-router", "ramp", "router.com",
    "vercel", "vercel-ai-gateway", "actual-computer", "aci",
})

# Providers that work without an API key (local servers).
KEYLESS_OK = {"custom", "lmstudio"}

# Endpoints the owner added by name (backlog 31): each its own provider "custom-<slug>" (OpenAI-compatible, the address typed in the
# app). Kept in the database (meta "custom_endpoints"), registered here at start-up and on every change, so get_provider and the lists
# see them like the built-in address-less providers.
_USER_ENDPOINTS: dict[str, Provider] = {}
USER_ENDPOINT_PREFIX = "custom-"

# Base URLs the user typed in the app (stored in the database, loaded by the server at start-up and on every change).
# They win over the environment and the data file. Only providers that take a URL (`Provider.takes_url`) get one.
#
# These addresses are deliberately NOT run through netguard's public-address rule: a custom endpoint is the user's own
# choice and is usually on their own network (Ollama / LM Studio / vLLM on 192.168.x.x, host.docker.internal, a Tailscale
# 100.x address). Only an authenticated owner can set one (no agent tool can), the provider client never follows redirects,
# and the URL is used only for that provider's model list and chat calls. Every other outbound request keeps netguard's rules.
_STORED_BASE_URLS: dict[str, str] = {}


def set_stored_base_url(name: str, url: str | None) -> None:
    if url:
        _STORED_BASE_URLS[name] = strip_operation(url.rstrip("/"))   # an address saved with ".../chat/completions" before 2026-11-15
    else:
        _STORED_BASE_URLS.pop(name, None)


def stored_base_url(name: str) -> str:
    return _STORED_BASE_URLS.get(name, "")


def clear_stored_base_urls() -> None:
    _STORED_BASE_URLS.clear()


# Where the data file's base_url is not the OpenAI-compatible path.
# Google documents an OpenAI-compatible surface under /openai.
_BASE_URL_FIXUPS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


@dataclass(slots=True)
class Provider:
    name: str
    display_name: str
    transport: str
    auth_type: str
    base_url: str
    api_key_envs: list[str] = field(default_factory=list)
    base_url_envs: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    default_headers: dict[str, str] = field(default_factory=dict)
    default_max_tokens: int | None = None
    signup_url: str = ""

    @property
    def takes_url(self) -> bool:
        """The user supplies the address: an OpenAI-compatible endpoint with no fixed base URL in the data file."""
        return self.transport in ("chat_completions", "responses") and not self.base_url and self.auth_type in ("api_key", "none")

    @property
    def supported(self) -> bool:
        return self.unsupported_reason() is None

    def unsupported_reason(self) -> str | None:
        if self.auth_type == "oauth_external" and self.name in OAUTH_PROVIDERS:
            pass  # openai-codex / qwen-oauth / minimax-oauth: see oauth.py
        elif self.auth_type not in SUPPORTED_AUTH:
            return f"auth type {self.auth_type!r} not implemented"
        if self.transport not in IMPLEMENTED_TRANSPORTS:
            return f"wire protocol {self.transport!r} not implemented"
        return None

    def resolve_api_key(self, explicit: str | None = None, env: dict[str, str] | None = None) -> str | None:
        if explicit:
            return explicit
        env = os.environ if env is None else env
        for var in self.api_key_envs:
            if env.get(var):
                return env[var]
        return None

    def resolve_base_url(self, explicit: str | None = None, env: dict[str, str] | None = None) -> str:
        """explicit argument, then the address stored in the app, then the environment, then the data file."""
        if explicit:
            return explicit.rstrip("/")
        if self.takes_url and (stored := _STORED_BASE_URLS.get(self.name)):
            return stored
        env = os.environ if env is None else env
        for var in self.base_url_envs:
            if env.get(var):
                return env[var].rstrip("/")
        return self.base_url.rstrip("/")


def _from_dict(d: dict[str, Any]) -> Provider:
    base = _BASE_URL_FIXUPS.get(d["name"], d.get("base_url", ""))
    return Provider(
        name=d["name"],
        display_name=d.get("display_name") or d["name"],
        transport=d["transport"],
        auth_type=d["auth_type"],
        base_url=base,
        api_key_envs=list(d.get("api_key_envs", [])),
        base_url_envs=list(d.get("base_url_envs", [])),
        aliases=list(d.get("aliases", [])),
        default_headers=dict(d.get("default_headers", {})),
        default_max_tokens=d.get("default_max_tokens"),
        signup_url=d.get("signup_url", ""),
    )


# Not in Hermes' provider list.
_EXTRA = [
    {"name": "openai", "display_name": "OpenAI (API key)", "transport": "chat_completions", "auth_type": "api_key",
     "base_url": "https://api.openai.com/v1", "api_key_envs": ["OPENAI_API_KEY"], "base_url_envs": ["OPENAI_BASE_URL"]},
    # Subscription CLIs (see cli_agents.py): auth belongs to the installed CLI.
    {"name": "claude-subscription-directsdk-experimental", "display_name": "Claude subscription",
     "transport": "cli_claude", "auth_type": "cli", "base_url": "", "aliases": ["claude-subscription", "claude-directsdk", "claude-cli"]},
    {"name": "antigravity-oauth", "display_name": "Google Antigravity (agy CLI)",
     "transport": "cli_agy", "auth_type": "cli", "base_url": "", "aliases": ["agy", "antigravity"]},
]


@lru_cache(maxsize=1)
def _load_base() -> dict[str, Provider]:
    raw = json.loads(resources.files("assistant.providers").joinpath("providers.json").read_text("utf-8"))
    table: dict[str, Provider] = {}
    for d in [*raw, *_EXTRA]:
        p = _from_dict(d)
        table[p.name] = p
    return table


def _load() -> dict[str, Provider]:
    return {**_load_base(), **_USER_ENDPOINTS}


def list_providers() -> list[Provider]:
    """Every concrete endpoint (all regional/plan variants)."""
    return sorted(_load().values(), key=lambda p: p.name)


# ---- families: one user-facing provider with region/plan variants ----
# variant name -> concrete provider name; the first variant is the default.
_FAMILIES: dict[str, tuple[str, dict[str, str]]] = {
    "alibaba": ("Alibaba Cloud (Qwen)", {
        "intl": "alibaba", "coding-plan": "alibaba-coding-plan", "token-plan": "alibaba-token-plan"}),
    "google": ("Google", {"ai-studio": "gemini", "vertex": "vertex"}),
    "custom": ("Custom endpoint", {"generic": "custom", "lmstudio": "lmstudio"}),
}


@dataclass(slots=True)
class Family:
    name: str
    display_name: str
    variants: dict[str, Provider]  # first = default

    @property
    def default(self) -> Provider:
        return next(iter(self.variants.values()))

    @property
    def takes_url(self) -> bool:
        """The user supplies the address: an OpenAI-compatible endpoint with no fixed base URL in the data file."""
        return self.transport in ("chat_completions", "responses") and not self.base_url and self.auth_type in ("api_key", "none")

    @property
    def supported(self) -> bool:
        return any(p.supported for p in self.variants.values())


def list_families() -> list[Family]:
    """The user-facing list: variants folded under one name, everything else as itself."""
    table = _load()
    grouped = {n for _, v in _FAMILIES.values() for n in v.values()}
    out = [
        Family(name, display, {v: table[c] for v, c in variants.items() if c in table})
        for name, (display, variants) in _FAMILIES.items()
    ]
    out += [Family(p.name, p.display_name, {"default": p}) for p in table.values() if p.name not in grouped]
    return sorted(out, key=lambda f: f.name)


def get_provider(name: str) -> Provider:
    """Resolve 'openrouter', a legacy name like 'gemini', a family ('google'), or 'family:variant'."""
    table = _load()
    key = name.strip().lower()
    if key in table:
        return table[key]
    if ":" in key:
        family, _, variant = key.partition(":")
        if family in _FAMILIES and variant in _FAMILIES[family][1]:
            return table[_FAMILIES[family][1][variant]]
        if family in _FAMILIES:
            raise KeyError(f"unknown variant {variant!r} for {family!r}; choose from {', '.join(_FAMILIES[family][1])}")
    if key in _FAMILIES:
        return table[next(iter(_FAMILIES[key][1].values()))]
    for p in table.values():
        if key in (a.lower() for a in p.aliases):
            return p
    raise KeyError(f"unknown provider {name!r}")


def custom_provider(
    base_url: str,
    transport: str = "chat_completions",
    name: str = "custom",
    headers: dict[str, str] | None = None,
) -> Provider:
    """Any endpoint: Ollama, vLLM, llama.cpp, LM Studio, a company gateway..."""
    return Provider(
        name=name,
        display_name=name,
        transport=transport,
        auth_type="api_key",
        base_url=base_url.rstrip("/"),
        default_headers=headers or {},
    )


def set_user_endpoints(items: list[dict[str, str]]) -> None:
    """[{"id": "custom-<slug>", "name": "<display>", "transport": "chat_completions"}] -> registered providers (replaces the old set)."""
    _USER_ENDPOINTS.clear()
    for it in items:
        pid = str(it.get("id") or "")
        if not pid.startswith(USER_ENDPOINT_PREFIX):
            continue
        p = custom_provider("", transport=str(it.get("transport") or "chat_completions"), name=pid)
        p.display_name = str(it.get("name") or pid)[:40]
        _USER_ENDPOINTS[pid] = p


MAX_BASE_URL = 300


# The operation a provider's docs show at the end of a full request address ("https://host/v1/chat/completions" in a curl example).
# People paste that; the base is what comes before it (the client adds /chat/completions, /models, ... itself).
_OPERATION = re.compile(r"/(?:chat/completions|completions|responses|models|embeddings)$", re.I)


def strip_operation(url: str) -> str:
    """The base of a pasted full request address: "https://h/v1/chat/completions" -> "https://h/v1"."""
    return _OPERATION.sub("", url)


def normalize_base_url(text: Any) -> str:
    """A user-typed endpoint as `http(s)://host[:port][/path]` without a trailing slash, or ValueError with the reason.

    Private and LAN addresses are accepted on purpose (see `_STORED_BASE_URLS`); cloud metadata link-local addresses are not.
    """
    import ipaddress
    from urllib.parse import urlsplit

    if not isinstance(text, str) or not text.strip():
        raise ValueError("base_url is required (example: http://192.168.0.10:11434/v1)")
    url = text.strip()
    if len(url) > MAX_BASE_URL:
        raise ValueError(f"base_url is too long (at most {MAX_BASE_URL} characters)")
    if any(c.isspace() for c in url) or not url.isprintable():
        raise ValueError("base_url must not contain spaces or control characters")
    if "\\" in url:
        raise ValueError("base_url must not contain backslashes")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("base_url must start with http:// or https://")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError("base_url must not contain a user name or password; enter the API key separately")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise ValueError("base_url must not contain a query (?) or fragment (#)")
    host = parts.hostname or ""
    if not host:
        raise ValueError("base_url needs a host name or address")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("base_url has an invalid port") from None
    if port == 0:
        raise ValueError("base_url has an invalid port")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError("base_url points at a link-local, multicast or unspecified address; use the server's real address")
    shown_host = f"[{host}]" if ip is not None and ip.version == 6 else host.lower()
    path = strip_operation(parts.path.rstrip("/")).rstrip("/")
    return f"{scheme}://{shown_host}{f':{port}' if port is not None else ''}{path}"
