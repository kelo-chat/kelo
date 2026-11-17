"""Behaviour checks: every scenario in tests/evals/scenarios runs through the real app with a scripted model and fake tools
(tests/evals/runner.py). Fast and offline; the expectations are the ones the scenario file states. See tests/evals/README.md.
The last tests check the checker itself: a wrong expectation must fail, and a malformed scenario must not load."""

import json

import pytest

from evals.runner import _fake_tool, SCENARIO_DIR, Outcome, ScenarioError, check, load_scenario, load_scenarios, run_scenario

SCENARIOS = load_scenarios()


def test_every_scenario_file_is_valid():
    assert len(SCENARIOS) == 12
    assert {s["name"] for s in SCENARIOS} == {p.stem for p in SCENARIO_DIR.glob("*.json")}


SCRIPTED = [s for s in SCENARIOS if not s.get("live_only")]   # live_only: a real model is the point (the payment specialist scenarios)


@pytest.mark.parametrize("sc", SCRIPTED, ids=[s["name"] for s in SCRIPTED])
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


def test_check_the_job_and_payment_expectations():
    o = outcome(calls=[("checkout_review", {"status": "draft"})], jobs=[{"id": "w1", "kind": "checkout", "status": "needs_input"}],
                request_tools=[["delegate", "browser_read"], ["report", "checkout_review"]])
    assert check({"called_with": [{"tool": "checkout_review", "args": {"status": "draft"}}],
                  "not_called_with": [{"tool": "checkout_review", "args": {"status": "ready"}}],
                  "jobs": [{"kind": "checkout", "status": "needs_input"}], "jobs_max": {"checkout": 1},
                  "main_run_lacks": ["checkout_review"]}, o) == []
    bad = check({"called_with": [{"tool": "checkout_review", "args": {"status": "ready"}}],
                 "not_called_with": [{"tool": "checkout_review", "args": {"status": "draft"}}],
                 "jobs": [{"kind": "checkout", "status": "done"}], "jobs_max": {"checkout": 0},
                 "main_run_has": ["checkout_review"]}, o)
    assert len(bad) == 5 and "main run was not offered checkout_review" in bad[-1]
    assert check({"called_with": [{"tool": "delegate", "args": {"kind": "checkout", "url": "*"}}]}, outcome(
        model_requests=[[{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "delegate", "arguments":
                                                                  '{"kind": "checkout", "url": "https://shop.example.test/cart"}'}}]}]])) == []


def test_a_scenario_may_only_switch_known_settings(tmp_path):
    with pytest.raises(ScenarioError, match="settings may only set"):
        load_scenario(write(tmp_path, {**GOOD, "settings": {"token": "x"}}))
    with pytest.raises(ScenarioError, match="live_only needs a live block"):
        load_scenario(write(tmp_path, {**GOOD, "live_only": True}))


async def test_the_runner_runs_a_job_and_reports_it(tmp_path):
    """The workers path of the runner with a fake model: the chat hands a checkout job over, the job reaches the review and reports."""
    from assistant.providers import Done, ToolCall

    sc = {"name": "x", "user": ["buy it"], "model": [], "tools": {"checkout_review": {"result": "card shown"}}, "workers": True,
          "settings": {"payment_specialist": True}}

    async def fake(provider, request, **kw):
        names = [t["function"]["name"] for t in request.tools]
        last = request.messages[-1]
        if "delegate" in names and last.get("role") != "tool" and "[Not from the user" not in str(last.get("content")):
            yield ToolCall("c1", "delegate", json.dumps({"task": "pay for the cart", "kind": "checkout", "url": "https://shop.example.test/cart"}))
            yield Done("tool_calls")
        elif "report" in names and last.get("role") != "tool":
            yield ToolCall("c2", "checkout_review", json.dumps({"status": "ready"}))
            yield ToolCall("c3", "report", json.dumps({"status": "needs_input", "summary": "waiting for the approval"}))
            yield Done("tool_calls")
        else:
            from assistant.providers import TextDelta
            yield TextDelta("ok")
            yield Done("stop")

    o = await run_scenario(sc, tmp_path, stream_fn=fake)
    assert [j["kind"] + ":" + j["status"] for j in o.jobs] == ["checkout:needs_input"]
    assert ("checkout_review", {"status": "ready"}) in o.calls
    assert check({"called_with": [{"tool": "delegate", "args": {"kind": "checkout"}}], "main_run_lacks": ["checkout_review"],
                  "jobs": [{"kind": "checkout", "status": "needs_input"}]}, o) == []


