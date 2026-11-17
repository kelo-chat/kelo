"""`assistant eval live` gates (assistant/eval_live.py): OpenAI is refused on the main model, the fallback and the env file; CI and a
missing --yes stop the run before anything is sent; the results file is written. No model is called: the scenario runner is replaced
by a fake, and the refusals are checked before it is ever reached."""

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from assistant import eval_live as live
from assistant.eval_cli import run_command
from assistant.store import Store

KEY = Fernet.generate_key().decode()


class FakeRunner:
    """Stands in for server/tests/evals/runner.py: records the calls and returns a canned outcome (no model, no app)."""

    def __init__(self, passed=True):
        self.calls = []
        self.passed = passed

    def load_scenarios(self):
        return [{"name": "choice-card-rule", "user": ["Buy me a phone case"], "live": {"expect": {"not_called": ["pay"]}}, "model": []},
                {"name": "plain", "user": ["hi", "again"], "model": []}]

    def run_live(self, sc, workdir, base):
        self.calls.append((sc["name"], base.provider, base.model))
        return SimpleNamespace(model_requests=[[{}]])

    def check(self, expect, outcome):
        return [] if self.passed else ["pay was called"]

    def live_expect(self, sc):
        return dict(sc.get("live", {}).get("expect", {}))


def home_with_profile(tmp_path: Path, **profile) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    store = Store(home / "assistant.db", KEY)
    store.set_profile(**profile)
    return home


def env_file(tmp_path: Path, home: Path, extra: str = "") -> Path:
    env = tmp_path / "env"
    env.write_text(f"ASSISTANT_HOME={home}\nASSISTANT_SECRET_KEY={KEY}\nASSISTANT_TOKEN={'t' * 32}\n{extra}", encoding="utf-8")
    return env


@pytest.mark.parametrize("value", ["openai", "gpt-4o", "o3-mini", "openrouter/openai/gpt-4.1", "api.openai.com", "chatgpt-4o-latest"])
def test_openai_names_are_recognised(value):
    assert live.is_openai(value)


@pytest.mark.parametrize("value", ["anthropic", "claude-sonnet-4", "gemini-2.5-flash", "kimi-k2", "ollama", "go-helper", ""])
def test_other_names_are_not_openai(value):
    assert not live.is_openai(value)


def test_an_openai_main_model_is_refused_before_any_run(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)   # run_live reads os.environ when no environ is given; CI runners set CI=true
    home = home_with_profile(tmp_path, provider="openai", model="gpt-4o")
    fake = FakeRunner()
    monkeypatch.setattr(live, "scenario_runner", lambda: fake)
    lines = []
    assert live.run_live(env_file(tmp_path, home), name=None, yes=True, out=lines.append, results_home=tmp_path / "r") == 2
    assert fake.calls == [] and "OpenAI" in lines[-1]


def test_an_openai_fallback_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4", fallback_provider="openai", fallback_model="gpt-5")
    monkeypatch.setattr(live, "scenario_runner", lambda: FakeRunner())
    lines = []
    assert live.run_live(env_file(tmp_path, home), name=None, yes=True, out=lines.append, results_home=tmp_path / "r") == 2
    assert "fallback" in lines[-1]


def test_an_openai_model_in_the_env_file_is_refused_too(tmp_path):
    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4")
    with pytest.raises(live.LiveRefused, match="OpenAI"):
        live.check_profile({"provider": "anthropic", "model": "claude-sonnet-4"}, {"ASSISTANT_MODEL": "gpt-4o"})
    assert home.is_dir()


def test_ci_and_a_missing_yes_stop_the_run(tmp_path, monkeypatch):
    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4")
    fake = FakeRunner()
    monkeypatch.setattr(live, "scenario_runner", lambda: fake)
    lines = []
    assert live.run_live(env_file(tmp_path, home), name=None, yes=True, out=lines.append, environ={"CI": "1"},
                         results_home=tmp_path / "r") == 2
    assert "CI" in lines[-1]
    lines.clear()
    assert live.run_live(env_file(tmp_path, home), name=None, yes=False, out=lines.append, environ={},
                         results_home=tmp_path / "r") == 2
    assert "--yes" in lines[-1]
    assert fake.calls == []


