"""The Kelo Nest HTTP API (nest_routes.py) on a real app: install from a GitHub link (preview, then confirm), the packages' rows and
updates, the screens' data and buttons, a button that asks Kelo (a fake model: no real model is ever called), and the package entry
tools in the chat. GitHub and the services are fakes (test_nest.World)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from test_nest import EXAMPLES, World

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def setup(tmp_path):
    seen: list = []

    async def model(provider, request, **k):
        seen.append(request)
        yield TextDelta("On it.")
        yield Done("stop")

    state = tmp_path / "state"
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(state / "assistant.db"),
                              content_dir=str(ROOT / "server" / "content"), home_dir=str(state / "home")), tools=[], stream_fn=model)
    c = TestClient(app)
    w = World(tmp_path / "world")
    nest = app.state.nest
    nest.fetcher, nest.http = w.fetch, w.http
    nest.draft_manager.fetcher, nest.draft_manager.http = w.fetch, w.http
    w.nest, w.registry, w.store = nest, app.state.packages, nest.store
    return app, c, w, seen


def phone(c) -> dict:
    code = c.post("/api/devices/pair", headers=AUTH).json()["code"]
    tok = c.post("/api/pair", json={"code": code, "name": "kid"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def install(c, w, pid: str, headers=AUTH):
    """Paste the package's GitHub link (the preview), then confirm with the permissions the owner was shown."""
    w.push(EXAMPLES / pid)
    card = c.post("/api/nest/install-from-link", json={"url": World.link(pid)}, headers=headers).json()
    return c.post("/api/nest/install-from-link/confirm", json={"token": card["token"], "accept": card["permissions"]["hash"]}, headers=headers)


def test_install_from_a_link_and_the_package_rows(tmp_path):
    app, c, w, _ = setup(tmp_path)
    w.push(EXAMPLES / "habit-tracker")
    kid = phone(c)
    assert c.get("/api/nest/status", headers=AUTH).json() == {"max_tabs": 2}
    # the catalog and the install-by-id routes are gone
    assert c.get("/api/nest/browse?q=habit", headers=kid).status_code == 404
    assert c.post("/api/nest/refresh", headers=AUTH).status_code in (404, 405)
    assert c.post("/api/nest/packages/habit-tracker/install", headers=AUTH, json={}).status_code in (404, 405)
    assert c.get("/api/nest/packages/habit-tracker", headers=AUTH).status_code == 404
    assert c.post("/api/nest/install-from-link", json={"url": "https://notgithub.com/a/b"}, headers=AUTH).status_code == 422

    card = c.post("/api/nest/install-from-link", json={"url": World.link("habit-tracker")}, headers={**AUTH, "Accept-Language": "ko"}).json()
    assert card["name"] == "습관 기록" and card["permissions"]["hash"] and card["token"]
    r = c.post("/api/nest/install-from-link/confirm", json={"token": card["token"], "accept": "0" * 64}, headers=AUTH)
    assert r.status_code == 409 and r.json()["error"] == "look_again" and r.json()["code"] == "look_again"   # the apps read "code"
    r = c.post("/api/nest/install-from-link/confirm", json={"token": card["token"], "accept": card["permissions"]["hash"]}, headers=kid)
    assert r.status_code == 200 and r.json()["state"] == "on" and r.json()["repo"] == "acme/habit-tracker" and "review" not in r.json()
    assert c.post("/api/nest/install-from-link/confirm", json={"token": card["token"], "accept": "0" * 64}, headers=AUTH).status_code == 410
    assert [i["id"] for i in c.get("/api/nest/installed", headers=kid).json()["items"]] == ["habit-tracker"]
    d = c.get("/api/nest/packages/habit-tracker", headers={**AUTH, "Accept-Language": "ko"}).json()
    assert d["name"] == "습관 기록" and d["update"] is None and d["repo"] == "acme/habit-tracker"
    assert c.get("/api/nest/updates", headers=AUTH).json()["items"] == []
    assert c.put("/api/nest/packages/habit-tracker/pin", headers=AUTH, json={"pinned": True}).json()["pinned"] is True
    assert c.put("/api/nest/packages/habit-tracker/permissions", headers=AUTH, json={"revoke": ["ui.tab"]}).json()["revoked"] == ["ui.tab"]
    assert c.get("/api/nest/extensions", headers=kid).json()["places"]["tab"] == []
    assert c.put("/api/nest/packages/habit-tracker/permissions", headers=AUTH, json={"revoke": ["hosts.x"]}).status_code == 422
    assert c.put("/api/nest/packages/habit-tracker/enabled", headers=AUTH, json={"enabled": False}).json()["state"] == "disabled"
    assert c.get("/api/nest/extensions/habit-tracker/data?surface=today", headers=AUTH).status_code == 403
    assert c.delete("/api/nest/packages/habit-tracker", headers=AUTH).json() == {"ok": True}
    assert c.delete("/api/nest/packages/habit-tracker", headers=AUTH).status_code == 404
    assert c.get("/api/nest/installed", headers=AUTH).json()["items"] == []


