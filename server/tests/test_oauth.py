import base64
import contextlib
import json
import time

import httpx
import pytest

from assistant.providers.types import ProviderError
from assistant.providers import ChatRequest, Done, TextDelta, stream_chat
from assistant.providers import oauth
from assistant.providers.oauth import OAuthError

REQ = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=50, temperature=0.2)


def jwt(exp: float, account: str = "acct-1") -> str:
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'none'})}.{b({'exp': exp, 'https://api.openai.com/auth': {'chatgpt_account_id': account}})}.sig"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_OAUTH_FILE", str(tmp_path / "oauth.json"))
    monkeypatch.setenv("HOME", str(tmp_path))  # no real ~/.qwen
    return tmp_path / "oauth.json"


async def run(provider, handler):
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return [e async for e in stream_chat(provider, REQ, client=c)]


async def test_not_logged_in():
    for p in ("openai-codex", "minimax-oauth", "qwen-oauth"):
        with pytest.raises(OAuthError, match="not logged in"):
            await run(p, lambda r: httpx.Response(500))


async def test_codex_request_shape_and_no_refresh_when_fresh(store):
    tok = jwt(time.time() + 3600)
    oauth._save("openai-codex", {"tokens": {"access_token": tok, "refresh_token": "r"}})
    seen = {}

    def handler(req):
        seen["url"], seen["h"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        body = ("event: response.output_text.delta\ndata: " + json.dumps({"type": "response.output_text.delta", "delta": "ok"})
                + "\n\nevent: response.completed\ndata: " + json.dumps({"type": "response.completed", "response": {}}) + "\n\n")
        return httpx.Response(200, content=body.encode())

    events = await run("openai-codex", handler)
    assert events == [TextDelta("ok"), Done("stop")]
    assert seen["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert seen["h"]["authorization"] == f"Bearer {tok}" and seen["h"]["chatgpt-account-id"] == "acct-1"
    assert seen["body"]["store"] is False and seen["body"]["instructions"]
    assert "max_output_tokens" not in seen["body"] and "temperature" not in seen["body"]


async def test_codex_refresh_rotates_and_persists(store):
    old = jwt(time.time() + 10)
    new = jwt(time.time() + 3600)
    oauth._save("openai-codex", {"tokens": {"access_token": old, "refresh_token": "r1"}})
    calls = []

    def handler(req):
        if req.url.host == "auth.openai.com":
            calls.append(dict(x.split("=") for x in req.content.decode().split("&")))
            return httpx.Response(200, json={"access_token": new, "refresh_token": "r2"})
        assert req.headers["authorization"] == f"Bearer {new}"
        return httpx.Response(200, content=b'event: response.completed\ndata: {"type":"response.completed","response":{}}\n\n')

    await run("openai-codex", handler)
    assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "r1"
    assert oauth._load()["openai-codex"]["tokens"] == {"access_token": new, "refresh_token": "r2"}
    assert oct(store.stat().st_mode & 0o777) == "0o600"


async def test_minimax_bearer_on_anthropic_transport_and_refresh(store):
    oauth._save("minimax-oauth", {
        "portal_base_url": "https://api.minimax.io", "inference_base_url": "https://api.minimax.io/anthropic",
        "client_id": "cid", "access_token": "old", "refresh_token": "r1", "expires_at": time.time() + 5})
    seen = {}

    def handler(req):
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json={"status": "success", "access_token": "new", "refresh_token": "r2", "expired_in": 900})
        seen["url"], seen["h"] = str(req.url), req.headers
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    await run("minimax-oauth", handler)
    assert seen["url"] == "https://api.minimax.io/anthropic/v1/messages"
    assert seen["h"]["authorization"] == "Bearer new" and "x-api-key" not in seen["h"]
    st = oauth._load()["minimax-oauth"]
    assert st["refresh_token"] == "r2" and st["expires_at"] > time.time() + 800


async def test_qwen_headers_and_cli_file_refresh(tmp_path):
    cli = tmp_path / ".qwen" / "oauth_creds.json"
    cli.parent.mkdir()
    cli.write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expiry_date": 1}))
    seen = {}

    def handler(req):
        if req.url.host == "chat.qwen.ai":
            return httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2", "expires_in": 3600})
        seen["url"], seen["h"] = str(req.url), req.headers
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    await run("qwen-oauth", handler)
    assert seen["url"] == "https://portal.qwen.ai/v1/chat/completions"
    assert seen["h"]["authorization"] == "Bearer a2" and seen["h"]["x-dashscope-authtype"] == "qwen-oauth"
    assert json.loads(cli.read_text())["refresh_token"] == "r2"


async def test_refresh_failure_is_clear_and_leaks_nothing(store):
    oauth._save("openai-codex", {"tokens": {"access_token": jwt(time.time() - 5), "refresh_token": "SECRET-RT"}})
    with pytest.raises(OAuthError) as ei:
        await run("openai-codex", lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    assert "re-login" in str(ei.value) and "SECRET-RT" not in str(ei.value)


async def test_401_forces_one_refresh_and_retries(store):
    """Token looks fresh but the server rejects it: refresh once, retry once."""
    old, new = jwt(time.time() + 3600, "a"), jwt(time.time() + 7200, "b")
    oauth._save("openai-codex", {"tokens": {"access_token": old, "refresh_token": "r1"}})
    auths = []

    def handler(req):
        if req.url.host == "auth.openai.com":
            return httpx.Response(200, json={"access_token": new, "refresh_token": "r2"})
        auths.append(req.headers["authorization"])
        if req.headers["authorization"] == f"Bearer {old}":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, content=b'event: response.completed\ndata: {"type":"response.completed","response":{}}\n\n')

    assert await run("openai-codex", handler) == [Done("stop")]
    assert auths == [f"Bearer {old}", f"Bearer {new}"]


async def test_401_after_retry_raises(store):
    tok = jwt(time.time() + 3600)
    oauth._save("openai-codex", {"tokens": {"access_token": tok, "refresh_token": "r1"}})

    def handler(req):
        if req.url.host == "auth.openai.com":
            return httpx.Response(200, json={"access_token": jwt(time.time() + 3600, "z"), "refresh_token": "r2"})
        return httpx.Response(401, json={})

    with pytest.raises(ProviderError) as ei:
        await run("openai-codex", handler)
    assert ei.value.status == 401


async def test_adopts_token_rotated_by_another_process(store):
    """If another process refreshed while we waited for the lock, don't burn the rotated refresh token."""
    old, theirs = jwt(time.time() + 10, "a"), jwt(time.time() + 3600, "b")
    oauth._save("openai-codex", {"tokens": {"access_token": old, "refresh_token": "r1"}})
    real_lock = oauth._afile_lock

    @contextlib.asynccontextmanager
    async def racing_lock():
        async with real_lock():
            oauth._save("openai-codex", {"tokens": {"access_token": theirs, "refresh_token": "r2"}})
            yield

    oauth._afile_lock = racing_lock
    try:
        def handler(req):
            assert req.url.host != "auth.openai.com", "must not refresh"
            return httpx.Response(200, content=b'event: response.completed\ndata: {"type":"response.completed","response":{}}\n\n')
        await run("openai-codex", handler)
    finally:
        oauth._afile_lock = real_lock


def test_concurrent_saves_do_not_lose_updates(store):
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_save_many, args=(f"p{i}",)) for i in range(4)]
    [p.start() for p in ps]
    [p.join() for p in ps]
    assert set(oauth._load()) == {f"p{i}-{j}" for i in range(4) for j in range(10)}


