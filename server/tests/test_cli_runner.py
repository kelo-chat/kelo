import httpx
import pytest

from assistant import i18n
from assistant.cli_runner import create_app
from assistant.providers import ChatRequest, Done, ProviderError, TextDelta, Thinking, ToolCall, Usage, stream_chat
from assistant.providers import cli_agents, cli_manage, cli_remote

TOKEN = "r" * 32
REQ = ChatRequest(model="sonnet", messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}],
                  max_tokens=50, temperature=0.2, extra={"effort": "low"})


def runner(stream_fn):
    app = create_app("claude", TOKEN, stream_fn=stream_fn)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner")


@pytest.fixture(autouse=True)
def remote_env(monkeypatch):
    monkeypatch.setenv("ASSISTANT_CLI_CLAUDE_URL", "http://runner")
    monkeypatch.setenv("CLI_RUNNER_TOKEN", TOKEN)


async def test_events_and_request_roundtrip_through_the_runner():
    seen = {}

    async def fake(provider, req):
        seen["provider"], seen["req"] = provider, req
        yield Thinking()
        yield TextDelta("he"); yield TextDelta("llo")
        yield ToolCall("c1", "search", '{"q": "x"}')
        yield Usage(3, 4); yield Done("tool_calls")

    async with runner(fake) as c:
        events = [e async for e in cli_remote.stream(cli_manage.CLAUDE, REQ, client=c)]
    assert events == [Thinking(), TextDelta("he"), TextDelta("llo"), ToolCall("c1", "search", '{"q": "x"}'), Usage(3, 4), Done("tool_calls")]
    assert seen["provider"] == cli_manage.CLAUDE and seen["req"] == REQ  # nothing lost on the way


async def test_runner_errors_keep_message_and_status():
    async def fake(provider, req):
        yield TextDelta("partial")
        raise ProviderError("rate limited", status=429)

    async with runner(fake) as c:
        got = []
        with pytest.raises(ProviderError, match="rate limited") as ei:
            async for e in cli_remote.stream(cli_manage.CLAUDE, REQ, client=c):
                got.append(e)
    assert got == [TextDelta("partial")] and ei.value.status == 429


async def test_runner_requires_the_shared_token(monkeypatch):
    async def fake(provider, req):
        raise AssertionError("must not run without auth")
        yield

    async with runner(fake) as c:
        assert (await c.post("/run", json={"request": {}})).status_code == 401
        assert (await c.get("/status")).status_code == 401
        assert (await c.get("/healthz")).status_code == 200
        monkeypatch.setenv("CLI_RUNNER_TOKEN", "x" * 32)  # server configured with a different token
        with pytest.raises(ProviderError, match="CLI_RUNNER_TOKEN"):
            [e async for e in cli_remote.stream(cli_manage.CLAUDE, REQ, client=c)]
        assert (await cli_remote.status(cli_manage.CLAUDE, client=c))["installed"] is False


async def test_sidecar_down_gives_the_command_that_turns_it_on(monkeypatch):
    monkeypatch.setenv("ASSISTANT_CLI_CLAUDE_URL", "http://127.0.0.1:1")  # nothing listens here
    with pytest.raises(ProviderError, match="docker compose --profile claude up -d --build"):
        [e async for e in cli_remote.stream(cli_manage.CLAUDE, REQ)]
    st = await cli_remote.status(cli_manage.CLAUDE)
    assert st["installed"] is False and "docker compose --profile claude up -d" in st["hint"]


