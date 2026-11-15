import json

import httpx
import pytest

from assistant.providers import (
    ChatRequest, Done, ProviderError, TextDelta, ToolCall, UnsupportedProvider, Usage,
    Provider, custom_provider, get_provider, list_families, list_providers, stream_chat,
)
from assistant.providers.transports import anthropic_messages as am



def sse(*events: tuple[str | None, dict | str]) -> bytes:
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}")
        out.append("data: " + (data if isinstance(data, str) else json.dumps(data)))
        out.append("")
    return ("\n".join(out) + "\n").encode()


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def collect(provider, req, **kw):
    return [e async for e in stream_chat(provider, req, **kw)]


REQ = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}])


# ---------- registry ----------

def test_registry_matches_hermes_count_and_lookup():
    assert len(list_providers()) == 34  # the pruned Hermes list + Mistral/Groq/Cerebras/Together/OpenGateway + plain "openai" + 2 CLI-backed
    assert get_provider("OpenRouter").name == "openrouter"
    assert get_provider("openrouter").resolve_base_url() == "https://openrouter.ai/api/v1"


def test_support_matrix():
    supported = {p.name for p in list_providers() if p.supported}
    assert {"openrouter", "anthropic", "xai", "deepseek", "gemini", "minimax", "custom", "lmstudio"} <= supported
    assert {"bedrock", "vertex", "openai"} <= supported
    assert not [p.name for p in list_providers() if not p.supported]   # unusable entries were removed from the catalog
    odd = Provider(name="x", display_name="x", transport="chat_completions", auth_type="oauth_device_code", base_url="https://x.example/v1")
    assert odd.unsupported_reason() and not odd.supported
    assert {"openai-codex", "qwen-oauth", "minimax-oauth"} <= supported


# ---------- openai-compatible ----------

async def test_openai_text_usage_and_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        body = sse(
            (None, {"choices": [{"delta": {"content": "Hel"}}]}),
            (None, {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
            (None, {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
            (None, "[DONE]"),
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = await collect("openrouter", REQ, api_key="sk-test", client=client_for(handler))
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello"
    assert Usage(3, 2) in events and events[-1] == Done("stop")
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["stream"] is True


async def test_openai_tool_call_assembled_across_chunks():
    def handler(request):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "browse", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"url":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"x.com"}'}}]}, "finish_reason": "tool_calls"}]},
        ]
        return httpx.Response(200, content=sse(*[(None, c) for c in chunks], (None, "[DONE]")))

    events = await collect("deepseek", REQ, api_key="k", client=client_for(handler))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert calls == [ToolCall("c1", "browse", '{"url":"x.com"}')]
    assert events[-1] == Done("tool_calls")


async def test_gemini_uses_openai_compat_path():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse((None, "[DONE]")))

    await collect("gemini", REQ, api_key="k", client=client_for(handler))
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


async def test_custom_local_endpoint_needs_no_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse((None, "[DONE]")))

    prov = custom_provider("http://localhost:11434/v1")
    await collect(prov, REQ, client=client_for(handler))
    assert seen == {"auth": None, "url": "http://localhost:11434/v1/chat/completions"}


# ---------- anthropic messages ----------

def test_anthropic_request_carries_cache_breakpoints_on_what_repeats():
    tools = [{"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object", "properties": {}}}}
             for n in ("core_a", "core_b", "loaded_c")]
    req = ChatRequest(model="m", tools=tools, stable_tools=2, messages=[
        {"role": "system", "content": "rules"}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"}, {"role": "system", "content": "a nudge"}, {"role": "user", "content": "go"}])
    body = am.build_body(req)
    eph = {"type": "ephemeral"}
    assert body["system"] == [{"type": "text", "text": "rules", "cache_control": eph}]        # only the leading system text
    assert [t.get("cache_control") for t in body["tools"]] == [None, eph, None]              # after the core set (the system mark covers all)
    assert body["messages"][-2]["content"][-1]["cache_control"] == eph                       # the history before this turn's message
    last = body["messages"][-1]["content"]
    assert last[0]["text"].startswith("[Not from the user") and "a nudge" in last[0]["text"]  # a later system note: user text
    assert last[-1] == {"type": "text", "text": "go", "cache_control": eph}                  # the conversation tail
    marks = json.dumps(body).count('"cache_control"')
    assert marks <= 4
    no_tools = am.build_body(ChatRequest(model="m", messages=[{"role": "user", "content": "x"}]))
    assert "system" not in no_tools and "tools" not in no_tools


async def test_anthropic_usage_counts_cache_reads_and_writes():
    def handler(request):
        return httpx.Response(200, content=sse(
            ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 900,
                                                                               "cache_creation_input_tokens": 90}}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}})))
    events = await collect("anthropic", REQ, api_key="sk-ant", client=client_for(handler))
    assert Usage(1000, 3, 900, 90) in events