def test_screens_buttons_and_a_button_that_asks_kelo(tmp_path):
    app, c, w, seen = setup(tmp_path)
    assert install(c, w, "habit-tracker").status_code == 200
    kid = phone(c)
    ext = c.get("/api/nest/extensions", headers=kid).json()
    assert ext["schema"] == 1 and ext["max_tabs"] == 2 and "check_circle" in ext["icons"]
    tab = ext["places"]["tab"][0]
    assert tab["key"] == "habit-tracker/habits" and tab["data_url"] == "/api/nest/extensions/habit-tracker/data?surface=habits"
    screen = c.get(tab["data_url"], headers=kid).json()
    assert screen["components"][1]["type"] == "form" and screen["components"][1]["submit"]["action"]["id"] == "check_in"
    assert c.get("/api/nest/extensions/habit-tracker/data?surface=nope", headers=kid).status_code == 404
    r = c.post("/api/nest/extensions/habit-tracker/actions/check_in", headers=kid, json={"form": {"habit": "Read"}})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert c.post("/api/nest/extensions/habit-tracker/actions/check_in", headers=kid, json={"form": {"habit": "Nope"}}).status_code == 422
    assert c.post("/api/nest/extensions/habit-tracker/actions/missing", headers=kid, json={}).status_code == 404
    rows = c.get(tab["data_url"], headers=kid).json()["components"][2]["rows"]
    assert rows[0]["title"] == "Read"
    # the agent button: a normal chat reply; the chat shows what was tapped, the model gets the package's words fenced
    r = c.post("/api/nest/extensions/habit-tracker/actions/weekly", headers={**AUTH, "Accept-Language": "en"}, json={})
    assert r.status_code == 200 and r.json()["status"] == "started"
    turn = r.json()["turn_id"]
    for _ in range(50):
        if c.get(f"/api/turns/{turn}?wait=0.2", headers=AUTH).json()["done"]:
            break
        time.sleep(0.05)
    user = [m for m in seen[0].messages if m["role"] == "user"][-1]
    text = user["content"] if isinstance(user["content"], str) else str(user["content"])
    assert text.startswith("Weekly review · Habit tracker") and "[UNTRUSTED CONTENT" in text and "[Not typed by the user" in text
    saved = [m for m in c.get("/api/messages", headers=AUTH).json() if m["role"] == "user"]
    assert [m["content"] for m in saved] == ["Weekly review · Habit tracker"]   # the chat shows what was tapped, not the package's words


def test_images_are_served_by_the_server(tmp_path):
    app, c, w, _ = setup(tmp_path)
    assert install(c, w, "example_parcels").status_code == 200
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 20
    w.answers["GET https://img.parcels.example.com/a.png"] = (200, png)
    r = c.get("/api/nest/extensions/example_parcels/image", params={"src": "https://img.parcels.example.com/a.png"}, headers=AUTH)
    assert r.status_code == 200 and r.content == png and r.headers["content-security-policy"] == "default-src 'none'"
    assert c.get("/api/nest/extensions/example_parcels/image", params={"src": "https://evil.example.net/a.png"}, headers=AUTH).status_code == 403
    # the connector row is the same sign-in/key row as before
    rows = {r["id"]: r for r in c.get("/api/connectors", headers=AUTH).json()}
    assert rows["example_parcels"]["connect"] == "key"


def test_the_share_turn_tells_the_owner_how_others_install_it(tmp_path):
    app, c, w, seen = setup(tmp_path)
    nest = app.state.nest
    nest.draft_manager.create("skill", "my-guide", "My guide", "Steps for my week")
    nest.store.save_credential("github-api", "GitHub · @octocat", ["api.github.com"], {"type": "header", "name": "Authorization", "prefix": "Bearer "})
    nest.store.set_credential_secret("github-api", "ghp_test_token_12345")
    nest.store.set_meta("oauth_scope.github", "public")
    r = c.post("/api/nest/drafts/my-guide/share", headers=AUTH)
    assert r.status_code == 202 and r.json()["turn_id"]
    for _ in range(50):
        if c.get(f"/api/turns/{r.json()['turn_id']}?wait=0.2", headers=AUTH).json()["done"]:
            break
        time.sleep(0.05)
    user = [m for m in seen[0].messages if m["role"] == "user"][-1]
    text = user["content"] if isinstance(user["content"], str) else str(user["content"])
    assert "Install from link" in text and "My -> Nest" in text
    assert "pull request" not in text.lower() and "Kelo Nest" not in text


def test_share_this_skill_route_copies_a_custom_skill_or_says_why_not(tmp_path):
    """POST /api/nest/drafts/from-skill/{id} ("share this skill" in the apps): an unknown skill is a plain refusal, never a crash."""
    _, c, _, _ = setup(tmp_path)
    r = c.post("/api/nest/drafts/from-skill/no-such-skill", headers=AUTH)
    assert r.status_code in (404, 409, 422), r.text


def test_a_link_that_is_not_a_package_says_so_in_the_owners_language(tmp_path):
    """The refusal's prefix is a translated server text; the detail after it stays as the checker wrote it."""
    import secrets
    _, c, w, _ = setup(tmp_path)
    commit = secrets.token_hex(20)
    w.archives[commit] = b"this is not an archive"
    w.heads["acme/broken"] = commit
    ko = c.post("/api/nest/install-from-link", json={"url": World.link("broken")}, headers={**AUTH, "Accept-Language": "ko"})
    en = c.post("/api/nest/install-from-link", json={"url": World.link("broken")}, headers={**AUTH, "Accept-Language": "en"})
    assert ko.status_code == en.status_code == 422, ko.text
    ko_detail, en_detail = ko.json()["detail"], en.json()["detail"]
    assert ko_detail.startswith("패키지가 아니에요: "), ko_detail
    assert en_detail.startswith("not a package: "), en_detail
    assert ko_detail.split(": ", 1)[1] == en_detail.split(": ", 1)[1]
