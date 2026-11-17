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
from assistant.tools.checkout import PROBE_JS, REFUSAL
from assistant.tools.browser import BrowserSession
from assistant.settings import _default_content_dir
from assistant.store import Store

SCENARIO_DIR = Path(__file__).parent / "scenarios"
TOKEN = "e" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SCENARIO_KEYS = {"name", "about", "profile", "user", "model", "tools", "expect", "approve", "real", "live", "settings", "workers", "live_only"}
EXPECT_KEYS = {
    "called", "not_called", "approval_asked", "reply_contains", "reply_not_contains", "no_secret_in", "prompt_contains",
    "model_calls", "status", "tool_error", "login_cards", "status_labels", "reply_language",
    "called_with", "not_called_with", "jobs", "jobs_max", "main_run_has", "main_run_lacks",
}
LIVE_EXPECT_KEYS = {"called", "not_called", "approval_asked", "reply_language", "called_with", "not_called_with", "jobs", "jobs_max",
                    "main_run_has", "main_run_lacks"}
SETTINGS_KEYS = {"payment_specialist"}   # the settings a scenario may switch (a kill switch under test)
TOOL_SPEC_KEYS = {"result", "error", "approval", "untrusted", "source", "description", "parameters", "by", "results", "real_schema", "refuse_selector_containing", "final_pages", "shows_page"}
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
    request_tools: list[list[str]] = field(default_factory=list)   # the tool names each model request offered
    jobs: list[dict[str, Any]] = field(default_factory=list)       # the background jobs at the end: id, kind, status (no texts)
    page: dict[str, Any] = field(default_factory=dict)             # the page the fake browser last showed: url, text, final (a final checkout step)

    def observed_calls(self) -> list[tuple[str, dict[str, Any]]]:
        """Every tool call anyone made (the main agent or a job, fake or real tool, run or refused): the calls the tools ran plus the
        calls the model asked for in the requests it was sent, each asked call once."""
        seen: dict[str, tuple[str, dict[str, Any]]] = {}
        for req in self.model_requests:
            for m in req:
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        args = {}
                    seen.setdefault(str(tc.get("id")), (str(fn.get("name")), args if isinstance(args, dict) else {}))
        return [*self.calls, *seen.values()]

    def refused(self, name: str) -> int:
        """How many calls of this tool came back as an error (the tool's own "Error: ..." answer), read from the results the model saw."""
        ids = {str(tc.get("id")) for req in self.model_requests for m in req for tc in (m.get("tool_calls") or [])
               if (tc.get("function") or {}).get("name") == name}
        results = {str(m.get("tool_call_id")): str(m.get("content") or "") for req in self.model_requests for m in req if m.get("role") == "tool"}
        return sum(1 for i in ids if results.get(i, "").lstrip().startswith("Error"))

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
        if not isinstance(spec, dict) or set(spec) - TOOL_SPEC_KEYS:
            raise ScenarioError(f"{path.name}: tool {name!r} has an unknown or bad spec")
        if ("by" in spec) != ("results" in spec):
            raise ScenarioError(f"{path.name}: tool {name!r}: by and results go together")
    if set(sc.get("settings", {})) - SETTINGS_KEYS:
        raise ScenarioError(f"{path.name}: settings may only set {sorted(SETTINGS_KEYS)}")
    if sc.get("live_only") and "live" not in sc:
        raise ScenarioError(f"{path.name}: live_only needs a live block")
    if "live" in sc and set(sc["live"]) - {"expect", "note"}:
        raise ScenarioError(f"{path.name}: live takes expect and note only")
    if "live" in sc and not set(sc["live"].get("expect", {})) <= LIVE_EXPECT_KEYS:
        raise ScenarioError(f"{path.name}: live expectations are {sorted(LIVE_EXPECT_KEYS)} only")
    return sc


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[dict[str, Any]]:
    return [load_scenario(p) for p in sorted(directory.glob("*.json"))]


