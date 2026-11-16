import json

import httpx
import pytest

from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running

PASSWORD = "hunter2-Pa$$w0rd!"
USER = "nahyun.kim@example.com"
KEY = "sk-live-ABCDEF123456"


def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home")), tools=[])


def nothing_secret(*texts):
    blob = "\n".join(texts)
    for leaked in (PASSWORD, USER, KEY, "nahyun"):
        assert leaked not in blob, leaked


async def test_every_vault_endpoint_needs_the_token(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        calls = [("GET", "/api/vault", None), ("POST", "/api/vault/logins", {"site": "github.com"}), ("PUT", "/api/vault/x/login", {"username": "a", "password": "b"}),
                 ("PATCH", "/api/vault/x", {"auto_allow": True}), ("GET", "/api/vault/log", None), ("PUT", "/api/vault/lock", {"locked": True}),
                 ("GET", "/api/credentials", None), ("DELETE", "/api/credentials/x", None)]
        for method, path, body in calls:
            r = await c.request(method, path, json=body)
            assert r.status_code == 401, (method, path)


async def test_a_login_is_added_filled_in_by_the_user_and_never_comes_back(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        made = (await c.post("/api/vault/logins", json={"site": "https://GitHub.com/login", "label": "GitHub"})).json()
        assert made["name"] == "login-github-com" and made["items"][0]["kind"] == "login" and made["items"][0]["has_secret"] is False
        r = await c.put("/api/vault/login-github-com/login", json={"username": USER, "password": PASSWORD})
        assert r.json() == {"ok": True, "saved": True, "mode": "now", "reply": "none", "request": None}
        listing = await c.get("/api/vault")
        item = listing.json()["items"][0]
        assert item["has_secret"] is True and item["username_hint"] == "na***@example.com" and "password" not in item and "secret" not in item
        assert (item["last_result"], item["last_result_at"], item["last_result_detail"]) == (None, None, "")                    # not tried yet
        again = await c.get("/api/credentials")
        log = await c.get("/api/vault/log")
        nothing_secret(r.text, listing.text, again.text, log.text, json.dumps(made))
        assert [e["action"] for e in log.json()] == ["saved", "added"] and log.json()[0]["detail"] == "website login, not tried yet"
        await c.put("/api/vault/login-github-com/login", json={"username": USER, "password": PASSWORD + "2"})
        assert (await c.get("/api/vault/log")).json()[0]["action"] == "replaced"


async def test_a_login_typed_for_one_use_is_not_stored_and_is_reported_as_entered_once(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        assert (await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD, "save": False})).json() == {"ok": True, "saved": False, "mode": "once", "reply": "none", "request": None}
        v = (await c.get("/api/vault")).json()
        assert v["entered_once"] == ["github"] and v["items"] == [] and [i["name"] for i in v["pending"]] == ["github"]   # held, not stored: not in the vault
        assert (await c.get("/api/vault/log")).json()[0]["action"] == "entered-once"
        await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD})          # saving afterwards replaces the one-use copy
        assert (await c.get("/api/vault")).json()["entered_once"] == []
        raw = (tmp_path / "s.db").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("s.db-*"))
        assert PASSWORD.encode() not in raw and USER.encode() not in raw


