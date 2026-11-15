"""Discover which models a provider actually offers, using the credentials already configured."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from . import effort
from .i18n import t
from .providers import cli_manage, oauth, releases
from .providers.client import ANTHROPIC_VERSION
from .providers.registry import KEYLESS_OK, USER_ENDPOINT_PREFIX, Provider
from .providers.types import ListUnavailable, ProviderError
from .windows import remember_provider_windows, window_from_listing

CACHE_SECONDS = 300.0
_cache: dict[str, tuple[float, list[str], str | None]] = {}   # provider -> (when, models, Codex client version the list was asked with)

_labels: dict[str, dict[str, str]] = {}

# The last good list of each provider, shown (marked stale) while a fresh one cannot be fetched. Also saved by the server (store meta
# "models_last:<provider>") so it survives a restart: {"models", "labels", "effort", "fetched_at" (epoch seconds)}.
_good: dict[str, dict[str, Any]] = {}
# provider -> (failures in a row, monotonic time before which the list is not asked again)
_backoff: dict[str, tuple[int, float]] = {}
# provider -> the short list that would have replaced a much longer last good one: taken once a second refresh says the same
_shrunk: dict[str, list[str]] = {}
BACKOFF_FIRST = 30.0
BACKOFF_MAX = 1800.0
SHRINK_MIN = 8          # a last good list this long or longer ...
SHRINK_RATIO = 0.5      # ... is not replaced by one under half its length until a second refresh confirms it


def _now() -> float:
    return time.monotonic()


def forget(provider: str) -> None:
    _cache.pop(provider, None)
    _labels.pop(provider, None)
    _good.pop(provider, None)
    _backoff.pop(provider, None)
    _shrunk.pop(provider, None)
    effort.forget(provider)


# ---- One way to write a model's name, shared with the apps (Android ModelNames, desktop ModelNames, iOS ModelNames): the
# server fills the label of every model its provider gave no name for with model_label(), and the apps use the same rule only
# before they have ever fetched a list (tests/fixtures/model_labels.json holds the cases every copy is tested against).
# "gemini-3.8-flash-medium" -> "Gemini 3.8 Flash (Medium)", "sonnet" -> "Sonnet (latest)", "gpt-oss-120b" -> "GPT-OSS 120B",
# "anthropic/claude-3-5-haiku-20241022" -> "Claude 3.5 Haiku", "qwen3:8b" -> "Qwen3 8B".
_ACRONYMS = {"gpt", "glm", "oss", "ai", "vl", "moe", "api"}
_DATE_TAIL = re.compile(r"[-_](20[0-9]{6}|20[0-9]{2}-[0-9]{2}-[0-9]{2})$")
_SIZE = re.compile(r"^[0-9]+(\.[0-9]+)?[bkmtBKMT]$")
_OPENAI_O = re.compile(r"^o[0-9]+$")
_LATEST_ALIASES = ("sonnet", "opus", "haiku")             # the claude CLI's names for the newest of each family
_TAIL_WORDS = ("minimal", "low", "medium", "high", "xhigh", "max", "thinking")   # a thinking level written into the id


def _digits(s: str) -> bool:
    return bool(s) and all("0" <= c <= "9" for c in s)


def _word(w: str) -> str:
    if w.lower() in _ACRONYMS or _SIZE.match(w):
        return w.upper()
    if _OPENAI_O.match(w):
        return w
    return w[0].upper() + w[1:] if w[0].isalpha() else w


def model_label(mid: str) -> str:
    """A model's name as people read it, from its id alone (see the block above). Never empty."""
    s = mid.strip().removeprefix("models/").rsplit("/", 1)[-1]
    s = _DATE_TAIL.sub("", s)
    if s.lower() in _LATEST_ALIASES:
        return f"{s[0].upper()}{s[1:].lower()} (latest)"
    parts = [p for p in re.split(r"[-_ :]", s) if p]
    if not parts:
        return mid.strip()
    merged: list[str] = []
    for p in parts:   # a version written with dashes ("5-5", "3-5"): one or two digits right after a short number
        prev = merged[-1] if merged else ""
        if prev and len(p) <= 2 and _digits(p) and len(prev) <= 3 and all("0" <= c <= "9" or c == "." for c in prev):
            merged[-1] = f"{prev}.{p}"
        else:
            merged.append(p)
    tail = ""
    if len(merged) >= 2 and merged[-1].lower() in _TAIL_WORDS:
        last = merged.pop()
        tail = f" ({last[0].upper()}{last[1:].lower()})"
    words: list[str] = []
    i = 0
    while i < len(merged):
        if merged[i].lower() == "gpt" and i + 1 < len(merged):   # OpenAI writes GPT-5.1, GPT-4o, GPT-OSS
            words.append(f"GPT-{_word(merged[i + 1])}")
            i += 2
        else:
            words.append(_word(merged[i]))
            i += 1
    return " ".join(words) + tail


