"""Calls Kelo never makes on a connected service, or always asks about, whoever asks.

One table of rules per connector (preset id, the id the connector has in the catalog), kept as data in content/forbidden_rules.json: the
code below is only the engine. A rule names the HTTP methods, a pattern for the
request's normalised path, optionally a pattern for the JSON body, and what the call does (`why`):

  code      runs code (on the owner's machine, in their automation, on their live store)
  takeover  changes who can get in, or mints a key that could (tokens, API keys, app passwords, users, mail passwords)
  system    manages the system the service runs on (restart, config, protection off, backup restore)
  data      deletes data that cannot come back

and a `mode`:

  never  refused before any request is made, whoever asks. Only for calls that break or hijack Kelo itself (Telegram webhook and
         polling), run arbitrary code on the owner's machine with no other purpose (Home Assistant shell, scripts) and calls that MINT A
         NEW SECRET that would come back to Kelo (access tokens, API keys, app passwords, deploy and SSH keys).
  ask    the step always shows an Allow/Deny card, even where the owner's connector setting is "allow", a standing rule, a task's
         permission or the "asked directly" rule for the owner's device app would have let it through (permissions.Gate).

The model is never told this list; it learns it from the refusal. `tools/connector_effects.py` prints the table into
docs/connector-permissions.md (a test keeps it in step).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .agent import Ours
from .i18n import N_, t

WHY = ("code", "takeover", "system", "data")
MODES = ("never", "ask")
PHRASE = {"code": "runs code", "takeover": "could take over an account", "system": "manages the system the service runs on",
          "data": "deletes data that cannot come back"}
CARD = {"code": N_("This can run code ({service})."), "takeover": N_("This can change who can get into {service}."),
        "system": N_("This can manage the system {service} runs on."), "data": N_("This can delete data in {service} that cannot come back.")}


@dataclass(frozen=True)
class Rule:
    methods: frozenset[str]        # {"POST", "PUT", "PATCH", "DELETE"} (or with "GET" too); "*" = every method
    path: re.Pattern[str]          # .search() on the normalised path; anchored with ^ where meant
    why: str                       # code | takeover | system | data
    mode: str = "ask"              # never | ask
    body: re.Pattern[str] | None = None   # also .search() on the JSON body's text; a call without a body never matches
    doc: str = ""                  # the service's own API documentation this was taken from
    sure: bool = True              # False: the doc page could not be read in full; the path is a conservative prefix


@dataclass(frozen=True)
class Hit:
    preset: str
    rule: Rule

    @property
    def service(self) -> str:
        return service_name(self.preset)

    @property
    def refusal(self) -> str:
        return Ours(f"error: not run. Kelo never makes this call: it {PHRASE[self.rule.why]} ({self.service}). Tell the owner they can do "
                    f"it in {self.service} itself if they really want it.")

    @property
    def card(self) -> str:
        return t(CARD[self.rule.why], service=self.service)


def _rule(raw: dict[str, Any]) -> Rule:
    why, mode = str(raw["why"]), str(raw.get("mode") or "ask")
    assert why in WHY and mode in MODES, raw
    body = raw.get("body")
    return Rule(frozenset(str(m) for m in raw["methods"]), re.compile(str(raw["path"])), why, mode,
                re.compile(str(body), re.I | re.S) if body else None, str(raw.get("doc") or ""), bool(raw.get("sure", True)))


def _load() -> dict[str, tuple[Rule, ...]]:
    """content/forbidden_rules.json (ASSISTANT_CONTENT_DIR in the container): per connector id, the rules above as data (docs/no-word-lists.md,
    Phase E). A file that is missing or unreadable raises: a server with no rules must not start."""
    root = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content")
    raw = json.loads((root / "forbidden_rules.json").read_text(encoding="utf-8"))
    return {str(pid): tuple(_rule(r) for r in rules) for pid, rules in raw.items()}


RULES: dict[str, tuple[Rule, ...]] = _load()


# ---------------------------------------------------------------------------------------------------------------------------------
def normalised_path(url: str) -> str:
    """The path of a full URL, else the text before "?" and "#": percent-decoded (again where it was escaped twice), lower-cased, "\\" as
    "/", "." and ".." segments and ;parameters resolved, runs of "/" as one and no trailing "/". "" for the root."""
    raw = re.sub(r"[\t\r\n]", "", str(url or "").strip())
    raw = urlsplit(raw).path if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I) else raw.split("?", 1)[0].split("#", 1)[0]
    for _ in range(3):
        again = unquote(raw)
        if again == raw:
            break
        raw = again
    out: list[str] = []
    for seg in raw.lower().replace("\\", "/").split("/"):
        seg = seg.split(";", 1)[0]
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return "/" + "/".join(out) if out else ""


def _body_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(body)


def match(preset_id: str, method: str, url: str, body: Any = None) -> Rule | None:
    """The rule a call falls under ("never" before "ask"), None when the connector has none or the call is fine."""
    rules = RULES.get(preset_id)
    if not rules:
        return None
    method, path, text = str(method or "GET").upper(), normalised_path(url), _body_text(body)
    found = [r for r in rules if ("*" in r.methods or method in r.methods) and r.path.search(path)
             and (r.body is None or (text and r.body.search(text)))]
    return next((r for r in found if r.mode == "never"), found[0] if found else None)


def refusal(preset_id: str, method: str, url: str, body: Any = None) -> str:
    """"" or the error for a call Kelo never makes (the "never" rules; an "ask" rule is the gate's business)."""
    rule = match(preset_id, method, url, body)
    return Hit(preset_id, rule).refusal if rule is not None and rule.mode == "never" else ""


def preset_of(item: str) -> str | None:
    """The preset (catalog connector id) of a vault item built into the server; None for anything else (an installed package, a
    connection the model registered itself): those have no rules."""
    from .effects import _item_ids
    return _item_ids().get((item or "").strip().casefold())


def classify(tool: Any, args: dict[str, Any] | None) -> Hit | None:
    """The rule a connector_get / connector_download / connector_send call falls under, else None."""
    name = str(getattr(tool, "name", tool) or "")
    if name not in ("connector_get", "connector_download", "connector_send") or not isinstance(args, dict):
        return None
    preset = preset_of(str(args.get("name") or ""))
    if preset is None or preset not in RULES:
        return None
    method = str(args.get("method") or "POST").upper() if name == "connector_send" else "GET"
    rule = match(preset, method, str(args.get("url") or ""), args.get("json") if name == "connector_send" else None)
    return Hit(preset, rule) if rule is not None else None


def service_name(preset_id: str) -> str:
    from .presets import KEYS
    if preset_id in KEYS:
        return KEYS[preset_id].name
    from .oauth_connect import PROVIDERS
    for p in PROVIDERS.values():
        if preset_id in p.serves:
            return p.name
    return preset_id


def table() -> list[tuple[str, Rule]]:
    """Every rule with its preset id, in a stable order (the doc is generated from it)."""
    return [(pid, r) for pid in sorted(RULES) for r in RULES[pid]]
