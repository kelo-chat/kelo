"""Behaviour checks: every scenario in tests/evals/scenarios runs through the real app with a scripted model and fake tools
(tests/evals/runner.py). Fast and offline; the expectations are the ones the scenario file states. See tests/evals/README.md.
The last tests check the checker itself: a wrong expectation must fail, and a malformed scenario must not load."""

import json

import pytest

from evals.runner import SCENARIO_DIR, Outcome, ScenarioError, check, load_scenario, load_scenarios, run_scenario

SCENARIOS = load_scenarios()


def test_every_scenario_file_is_valid():
    assert len(SCENARIOS) == 8
    assert {s["name"] for s in SCENARIOS} == {p.stem for p in SCENARIO_DIR.glob("*.json")}


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
async def test_scenario(sc, tmp_path):
    outcome = await run_scenario(sc, tmp_path)
    assert check(sc["expect"], outcome) == []


def write(tmp_path, data, name="a-case.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


GOOD = {"name": "a-case", "about": "x", "user": ["hi"], "model": [[{"text": "hi"}]], "expect": {"reply_contains": ["hi"]}}


def test_a_scenario_with_a_typo_does_not_load(tmp_path):
    with pytest.raises(ScenarioError, match="unknown expectations"):
        load_scenario(write(tmp_path, {**GOOD, "expect": {"reply_contian": ["hi"]}}))
    with pytest.raises(ScenarioError, match="unknown keys"):
        load_scenario(write(tmp_path, {**GOOD, "userr": []}))
    with pytest.raises(ScenarioError, match="name must be"):
        load_scenario(write(tmp_path, GOOD, name="other.json"))
    with pytest.raises(ScenarioError, match="a model item"):
        load_scenario(write(tmp_path, {**GOOD, "model": [[{"tool": "x", "text": "y"}]]}))
    with pytest.raises(ScenarioError, match="unknown real tool"):
        load_scenario(write(tmp_path, {**GOOD, "real": ["send_everything"]}))
    with pytest.raises(ScenarioError, match="live expectations"):
        load_scenario(write(tmp_path, {**GOOD, "live": {"expect": {"reply_contains": ["hi"]}}}))
    assert load_scenario(write(tmp_path, GOOD))["name"] == "a-case"


def outcome(**kw):
    o = Outcome()
    for k, v in kw.items():
        setattr(o, k, v)
    return o


def test_check_fails_on_each_wrong_expectation():
    o = outcome(calls=[("pay", {})], events=[{"type": "text", "text": "done"}], approvals=[], statuses=[200], login_cards=2,
                extra_model_calls=1)
    bad = check({"not_called": ["pay"], "reply_contains": ["nothing"], "approval_asked": True, "status": 409, "login_cards": 1,
                 "model_calls": 0}, o)
    assert bad == ["pay was called", "approval_asked is False, expected True", "reply lacks 'nothing'",
                   "the model was called 1 time(s) past its script", "turn start answered [200], expected 409",
                   "2 open login cards, expected 1"]
    assert check({"called": ["pay"], "reply_not_contains": ["done"]}, o)[:1] == ["reply has 'done'"]


def test_check_finds_a_secret_in_the_reply_and_the_log_but_not_when_absent():
    o = outcome(secrets=["sk-proj-EXAMPLEKEYNOTREAL000000"], events=[{"type": "text", "text": "key sk-proj-EXAMPLEKEYNOTREAL000000"}])
    assert check({"no_secret_in": ["reply"]}, o) == ["a secret from the user's message shows in reply"]
    o2 = outcome(secrets=["sk-proj-EXAMPLEKEYNOTREAL000000"], log_lines=["saved sk-proj-EXAMPLEKEYNOTREAL000000"])
    assert check({"no_secret_in": ["log", "tool_args"]}, o2) == ["a secret from the user's message shows in log"]
    assert check({"no_secret_in": ["reply", "tool_args", "log"]}, outcome(secrets=["sk-x"])) == []


def test_tool_error_must_reach_the_app_and_the_model():
    events = [{"type": "tool_result", "name": "web_search", "is_error": True, "output": "error: down"}]
    o = outcome(events=events, model_requests=[[{"role": "tool", "content": "error: down"}]])
    assert check({"tool_error": {"name": "web_search", "visible_to_model": "down"}}, o) == []
    hidden = outcome(events=events, model_requests=[[{"role": "tool", "content": "ok"}]])
    assert check({"tool_error": {"name": "web_search", "visible_to_model": "down"}}, hidden) == [
        "the model did not see the error 'down' in the tool result"]
    assert check({"tool_error": {"name": "web_search"}}, outcome(events=[])) == ["web_search did not report an error to the app"]