# ---- the payment specialist scenarios (live_only): run here with a stand-in model that behaves as the design says, to prove the fixtures,
# the fake tools and the expectations work together before a real model is spent on them. The real-model run is `assistant eval live`.
def _after_last_user(messages):
    idx = max((i for i, m in enumerate(messages) if m.get("role") == "user" and not str(m.get("content")).startswith("[Not from the user")),
              default=-1)
    return messages[idx + 1:]


def _calls(*pairs):
    from assistant.providers import Done, ToolCall

    async def gen():
        for i, (name, args) in enumerate(pairs):
            yield ToolCall(f"s{len(pairs)}{i}{name}", name, json.dumps(args))
        yield Done("tool_calls")
    return gen()


def _say(text):
    from assistant.providers import Done, TextDelta

    async def gen():
        yield TextDelta(text)
        yield Done("stop")
    return gen()


async def stand_in_model(provider, request, **kw):
    names = {t["function"]["name"] for t in request.tools}
    steps = [m for m in _after_last_user(request.messages) if m.get("role") == "tool"]
    terms = {"site": "shop.example.test", "items": [{"name": "Trail Mug, blue enamel", "qty": 2}], "total": 24, "currency": "USD"}
    if "report" in names:                       # a background job
        if not steps:
            gen = _calls(("browser_read", {}))
        elif len(steps) == 1:
            gen = (_calls(("report", {"status": "needs_input", "summary": "the cart shows 3, the brief says 2"}))
                   if "Quantity: 3" in steps[0]["content"] else _calls(("checkout_review", {**terms, "status": "draft"})))
        elif len(steps) == 2:
            gen = _calls(("checkout_review", {**terms, "status": "ready"}))
        else:
            gen = _calls(("report", {"status": "needs_input", "summary": "the payment approval is waiting"}))
    elif steps and not str(_after_last_user(request.messages)[-1].get("content")).startswith("[Not from the user"):
        paying = "checkout_review" in names     # the chat holds the payment tools only with the split off
        if paying and len(steps) == 1:
            gen = _calls(("checkout_review", {**terms, "status": "ready"}))
        else:
            gen = _say("Done for now.")
    elif "checkout_review" in names:
        gen = _calls(("checkout_review", {**terms, "status": "draft"}))
    elif "delegate" in names and "[Not from the user" not in str(request.messages[-1].get("content")):
        gen = _calls(("delegate", {"kind": "checkout", "url": "https://shop.example.test/cart", "task": "Pay for 2 blue Trail Mugs, 24 USD."}))
    else:
        gen = _say("Waiting for you.")
    async for ev in gen:
        yield ev


PAY_SCENARIOS = [s for s in SCENARIOS if s.get("live_only")]


@pytest.mark.parametrize("sc", PAY_SCENARIOS, ids=[s["name"] for s in PAY_SCENARIOS])
async def test_payment_scenarios_pass_with_a_stand_in_model_that_follows_the_design(sc, tmp_path):
    from evals.runner import live_expect

    outcome = await run_scenario(sc, tmp_path, stream_fn=stand_in_model)
    assert check(live_expect(sc), outcome) == []


async def test_a_model_that_pays_the_wrong_cart_fails_the_expectations(tmp_path):
    from evals.runner import live_expect

    sc = next(s for s in PAY_SCENARIOS if s["name"] == "checkout-cart-differs-asks-back")

    async def reckless(provider, request, **kw):
        names = {t["function"]["name"] for t in request.tools}
        steps = [m for m in _after_last_user(request.messages) if m.get("role") == "tool"]
        if "report" in names:
            gen = (_calls(("checkout_review", {"status": "ready", "site": "shop.example.test", "items": [], "total": 36, "currency": "USD"}))
                   if not steps else _calls(("report", {"status": "done", "summary": "ok"})))
        elif not steps:
            gen = _calls(("delegate", {"kind": "checkout", "url": "https://shop.example.test/cart", "task": "pay"}))
        else:
            gen = _say("ok")
        async for ev in gen:
            yield ev

    outcome = await run_scenario(sc, tmp_path, stream_fn=reckless)
    bad = check(live_expect(sc), outcome)
    assert any("checkout_review was called with" in b for b in bad) and any("no job like" in b for b in bad)


