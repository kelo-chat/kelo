"""Scenario runner for the behaviour checks (see README.md in this folder).

A scenario is a JSON file: the user's messages, the scripted model's replies (one list per model call), fake tools (canned results or
canned errors), and what to expect. The run goes through the real app (create_app), the same turn path the apps use (POST /api/turns,
followed with GET /api/turns/{id}), so the approval cards, the fences and the system prompt are the real ones. Nothing leaves the
machine: the model is scripted and the tools are fakes (or real vault tools on a temporary store, when a scenario asks for them).

Two entry points: `run_scenario` (what the tests and `assistant eval live` use) and `check` (the expectations of a scenario against an
outcome). `load_scenarios` validates the files, so a typo in a key fails the suite instead of passing silently.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.secret_guard import find as find_secrets
from assistant.server import Settings, create_app
from assistant.settings import _default_content_dir
from assistant.store import Store

SCENARIO_DIR = Path(__file__).parent / "scenarios"
TOKEN = "e" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SCENARIO_KEYS = {"name", "about", "profile", "user", "model", "tools", "expect", "approve", "real", "live"}
EXPECT_KEYS = {
    "called", "not_called", "approval_asked", "reply_contains", "reply_not_contains", "no_secret_in", "prompt_contains",
    "model_calls", "status", "tool_error", "login_cards", "status_labels", "reply_language",
}
NO_SECRET_PLACES = {"reply", "tool_args", "log"}
REAL_TOOLS = {"request_login"}   # the real vault tools a scenario may ask for ("real": [...]); see _real_tools


class ScenarioError(ValueError):
    """A scenario file that does not follow the format (raised at load time, never during a run)."""


@dataclass
class Outcome:
    """Everything a run recorded: the calls the tools really ran, the approval cards, the events, and what the model received."""
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    model_requests: list[list[dict[str, Any]]] = field(default_factory=list)
    extra_model_calls: int = 0           # calls past the end of the script
    statuses: list[int] = field(default_factory=list)      # HTTP status of each POST /api/turns
    status_bodies: list[str] = field(default_factory=list)  # ... and its body when it is not 200
    log_lines: list[str] = field(default_factory=list)
    login_cards: int = 0
    secrets: list[str] = field(default_factory=list)       # values the secret guard found in the user's messages (checked, never shown)

    @property
    def reply(self) -> str:
        return "".join(e.get("text", "") for e in self.events if e.get("type") == "text")

    @property
    def prompt_text(self) -> str:
        """Everything the scripted model was sent, as text (system prompt, messages, tool results)."""
        return "\n".join(json.dumps(m, ensure_ascii=False) for req in self.model_requests for m in req)


# ---- loading ----------------------------------------------------------------------------------------------------------------------
def load_scenario(path: Path) -> dict[str, Any]:
    try:
        sc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ScenarioError(f"{path.name}: not readable JSON: {e}") from e
    if not isinstance(sc, dict):
        raise ScenarioError(f"{path.name}: the top level must be an object")
    unknown = set(sc) - SCENARIO_KEYS
    if unknown:
        raise ScenarioError(f"{path.name}: unknown keys {sorted(unknown)}")
    for key in ("name", "about", "user", "model", "expect"):
        if key not in sc:
            raise ScenarioError(f"{path.name}: missing {key!r}")
    if sc["name"] != path.stem:
        raise ScenarioError(f"{path.name}: name must be {path.stem!r}")
    if not isinstance(sc["user"], list) or not sc["user"] or not all(isinstance(u, str) for u in sc["user"]):
        raise ScenarioError(f"{path.name}: user must be a non-empty list of strings")
    for step in sc["model"]:
        if not isinstance(step, list):
            raise ScenarioError(f"{path.name}: each model call is a list of items")
        for item in step:
            if not (isinstance(item, dict) and (("tool" in item) != ("text" in item))):
                raise ScenarioError(f"{path.name}: a model item is {{\"tool\", \"args\"}} or {{\"text\"}}, got {item!r}")
    unknown_expect = set(sc["expect"]) - EXPECT_KEYS
    if unknown_expect:
        raise ScenarioError(f"{path.name}: unknown expectations {sorted(unknown_expect)}")
    for name in sc.get("real", []):
        if name not in REAL_TOOLS:
            raise ScenarioError(f"{path.name}: unknown real tool {name!r} (known: {sorted(REAL_TOOLS)})")
    for name, spec in sc.get("tools", {}).items():
        if not isinstance(spec, dict) or set(spec) - {"result", "error", "approval", "untrusted", "source", "description", "parameters"}:
            raise ScenarioError(f"{path.name}: tool {name!r} has an unknown or bad spec")
    if "live" in sc and set(sc["live"]) - {"expect", "note"}:
        raise ScenarioError(f"{path.name}: live takes expect and note only")
    if "live" in sc and not set(sc["live"].get("expect", {})) <= {"called", "not_called", "approval_asked", "reply_language"}:
        raise ScenarioError(f"{path.name}: live expectations are called, not_called, approval_asked, reply_language only")
    return sc


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[dict[str, Any]]:
    return [load_scenario(p) for p in sorted(directory.glob("*.json"))]


# ---- fakes ------------------------------------------------------------------------------------------------------------------------
def _fake_tool(name: str, spec: dict[str, Any], outcome: Outcome) -> Tool:
    async def handler(args: dict[str, Any]) -> str:
        outcome.calls.append((name, dict(args)))
        if "error" in spec:
            raise RuntimeError(spec["error"])
        return str(spec.get("result", "ok"))

    approval = spec.get("approval")
    auto = None
    if isinstance(approval, dict) and "auto_within" in approval:   # a grant: runs without a card up to this amount
        cap = float(approval["auto_within"])

        def auto(args: dict[str, Any]) -> bool:
            try:
                return float(args.get("amount", 0)) <= cap
            except (TypeError, ValueError):
                return False

    params = spec.get("parameters") or {"type": "object", "properties": {}, "additionalProperties": True}
    return Tool(name, spec.get("description", f"{name} (scenario fake)"), params, handler, requires_approval=approval is not None,
                untrusted=bool(spec.get("untrusted", False)), source=spec.get("source", ""), auto_approve=auto)


def _real_tools(names: list[str], store: Store) -> list[Tool]:
    from assistant.tools.vault_tools import vault_tools
    by_name = {t.name: t for t in vault_tools(store, None)}
    return [by_name[n] for n in names]


def scripted_model(steps: list[list[dict[str, Any]]], outcome: Outcome) -> Callable[..., AsyncIterator[Any]]:
    """Plays one list of items per model call; each call's request is kept (a copy) for the expectations."""
    queue = list(steps)
    counter = [0]

    async def fn(provider: str, request: Any, **kw: Any) -> AsyncIterator[Any]:
        outcome.model_requests.append(copy.deepcopy(request.messages))
        step = queue.pop(0) if queue else None
        if step is None:
            outcome.extra_model_calls += 1
            step = [{"text": "(no scripted reply left)"}]
        has_tool = False
        for item in step:
            if "tool" in item:
                counter[0] += 1
                has_tool = True
                yield ToolCall(f"call-{counter[0]}", item["tool"], json.dumps(item.get("args", {}), ensure_ascii=False))
            else:
                yield TextDelta(item["text"])
        yield Done("tool_calls" if has_tool else "stop")

    return fn