async def test_bedrock_puts_no_cache_points_for_models_without_them():
    fake = FakeBedrock([{"messageStop": {"stopReason": "end_turn"}},
                        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 1, "cacheReadInputTokens": 40}}}])
    req = ChatRequest(model="meta.llama3", messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}])
    events = await collect("bedrock", req, aws_client=fake)
    assert fake.kwargs["system"] == [{"text": "sys"}] and Usage(45, 1, 40, 0) in events


def test_anthropic_message_conversion():
    system, msgs = am.convert_messages([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "ok", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "browse", "arguments": '{"u":1}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "page"},
        {"role": "user", "content": "thanks"},
    ])
    assert system == "be brief"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]  # strictly alternating
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "browse", "input": {"u": 1}}
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][1] == {"type": "text", "text": "thanks"}  # merged into same user turn


async def test_anthropic_stream_text_tool_and_headers():
    seen = {}

    def handler(request):
        seen["h"], seen["url"] = request.headers, str(request.url)
        body = sse(
            ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 7}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Sure"}}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "browse"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"a"'}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ":1}"}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}),
        )
        return httpx.Response(200, content=body)

    events = await collect("anthropic", REQ, api_key="sk-ant", client=client_for(handler))
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["h"]["x-api-key"] == "sk-ant" and seen["h"]["anthropic-version"] == "2023-06-01"
    assert "authorization" not in seen["h"]
    assert TextDelta("Sure") in events
    assert ToolCall("tu1", "browse", '{"a":1}') in events
    assert Usage(7, 9) in events and events[-1] == Done("tool_calls")


async def test_minimax_uses_anthropic_wire_on_its_own_host():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse(("message_delta", {"delta": {"stop_reason": "end_turn"}})))

    await collect("minimax", REQ, api_key="k", client=client_for(handler))
    assert seen["url"] == "https://api.minimax.io/anthropic/v1/messages"


# ---------- responses api ----------

async def test_responses_stream_text_and_function_call():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        body = sse(
            ("response.output_text.delta", {"delta": "Hi"}),
            ("response.output_item.added", {"item": {"type": "function_call", "id": "i1", "call_id": "c9", "name": "browse"}}),
            ("response.function_call_arguments.delta", {"item_id": "i1", "delta": '{"q":'}),
            ("response.function_call_arguments.delta", {"item_id": "i1", "delta": '"z"}'}),
            ("response.completed", {"response": {"usage": {"input_tokens": 4, "output_tokens": 5}}}),
        )
        return httpx.Response(200, content=body)

    req = ChatRequest(model="m", messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}])
    events = await collect("xai", req, api_key="k", client=client_for(handler))
    assert seen["url"] == "https://api.x.ai/v1/responses"
    assert seen["body"]["instructions"] == "sys"
    assert TextDelta("Hi") in events and ToolCall("c9", "browse", '{"q":"z"}') in events
    assert Usage(4, 5) in events and events[-1] == Done("tool_calls")


# ---------- errors ----------