def _save_many(prefix):
    for j in range(10):
        oauth._save(f"{prefix}-{j}", {"n": j})


def test_store_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_OAUTH_FILE", str(tmp_path / "sub" / "oauth.json"))
    oauth._save("x", {})
    assert oct((tmp_path / "sub").stat().st_mode & 0o777) == "0o700"


async def test_oauth_token_not_sent_to_foreign_base_url(store):
    oauth._save("openai-codex", {"tokens": {"access_token": jwt(time.time() + 3600), "refresh_token": "r"}})
    c = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("request must not be sent")))
    with pytest.raises(ProviderError, match="refusing to send OAuth token"):
        [e async for e in stream_chat("openai-codex", REQ, client=c, base_url="https://evil.example/v1")]


async def test_qwen_resource_url_allowlist(store):
    assert oauth._qwen_base_url("portal.qwen.ai") == "https://portal.qwen.ai/v1"
    assert oauth._qwen_base_url("evil.example.com") is None
    assert oauth._qwen_base_url("qwen.ai.evil.com") is None


async def test_minimax_refresh_missing_fields_is_clean_error(store):
    oauth._save("minimax-oauth", {
        "portal_base_url": "https://api.minimax.io", "inference_base_url": "https://api.minimax.io/anthropic",
        "client_id": "cid", "access_token": "old", "refresh_token": "r1", "expires_at": 0})
    with pytest.raises(OAuthError, match="usable token"):
        await run("minimax-oauth", lambda r: httpx.Response(200, json={"status": "success"}))


def test_status_and_logout(store):
    oauth._save("qwen-oauth", {"access_token": "a"})
    assert oauth.status()["qwen-oauth"]["logged_in"] and not oauth.status()["openai-codex"]["logged_in"]
    assert oauth.logout("qwen-oauth") and not oauth.logout("qwen-oauth")


def test_store_path_follows_assistant_home(tmp_path, monkeypatch):
    monkeypatch.delenv("ASSISTANT_OAUTH_FILE")
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "state"))
    assert oauth.store_path() == tmp_path / "state" / "oauth.json"  # Docker: /state is the only writable volume
    monkeypatch.delenv("ASSISTANT_HOME")
    assert oauth.store_path() == tmp_path / ".assistant" / "oauth.json"  # HOME patched by the fixture
    monkeypatch.setenv("ASSISTANT_OAUTH_FILE", str(tmp_path / "x.json"))
    assert oauth.store_path() == tmp_path / "x.json"  # explicit override still wins