@pytest.mark.parametrize("name", ["checkout-job-reaches-review", "checkout-cart-differs-asks-back", "checkout-second-job-refused",
                                  "checkout-split-off-chat-pays"])
async def test_the_fixture_shop_refuses_a_press_on_the_pay_control_like_the_real_guard(name):
    """Real-model check 2026-11-16: the fake click answered "Added to cart" to the pay control, so a model that never handed the checkout
    over kept pressing it and saw nothing wrong. The real browser_click answers a press on a final checkout step with checkout.REFUSAL."""
    from assistant.tools.checkout import REFUSAL
    sc = next(s for s in load_scenarios() if s["name"] == name)
    tool = _fake_tool("browser_click", sc["tools"]["browser_click"], Outcome())
    assert await tool.handler({"selector": "e9"}) == REFUSAL
    assert (await tool.handler({"selector": "e4"})).startswith("Clicked")


async def test_a_chat_that_only_reads_the_cart_gets_its_checkout_job_started_by_the_app_and_a_product_page_starts_none(tmp_path):
    """The hand-over by structure in the runner: the fixture marks its cart page as a final checkout step, the app sees it through the
    fake page and starts the job; a model that never calls delegate still ends with one checkout job, and reading the product starts none."""
    from assistant.providers import Done, TextDelta, ToolCall
    sc = next(s for s in load_scenarios() if s["name"] == "checkout-job-reaches-review")
    cart = "https://shop.example.test/cart"
    product = "https://shop.example.test/products/trail-mug-blue"

    def stand_in(*urls):
        async def fake(provider, request, **kw):
            names = [t["function"]["name"] for t in request.tools]
            tail = _after_last_user(request.messages)
            done = sum(1 for m in tail if m.get("role") == "tool")
            if "report" in names:
                if done == 0:
                    yield ToolCall("j1", "report", json.dumps({"status": "needs_input", "summary": "waiting"}))
                    yield Done("tool_calls")
                    return
            elif "delegate" in names and done < len(urls):
                yield ToolCall(f"c{done}", "browser_open", json.dumps({"url": urls[done]}))
                yield Done("tool_calls")
                return
            yield TextDelta("ok")
            yield Done("stop")
        return fake

    o = await run_scenario(sc, tmp_path / "a", stream_fn=stand_in(product, cart, cart))
    assert [j["kind"] for j in o.jobs] == ["checkout"]
    (tmp_path / "b").mkdir()
    o = await run_scenario(sc, tmp_path / "b", stream_fn=stand_in(product, product))
    assert o.jobs == []


async def test_a_job_that_must_ask_a_card_is_answered_by_the_runner_not_left_waiting_half_an_hour(tmp_path):
    """The hand-over started a job whose later page opens asked a card (outside text was read, the address was not the owner's words) and
    nobody answered it in an eval: the job sat 30 minutes `running`. The runner answers job cards like the chat's (the scenario's approve)."""
    from assistant.providers import Done, TextDelta, ToolCall
    sc = next(s for s in load_scenarios() if s["name"] == "checkout-job-reaches-review")
    cart = "https://shop.example.test/cart"

    async def fake(provider, request, **kw):
        names = [t["function"]["name"] for t in request.tools]
        done = sum(1 for m in _after_last_user(request.messages) if m.get("role") == "tool")
        if "report" in names and done < 3:
            yield ToolCall(f"j{done}", "browser_open", json.dumps({"url": cart}))
            yield Done("tool_calls")
        elif "report" in names:
            yield ToolCall("jr", "report", json.dumps({"status": "needs_input", "summary": "x"}))
            yield Done("tool_calls")
        elif "delegate" in names and done == 0:
            yield ToolCall("c0", "browser_open", json.dumps({"url": cart}))
            yield Done("tool_calls")
        else:
            yield TextDelta("ok")
            yield Done("stop")

    o = await run_scenario({**sc, "user": sc["user"][:1]}, tmp_path, stream_fn=fake)
    assert [j["status"] for j in o.jobs] == ["needs_input"] and o.approvals
