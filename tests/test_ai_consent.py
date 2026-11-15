"""Asking once before chats go to an AI service (ai_consent.py; App Store guideline 5.1.2(i))."""

import httpx
import pytest

from assistant import ai_consent
from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, read_events


def recording():
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        yield TextDelta("ok")
        yield Done()
    return fn, calls


def app_for(tmp_path, provider="anthropic", fn=None):
    return create_app(Settings(token=TOKEN, provider=provider, model="m", cdp_host=None, db_path=str(tmp_path / "s.db")),
                      tools=[], stream_fn=fn)


@pytest.mark.parametrize("name,company", [
    ("anthropic", "Anthropic"), ("claude-subscription", "Anthropic"), ("openai", "OpenAI"), ("openai-codex", "OpenAI"),
    ("google", "Google"), ("gemini", "Google"), ("vertex", "Google"), ("agy", "Google"), ("openrouter", "OpenRouter"),
    ("bedrock", "Amazon Web Services"), ("qwen-oauth", "Alibaba Cloud"), ("mistral", "Mistral AI"),
])
def test_each_provider_names_its_company(name, company):
    assert ai_consent.company_of(name) == company


def test_every_catalog_provider_has_a_company_or_is_local():
    from assistant.providers.registry import list_providers
    for p in list_providers():
        if p.takes_url or p.name in ("custom", "lmstudio"):
            continue
        assert p.name in ai_consent.COMPANIES, p.name


@pytest.mark.parametrize("host,private", [
    ("localhost", True), ("127.0.0.1", True), ("192.168.0.20", True), ("10.1.2.3", True), ("100.101.102.103", True),
    ("freellmapi", True), ("host.docker.internal", True), ("box.tail1234.ts.net", True), ("nas.local", True),
    ("api.example.com", False), ("8.8.8.8", False),
])
def test_private_hosts(host, private):
    assert ai_consent.is_private_host(host) is private


def test_a_custom_endpoint_is_a_company_only_on_the_internet():
    from assistant.providers.registry import set_stored_base_url
    try:
        set_stored_base_url("custom", "http://192.168.0.5:1234/v1")
        assert ai_consent.company_of("custom") is None
        set_stored_base_url("custom", "https://llm.example.com/v1")
        assert ai_consent.company_of("custom") == "llm.example.com"
    finally:
        set_stored_base_url("custom", None)


async def test_held_back_until_agreed_once_then_never_again(tmp_path):
    fn, calls = recording()
    async with Running(app_for(tmp_path, fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        st = (await c.get("/api/ai-consent")).json()
        assert st["accepted"] is False and st["needed"] is True and st["company"] == "Anthropic"
        assert st["privacy_url"] == "https://kelo.chat/privacy"
        for path in ("/api/talk", "/api/turns"):
            r = await c.post(path, json={"text": "hello", "ai_consent": "check"})
            assert r.status_code == 428, r.text
            assert r.json()["detail"] == {"code": "ai_consent_required", "company": "Anthropic",
                                          "privacy_url": "https://kelo.chat/privacy", "version": 1}
        assert calls == [] and (await c.get("/api/messages")).json() == []      # nothing stored, nothing sent
        assert (await c.put("/api/ai-consent", json={"accepted": False})).status_code == 422   # Not now records nothing
        assert (await c.get("/api/ai-consent")).json()["accepted"] is False

        st = (await c.put("/api/ai-consent", json={"accepted": True})).json()
        assert st["accepted"] is True and st["needed"] is False
        assert st["record"]["device"] == "owner" and st["record"]["company"] == "Anthropic" and st["record"]["accepted_at"] > 0
        async with c.stream("POST", "/api/talk", json={"text": "hello", "ai_consent": "check"}) as r:
            assert r.status_code == 200
            await read_events(r)
        assert len(calls) == 1

        # another company later: no new question (the model picker says where chats go)
        assert (await c.put("/api/profile", json={"provider": "openrouter", "model": "x"})).status_code == 200
        st = (await c.get("/api/ai-consent")).json()
        assert st["company"] == "OpenRouter" and st["needed"] is False
        async with c.stream("POST", "/api/talk", json={"text": "again", "ai_consent": "check"}) as r:
            assert r.status_code == 200
            await read_events(r)
        assert len(calls) == 2

        # taking it back asks again
        assert (await c.delete("/api/ai-consent")).json()["accepted"] is False
        assert (await c.delete("/api/ai-consent")).status_code == 404
        assert (await c.post("/api/turns", json={"text": "x", "ai_consent": "check"})).status_code == 428


async def test_clients_that_do_not_ask_are_not_held(tmp_path):
    fn, calls = recording()
    async with Running(app_for(tmp_path, fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hello"}) as r:
            assert r.status_code == 200
            await read_events(r)
        assert len(calls) == 1
        assert (await c.post("/api/turns", json={"text": "x", "ai_consent": "maybe"})).status_code == 422


async def test_a_local_model_needs_no_answer(tmp_path):
    from assistant.providers.registry import set_stored_base_url
    fn, calls = recording()
    set_stored_base_url("lmstudio", "http://192.168.0.9:1234/v1")
    try:
        Store(tmp_path / "s.db").set_profile(provider="lmstudio", model="local")
        async with Running(app_for(tmp_path, provider="", fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            st = (await c.get("/api/ai-consent")).json()
            assert st["company"] is None and st["needed"] is False
            async with c.stream("POST", "/api/talk", json={"text": "hi", "ai_consent": "check"}) as r:
                assert r.status_code == 200
                await read_events(r)
    finally:
        set_stored_base_url("lmstudio", None)


async def test_one_answer_for_the_whole_server_and_providers_say_where_chats_go(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/ai-consent", json={"accepted": True})
    # a restart (another phone, the same server): still agreed
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/ai-consent")).json()["accepted"] is True
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert provs["anthropic"]["company"] == "Anthropic" and provs["openrouter"]["company"] == "OpenRouter"