async def test_http_error_does_not_leak_api_key():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(ProviderError) as ei:
        await collect("openrouter", REQ, api_key="sk-SECRET", client=client_for(handler))
    assert ei.value.status == 401 and "sk-SECRET" not in str(ei.value)


async def test_missing_key_and_unsupported_auth():
    with pytest.raises(ProviderError, match="no API key"):
        await collect("openrouter", REQ, env={})
    odd = Provider(name="odd", display_name="odd", transport="chat_completions", auth_type="oauth_device_code", base_url="https://x.example/v1")
    with pytest.raises(UnsupportedProvider, match="odd: auth type"):
        await collect(odd, REQ)


# ---------- bedrock ----------

class FakeBedrock:
    def __init__(self, events):
        self.events, self.kwargs = events, None

    def converse_stream(self, **kwargs):
        self.kwargs = kwargs
        return {"stream": iter(self.events)}


async def test_bedrock_converse_text_tool_usage():
    fake = FakeBedrock([
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Yo"}}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "b1", "name": "browse"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"u":'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '1}'}}}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 6}}},
    ])
    req = ChatRequest(model="anthropic.claude-x", max_tokens=50, messages=[
        {"role": "system", "content": "sys"}, {"role": "user", "content": "go"}])
    events = await collect("bedrock", req, aws_client=fake)
    assert TextDelta("Yo") in events and ToolCall("b1", "browse", '{"u":1}') in events
    assert Usage(5, 6) in events and events[-1] == Done("tool_calls")
    assert fake.kwargs["system"] == [{"text": "sys"}, {"cachePoint": {"type": "default"}}]          # a Claude model: cache points
    assert fake.kwargs["messages"] == [{"role": "user", "content": [{"text": "go"}, {"cachePoint": {"type": "default"}}]}]
    assert fake.kwargs["inferenceConfig"] == {"maxTokens": 50}


async def test_bedrock_errors_are_wrapped_without_leaking():
    class Boom:
        def converse_stream(self, **kw):
            raise RuntimeError("AccessDenied for AKIAXXXX")

    with pytest.raises(ProviderError, match="bedrock error"):
        await collect("bedrock", REQ, aws_client=Boom())


# ---------- vertex ----------

async def test_vertex_uses_adc_token_and_regional_endpoint():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        return httpx.Response(200, content=sse((None, "[DONE]")))

    env = {"GOOGLE_CLOUD_PROJECT": "proj1", "GOOGLE_CLOUD_LOCATION": "us-central1"}
    await collect("vertex", REQ, env=env, client=client_for(handler),
                  vertex_token_source=lambda: ("ya29.tok", None))
    assert seen["url"] == ("https://us-central1-aiplatform.googleapis.com/v1/projects/proj1/"
                           "locations/us-central1/endpoints/openapi/chat/completions")
    assert seen["auth"] == "Bearer ya29.tok"


async def test_vertex_global_location_and_missing_project():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse((None, "[DONE]")))

    await collect("vertex", REQ, env={}, client=client_for(handler),
                  vertex_token_source=lambda: ("t", "adc-proj"))
    assert seen["url"].startswith("https://aiplatform.googleapis.com/v1/projects/adc-proj/locations/global/")
    with pytest.raises(ProviderError, match="GOOGLE_CLOUD_PROJECT"):
        await collect("vertex", REQ, env={}, vertex_token_source=lambda: ("t", None))


# ---------- families ----------

def test_families_fold_variants_without_losing_any_endpoint():
    fams = list_families()
    folded = {p.name for f in fams for p in f.variants.values()}
    assert folded == {p.name for p in list_providers()}            # nothing dropped, nothing duplicated
    assert sum(len(f.variants) for f in fams) == len(list_providers())
    names = [f.name for f in fams]
    assert len(names) == len(set(names))
    assert len([f for f in fams if f.supported]) == 30
    alibaba = next(f for f in fams if f.name == "alibaba")
    assert len(alibaba.variants) == 3 and alibaba.default.name == "alibaba"