# ---- running ----------------------------------------------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Log(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__(logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record.getMessage())


async def _drive(c: httpx.AsyncClient, text: str, approve: bool, outcome: Outcome) -> None:
    started = await c.post("/api/turns", json={"text": text}, headers=AUTH)
    outcome.statuses.append(started.status_code)
    if started.status_code != 200:
        outcome.status_bodies.append(started.text)
        return
    turn_id, after = started.json()["id"], 0
    for _ in range(400):
        body = (await c.get(f"/api/turns/{turn_id}", params={"after": after, "wait": 2}, headers=AUTH)).json()
        for ev in body["events"]:
            if ev.get("type") == "reaction":
                continue
            outcome.events.append(ev)
            if ev.get("type") == "approval_request":
                outcome.approvals.append(ev)
                resp = await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": approve}, headers=AUTH)
                if resp.status_code != 200:
                    raise AssertionError(f"approval answer refused: {resp.status_code}")
        after = body["next"]
        if body["done"]:
            return
    raise AssertionError("the reply never ended")


async def run_scenario(sc: dict[str, Any], workdir: Path, *, stream_fn: Callable[..., AsyncIterator[Any]] | None = None,
                       settings_kw: dict[str, Any] | None = None, base_settings: Settings | None = None) -> Outcome:
    """Runs one scenario in `workdir` (a temporary folder). `base_settings` (live tier) takes the instance's provider, model and keys
    and runs the app's own model call; the scripted model is then not used. Tools are the scenario's fakes in both cases."""
    outcome = Outcome()
    for text in sc["user"]:
        outcome.secrets += [text[h.start:h.end] for h in find_secrets(text) if h.end > h.start]
    tools = [_fake_tool(n, spec, outcome) for n, spec in sc.get("tools", {}).items()]
    db_path = workdir / "app.db"
    store = None
    if sc.get("real"):   # the real tools write to the app's own database (the same file, its key next to it)
        store = Store(db_path, None)
        tools += _real_tools(sc["real"], store)
    if base_settings is not None:
        settings = replace(base_settings, token=TOKEN, cdp_host=None, db_path=str(db_path), home_dir=str(workdir / "home"),
                           content_dir=_default_content_dir(), **(settings_kw or {}))
        app = create_app(settings, tools=tools) if stream_fn is None else create_app(settings, tools=tools, stream_fn=stream_fn)
    else:
        model = stream_fn or scripted_model(sc.get("model", []), outcome)
        model_name = sc.get("profile", {}).get("model", "scripted")
        settings = Settings(token=TOKEN, provider="p", model=model_name, cdp_host=None, db_path=str(db_path),
                            content_dir=_default_content_dir(), **(settings_kw or {}))
        app = create_app(settings, tools=tools, stream_fn=model)
    handler = _Log(outcome.log_lines)
    root = logging.getLogger("assistant")
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            for text in sc["user"]:
                await _drive(c, text, bool(sc.get("approve", False)), outcome)
    finally:
        server.should_exit = True
        await task
        root.removeHandler(handler)
        root.setLevel(old_level)
    if store is not None:
        outcome.login_cards = len(store._all("SELECT id FROM secret_requests WHERE kind = 'login' AND state = 'open'"))
    return outcome