def test_the_cli_refuses_without_yes(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4")
    monkeypatch.setattr(live, "scenario_runner", lambda: FakeRunner())
    lines = []
    args = Namespace(evcmd="live", scenario=None, instance="main", env_file=env_file(tmp_path, home), yes=False)
    assert run_command(args, out=lines.append) == 2


def test_a_run_with_a_fake_runner_writes_one_result_file(tmp_path, monkeypatch):
    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4")
    fake = FakeRunner(passed=False)
    monkeypatch.setattr(live, "scenario_runner", lambda: fake)
    lines = []
    results = tmp_path / "results"
    code = live.run_live(env_file(tmp_path, home), name=None, yes=True, out=lines.append, environ={}, results_home=results)
    assert code == 1
    assert fake.calls == [("choice-card-rule", "anthropic", "claude-sonnet-4")]
    assert any(line.startswith("FAIL choice-card-rule") for line in lines)
    written = next((results / "evals").glob("*.json"))
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["scenarios"][0]["name"] == "choice-card-rule" and data["scenarios"][0]["passed"] is False
    assert "model_requests" in data["scenarios"][0] and "reply" not in json.dumps(data)


def test_select_and_estimate():
    scenarios = [{"name": "a", "user": ["x", "y"], "live": {}}, {"name": "b", "user": ["z"]}]
    assert [s["name"] for s in live.select(scenarios, None)] == ["a"]
    assert live.estimate(live.select(scenarios, None)) == 2 * live.REQUESTS_PER_TURN
    with pytest.raises(live.LiveRefused):
        live.select(scenarios, "b")


def test_a_live_run_never_moves_or_deletes_keys_in_the_shared_key_container(tmp_path, monkeypatch):
    """The copy of the database can be older than the instance: while the run is on, the two start-up jobs that write to the key
    container (move provider keys in, sync connector keys / delete entries) are switched off, and they are back afterwards."""
    from assistant import cred_keyring, keyring_client

    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4")
    seen = {}

    class Spy(FakeRunner):
        def run_live(self, sc, workdir, base):
            seen["sync"], seen["move"] = cred_keyring.sync, keyring_client.move_keys
            return super().run_live(sc, workdir, base)

    before = cred_keyring.sync, keyring_client.move_keys
    monkeypatch.setattr(live, "scenario_runner", lambda: Spy())
    live.run_live(env_file(tmp_path, home), name=None, yes=True, out=lambda s: None, environ={}, results_home=tmp_path / "r")
    assert seen["sync"] is not before[0] and seen["move"] is not before[1]
    assert (cred_keyring.sync, keyring_client.move_keys) == before


def test_the_scenario_starts_in_an_empty_conversation(tmp_path, monkeypatch):
    """The copy holds the owner's real chat, tasks and jobs; a scenario that reads them as its own past (old tool calls, an open
    shop job) is not a fresh test. The copy's conversation is cleared first; the profile stays."""
    import sqlite3

    home = home_with_profile(tmp_path, provider="anthropic", model="claude-sonnet-4")
    store = Store(home / "assistant.db", KEY)
    store.add_message("user", "an old chat line")
    seen = {}

    class Spy(FakeRunner):
        def run_live(self, sc, workdir, base):
            db = sqlite3.connect(workdir / "app.db")
            seen["messages"] = db.execute("SELECT count(*) FROM messages").fetchone()[0]
            db.close()
            return super().run_live(sc, workdir, base)

    monkeypatch.setattr(live, "scenario_runner", lambda: Spy())
    live.run_live(env_file(tmp_path, home), name=None, yes=True, out=lambda s: None, environ={}, results_home=tmp_path / "r")
    assert seen["messages"] == 0
    assert Store(home / "assistant.db", KEY).messages()   # the source database was not touched
