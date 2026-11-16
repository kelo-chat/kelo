"""Providers reached through a consumer subscription sign-in are marked unofficial and need the user's one-time consent per provider."""

import httpx

from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, scripted


async def test_unofficial_providers_are_marked_and_need_consent_once(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="openai", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                              unofficial_providers=True), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        agy = next(p for n, p in provs.items() if n.startswith("antigravity"))
        assert agy["unofficial"] is True and agy["vendor"] == "Google" and agy["consent"] is None and agy["consent_version"] >= 1
        assert all(p["unofficial"] is False for n, p in provs.items() if n in ("openai", "openrouter", "anthropic"))
        r = await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "x"})
        assert r.status_code == 428 and "consent_required:antigravity-oauth" in r.json()["detail"]
        assert (await c.put("/api/providers/openai/consent", json={"accepted": True})).status_code == 404
        assert (await c.put("/api/providers/agy/consent", json={"accepted": False})).status_code == 422
        ok = (await c.put("/api/providers/agy/consent", json={"accepted": True, "version": 1})).json()
        assert ok["name"] == "antigravity-oauth" and ok["consent"]["version"] == 1
        assert (await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "x"})).status_code == 200
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert provs[agy["name"]]["consent"]["accepted_at"] > 0
        # the fallback slot asks too
        r = await c.put("/api/profile", json={"fallback_provider": "openai-codex", "fallback_model": "y"})
        assert r.status_code == 428 and "openai-codex" in r.json()["detail"]


UNOFFICIAL = ("claude-subscription-directsdk-experimental", "antigravity-oauth", "openai-codex", "qwen-oauth", "minimax-oauth")


def test_the_setting_is_off_unless_the_env_turns_it_on():
    base = {"ASSISTANT_TOKEN": TOKEN}
    assert Settings.from_env(base).unofficial_providers is False
    assert Settings.from_env({**base, "ASSISTANT_UNOFFICIAL_PROVIDERS": "0"}).unofficial_providers is False
    assert Settings.from_env({**base, "ASSISTANT_UNOFFICIAL_PROVIDERS": "1"}).unofficial_providers is True


async def test_off_by_default_hidden_and_refused_but_one_in_use_keeps_working(tmp_path):
    from assistant.store import Store
    db = tmp_path / "s.db"
    Store(db).set_profile(provider="openai", model="m", fallback_provider="agy", fallback_model="g")   # set up while it was on
    app = create_app(Settings(token=TOKEN, provider="openai", model="m", cdp_host=None, db_path=str(db)), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert "openai" in provs and "minimax" in provs                       # official ones as always (minimax with a key)
        assert provs["antigravity-oauth"]["unofficial"] is True               # in use as the fallback: still listed
        assert not set(provs) & (set(UNOFFICIAL) - {"antigravity-oauth"})     # the rest are not there at all
        r = await c.put("/api/profile", json={"provider": "openai-codex", "model": "x"})
        assert r.status_code == 403 and r.json()["detail"].startswith("unofficial_disabled:openai-codex")
        assert "assistant enable oauth" in r.json()["detail"]
        # saving the profile again with the one in use is fine; other settings too
        assert (await c.put("/api/profile", json={"fallback_provider": "agy", "fallback_model": "g2"})).status_code == 200
        assert (await c.put("/api/profile", json={"effort": ""})).status_code == 200
        # moving it from the fallback to the main slot keeps it in use (consent recorded first, as the app does)
        assert (await c.put("/api/providers/agy/consent", json={"accepted": True})).status_code == 200
        moved = await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "g"})
        assert moved.status_code == 200 and moved.json()["fallback_provider"] == ""
        # once the copy stops using it, it is hidden like the others
        await c.put("/api/profile", json={"provider": "openai", "model": "m"})
        assert "antigravity-oauth" not in {p["name"] for p in (await c.get("/api/providers")).json()}
        assert (await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "g"})).status_code == 403


async def test_on_lists_every_unofficial_provider(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="openai", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                              unofficial_providers=True), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        names = {p["name"] for p in (await c.get("/api/providers")).json()}
        assert set(UNOFFICIAL) <= names


async def test_connectors_mark_the_widely_used_ones_as_featured(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="openai", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")),
                     tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        rows = (await c.get("/api/connectors")).json()
        featured = sorted((r for r in rows if r.get("featured")), key=lambda r: r["rank"])
        assert featured and featured[0]["id"] == "gmail" and all(isinstance(r["rank"], int) for r in featured)
        assert len(featured) <= 40 and len(rows) > len(featured)
