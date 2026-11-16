"""The thinking level on a custom OpenAI-compatible endpoint (EXAONE, vLLM, ...): what is sent per level, the 400 that drops the
field and is remembered, reasoning that never becomes answer text, the stall guard and the long-think status line.

Nothing here calls a real provider: MockTransport handlers, a small local HTTP server and scripted stream functions only."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import pytest

from assistant import effort
from assistant.providers import ChatRequest, Done, TextDelta, Thinking, ToolCall, Usage, custom_provider, stream_chat
from assistant.providers import refused
from assistant.providers.transports import openai_chat
from assistant.providers.types import ProviderError, ProviderStalled
from assistant.resume import is_outage
from assistant.server import Settings, chat_error_text, create_app

from test_server import AUTH, TOKEN, Running, read_events

BASE = "http://exaone.test/v1"
MODEL = "k-exaone_v2.5"


@pytest.fixture(autouse=True)
def _clean_notes():
    refused.on_change = None
    refused.forget()
    yield
    refused.on_change = None
    refused.forget()


def sse(*chunks) -> bytes:
    return "".join(f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks).encode()


def answer(text="ok") -> bytes:
    return sse({"choices": [{"delta": {"content": text}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}, "[DONE]")


async def run(req: ChatRequest, handler, base=BASE):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        return [e async for e in stream_chat(custom_provider(base), req, client=c)]


def req(level: str | None) -> ChatRequest:
    return ChatRequest(model=MODEL, messages=[{"role": "user", "content": "hi"}], extra=effort.request_extra("custom", level, MODEL))


# ---------------------------------------------------------------------------------------------------------------- request body
async def test_each_level_sends_reasoning_effort_and_the_default_sends_nothing():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=answer())

    for level in ("", None, "low", "medium", "high", "max", "xhigh"):
        await run(req(level), handler)
    sent = [b.get("reasoning_effort", "<absent>") for b in bodies]
    # "" / None: nothing at all (the server's own default); xhigh is not a custom level: the next lower one (high)
    assert sent == ["<absent>", "<absent>", "low", "medium", "high", "max", "high"]
    assert effort.levels("custom") == ("low", "medium", "high", "max")
    assert all("reasoning" not in b for b in bodies)   # the plain Chat Completions field only, never OpenRouter's object


# ---------------------------------------------------------------------------------------------------------------- 400 fallback
async def test_a_400_naming_the_field_is_retried_without_it_and_remembered():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument supplied: reasoning_effort"}})
        return httpx.Response(200, content=answer("hello"))

    ev = await run(req("high"), handler)
    assert [e.text for e in ev if isinstance(e, TextDelta)] == ["hello"]           # the turn did not fail
    assert ["reasoning_effort" in b for b in bodies] == [True, False]
    assert refused.refused(refused.EFFORT_FIELD, BASE, MODEL)
    assert effort.refused_for("custom", BASE, MODEL) and not effort.refused_for("custom", BASE, "other-model")

    bodies.clear()
    await run(req("high"), handler)                                                # the next request leaves it out from the start
    assert ["reasoning_effort" in b for b in bodies] == [False]
    bodies.clear()
    await run(req("high"), handler, base="http://other.test/v1")                   # another address is tried with it again
    assert ["reasoning_effort" in b for b in bodies] == [True, False]


async def test_a_400_only_with_the_field_is_remembered_once_the_retry_works():
    def handler(request):
        if "reasoning_effort" in json.loads(request.content):
            return httpx.Response(400, json={"error": "bad request"})                # says nothing about the field
        return httpx.Response(200, content=answer())

    await run(req("low"), handler)
    assert refused.refused(refused.EFFORT_FIELD, BASE, MODEL)


async def test_a_400_that_is_not_about_the_field_is_raised_and_not_remembered():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "messages: bad shape"})

    with pytest.raises(ProviderError) as info:
        await run(req("max"), handler)
    assert info.value.status == 400 and len(calls) == 2                            # one retry, no more
    assert not refused.refused(refused.EFFORT_FIELD, BASE, MODEL)

    calls.clear()
    with pytest.raises(ProviderError):
        await run(req(""), handler)                                                # no field: no retry at all
    assert len(calls) == 1


async def test_the_note_is_kept_by_the_server_and_shown_in_the_model_list(tmp_path):
    seen = []

    async def fn(provider, request, **kw):
        seen.append(dict(request.extra))
        yield TextDelta("ok")
        yield Done("stop")

    s = Settings(token=TOKEN, cdp_host=None, db_path=str(tmp_path / "a.db"))
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/providers/custom/endpoint", json={"base_url": BASE})).status_code == 200
        assert (await c.put("/api/profile", json={"provider": "custom", "model": MODEL, "effort": "max"})).status_code == 200
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert provs["custom"]["effort_levels"] == ["low", "medium", "high", "max"]
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
        assert seen[-1] == {"reasoning_effort": "max"}
        await c.put("/api/profile", json={"effort": ""})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
        assert seen[-1] == {}                                                      # 기본: nothing sent

        refused.mark(refused.EFFORT_FIELD, BASE, MODEL)                            # what the client notes after the 400
        info = (await c.get("/api/models", params={"provider": "custom"})).json()["info"]
        assert info[MODEL]["effort_levels"] == [] and info[MODEL]["effort_note"] == "This server does not accept a thinking level"
        ko = (await c.get("/api/models", params={"provider": "custom"}, headers={"Accept-Language": "ko"})).json()["info"]
        assert ko[MODEL]["effort_note"] == "이 서버는 생각 수준 설정을 받지 않아요"

    refused.on_change = None
    refused.forget()   # a restart: the note comes back from the database
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert refused.refused(refused.EFFORT_FIELD, BASE, MODEL)
        await c.put("/api/providers/custom/endpoint", json={"base_url": BASE})     # saving the address again: tried once more
        assert not refused.refused(refused.EFFORT_FIELD, BASE, MODEL)


# ------------------------------------------------------------------------------------------------------- reasoning is not text
async def test_reasoning_content_never_becomes_answer_text_and_its_tokens_are_counted():
    secret = "SECRET-REASONING"

    def handler(request):
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"reasoning_content": f"{secret} step 1"}}]},
            {"choices": [{"delta": {"reasoning_content": f"{secret} step 2", "content": None}}]},
            {"choices": [{"delta": {"content": "답이에요"}}]},
            {"choices": [{"delta": {"reasoning_content": f"{secret} again"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 42}}},
            "[DONE]"))

    ev = await run(req("low"), handler)
    assert [type(e).__name__ for e in ev] == ["Thinking", "TextDelta", "Thinking", "Usage", "ToolCall", "Done"]
    assert [e.text for e in ev if isinstance(e, TextDelta)] == ["답이에요"]
    assert all(secret not in repr(e) for e in ev)
    assert next(e for e in ev if isinstance(e, Usage)).reasoning_tokens == 42
    assert openai_chat.reasoning_tokens({"reasoning_tokens": 7}) == 7 and openai_chat.reasoning_tokens({}) == 0


async def test_reasoning_never_reaches_the_chat_and_the_turn_logs_reasoning_tokens(tmp_path):
    async def fn(provider, request, **kw):
        yield Thinking()
        yield TextDelta("final")
        yield Usage(5, 9, reasoning_tokens=7)
        yield Done("stop")

    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    keep = Keep(level=logging.DEBUG)
    logger = logging.getLogger("uvicorn.error")   # uvicorn's own logger (its config resets the handlers: added once it runs)
    s = Settings(token=TOKEN, provider="custom", model=MODEL, cdp_host=None, db_path=str(tmp_path / "a.db"))
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        level = logger.level
        logger.addHandler(keep)
        logger.setLevel(logging.INFO)
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            events = await read_events(r)
    assert [e["type"] for e in events if e["type"] in ("thinking", "text")] == ["thinking", "text"]
    assert "".join(e.get("text", "") for e in events if e["type"] == "text") == "final"
    logger.removeHandler(keep)
    logger.setLevel(level)
    assert any("9 output (7 of them reasoning)" in line for line in lines), lines


# ---------------------------------------------------------------------------------------------------------------- stall guard
class FakeStream:
    """A tiny HTTP/1.1 server that answers /chat/completions with SSE lines at the given delays, then holds or closes."""

    def __init__(self, lines: list[tuple[float, str]], hold: float = 0.0):
        self.lines, self.hold = lines, hold
        self.server: asyncio.base_events.Server | None = None
        self.writers: list[asyncio.StreamWriter] = []

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next((int(h.split(b":")[1]) for h in head.split(b"\r\n") if h.lower().startswith(b"content-length:")), 0)
            await reader.readexactly(length)
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n")
            await writer.drain()
            for delay, line in self.lines:
                await asyncio.sleep(delay)
                writer.write(f"data: {line}\n\n".encode())
                await writer.drain()
            await asyncio.sleep(self.hold)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def __aenter__(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/v1"

    async def __aexit__(self, *exc) -> None:
        for w in self.writers:
            w.close()
        assert self.server is not None
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)


def reasoning(text: str) -> str:
    return json.dumps({"choices": [{"delta": {"reasoning_content": text}}]})


async def test_a_provider_that_goes_silent_ends_with_a_stall_error(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROVIDER_STALL_SECONDS", "1")
    async with FakeStream([(0.0, reasoning("a"))], hold=10) as base:
        started = time.monotonic()
        got = []
        with pytest.raises(ProviderStalled) as info:
            async for e in stream_chat(custom_provider(base), req("high")):   # no client passed: the client's own timeouts apply
                got.append(e)
        assert time.monotonic() - started < 5
    assert got == [Thinking()] and info.value.seconds == 1
    assert not is_outage(info.value)                         # not "the model is down, wait for it": the turn ends with its line
    assert "5 minutes" in chat_error_text(ProviderStalled(300))


async def test_streamed_reasoning_keeps_a_long_think_alive(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROVIDER_STALL_SECONDS", "1")
    lines = [(0.4, reasoning(str(i))) for i in range(6)]    # 2.4 s of thinking, never 1 s of silence
    lines += [(0.1, json.dumps({"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]})), (0.0, "[DONE]")]
    async with FakeStream(lines) as base:
        ev = [e async for e in stream_chat(custom_provider(base), req("max"))]
    assert [e.text for e in ev if isinstance(e, TextDelta)] == ["done"]


# ---------------------------------------------------------------------------------------------------------- long-think status
async def test_status_says_a_long_think_after_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.LONG_THINKING_SECONDS", 2.0)
    gate = asyncio.Event()

    async def fn(provider, request, **kw):
        yield Thinking()
        await gate.wait()
        yield TextDelta("ok")
        yield Done("stop")

    s = Settings(token=TOKEN, provider="custom", model=MODEL, cdp_host=None, db_path=str(tmp_path / "a.db"))
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "생각해줘"})).json()["id"]
        status = None
        for _ in range(100):
            status = (await c.get("/api/agent/status")).json()["status"]
            if status == "Thinking":
                break
            await asyncio.sleep(0.02)
        assert status == "Thinking"
        await asyncio.sleep(2.1)
        assert (await c.get("/api/agent/status")).json()["status"] == "Thinking for a long time"
        gate.set()
        for _ in range(200):
            if (await c.get(f"/api/turns/{tid}")).json().get("done"):
                break
            await asyncio.sleep(0.02)