# ---- fakes ------------------------------------------------------------------------------------------------------------------------
def _real_schemas(names: set[str]) -> dict[str, tuple[str, dict[str, Any]]]:
    """The description and parameters of the app's own tools of these names (a throwaway app, no browser is ever opened), so a
    scenario's fake shows the model exactly what the real tool shows it and cannot drift from it."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host="127.0.0.1", db_path=str(Path(d) / "a.db"),
                                  home_dir=str(Path(d) / "home"), content_dir=_default_content_dir()), with_worker_tools=True)
        return {t.name: (t.description, t.parameters) for t in app.state.tools if t.name in names}


def _fake_tool(name: str, spec: dict[str, Any], outcome: Outcome, real: dict[str, tuple[str, dict[str, Any]]] | None = None) -> Tool:
    def shown(args: dict[str, Any], text: str) -> str:
        """The fixture marks which of its pages is a final checkout step (final_pages: addresses, for a tool that opens by address); the
        fake browser's page structure then says so to the app (FakePage), as the real probe does for a real page. `shows_page`: a reading,
        which shows the page last opened."""
        if spec.get("shows_page"):
            return str(outcome.page.get("text") or text)
        if "final_pages" in spec:
            url = str(args.get(spec["by"], ""))
            outcome.page = {"url": url, "text": text, "final": url in spec["final_pages"]}
        return text

    async def handler(args: dict[str, Any]) -> str:
        outcome.calls.append((name, dict(args)))
        if "error" in spec:
            raise RuntimeError(spec["error"])
        for part, text in (spec.get("refuse_selector_containing") or {}).items():
            # the real guard answers a press on a final checkout step with checkout.REFUSAL (the fixture's pay control is e9); the fake must too
            if part in str(args.get("selector", "")):
                return REFUSAL if text == "$REFUSAL" else str(text)
        if "by" in spec:   # the result depends on one argument (checkout_review: draft, ready)
            by = str(args.get(spec["by"], ""))
            if by in spec["results"]:
                return shown(args, str(spec["results"][by]))
        return shown(args, str(spec.get("result", "ok")))

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
    description = spec.get("description", f"{name} (scenario fake)")
    if spec.get("real_schema") and real and name in real:
        description, params = real[name]
    return Tool(name, description, params, handler, requires_approval=approval is not None,
                untrusted=bool(spec.get("untrusted", False)), source=spec.get("source", ""), auto_approve=auto)


class FakePage:
    """The page of an eval's browser: what the fake page tools last showed. Its structure is what the real probe (tools/checkout.py
    read_page) reads: a final checkout step has payment inputs and an order total beside a control, any other page has none."""
    frames: list[Any] = []

    def __init__(self, outcome: Outcome):
        self.outcome = outcome

    @property
    def url(self) -> str:
        return str(self.outcome.page.get("url") or "about:blank")

    def is_closed(self) -> bool:
        return False

    def on(self, *a: Any, **k: Any) -> None:
        return None

    async def title(self) -> str:
        return "shop"

    async def close(self) -> None:
        return None

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if js is not PROBE_JS:   # only the checkout probe is answered: every other page script fails as it did when no page existed
            raise RuntimeError("no browser in an eval")
        final = bool(self.outcome.page.get("final"))
        return {"url": self.url, "text": str(self.outcome.page.get("text") or ""), "payment_inputs": 1 if final else 0,
                "totals": ["total"] if final else [], "controls": [{"role": "button", "label": "pay"}] if final else [], "marked": None,
                "currency": ""}


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
        outcome.request_tools.append([t["function"]["name"] for t in (request.tools or [])])
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


def recording_model(outcome: Outcome, inner: Callable[..., AsyncIterator[Any]] | None = None) -> Callable[..., AsyncIterator[Any]]:
    """The app's own model call (or `inner`), with each request noted (messages and offered tool names) so a live run can count and
    inspect them."""
    from assistant.server import default_stream_chat
    inner = inner or default_stream_chat

    async def fn(provider: str, request: Any, **kw: Any) -> AsyncIterator[Any]:
        outcome.model_requests.append(copy.deepcopy(request.messages))
        outcome.request_tools.append([t["function"]["name"] for t in (request.tools or [])])
        async for ev in inner(provider, request, **kw):
            yield ev

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


async def _jobs(c: httpx.AsyncClient) -> list[dict[str, Any]]:
    items = (await c.get("/api/workers", headers=AUTH)).json().get("items", [])
    return [{"id": i.get("id"), "kind": i.get("kind"), "status": i.get("status")} for i in items]


async def _answer_job_cards(c: httpx.AsyncClient, approve: bool, outcome: Outcome, answered: set[str]) -> None:
    """A background job's approval card is no turn's event: nobody in an eval answers it, and a job waits 30 minutes for it. Answered here the
    way the chat's cards are (the scenario's `approve`, default no)."""
    for e in (await c.get("/api/approvals/pending", headers=AUTH)).json().get("items", []):
        aid = str(e.get("approval_id") or e.get("id") or "")
        if not aid or aid in answered or e.get("kind") == "location":
            continue
        answered.add(aid)
        outcome.approvals.append(e)
        await c.post(f"/api/approvals/{aid}", json={"approve": approve}, headers=AUTH)


async def _settle(c: httpx.AsyncClient, outcome: Outcome, quiet: float = 25.0, limit: float = 600.0, approve: bool = False) -> None:
    """After a turn that may have started background jobs: wait until no job runs and the model has been quiet for `quiet` seconds
    (a finished job hands its report to the main agent, which may answer or resume the job)."""
    loop = asyncio.get_running_loop()
    start, seen, since = loop.time(), -1, loop.time()
    answered: set[str] = set()
    while loop.time() - start < limit:
        await asyncio.sleep(0.5)
        await _answer_job_cards(c, approve, outcome, answered)
        running = any(j["status"] == "running" for j in await _jobs(c))
        n = len(outcome.model_requests)
        if running or n != seen:
            seen, since = n, loop.time()
        elif loop.time() - since >= quiet:
            return


async def run_scenario(sc: dict[str, Any], workdir: Path, *, stream_fn: Callable[..., AsyncIterator[Any]] | None = None,
                       settings_kw: dict[str, Any] | None = None, base_settings: Settings | None = None) -> Outcome:
    """Runs one scenario in `workdir` (a temporary folder). `base_settings` (live tier) takes the instance's provider, model and keys
    and runs the app's own model call; the scripted model is then not used. Tools are the scenario's fakes in both cases."""
    outcome = Outcome()
    for text in sc["user"]:
        outcome.secrets += [text[h.start:h.end] for h in find_secrets(text) if h.end > h.start]
    wanted = {n for n, spec in sc.get("tools", {}).items() if spec.get("real_schema")}
    real = _real_schemas(wanted) if wanted else {}
    tools = [_fake_tool(n, spec, outcome, real) for n, spec in sc.get("tools", {}).items()]
    workers = bool(sc.get("workers"))
    app_kw: dict[str, Any] = {}
    if workers:   # delegate and the job tools, and a browser session over a fake page (the tools are fakes; the page says what it is)
        page = FakePage(outcome)

        async def connect() -> Any:
            return page
        app_kw = {"with_worker_tools": True, "browser": BrowserSession(connect=connect)}
    settings_kw = {**(settings_kw or {}), **sc.get("settings", {})}
    db_path = workdir / "app.db"
    store = None
    if sc.get("real"):   # the real tools write to the app's own database (the same file, its key next to it)
        store = Store(db_path, None)
        tools += _real_tools(sc["real"], store)
    if base_settings is not None:
        settings = replace(base_settings, token=TOKEN, cdp_host=None, db_path=str(db_path), home_dir=str(workdir / "home"),
                           content_dir=_default_content_dir(), **settings_kw)
        app = create_app(settings, tools=tools, stream_fn=recording_model(outcome, stream_fn), **app_kw)
    else:
        model = recording_model(outcome, stream_fn) if stream_fn else scripted_model(sc.get("model", []), outcome)
        model_name = sc.get("profile", {}).get("model", "scripted")
        settings = Settings(token=TOKEN, provider="p", model=model_name, cdp_host=None, db_path=str(db_path),
                            content_dir=_default_content_dir(), **settings_kw)
        app = create_app(settings, tools=tools, stream_fn=model, **app_kw)
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
                if workers:
                    await _settle(c, outcome, quiet=25.0 if base_settings is not None else 1.0, limit=1500.0 if base_settings is not None else 600.0, approve=bool(sc.get("approve", False)))   # a live model takes ~30 s a request
            if workers:
                outcome.jobs = await _jobs(c)
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


def _has_args(got: dict[str, Any], want: dict[str, Any]) -> bool:
    """Every key of `want` is in `got` with that value; a value of "*" only needs the key to be there and not empty."""
    return all((bool(got.get(k)) if v == "*" else got.get(k) == v) for k, v in want.items())


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
    observed = outcome.observed_calls()
    for want in expect.get("called_with", []):
        if not any(name == want["tool"] and _has_args(args, want.get("args", {})) for name, args in observed):
            bad.append(f"{want['tool']} was not called with {want.get('args', {})}")
    for want in expect.get("not_called_with", []):
        if any(name == want["tool"] and _has_args(args, want.get("args", {})) for name, args in observed):
            bad.append(f"{want['tool']} was called with {want.get('args', {})}")
    for want in expect.get("jobs", []):
        if not any(_has_args(j, want) for j in outcome.jobs):
            bad.append(f"no job like {want} (jobs: {[(j['kind'], j['status']) for j in outcome.jobs]})")
    for kind, most in expect.get("jobs_max", {}).items():
        n = sum(1 for j in outcome.jobs if j["kind"] == kind)
        if n > most:
            bad.append(f"{n} jobs of kind {kind}, at most {most}")
    main_runs = [set(names) for names in outcome.request_tools if "delegate" in names]   # a run that can hand work off is the main agent
    for name in expect.get("main_run_has", []):
        if not main_runs or not all(name in names for names in main_runs):
            bad.append(f"the main run was not offered {name}")
    for name in expect.get("main_run_lacks", []):
        if any(name in names for names in main_runs):
            bad.append(f"the main run was offered {name}")
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
