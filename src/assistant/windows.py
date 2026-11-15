"""How many tokens a model can take in (its context window), and where that number came from.

Sources, first that applies wins:
  override  the owner's own number for this model (ASSISTANT_MODEL_CONTEXT="model=tokens,provider/model=tokens")
  learned   a request to this model failed with a context-length error: a lower limit was remembered (database). It stays until the
            provider's own model list reports a different window than the one it was learned against.
  provider  the provider's model list said so (OpenRouter `context_length`, Gemini `inputTokenLimit`, Groq `context_window`, ...);
            collected whenever the model list is fetched, kept in memory and in the database
  table     providers/model_windows.json: common families (Claude, Gemini, GPT/o-series, DeepSeek, Mistral, Llama, Qwen, GLM, ...)
  default   150,000 (see the data file for why)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

CONTEXT_FIELDS = ("context_length", "context_window", "max_context_length", "inputTokenLimit", "input_token_limit", "max_input_tokens",
                  "max_model_len", "context_size")
OUTPUT_FIELDS = ("max_completion_tokens", "outputTokenLimit", "output_token_limit", "max_output_tokens", "max_tokens")


@dataclass(frozen=True, slots=True)
class Window:
    context: int
    max_output: int | None
    source: str   # override | learned | provider | table | default


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    raw = json.loads(resources.files("assistant.providers").joinpath("model_windows.json").read_text("utf-8"))
    raw["_compiled"] = [(re.compile(r["match"]), int(r["context"]), r.get("max_output")) for r in raw["rules"]]
    return raw


def default_context() -> int:
    return int(_data()["default"]["context"])


def table_window(model: str) -> tuple[int, int | None] | None:
    m = (model or "").strip().lower().removeprefix("models/")
    for rx, ctx, out in _data()["_compiled"]:
        if rx.search(m):
            return ctx, (int(out) if out else None)
    return None


def _int(v: Any) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def window_from_listing(row: Any) -> tuple[int, int | None] | None:
    """(context, max output) from one entry of a provider's /models answer, when it says."""
    if not isinstance(row, dict):
        return None
    ctx = next((n for f in CONTEXT_FIELDS if (n := _int(row.get(f)))), None)
    if ctx is None:
        return None
    top = row.get("top_provider") if isinstance(row.get("top_provider"), dict) else {}
    out = next((n for f in OUTPUT_FIELDS if (n := _int(top.get(f)) or _int(row.get(f)))), None)
    return ctx, (out if out and out < ctx else None)


_provider: dict[str, dict[str, tuple[int, int | None]]] = {}   # provider -> model id -> (context, max output)


def remember_provider_windows(provider: str, found: dict[str, tuple[int, int | None]]) -> None:
    if found:
        _provider[provider] = dict(found)


def provider_windows(provider: str) -> dict[str, tuple[int, int | None]]:
    return dict(_provider.get(provider, {}))


def parse_overrides(text: str) -> dict[str, int]:
    """'gpt-4o-mini=64000, openrouter/some/model=32k' -> {"gpt-4o-mini": 64000, ...}. Bad items are skipped."""
    out: dict[str, int] = {}
    for item in (text or "").split(","):
        key, sep, val = item.strip().rpartition("=")
        val = val.strip().lower()
        mult = 1000 if val.endswith("k") else 1
        n = _int(val.removesuffix("k")) if sep and key.strip() else None
        if n:
            out[key.strip()] = n * mult
    return out


def lookup(provider: str, model: str, *, overrides: dict[str, int] | None = None,
           stored: dict[str, Any] | None = None, learned: dict[str, Any] | None = None) -> Window:
    """`stored`: the provider's model windows saved in the database ({model: [context, max_output]}), used when memory has none.
    `learned`: {"context", "base_context", "base_source"} remembered after a context-length error."""
    for key in (f"{provider}/{model}", model):
        if overrides and key in overrides:
            return Window(int(overrides[key]), None, "override")
    base: Window | None = None
    mem = _provider.get(provider, {}).get(model) or _provider.get(provider, {}).get(model.removeprefix("models/"))
    if mem is None and stored and isinstance(stored.get(model), (list, tuple)) and stored[model]:
        mem = (int(stored[model][0]), _int(stored[model][1]) if len(stored[model]) > 1 else None)
    if mem:
        base = Window(int(mem[0]), mem[1], "provider")
    elif hit := table_window(model):
        base = Window(hit[0], hit[1], "table")
    else:
        base = Window(default_context(), None, "default")
    if learned and _int(learned.get("context")) and int(learned["context"]) < base.context:
        changed = base.source == "provider" and (learned.get("base_source") != "provider" or _int(learned.get("base_context")) != base.context)
        if not changed:   # the provider's list now reports something else: trust it over the old failure
            return Window(int(learned["context"]), base.max_output, "learned")
    return base
