"""The Kelo Nest HTTP API (nest_routes.py) on a real app: owner-only changes, reading for every signed-in device, the screens' data and
buttons, a button that asks Kelo (a fake model: no real model is ever called), and the package entry tools in the chat."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from test_nest import EXAMPLES, World

ROOT = Path(__file__).resolve().parents[1]
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
                              content_dir=str(ROOT / "content"), home_dir=str(state / "home")), tools=[], stream_fn=model)
    c = TestClient(app)
    w = World(tmp_path / "world")
    nest = app.state.nest
    nest.keys, nest.fetcher, nest.http = [w.key.public_key()], w.fetch, w.http
    w.nest, w.registry, w.store = nest, app.state.packages, nest.store
    return app, c, w, seen


def phone(c) -> dict:
    code = c.post("/api/devices/pair", headers=AUTH).json()["code"]
    tok = c.post("/api/pair", json={"code": code, "name": "kid"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_browse_details_install_and_owner_only_changes(tmp_path):
    app, c, w, _ = setup(tmp_path)
    w.publish(EXAMPLES / "habit-tracker", verified=True)
    w.sign_index()
    kid = phone(c)
    assert c.get("/api/nest/status", headers=AUTH).json()["available"] is True
    items = c.get("/api/nest/browse?q=habit", headers=kid).json()["items"]
    assert [i["id"] for i in items] == ["habit-tracker"] and items[0]["installed"] is None
    assert c.get("/api/nest/browse?kind=theme", headers=AUTH).status_code == 422
    d = c.get("/api/nest/packages/habit-tracker", headers={**AUTH, "Accept-Language": "ko"}).json()
    assert d["name"] == "습관 기록" and d["permissions"]["hash"]
    assert c.get("/api/nest/packages/nothing-here", headers=AUTH).status_code == 404
    r = c.post("/api/nest/packages/habit-tracker/install", headers=AUTH, json={})
    assert r.status_code == 409 and r.json()["permissions"]["hash"] == d["permissions"]["hash"]
    accept = {"accept": d["permissions"]["hash"]}
    assert c.post("/api/nest/packages/habit-tracker/install", headers=kid, json=accept).status_code == 403
    r = c.post("/api/nest/packages/habit-tracker/install", headers=AUTH, json=accept)
    assert r.status_code == 200 and r.json()["state"] == "on"
    assert [i["id"] for i in c.get("/api/nest/installed", headers=kid).json()["items"]] == ["habit-tracker"]
    assert c.get("/api/nest/updates", headers=AUTH).json()["items"] == []
    for method, path, body in [("put", "/api/nest/packages/habit-tracker/pin", {"pinned": True}),
                               ("put", "/api/nest/packages/habit-tracker/enabled", {"enabled": False}),
                               ("put", "/api/nest/packages/habit-tracker/permissions", {"revoke": ["ui.tab"]}),
                               ("put", "/api/nest/extensions/tabs", {"show": []}),
                               ("post", "/api/nest/refresh", None)]:
        assert getattr(c, method)(path, headers=kid, **({"json": body} if body is not None else {})).status_code == 403, path
    assert c.put("/api/nest/packages/habit-tracker/pin", headers=AUTH, json={"pinned": True}).json()["pinned"] is True
    assert c.put("/api/nest/packages/habit-tracker/permissions", headers=AUTH, json={"revoke": ["ui.tab"]}).json()["revoked"] == ["ui.tab"]
    assert c.get("/api/nest/extensions", headers=kid).json()["places"]["tab"] == []
    assert c.put("/api/nest/packages/habit-tracker/permissions", headers=AUTH, json={"revoke": ["hosts.x"]}).status_code == 422
    assert c.put("/api/nest/packages/habit-tracker/enabled", headers=AUTH, json={"enabled": False}).json()["state"] == "disabled"
    assert c.get("/api/nest/extensions/habit-tracker/data?surface=today", headers=AUTH).status_code == 403
    assert c.delete("/api/nest/packages/habit-tracker", headers=kid).status_code == 403
    assert c.delete("/api/nest/packages/habit-tracker", headers=AUTH).json() == {"ok": True}
    assert c.delete("/api/nest/packages/habit-tracker", headers=AUTH).status_code == 404
    assert c.get("/api/nest/installed", headers=AUTH).json()["items"] == []


def test_screens_buttons_and_a_button_that_asks_kelo(tmp_path):
    app, c, w, seen = setup(tmp_path)
    w.publish(EXAMPLES / "habit-tracker")
    w.sign_index()
    d = c.get("/api/nest/packages/habit-tracker", headers=AUTH).json()
    c.post("/api/nest/packages/habit-tracker/install", headers=AUTH, json={"accept": d["permissions"]["hash"]})
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
    w.publish(EXAMPLES / "example_parcels")
    w.sign_index()
    d = c.get("/api/nest/packages/example_parcels", headers=AUTH).json()
    assert c.post("/api/nest/packages/example_parcels/install", headers=AUTH, json={"accept": d["permissions"]["hash"]}).status_code == 200
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 20
    w.answers["GET https://img.parcels.example.com/a.png"] = (200, png)
    r = c.get("/api/nest/extensions/example_parcels/image", params={"src": "https://img.parcels.example.com/a.png"}, headers=AUTH)
    assert r.status_code == 200 and r.content == png and r.headers["content-security-policy"] == "default-src 'none'"
    assert c.get("/api/nest/extensions/example_parcels/image", params={"src": "https://evil.example.net/a.png"}, headers=AUTH).status_code == 403
    # the connector row is the same sign-in/key row as before
    rows = {r["id"]: r for r in c.get("/api/connectors", headers=AUTH).json()}
    assert rows["example_parcels"]["connect"] == "key"