def test_family_and_variant_resolution():
    assert get_provider("alibaba").name == "alibaba"
    assert get_provider("alibaba:coding-plan").name == "alibaba-coding-plan"
    assert get_provider("google").name == "gemini"
    assert get_provider("google:vertex").name == "vertex"
    assert get_provider("minimax").name == "minimax"
    assert get_provider("custom:lmstudio").name == "lmstudio"
    assert get_provider("gemini").name == "gemini"                 # legacy names still work
    with pytest.raises(KeyError, match="choose from"):
        get_provider("alibaba:mars")
    with pytest.raises(KeyError):
        get_provider("nope:cn")


async def test_variant_selects_the_right_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse((None, "[DONE]")))

    await collect("alibaba:token-plan", REQ, api_key="k", client=client_for(handler))
    assert seen["url"] == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"


# ---------- "thinking" status signal (never the reasoning text itself) ----------

async def test_thinking_signal_from_every_wire_format():
    from assistant.providers import Thinking

    def oa(request):  # OpenAI-compatible: DeepSeek `reasoning_content`, OpenRouter `reasoning`
        return httpx.Response(200, content=sse(
            (None, {"choices": [{"delta": {"reasoning_content": "hmm "}}]}),
            (None, {"choices": [{"delta": {"reasoning": "more"}}]}),
            (None, {"choices": [{"delta": {"content": "Hi"}}]}),
            (None, {"choices": [{"delta": {"reasoning": "again"}}]}),
            (None, {"choices": [{"delta": {}, "finish_reason": "stop"}]}), (None, "[DONE]")))

    ev = await collect("deepseek", REQ, api_key="k", client=client_for(oa))
    assert [type(e).__name__ for e in ev if not isinstance(e, (Usage, Done))] == ["Thinking", "TextDelta", "Thinking"]
    assert all("hmm" not in repr(e) for e in ev)  # status only: no reasoning text leaks into events

    def anth(request):
        return httpx.Response(200, content=sse(
            ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "secret"}}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Yo"}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}})))

    ev = await collect("anthropic", REQ, api_key="k", client=client_for(anth))
    assert ev[:2] == [Thinking(), TextDelta("Yo")] and all("secret" not in repr(e) for e in ev)

    def resp(request):
        return httpx.Response(200, content=sse(
            ("response.output_item.added", {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "r1"}}),
            ("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta", "delta": "x"}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "ok"}),
            ("response.completed", {"type": "response.completed", "response": {}})))

    from assistant.providers.transports import responses  # codex needs an OAuth login: use the transport directly
    out = [e async for e in responses.stream(client_for(resp), "https://x.test", {}, REQ)]
    assert out[:2] == [Thinking(), TextDelta("ok")]


# ---------- catalog: pruned and extended ----------

REMOVED = ("actual", "arcee", "solstice", "commandcode", "commandcode-anthropic", "gmi", "nous", "stepfun", "tencent-tokenhub",
           "router", "meta-ai", "alibaba-cn", "alibaba-coding-plan-cn", "alibaba-token-plan-cn", "minimax-cn", "xiaomi",
           "ai-gateway", "kilocode", "opencode-zen", "opencode-go")


def test_removed_providers_are_gone():
    names = {p.name for p in list_providers()}
    for name in REMOVED:
        assert name not in names
        with pytest.raises(KeyError):
            get_provider(name)
    assert {"alibaba", "alibaba-coding-plan", "alibaba-token-plan", "minimax", "minimax-oauth", "azure-foundry"} <= names