def label_all(models: list[str], given: dict[str, str]) -> dict[str, str]:
    """A label for every model: the provider's own name when it gave one, else model_label()."""
    out = {}
    for m in models:
        name = (given.get(m) or "").strip()
        out[m] = name if name and name != m else model_label(m)
    return out


def labels(provider: str) -> dict[str, str]:
    """Display names for the last model list of a provider (only some providers give any)."""
    return _labels.get(provider, {})


def is_configured(prov: Provider, stored_key: str | None = None) -> bool | None:
    """True/False when we can tell from local state; None for providers whose auth we cannot probe."""
    if prov.name in oauth.OAUTH_PROVIDERS:
        return bool(oauth.status().get(prov.name, {}).get("logged_in"))
    if prov.takes_url and not prov.resolve_base_url():
        return False   # an endpoint the user must point somewhere: nothing works until it has an address
    if prov.auth_type == "api_key":
        return prov.name in KEYLESS_OK or prov.name.startswith(USER_ENDPOINT_PREFIX) or not prov.api_key_envs or prov.resolve_api_key(stored_key) is not None
    if prov.auth_type == "none":
        return True
    return None


def _parse(payload: Any) -> list[str]:
    rows = payload.get("data") or payload.get("models") or [] if isinstance(payload, dict) else payload
    ids = []
    for r in rows if isinstance(rows, list) else []:
        mid = r if isinstance(r, str) else (r.get("id") or r.get("slug") or r.get("name") or "")
        if isinstance(mid, str) and mid:
            ids.append(mid.removeprefix("models/"))
    return sorted(set(ids))