async def test_status_goes_through_the_sidecar_with_version_and_update_hint(monkeypatch):
    async def fake_status(provider, local=False):
        assert local is True  # the runner reports on its own CLI, never recurses into another sidecar
        return {"installed": True, "installable": True, "logged_in": False, "version": "2.9.9 (Claude Code)", "hint": "x"}

    monkeypatch.setattr("assistant.cli_runner.cli_manage.cli_status", fake_status)
    app = create_app("claude", TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as c:
        st = await cli_remote.status(cli_manage.CLAUDE, client=c)
    assert st["installed"] and st["logged_in"] is False and st["remote"] and st["installable"] is False
    assert st["version"] == "2.9.9 (Claude Code)"
    assert "docker compose exec -it claude-cli claude" in st["hint"]  # how to log in inside the sidecar
    assert st["update_hint"] == "Update to the latest: assistant update --cli claude"
    tok = i18n.use("ko")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as c:
            st = await cli_remote.status(cli_manage.CLAUDE, client=c)
    finally:
        i18n.reset(tok)
    assert st["update_hint"] == "최신으로 업데이트: assistant update --cli claude"


async def test_stream_chat_uses_the_sidecar_when_configured(monkeypatch):
    async def remote(provider, req, **kw):
        yield TextDelta("from sidecar"); yield Done("stop")

    async def local(provider, req):
        raise AssertionError("must not run the CLI in the server process")
        yield

    monkeypatch.setattr(cli_remote, "stream", remote)
    monkeypatch.setattr(cli_agents, "stream", local)
    assert [e async for e in stream_chat(cli_manage.CLAUDE, REQ)] == [TextDelta("from sidecar"), Done("stop")]
    monkeypatch.delenv("ASSISTANT_CLI_CLAUDE_URL")  # unset = run locally
    with pytest.raises(AssertionError, match="server process"):
        [e async for e in stream_chat(cli_manage.CLAUDE, REQ)]


async def test_install_is_refused_in_sidecar_mode(monkeypatch):
    monkeypatch.setenv("ASSISTANT_CLI_AGY_URL", "http://agy-cli:9300")
    with pytest.raises(ProviderError, match="--profile agy"):
        await cli_manage.install_agy()


def test_runner_refuses_to_start_misconfigured():
    with pytest.raises(RuntimeError, match="claude"):
        create_app("codex", TOKEN)
    with pytest.raises(RuntimeError, match="CLI_RUNNER_TOKEN"):
        create_app("agy", "short")


async def test_models_list_through_the_sidecar_with_labels_and_errors(monkeypatch):
    async def fake_models(provider, local=False):
        assert local is True
        return {"gemini-x": "Gemini X", "plain": ""}

    monkeypatch.setenv("ASSISTANT_CLI_AGY_URL", "http://runner")
    monkeypatch.setattr("assistant.cli_runner.cli_manage.cli_models", fake_models)
    app = create_app("agy", TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as c:
        assert (await c.get("/models")).status_code == 401
        assert await cli_remote.models(cli_manage.AGY, client=c) == {"gemini-x": "Gemini X", "plain": ""}

        async def failing(provider, local=False):
            raise ProviderError("Check that you are signed in to agy")
        monkeypatch.setattr("assistant.cli_runner.cli_manage.cli_models", failing)
        with pytest.raises(ProviderError, match="signed in"):
            await cli_remote.models(cli_manage.AGY, client=c)


async def test_agy_models_command_is_parsed(tmp_path, monkeypatch):
    fake = tmp_path / "agy"
    fake.write_text("#!/bin/sh\nprintf 'Fetching available models...\\ngemini-3.8-flash-low\\tGemini 3.8 Flash (Low)\\nclaude-sonnet-4-6\\tClaude Sonnet 4.6\\n'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("AGY_COMMAND", str(fake))
    monkeypatch.delenv("ASSISTANT_CLI_AGY_URL", raising=False)
    monkeypatch.delenv("ASSISTANT_CLI_CLAUDE_URL")  # local mode for this test
    assert await cli_manage.cli_models(cli_manage.AGY) == {
        "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low)", "claude-sonnet-4-6": "Claude Sonnet 4.6"}

    fake.write_text("#!/bin/sh\necho 'Please sign in first'\n")  # not logged in: no tab-separated rows
    with pytest.raises(ProviderError, match="signed in to agy"):
        await cli_manage.cli_models(cli_manage.AGY)
    monkeypatch.setenv("AGY_COMMAND", str(tmp_path / "missing"))
    with pytest.raises(ProviderError):
        await cli_manage.cli_models(cli_manage.AGY)
    assert "sonnet" in await cli_manage.cli_models(cli_manage.CLAUDE)  # claude: fixed list of accepted ids


CLAUDE_USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 28% used · resets Nov 6 at 6am (Asia/Seoul)
Current week (all models): 63% used · resets Nov 7 at 1am (Asia/Seoul)

What's contributing to your usage?
Approximate · sessions on this machine only · shares overlap

Last 24h · 442 requests · 2 sessions
  91%  Start fresh when you switch tasks · run /clear
"""
AGY_USAGE = (
    "Gemini Models\tWeekly Limit Remaining\t100%\t2026-11-12T12:18:47Z\n"
    "Gemini Models\tFive Hour Limit Remaining\t99%\t2026-11-05T21:58:11Z\n"
    "Claude and GPT models\tWeekly Limit Remaining\t45%\t2026-11-08T15:38:14Z\n")


def test_claude_usage_is_parsed_and_reset_times_become_utc():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)
    w = cli_manage.parse_claude_usage(CLAUDE_USAGE, now)
    assert [(x["label"], x["used_percent"]) for x in w] == [("Current session", 28), ("Current week (all models)", 63)]
    assert w[0]["resets_at"] == "2026-11-05T21:00:00Z" and w[1]["resets_at"] == "2026-11-06T16:00:00Z"  # KST -> UTC
    assert w[0]["resets_text"] == "Nov 6 at 6am (Asia/Seoul)"  # the CLI's wording is kept as a fallback
    # the per-machine "what is contributing" breakdown must not leak in as a window
    assert len(w) == 2 and cli_manage.parse_claude_usage("Please log in", now) == []


def test_claude_reset_time_edge_cases():
    from datetime import datetime, timezone
    now = datetime(2026, 12, 30, 12, 0, tzinfo=timezone.utc)
    f = lambda s: cli_manage._claude_reset_iso(s, now)  # noqa: E731
    assert f("Jan 2 at 1am (UTC)") == "2027-01-02T01:00:00Z"        # rolls into next year
    assert f("Dec 31 at 12am (UTC)") == "2026-12-31T00:00:00Z"      # 12am is midnight
    assert f("Dec 31 at 12pm (UTC)") == "2026-12-31T12:00:00Z"      # 12pm is noon
    assert f("Dec 31 at 6:30pm (UTC)") == "2026-12-31T18:30:00Z"
    assert f("whenever") is None and f("Dec 31 at 6am (Not/AZone)") is None  # never raises


def test_agy_usage_reports_used_not_remaining():
    w = cli_manage.parse_agy_usage(AGY_USAGE)
    assert [(x["group"], x["label"], x["used_percent"]) for x in w] == [
        ("Gemini Models", "Weekly Limit Remaining", 0), ("Gemini Models", "Five Hour Limit Remaining", 1),
        ("Claude and GPT models", "Weekly Limit Remaining", 55)]
    assert w[1]["resets_at"] == "2026-11-05T21:58:11Z"
    assert cli_manage.parse_agy_usage("Fetching...\nnot a row") == []


async def test_cli_usage_runs_the_clis_usage_command(tmp_path, monkeypatch, real_cli_usage):
    monkeypatch.delenv("ASSISTANT_CLI_CLAUDE_URL")
    fake = tmp_path / "agy"
    (tmp_path / "usage.txt").write_text(AGY_USAGE)
    fake.write_text(f"#!/bin/sh\n[ \"$1 $2\" = '-p /usage' ] || exit 3\ncat {tmp_path / 'usage.txt'}\n")  # (printf would eat the % signs)
    fake.chmod(0o755)
    monkeypatch.setenv("AGY_COMMAND", str(fake))
    monkeypatch.delenv("ASSISTANT_CLI_AGY_URL", raising=False)
    w = await cli_manage.cli_usage(cli_manage.AGY)
    assert len(w) == 3 and w[2]["used_percent"] == 55
    fake.write_text("#!/bin/sh\necho 'Please sign in'\n")  # not logged in
    with pytest.raises(ProviderError, match="signed in"):
        await cli_manage.cli_usage(cli_manage.AGY)
    monkeypatch.setenv("AGY_COMMAND", str(tmp_path / "missing"))
    with pytest.raises(ProviderError):
        await cli_manage.cli_usage(cli_manage.AGY)


async def test_usage_through_the_sidecar(monkeypatch):
    async def fake(provider, local=False):
        assert local is True
        return [{"group": None, "label": "Current session", "used_percent": 28, "resets_at": None, "resets_text": None}]

    monkeypatch.setattr("assistant.cli_runner.cli_manage.cli_usage", fake)
    app = create_app("claude", TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as c:
        assert (await c.get("/usage")).status_code == 401
        assert (await cli_remote.usage(cli_manage.CLAUDE, client=c))[0]["used_percent"] == 28

        async def failing(provider, local=False):
            raise ProviderError("Could not get usage information. Check that you are signed in")
        monkeypatch.setattr("assistant.cli_runner.cli_manage.cli_usage", failing)
        with pytest.raises(ProviderError, match="signed in"):
            await cli_remote.usage(cli_manage.CLAUDE, client=c)
        tok = i18n.use("ko")   # the sidecar answers in English; the server shows its fixed messages in the user's language
        try:
            with pytest.raises(ProviderError, match="로그인했는지 확인하세요"):
                await cli_remote.usage(cli_manage.CLAUDE, client=c)
        finally:
            i18n.reset(tok)


def test_every_event_type_can_cross_the_sidecar():
    """encode/decode must cover the whole Event union, so a new event type cannot silently break CLI providers."""
    import typing
    from assistant.providers.types import Event
    samples = {"TextDelta": TextDelta("x"), "ToolCall": ToolCall("i", "n", "{}"), "Usage": Usage(1, 2),
               "Done": Done("stop"), "Thinking": Thinking()}
    assert {t.__name__ for t in typing.get_args(Event)} == set(samples), "add the new event to this test AND to cli_remote"
    for ev in samples.values():
        assert cli_remote.decode_event(cli_remote.encode_event(ev)) == ev


async def test_warm_endpoint_asks_for_the_token_and_hands_the_request_to_cli_agents(monkeypatch):
    seen = []

    async def fake_warm(provider, req):
        seen.append((provider, req.purpose, req.model))
        return True

    monkeypatch.setattr(cli_agents, "warm", fake_warm)
    app = create_app("agy", TOKEN, stream_fn=lambda *a: None)
    body = {"request": {"model": "m", "messages": [], "purpose": "chat"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as c:
        assert (await c.post("/warm", json=body)).status_code == 401
        r = await c.post("/warm", json=body, headers={"authorization": f"Bearer {TOKEN}"})
        assert r.json() == {"warm": True} and seen == [(cli_manage.AGY, "chat", "m")]
        monkeypatch.setenv("ASSISTANT_CLI_AGY_URL", "http://runner")
        assert await cli_remote.warm(cli_manage.AGY, ChatRequest(model="m", messages=[], purpose="chat"), client=c) is True