# ---- expectations -----------------------------------------------------------------------------------------------------------------
def _hangul_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    return sum("가" <= ch <= "힣" for ch in letters) / len(letters) if letters else 0.0


def check(expect: dict[str, Any], outcome: Outcome) -> list[str]:
    """Every expectation that does not hold, as a sentence. An empty list means the scenario passed."""
    bad: list[str] = []
    called = {name for name, _ in outcome.calls}
    for name in expect.get("called", []):
        if name not in called:
            bad.append(f"{name} was not called")
    for name in expect.get("not_called", []):
        if name in called:
            bad.append(f"{name} was called")
    if "approval_asked" in expect and bool(outcome.approvals) != bool(expect["approval_asked"]):
        bad.append(f"approval_asked is {bool(outcome.approvals)}, expected {expect['approval_asked']}")
    for needle in expect.get("reply_contains", []):
        if needle not in outcome.reply:
            bad.append(f"reply lacks {needle!r}")
    for needle in expect.get("reply_not_contains", []):
        if needle in outcome.reply:
            bad.append(f"reply has {needle!r}")
    for needle in expect.get("prompt_contains", []):
        if needle not in outcome.prompt_text:
            bad.append(f"the model was not sent {needle!r}")
    if "model_calls" in expect and len(outcome.model_requests) != expect["model_calls"]:
        bad.append(f"the model was called {len(outcome.model_requests)} times, expected {expect['model_calls']}")
    if outcome.extra_model_calls:
        bad.append(f"the model was called {outcome.extra_model_calls} time(s) past its script")
    if "status" in expect and expect["status"] not in outcome.statuses:
        bad.append(f"turn start answered {outcome.statuses}, expected {expect['status']}")
    if "tool_error" in expect:
        spec = expect["tool_error"]
        results = [e for e in outcome.events if e.get("type") == "tool_result" and e.get("name") == spec["name"]]
        if not any(e.get("is_error") for e in results):
            bad.append(f"{spec['name']} did not report an error to the app")
        if spec.get("visible_to_model") and not any(spec["visible_to_model"] in json.dumps(m, ensure_ascii=False)
                                                    for req in outcome.model_requests for m in req if m.get("role") == "tool"):
            bad.append(f"the model did not see the error {spec['visible_to_model']!r} in the tool result")
    if "login_cards" in expect and outcome.login_cards != expect["login_cards"]:
        bad.append(f"{outcome.login_cards} open login cards, expected {expect['login_cards']}")
    if "status_labels" in expect:
        labelled = sum(1 for e in outcome.events if e.get("type") == "tool_started" and e.get("label"))
        if labelled < expect["status_labels"]:
            bad.append(f"{labelled} tool steps had a live status line, expected {expect['status_labels']}")
    for place in expect.get("no_secret_in", []):
        if place not in NO_SECRET_PLACES:
            bad.append(f"unknown place {place!r} in no_secret_in")
            continue
        haystack = {
            "reply": outcome.reply,
            "tool_args": json.dumps([a for _, a in outcome.calls], ensure_ascii=False),
            "log": "\n".join(outcome.log_lines),
        }[place]
        if any(s and s in haystack for s in outcome.secrets):
            bad.append(f"a secret from the user's message shows in {place}")
    if "reply_language" in expect:
        want = expect["reply_language"]
        share = _hangul_share(outcome.reply)
        if (want == "ko" and share < 0.3) or (want == "en" and share > 0.1):
            bad.append(f"reply is not in {want} (Hangul share {share:.2f})")
    return bad


def live_expect(sc: dict[str, Any]) -> dict[str, Any]:
    """The expectations a live run checks: the scenario's own `live` block, which is looser than the scripted ones."""
    return dict(sc.get("live", {}).get("expect", {}))


def run_live(sc: dict[str, Any], workdir: Path, base_settings: Settings) -> Outcome:
    """The live tier's entry point (assistant eval live): one scenario, the instance's model, synchronous."""
    return asyncio.run(run_scenario(sc, workdir, base_settings=base_settings))