@pytest.mark.parametrize("name,base,env", [
    ("mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    ("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    ("together", "https://api.together.ai/v1", "TOGETHER_API_KEY"),
])
async def test_new_openai_compatible_providers(name, base, env, monkeypatch):
    from assistant.models import _cache, list_models
    p = get_provider(name)
    assert p.supported and p.transport == "chat_completions" and p.api_key_envs == [env] and p.signup_url.startswith("https://")
    assert p.resolve_base_url(env={}) == base and not p.takes_url
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "m-2"}, {"id": "m-1"}]})
        return httpx.Response(200, content=sse((None, {"choices": [{"delta": {"content": "hi"}}]}), (None, "[DONE]")))

    _cache.clear()
    monkeypatch.setenv(env, "k-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await list_models(p, c, refresh=True) == ["m-1", "m-2"]
    events = await collect(name, REQ, api_key="k", client=client_for(handler))
    assert seen[0] == (f"{base}/models", "Bearer k-test")
    assert seen[1][0] == f"{base}/chat/completions" and any(isinstance(e, TextDelta) for e in events)


# ---------- the output cap and a balance that can pay for less (OpenRouter) ----------

async def test_openrouter_gets_a_sensible_output_cap_and_a_402_naming_what_it_can_afford_is_tried_once_under_it():
    """Without max_tokens OpenRouter holds credit for the model's whole output limit (131072 for some) and turns a small balance down.
    Kelo asks for the provider's default (16384) or the model's own lower limit; a 402 that names an affordable number is tried once
    more a little under it, and a second refusal is not tried again."""
    from assistant import windows
    from assistant.providers.client import affordable
    seen: list[int | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body.get("max_tokens"))
        if len(seen) == 1:
            return httpx.Response(402, json={"error": {"code": 402, "message": "This request requires more credits, or fewer max_tokens. "
                                                       "You requested up to 16384 tokens, but can only afford 9882."}})
        return httpx.Response(200, content=sse((None, {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}), (None, "[DONE]")),
                              headers={"content-type": "text/event-stream"})

    events = await collect("openrouter", ChatRequest(model="some/model", messages=[{"role": "user", "content": "hi"}]), api_key="k",
                           client=client_for(handler))
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "ok"
    assert seen == [16384, 8893]                        # the default cap, then 90% of what it can afford

    windows.remember_provider_windows("openrouter", {"small/model": (32000, 4096)})
    seen.clear()
    await collect("openrouter", ChatRequest(model="small/model", messages=[{"role": "user", "content": "hi"}]), api_key="k",
                  client=client_for(lambda r: (seen.append(json.loads(r.content).get("max_tokens")),
                                               httpx.Response(200, content=sse((None, "[DONE]")), headers={"content-type": "text/event-stream"}))[1]))
    assert seen == [4096]                               # the model's own lower output limit

    calls = []

    def broke(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(402, json={"error": {"message": "requires more credits, or fewer max_tokens. You requested up to 16384 tokens, "
                                                              "but can only afford 300."}})
    with pytest.raises(ProviderError) as e:
        await collect("openrouter", ChatRequest(model="some/model", messages=[{"role": "user", "content": "hi"}]), api_key="k",
                      client=client_for(broke))
    assert e.value.status == 402 and len(calls) == 1    # too little to answer with: no second try, the balance ran out
    assert affordable("can only afford 9882") == 8893 and affordable("can only afford 500") is None and affordable("nothing") is None
    seen.clear()
    await collect("openai", ChatRequest(model="gpt-x", messages=[{"role": "user", "content": "hi"}]), api_key="k",
                  client=client_for(lambda r: (seen.append(json.loads(r.content).get("max_tokens")),
                                               httpx.Response(200, content=sse((None, "[DONE]")), headers={"content-type": "text/event-stream"}))[1]))
    assert seen == [None]                               # providers without a default cap are left as they were


def test_opengateway_is_an_openai_compatible_key_provider():
    p = get_provider("opengateway")
    assert p.display_name == "OpenGateway" and p.transport == "chat_completions" and p.auth_type == "api_key"
    assert p.resolve_base_url() == "https://apis.opengateway.ai/v1" and p.api_key_envs[0] == "OPENGATEWAY_API_KEY"
    assert get_provider("open-gateway").name == "opengateway" and p.supported
