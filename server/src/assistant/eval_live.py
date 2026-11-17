"""`assistant eval live`: the scenarios marked "live" run against the instance's own model, through the normal provider path.

It spends the owner's model quota, so it refuses to start unless every condition holds:
- `--yes` is given (the estimate of model requests is printed first and the run needs the flag);
- the environment has no `CI` variable (live runs never happen in CI);
- neither the main model nor the fallback is an OpenAI model or an OpenAI endpoint (checked on the profile in the instance's database and
  on its env file; the check is by provider name, model id and address, so an OpenAI model behind another name is caught too).
Refusals exit with status 2 before anything is sent. The run uses a private copy of the instance's database (chat, keys and vault are
read from it, nothing is written back), the fake tools of the scenario (no real tool runs), and writes one result file to
~/.kelo/evals/<date>.json with pass/fail per scenario and the failed expectations (never a reply text).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .chat_clear import clear_tables
from .eval_export import copy_database, db_of, env_for
from .store import Store

OPENAI_PATTERNS = re.compile(r"openai|azure[-_ ]?openai|chatgpt|\bgpt[-_.0-9a-z]*|\bo[134]-|\bo[134]$|api\.openai\.com|openai\.azure\.com", re.I)
REQUESTS_PER_TURN = 3        # a rough planning figure for the estimate (a turn is one or more model calls)


class LiveRefused(RuntimeError):
    """A condition that stops the live run (the message says which one and how to fix it)."""


def is_openai(*fields: str) -> bool:
    """True when any of the given values (provider id, model id, address) names OpenAI."""
    return any(f and OPENAI_PATTERNS.search(f) for f in fields)


def check_profile(profile: dict[str, str], env: dict[str, str]) -> None:
    """Refuses an OpenAI main or fallback model, read from the database profile and from the env file (both must pass)."""
    names = {
        "main provider": profile.get("provider", ""), "main model": profile.get("model", ""),
        "fallback provider": profile.get("fallback_provider", ""), "fallback model": profile.get("fallback_model", ""),
        "background provider": profile.get("background_provider", ""), "background model": profile.get("background_model", ""),
        "env provider": env.get("ASSISTANT_PROVIDER", ""), "env model": env.get("ASSISTANT_MODEL", ""),
        "env base url": env.get("ASSISTANT_OPENAI_BASE_URL", "") + env.get("ASSISTANT_BASE_URL", ""),
    }
    for label, value in names.items():
        if is_openai(value):
            raise LiveRefused(f"refused: the {label} is an OpenAI model or endpoint ({value}); live runs never send to OpenAI. "
                              "Pick another provider in the app's settings first.")


def check_gates(*, yes: bool, environ: dict[str, str]) -> None:
    if "CI" in environ:
        raise LiveRefused("refused: CI is set; live runs never happen in CI")
    if not yes:
        raise LiveRefused("refused: a live run spends the owner's model quota; add --yes to run it (the estimate is printed first)")


def estimate(scenarios: list[dict[str, Any]]) -> int:
    return sum(max(1, len(sc["user"])) * REQUESTS_PER_TURN for sc in scenarios)


def select(scenarios: list[dict[str, Any]], name: str | None) -> list[dict[str, Any]]:
    live = [sc for sc in scenarios if "live" in sc]
    if name:
        live = [sc for sc in live if sc["name"] == name]
        if not live:
            raise LiveRefused(f"no live scenario named {name!r}")
    return live


def results_path(now: float | None = None, home: Path | None = None) -> Path:
    day = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
    base = (home or Path.home() / ".kelo") / "evals"
    return base / f"{day}.json"


def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "scenarios": rows}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def scenario_runner():
    """The scenario runner and the scenarios live with the tests (server/tests/evals), so this runs from a repository checkout."""
    tests = Path(__file__).resolve().parents[2] / "tests"
    if not (tests / "evals" / "runner.py").is_file():
        raise LiveRefused("refused: the scenarios are in the repository (server/tests/evals); run this from a checkout")
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    return importlib.import_module("evals.runner")


def read_only_keyring() -> Callable[[], None]:
    """A live run on a copy of the database may share the instance's key container (docs/browser-workers.md). The copy can be older than
    the instance, so nothing may be moved, written or deleted there: the two start-up jobs that do that are switched off. Sending the ring key
    (unlock) and reading the entries stay. Returns the function that puts the originals back."""
    from . import cred_keyring, keyring_client

    old = cred_keyring.sync, keyring_client.move_keys

    async def nothing(*a: Any, **k: Any) -> dict[str, int]:
        return {}

    async def no_moves(*a: Any, **k: Any) -> int:
        return 0

    cred_keyring.sync, keyring_client.move_keys = nothing, no_moves

    def restore() -> None:
        cred_keyring.sync, keyring_client.move_keys = old
    return restore


def run_live(env_file: Path, *, name: str | None, yes: bool, out: Callable[[str], None] = print,
             environ: dict[str, str] | None = None, results_home: Path | None = None) -> int:
    """Runs the live scenarios. Returns the exit status: 0 all passed, 1 some failed, 2 refused."""
    from .settings import Settings

    environ = dict(os.environ) if environ is None else environ
    rows: list[dict[str, Any]] = []
    restore = read_only_keyring()
    try:
        check_gates(yes=yes, environ=environ)
        env = env_for(env_file)
        db, key = db_of(env)
        runner = scenario_runner()
        scenarios = select(runner.load_scenarios(), name)
        out(f"{len(scenarios)} live scenario(s), about {estimate(scenarios)} model requests")
        work = Path(tempfile.mkdtemp(prefix="kelo-live-"))
        try:
            copy = copy_database(db, work)
            store = Store(copy, key or None)
            profile = store.profile()
            clear_tables(store)   # a scenario is a fresh conversation: the owner's chat, tasks and jobs in the copy would be read as its past
            check_profile(profile, env)
            if not profile.get("provider") or not profile.get("model"):
                raise LiveRefused("refused: no main model is set in the app; set one first")
            for sc in scenarios:
                d = work / sc["name"]
                d.mkdir()
                shutil.copy2(copy, d / "app.db")
                if (work / "master.key").is_file():
                    shutil.copy2(work / "master.key", d / "master.key")
                base = replace(Settings.from_env(env), provider=profile["provider"], model=profile["model"])
                outcome = runner.run_live(sc, d, base)
                failures = runner.check(runner.live_expect(sc), outcome)
                ok = not failures
                out(f"{'PASS' if ok else 'FAIL'} {sc['name']}" + ("" if ok else ": " + "; ".join(failures)))
                rows.append({"name": sc["name"], "passed": ok, "failures": failures, "model_requests": len(outcome.model_requests),
                             "tools_called": [n for n, _ in getattr(outcome, "calls", [])],   # tool names in order, never arguments or texts
                             "jobs": [(j["kind"], j["status"]) for j in getattr(outcome, "jobs", [])]})
                if hasattr(outcome, "observed_calls"):   # every tool call in order (names and the kind of a delegate, never other arguments)
                    seen = [n + (f"[{a.get('kind')}]" if n == "delegate" else "") + (f"[{a.get('status')}]" if n == "checkout_review" else "")
                            for n, a in outcome.observed_calls()[len(outcome.calls):]]
                    jobs = ", ".join(j["kind"] + ":" + j["status"] for j in outcome.jobs)
                    out(f"  requests {len(outcome.model_requests)}; calls {', '.join(seen)}; delegate errors {outcome.refused('delegate')}; jobs {jobs}")
                    rows[-1]["calls"] = seen
                if environ.get("KELO_EVAL_SHOW"):   # debugging a failed scenario: the fixture texts the model saw last and its reply (never written to the results file)
                    tool_texts = [str(m.get("content") or "")[:300] for m in (outcome.model_requests[-1] if outcome.model_requests else []) if m.get("role") == "tool"]
                    out("  last tool results: " + " | ".join(t.replace("\n", " / ") for t in tool_texts[-4:]))
                    out("  reply: " + getattr(outcome, "reply", "")[:800].replace("\n", " / "))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except LiveRefused as e:
        out(str(e))
        return 2
    finally:
        restore()
    write_results(results_path(home=results_home), rows)
    return 0 if all(r["passed"] for r in rows) else 1
