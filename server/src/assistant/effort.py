"""Reasoning effort ("how hard should the model think"), mapped to what each provider really accepts.

Only providers whose request format is known are listed. For every other provider nothing is sent,
because an unknown field makes many APIs answer 400. The setting is then simply not offered in the UI.

Per model, what the provider's own model list says wins over the per-provider default below (levels_from_listing):
  openai-codex  `supported_reasoning_levels: [{"effort": "low"}, ...]` (chatgpt.com/backend-api/codex/models)
  anthropic     `capabilities.effort.{supported, low, medium, high, xhigh, max}` (GET /v1/models); sent as output_config.effort
  openrouter    `supported_parameters` contains "reasoning" (or not: then nothing is sent for that model)
  gemini        `thinking: true|false` (GET /v1beta/models)
The default list is the fallback for a model the list says nothing about. agy encodes the level in the model id (its listing has no
fields), and the OpenAI, xAI and most other /models answers carry no reasoning fields at all.

A custom OpenAI-compatible endpoint (vLLM, SGLang, EXAONE, a company gateway...) gets low/medium/high/max as `reasoning_effort`,
and nothing while the owner leaves it at the default. A server that answers 400 to the field is asked again without it and noted
(providers/refused.py): from then on that address and model get nothing, and the app shows `refused_note` instead of the levels.
"""

from __future__ import annotations

from typing import Any

from .i18n import N_, t
from .providers import cli_manage, refused

ORDER = ("low", "medium", "high", "xhigh", "max")
_THREE = ("low", "medium", "high")

# provider name -> levels it accepts
_LEVELS: dict[str, tuple[str, ...]] = {
    cli_manage.CLAUDE: ORDER,       # claude CLI --effort
    "openai-codex": _THREE,         # Responses API: reasoning.effort
    "openai": _THREE,               # Chat Completions: reasoning_effort (reasoning models only)
    "gemini": _THREE,               # OpenAI-compatible surface: reasoning_effort
    "openrouter": _THREE,           # reasoning.effort
    "custom": ("low", "medium", "high", "max"),   # Chat Completions: reasoning_effort (only when chosen; a 400 drops it, refused.py)
}

REFUSED_NOTE = N_("This server does not accept a thinking level")


def refused_for(provider_name: str, base_url: str, model: str | None) -> bool:
    """This address and model turned the thinking level away before (providers/refused.py)."""
    is_custom = provider_name == "custom" or provider_name.startswith("custom-")
    return bool(model) and (provider_name in _LEVELS or is_custom) and refused.refused(refused.EFFORT_FIELD, base_url, model or "")


def refused_note() -> str:
    return t(REFUSED_NOTE)


# Shown instead of a selector when a provider has its own way of choosing the effort.
_NOTES = {cli_manage.AGY: N_("For agy this is set by the low/medium/high at the end of the model name. Choose it in the model picker")}


def note(provider_name: str) -> str | None:
    text = _NOTES.get(provider_name)
    return t(text) if text else None


def _agy_flag_applies(model: str | None) -> bool:
    """agy's own rule (vendor/antigravity/models.py): --effort only for a Gemini id without an effort suffix."""
    m = model or ""
    return m.startswith("gemini-") and m.rpartition("-")[2] not in ORDER


_listed: dict[str, dict[str, tuple[str, ...]]] = {}   # provider -> model id -> levels its model list states (() = no reasoning)


def _on(v: Any) -> bool:
    return isinstance(v, dict) and v.get("supported") is True


def levels_from_listing(provider_name: str, row: Any) -> tuple[str, ...] | None:
    """The levels one entry of the provider's model list states; () when it says the model has none; None when it does not say."""
    if not isinstance(row, dict):
        return None
    if provider_name == "openai-codex" and isinstance(row.get("supported_reasoning_levels"), list):
        said = {(x.get("effort") if isinstance(x, dict) else x) for x in row["supported_reasoning_levels"]}
        return tuple(lv for lv in ORDER if lv in said)
    if provider_name == "anthropic" and isinstance(row.get("capabilities"), dict) and isinstance(row["capabilities"].get("effort"), dict):
        eff = row["capabilities"]["effort"]
        return tuple(lv for lv in ORDER if _on(eff.get(lv))) if eff.get("supported") is True else ()
    if provider_name == "openrouter" and isinstance(row.get("supported_parameters"), list):
        return _THREE if {"reasoning", "reasoning_effort"} & set(row["supported_parameters"]) else ()
    if provider_name == "gemini" and isinstance(row.get("thinking"), bool):
        return _THREE if row["thinking"] else ()
    return None


def remember_listing(provider_name: str, found: dict[str, tuple[str, ...]]) -> None:
    """The levels a fresh model list stated, by model id (replaces what an older list said)."""
    if found:
        _listed[provider_name] = {m: tuple(v) for m, v in found.items()}


def restore(provider_name: str, saved: Any) -> None:
    """What a saved list stated ({model: [levels]}), used after a restart until the list is fetched again."""
    if provider_name not in _listed and isinstance(saved, dict):
        rows = {str(m): tuple(lv for lv in v if lv in ORDER) for m, v in saved.items() if isinstance(v, list)}
        if rows:
            _listed[provider_name] = rows


def listed(provider_name: str) -> dict[str, tuple[str, ...]]:
    return dict(_listed.get(provider_name, {}))


def forget(provider_name: str) -> None:
    _listed.pop(provider_name, None)


def levels(provider_name: str, model: str | None = None) -> tuple[str, ...]:
    """The model's levels when its provider's list said, else the provider's default."""
    if model:
        rows = _listed.get(provider_name, {})
        hit = rows.get(model, rows.get(model.removeprefix("models/")))
        if hit is not None:
            return hit
    if provider_name == "custom" or provider_name.startswith("custom-"):
        return ("low", "medium", "high", "max")
    return _LEVELS.get(provider_name, ())


def clamp(provider_name: str, effort: str | None, model: str | None = None) -> str | None:
    """The highest level the provider (or this model) has that does not exceed the request; None when unsupported."""
    accepted = levels(provider_name, model)
    if not accepted or effort not in ORDER:
        return None
    ok = [lv for lv in accepted if ORDER.index(lv) <= ORDER.index(effort)]
    return ok[-1] if ok else accepted[0]


def request_extra(provider_name: str, effort: str | None, model: str | None = None) -> dict[str, Any]:
    """Extra request fields for `ChatRequest.extra`; empty when unset or unsupported."""
    if provider_name == cli_manage.AGY:  # no fixed level list: it depends on the model id
        return {"effort": effort} if effort in ORDER and _agy_flag_applies(model) else {}
    level = clamp(provider_name, effort, model)
    if level is None:
        return {}
    if provider_name == cli_manage.CLAUDE:
        return {"effort": level}  # cli_agents turns this into the CLI's own flag
    if provider_name == "anthropic":
        return {"output_config": {"effort": level}}   # only reached for a model whose /v1/models entry states effort levels
    if provider_name in ("openai-codex", "openrouter"):
        return {"reasoning": {"effort": level}}
    return {"reasoning_effort": level}