def _rows(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    rows = payload.get("data") or payload.get("models") or [] if isinstance(payload, dict) else payload
    out = []
    for r in rows if isinstance(rows, list) else []:
        mid = (r.get("id") or r.get("slug") or r.get("name") or "") if isinstance(r, dict) else ""
        if isinstance(mid, str) and mid:
            out.append((mid.removeprefix("models/"), r))
    return out


def _windows(payload: Any) -> dict[str, tuple[int, int | None]]:
    """{model id: (context window, max output)} for the entries of a /models answer that say (OpenRouter, Gemini, Groq, Anthropic,
    Codex, Mistral, ...)."""
    return {mid: w for mid, r in _rows(payload) if (w := window_from_listing(r))}


def _efforts(provider: str, payload: Any) -> dict[str, tuple[str, ...]]:
    """{model id: thinking levels} for the entries whose listing states them (effort.levels_from_listing)."""
    return {mid: lv for mid, r in _rows(payload) if (lv := effort.levels_from_listing(provider, r)) is not None}


def _display_names(payload: Any) -> dict[str, str]:
    """{model id: display name} from the listing's own `display_name` / `displayName` (Anthropic, Codex, Gemini)."""
    out = {}
    for mid, r in _rows(payload):
        name = r.get("display_name") or r.get("displayName")
        if isinstance(name, str) and name.strip() and name.strip() != mid:
            out[mid] = name.strip()[:80]
    return out


async def list_models(prov: Provider, client: httpx.AsyncClient, *, refresh: bool = False,
                      api_key: str | None = None) -> list[str]:
    """Raises ProviderError with a user-facing reason when the list cannot be fetched (ListUnavailable when the reason passes).

    openai-codex: the list is asked with the same Codex client version every turn presents (releases.codex_version()); a list asked
    with an older version is asked again, so the app never offers a model the turn's version would not get."""
    codex_ver = await releases.codex_version() if prov.name == "openai-codex" else None
    if hit := _cache.get(prov.name):
        if not refresh and time.monotonic() - hit[0] < CACHE_SECONDS and hit[2] == codex_ver:
            return hit[1]
    if prov.name in (cli_manage.CLAUDE, cli_manage.AGY):  # CLI-backed: ask the CLI (or its sidecar)
        found = await cli_manage.cli_models(prov.name)
        _cache[prov.name] = (time.monotonic(), list(found), None)
        _labels[prov.name] = {k: v for k, v in found.items() if v}
        return list(found)

    headers = {"accept": "application/json", **prov.default_headers}
    if prov.name in oauth.OAUTH_PROVIDERS:
        oauth_url, key, extra = await oauth.resolve(prov.name, client=client)
        base = (oauth_url or prov.resolve_base_url()).rstrip("/")
        headers |= {**extra, "authorization": f"Bearer {key}"}
    elif prov.auth_type in ("api_key", "none"):
        key = prov.resolve_api_key(api_key)
        if key is None and prov.api_key_envs and prov.name not in KEYLESS_OK and not prov.name.startswith(USER_ENDPOINT_PREFIX):
            raise ProviderError(t("No API key ({names})", names=", ".join(prov.api_key_envs)))
        base = prov.resolve_base_url()
        if prov.transport == "anthropic_messages":
            headers |= {"anthropic-version": ANTHROPIC_VERSION, **({"x-api-key": key} if key else {})}
        elif key:
            headers["authorization"] = f"Bearer {key}"
    else:
        raise ProviderError(t("This provider cannot list its models. Type the model yourself"))
    if not base:
        raise ProviderError(t("No base URL is set. Enter the address first"))

    url = {"anthropic_messages": f"{base}/v1/models", "codex_responses": f"{base}/models"}.get(prov.transport, f"{base}/models")
    params: dict[str, Any] | None = None
    if codex_ver:   # the official client sends its whole x.y.z (models-manager client_version_to_whole)
        params = {"client_version": codex_ver}
    elif prov.name == "anthropic":
        params = {"limit": 1000}   # the default page is 20 models
    try:
        r = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as e:
        raise ListUnavailable(t("Could not reach the provider ({error})", error=type(e).__name__)) from None
    if r.status_code != 200:
        err = ListUnavailable if r.status_code >= 500 or r.status_code in (408, 429) else ProviderError
        raise err(t("Could not get the model list (HTTP {status}). Type the model yourself", status=r.status_code), status=r.status_code)
    try:
        payload = r.json()
        models = _parse(payload)
    except ValueError:
        payload, models = None, []
    if not models:
        raise ProviderError(t("The provider did not return a model list. Type the model yourself"))
    remember_provider_windows(prov.name, _windows(payload))   # the context window of each model, when the list says
    effort.remember_listing(prov.name, _efforts(prov.name, payload))   # the thinking levels of each model, when the list says
    _labels[prov.name] = _display_names(payload)
    _cache[prov.name] = (time.monotonic(), models, codex_ver)
    return models


def _backoff_after(failures: int) -> float:
    """30 s, 1 min, 2 min, ... up to 30 min."""
    return min(BACKOFF_FIRST * 2 ** max(0, failures - 1), BACKOFF_MAX)


def _answer(provider: str, good: dict[str, Any], *, stale: bool, error: str | None = None) -> dict[str, Any]:
    retry = _backoff.get(provider)
    return {"models": list(good["models"]), "labels": dict(good.get("labels") or {}), "stale": stale,
            "fetched_at": good.get("fetched_at"), "error": error,
            "retry_in": max(0, round(retry[1] - _now())) if stale and retry else None}


async def model_list(prov: Provider, client: httpx.AsyncClient, *, refresh: bool = False, api_key: str | None = None,
                     load: Callable[[], Any] | None = None, save: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """The model list for the app: {"models", "labels", "stale", "fetched_at", "error", "retry_in"}.

    A fetch that fails for a passing reason (ListUnavailable) answers with the last good list (from memory, else `load()`, the copy
    the server saved) marked stale, and the provider is not asked again for 30 s, then 1, 2, 4 ... up to 30 minutes (an explicit
    refresh asks anyway). The error only shows when there has never been a good list. A list much shorter than the last good one
    (under half of at least SHRINK_MIN models) is taken only when a second refresh returns the same, so a truncated answer never
    replaces it. Any other error (no key, signed out, 401) is shown as it is."""
    name = prov.name
    good = _good.get(name)
    if good is None and load is not None:
        saved = load()
        if isinstance(saved, dict) and isinstance(saved.get("models"), list) and saved["models"]:
            good = _good[name] = {"models": [str(m) for m in saved["models"]], "labels": saved.get("labels") or {},
                                  "effort": saved.get("effort") or {}, "fetched_at": saved.get("fetched_at")}
            effort.restore(name, good["effort"])
            _labels.setdefault(name, dict(good["labels"]))
    wait = _backoff.get(name)
    if good is not None and wait and not refresh and _now() < wait[1]:
        return _answer(name, good, stale=True)
    try:
        found = await list_models(prov, client, refresh=refresh, api_key=api_key)
    except ListUnavailable as e:
        _cache.pop(name, None)
        fails = (wait[0] if wait else 0) + 1
        _backoff[name] = (fails, _now() + _backoff_after(fails))
        if good is None:
            return {"models": [], "labels": {}, "stale": False, "fetched_at": None, "error": str(e), "retry_in": None}
        return _answer(name, good, stale=True)
    _backoff.pop(name, None)
    if good is not None and len(good["models"]) >= SHRINK_MIN and len(found) < len(good["models"]) * SHRINK_RATIO:
        if _shrunk.get(name) != found:   # first time: keep the long list, ask again on the next look
            _shrunk[name] = list(found)
            _cache.pop(name, None)
            _labels[name] = dict(good.get("labels") or {})
            effort.remember_listing(name, {m: tuple(v) for m, v in (good.get("effort") or {}).items()})
            return _answer(name, good, stale=True)
    _shrunk.pop(name, None)
    good = _good[name] = {"models": list(found), "labels": labels(name), "effort": {m: list(v) for m, v in effort.listed(name).items()},
                          "fetched_at": round(time.time())}
    if save is not None:
        save(good)
    return _answer(name, good, stale=False)