async def test_bad_sites_odd_values_and_wrong_items_are_refused(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for site in ("http://github.com", "https://127.0.0.1", "localhost", "https://u:p@github.com", ""):
            assert (await c.post("/api/vault/logins", json={"site": site})).status_code == 422, site
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        assert (await c.post("/api/vault/logins", json={"site": "gitlab.com", "name": "github"})).status_code == 200      # same kind: re-registering is fine
        await c.post("/api/credentials", json={"name": "acme", "hosts": ["api.acme.com"], "placement": {"type": "bearer_header"}})
        assert (await c.post("/api/vault/logins", json={"site": "github.com", "name": "acme"})).status_code == 422       # a service key's name is not reused
        assert (await c.post("/api/credentials", json={"name": "github", "hosts": ["api.github.com"], "placement": {"type": "bearer_header"}})).status_code == 409
        for body in ({"username": "a\nb", "password": "x"}, {"username": "a", "password": "x\ry"}, {"username": "", "password": "x"}, {"username": "a", "password": ""},
                     {"username": "a" * 201, "password": "x"}, {"username": "a", "password": "x" * 513}):
            assert (await c.put("/api/vault/github/login", json=body)).status_code == 422, body
        assert (await c.put("/api/vault/ghost/login", json={"username": "a", "password": "b"})).status_code == 404
        assert (await c.put("/api/vault/acme/login", json={"username": "a", "password": "b"})).status_code == 404         # a service key takes no login
        assert (await c.put("/api/credentials/github/secret", json={"secret": "sk-live-ABCDEF123456"})).status_code == 404
        assert (await c.put("/api/vault/github/login", json={"username": "a b", "password": "pass word with spaces"})).status_code == 200


async def test_only_the_user_can_allow_sign_ins_without_asking_rename_lock_and_delete(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        await c.post("/api/credentials", json={"name": "acme", "hosts": ["api.acme.com"], "placement": {"type": "bearer_header"}})
        items = (await c.patch("/api/vault/github", json={"auto_allow": True, "label": "  Work GitHub "})).json()
        gh = next(i for i in items if i["name"] == "github")
        assert gh["auto_allow"] is True and gh["label"] == "Work GitHub"
        assert (await c.patch("/api/vault/acme", json={"auto_allow": True})).status_code == 422
        assert (await c.patch("/api/vault/ghost", json={"auto_allow": True})).status_code == 404
        assert (await c.put("/api/vault/lock", json={"locked": True})).json() == {"locked": True} and (await c.get("/api/vault")).json()["locked"] is True
        assert (await c.put("/api/vault/lock", json={"locked": False})).json() == {"locked": False}
        assert (await c.delete("/api/credentials/github")).status_code == 200 and (await c.delete("/api/credentials/github")).status_code == 404
        actions = [(e["item"], e["action"]) for e in (await c.get("/api/vault/log")).json()]
        assert ("github", "setting") in actions and ("vault", "locked") in actions and ("vault", "unlocked") in actions and ("github", "deleted") in actions


async def test_service_keys_get_the_same_care_and_a_trail(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/credentials", json={"name": "acme", "label": "Acme", "hosts": ["api.acme.com"], "placement": {"type": "bearer_header"}})
        r = await c.put("/api/credentials/acme/secret", json={"secret": KEY})
        listing = await c.get("/api/vault")
        assert listing.json()["items"][0]["kind"] == "api" and listing.json()["items"][0]["has_secret"] is True
        await c.put("/api/credentials/acme/secret", json={"secret": KEY + "x"})
        log = await c.get("/api/vault/log")
        assert [e["action"] for e in log.json()] == ["replaced", "saved", "added"]
        nothing_secret(r.text, listing.text, log.text)


async def test_the_log_can_be_limited_and_the_newest_comes_first(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for i in range(8):
            await c.put("/api/vault/lock", json={"locked": i % 2 == 0})
        assert len((await c.get("/api/vault/log?limit=3")).json()) == 3 and len((await c.get("/api/vault/log?limit=0")).json()) == 1
        assert len((await c.get("/api/vault/log?limit=100000")).json()) == 8
        assert (await c.get("/api/vault/log?limit=3")).json()[0]["action"] == "unlocked"


# ---- the vault lists only what it really holds; a request nobody answered is not an item

async def test_a_login_that_was_asked_for_but_never_entered_is_not_in_the_vault(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github", "label": "GitHub"})    # the assistant asked / the user started adding
        v = (await c.get("/api/vault")).json()
        assert v["items"] == [] and [(i["name"], i["has_secret"]) for i in v["pending"]] == [("github", False)]
        await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD})                    # now it holds a login
        v = (await c.get("/api/vault")).json()
        assert [i["name"] for i in v["items"]] == ["github"] and v["pending"] == []


async def test_closing_the_entry_form_forgets_the_request_but_never_a_stored_login(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        assert (await c.delete("/api/vault/github/draft")).json() == {"ok": True, "deleted": True, "reply": "none", "request": None}
        v = (await c.get("/api/vault")).json()
        assert v["items"] == [] and v["pending"] == []
        assert (await c.get("/api/vault/log")).json()[0]["action"] == "cancelled"
        assert (await c.delete("/api/vault/github/draft")).json() == {"ok": True, "deleted": False, "reply": "none", "request": None}              # nothing left to forget
        await c.post("/api/vault/logins", json={"site": "gitlab.com", "name": "gitlab"})
        await c.put("/api/vault/gitlab/login", json={"username": USER, "password": PASSWORD})
        assert (await c.delete("/api/vault/gitlab/draft")).json() == {"ok": True, "deleted": False, "reply": "none", "request": None}              # it holds a login: untouched
        assert [i["name"] for i in (await c.get("/api/vault")).json()["items"]] == ["gitlab"]


async def test_a_login_held_in_memory_is_not_forgotten_by_closing_the_form(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD, "mode": "after_success"})
        assert (await c.delete("/api/vault/github/draft")).json() == {"ok": True, "deleted": False, "reply": "none", "request": None}
        v = (await c.get("/api/vault")).json()
        assert v["verifying"] == ["github"] and [i["name"] for i in v["pending"]] == ["github"]


async def test_requests_nobody_answered_are_forgotten_after_a_day(tmp_path, monkeypatch):
    import assistant.api.vault as vault_routes
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        await c.post("/api/vault/logins", json={"site": "gitlab.com", "name": "gitlab"})
        await c.put("/api/vault/gitlab/login", json={"username": USER, "password": PASSWORD, "mode": "after_success"})     # held, so kept
        assert len((await c.get("/api/vault")).json()["pending"]) == 2                  # a fresh request stays
        monkeypatch.setattr(vault_routes, "DRAFT_KEEP_SECONDS", -1.0)                  # a day has passed
        v = (await c.get("/api/vault")).json()
        assert [i["name"] for i in v["pending"]] == ["gitlab"] and v["items"] == []


def test_asking_again_makes_a_request_fresh_again_but_a_stored_login_keeps_its_date(tmp_path):
    from assistant.store import Store
    st = Store(tmp_path / "s.db")
    st.save_credential("github", "GitHub", ["github.com"], {}, "", kind="login")
    st._run("UPDATE credentials SET created_at = 1000 WHERE name = 'github'")                 # an old request
    st.save_credential("github", "GitHub", ["github.com"], {}, "", kind="login")             # the assistant asks again
    assert st._all("SELECT created_at FROM credentials")[0]["created_at"] > 1_000_000
    st.set_login("github", USER, PASSWORD)
    st._run("UPDATE credentials SET created_at = 1000 WHERE name = 'github'")
    st.save_credential("github", "GitHub", ["github.com"], {}, "", kind="login")             # same site: the stored login stays, and so does its date
    assert st._all("SELECT created_at FROM credentials")[0]["created_at"] == 1000
    assert st.purge_drafts(2_000_000_000) == 0 and st.credential("github")["has_secret"]

