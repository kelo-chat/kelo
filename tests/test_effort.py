import httpx
import pytest

from assistant import effort
from assistant.providers import Done, TextDelta
from assistant.providers import cli_manage
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, read_events, scripted, settings  # noqa: F401 (shared helpers)


def test_each_provider_gets_the_field_it_actually_understands():
    assert effort.request_extra(cli_manage.CLAUDE, "high") == {"effort": "high"}      # cli_agents -> --effort
    assert effort.request_extra(cli_manage.AGY, "max", "gemini-3.8-flash") == {"effort": "max"}   # suffixless Gemini only
    assert effort.request_extra("openai-codex", "medium") == {"reasoning": {"effort": "medium"}}
    assert effort.request_extra("openrouter", "low") == {"reasoning": {"effort": "low"}}
    assert effort.request_extra("openai", "high") == {"reasoning_effort": "high"}
    assert effort.request_extra("gemini", "low") == {"reasoning_effort": "low"}


def test_agy_only_takes_effort_for_a_suffixless_gemini_id_like_the_plugin_does():
    for model in ("gemini-3.8-flash-high", "gemini-3.8-flash-low", "claude-opus-4-6-thinking", "gpt-oss-120b-medium", None):
        assert effort.request_extra(cli_manage.AGY, "high", model) == {}   # the plugin would silently drop it
    assert effort.levels(cli_manage.AGY) == () and "model name" in effort.note(cli_manage.AGY)


def test_unsupported_or_unset_sends_nothing():
    for name in ("anthropic", "deepseek", "xai", "unknown"):
        assert effort.request_extra(name, "high") == {}            # unknown fields make many APIs answer 400
    assert effort.request_extra(cli_manage.CLAUDE, None) == {} and effort.request_extra(cli_manage.CLAUDE, "") == {}
    assert effort.request_extra(cli_manage.CLAUDE, "turbo") == {}  # not a level


def test_levels_a_provider_lacks_are_clamped_down_not_sent():
    assert effort.clamp("openrouter", "max") == "high" and effort.clamp("openrouter", "xhigh") == "high"
    assert effort.clamp("openai-codex", "medium") == "medium"
    assert effort.clamp(cli_manage.CLAUDE, "xhigh") == "xhigh"
    assert effort.levels("anthropic") == ()


async def test_effort_from_profile_or_request_reaches_the_provider_and_the_fallback_gets_its_own_mapping():
    seen = []

    async def fn(provider, request, **kw):
        seen.append((provider, dict(request.extra)))
        if provider == "openrouter":
            raise RuntimeError("boom")
        yield TextDelta("ok"); yield Done("stop")

    app = create_app(Settings(token=TOKEN, cdp_host=None, unofficial_providers=True), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/profile", json={"effort": "turbo"})).status_code == 422
        await c.put(f"/api/providers/{cli_manage.CLAUDE}/consent", json={"accepted": True})   # unofficial: consent first
        await c.put("/api/profile", json={"provider": "openrouter", "model": "m", "effort": "max",
                                          "fallback_provider": cli_manage.CLAUDE, "fallback_model": "sonnet"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
        # an explicit effort on /api/chat wins over the profile
        async with c.stream("POST", "/api/chat", headers=AUTH, json={
                "provider": cli_manage.CLAUDE, "model": "sonnet", "effort": "low",
                "messages": [{"role": "user", "content": "x"}]}) as r:
            await read_events(r)
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
    assert seen[0] == ("openrouter", {"reasoning": {"effort": "high"}})          # max clamped to openrouter's top level
    assert seen[1] == (cli_manage.CLAUDE, {"effort": "max"})                     # fallback: claude does accept max
    assert seen[2] == (cli_manage.CLAUDE, {"effort": "low"})
    assert provs[cli_manage.AGY]["effort_levels"] == [] and "model name" in provs[cli_manage.AGY]["effort_note"]
    assert provs["openrouter"]["effort_levels"] == ["low", "medium", "high"]
    assert provs["anthropic"]["effort_levels"] == [] and provs[cli_manage.CLAUDE]["effort_levels"][-1] == "max"
