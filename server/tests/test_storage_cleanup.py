"""Cleanup suggestions (storage_cleanup.py): what is listed, that nothing goes without a confirmed request (or the agent's approval
card), and that notes, memory and the database are never listed nor deleted."""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

from assistant.agent.loop import ApprovalRequest  # noqa: F401 - the approval card type the agent's delete goes through
from assistant.deliverables import Deliverables
from assistant.home import Home
from assistant.quota import Quota
from assistant.server import Settings, create_app
from assistant.storage_cleanup import KEEP_MEMORY_PASSES, KEEP_UPDATE_BACKUPS, OLD_DAYS, Cleanup, cleanup_tools
from assistant.tools.image_tools import ImageStore

from test_server import AUTH, TOKEN, Running

DAY = 86400


def age(path, days):
    t = time.time() - days * DAY
    os.utime(path, (t, t))
    return path


@pytest.fixture
def world(tmp_path):
    state = tmp_path / "state"
    home = Home(state / "home")
    home.migrate()
    (state / "assistant.db").write_bytes(b"db" * 1000)
    (state / "assistant.db-wal").write_bytes(b"w" * 100)
    up = home.root / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    age(_write(up / "old.pdf", 5000), OLD_DAYS + 5)
    age(_write(up / "photo.jpg", 3000), OLD_DAYS + 1)
    _write(up / "new.pdf", 4000)                                         # recent: not offered
    _write(home.root / "notes" / "extracted" / "old.md", 800)            # the text read from old.pdf stays
    _write(home.root / "notes" / "todo.md", 100)
    _write(home.root / "memory" / "people" / "ann.md", 100)
    images = ImageStore(home.root / ".tryon")
    images.save("shoe-1.png", b"p" * 2000)
    results = Deliverables(home)
    age(_write(results.dir / "report.xlsx", 7000), OLD_DAYS + 3)
    _write(results.dir / "fresh.csv", 100)
    results.save("gone.txt", b"g" * 600)
    results.trash("gone.txt")
    hist = state / "browser-history"
    old_tab = hist / "task_a1"
    _write(old_tab / "000001.jpg", 9000)
    (old_tab / "manifest.jsonl").write_text(json.dumps({"seq": 1, "url": "https://example.com", "title": "Example shop"}) + "\n")
    for p in old_tab.iterdir():
        age(p, 2)
    age(old_tab, 2)
    _write(hist / "task_live" / "000001.jpg", 500)                      # written just now: maybe still running
    b = state / ".backups"
    for i in range(4):
        d = b / f"2026110{i}-0000"
        _write(d / "state.tar.gz", 1000 * (i + 1))
        (d / "meta.json").write_text(json.dumps({"id": d.name, "label": "before update", "created": 1000 + i, "bytes": 1000 * (i + 1)}))
    passes = home.root / ".memory-passes"
    for i in range(KEEP_MEMORY_PASSES + 2):
        _write(passes / f"p{i:03d}" / "files" / "MEMORY.md", 50)
    q = Quota(state, 0)
    for part in (home, images):
        part.quota = q
    return Cleanup(home=home, images=images, results=results, state_root=state, quota=q), state, home


def _write(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * n)
    return path


def ids(listing, group=None):
    return {i["id"] for g in listing["groups"] if group in (None, g["id"]) for i in g["items"]}


def test_the_list_offers_only_what_is_safe_with_sizes_and_dates(world):
    c, state, home = world
    data = c.groups()
    assert [g["id"] for g in data["groups"]] == ["uploads", "images", "browser_history", "results", "backups"]
    assert ids(data, "uploads") == {"uploads:old.pdf", "uploads:photo.jpg"}            # the new upload stays off the list
    old = next(i for g in data["groups"] for i in g["items"] if i["id"] == "uploads:old.pdf")
    assert old["bytes"] == 5000 and old["keeps_text"] is True and old["date"] < time.time() - OLD_DAYS * DAY
    photo = next(i for g in data["groups"] for i in g["items"] if i["id"] == "uploads:photo.jpg")
    assert photo["keeps_text"] is False
    assert ids(data, "images") == {"images:shoe-1.png"}
    assert ids(data, "browser_history") == {"browser_history:task_a1"}                 # the live tab is left alone
    tab = next(i for g in data["groups"] for i in g["items"] if i["id"] == "browser_history:task_a1")
    assert tab["name"] == "Example shop"
    res = {i["id"]: i for g in data["groups"] if g["id"] == "results" for i in g["items"]}
    assert "results:report.xlsx" in res and "results:fresh.csv" not in res
    assert [i["trashed"] for k, i in res.items() if k.startswith("results:trash/")] == [True]
    backups = {i["id"]: i for g in data["groups"] if g["id"] == "backups" for i in g["items"]}
    update = sorted(k for k in backups if k.startswith("backups:update/"))
    assert update == ["backups:update/20261100-0000", "backups:update/20261101-0000"]   # the newest two are kept
    assert 4 - len(update) == KEEP_UPDATE_BACKUPS
    memory = sorted(k for k in backups if k.startswith("backups:memory/"))
    assert memory == ["backups:memory/p000", "backups:memory/p001"]                     # the newest passes are kept
    everything = json.dumps(data)
    assert "assistant.db" not in everything and "todo.md" not in everything and "ann.md" not in everything
    assert "extracted" not in everything
    g = {g["id"]: g for g in data["groups"]}
    assert g["uploads"]["bytes"] == 8000 and g["uploads"]["count"] == 2 and data["total"] == sum(x["bytes"] for x in data["groups"])


def test_deleting_removes_only_listed_items_and_keeps_the_text_of_an_upload(world):
    c, state, home = world
    asked = ["uploads:old.pdf", "images:shoe-1.png", "browser_history:task_a1", "results:report.xlsx", "backups:update/20261100-0000",
             "backups:memory/p000"]
    trash_id = next(i for i in ids(c.groups(), "results") if "trash/" in i)
    out = c.remove([*asked, trash_id])
    assert sorted(out["removed"]) == sorted([*asked, trash_id]) and out["skipped"] == []
    assert out["freed"] >= 5000 + 2000 + 9000 + 7000 + 1000 + 50 + 600
    assert not (home.root / "uploads" / "old.pdf").exists()
    assert (home.root / "notes" / "extracted" / "old.md").is_file()                     # what was read from it stays
    assert not (home.root / ".tryon" / "shoe-1.png").exists() and not (state / "browser-history" / "task_a1").exists()
    assert not (state / ".backups" / "20261100-0000").exists() and (state / ".backups" / "20261103-0000").exists()
    assert c.results.trashed() == []                                                      # the trash index forgets it too
    assert (home.root / "uploads" / "new.pdf").exists() and (home.root / "uploads" / "photo.jpg").exists()   # not asked: kept
    assert ids(out) == ids(c.groups())                                                     # the answer carries the list after


def test_notes_memory_and_the_database_are_never_deleted_whatever_is_sent(world):
    c, state, home = world
    before = {p: p.read_bytes() for p in [state / "assistant.db", state / "assistant.db-wal", home.root / "notes" / "todo.md",
                                          home.root / "memory" / "people" / "ann.md", home.root / "notes" / "extracted" / "old.md",
                                          home.root / "MEMORY.md"] if p.exists()}
    attempts = ["database:assistant.db", "uploads:../../assistant.db", "uploads:../notes/todo.md", "images:../notes/todo.md",
                "backups:update/../../assistant.db", "results:../memory/people/ann.md", "notes:todo.md", "uploads:new.pdf",
                "browser_history:task_live", "backups:update/20261103-0000", "backups:memory/p004", "uploads:", "", "x" * 300]
    out = c.remove(attempts)
    assert out["removed"] == [] and set(out["skipped"]) == set(dict.fromkeys(attempts))
    assert {p: p.read_bytes() for p in before} == before
    assert (home.root / "uploads" / "new.pdf").exists() and (state / "browser-history" / "task_live").exists()


def test_a_listed_path_that_was_swapped_for_a_link_to_notes_is_not_followed(world):
    c, state, home = world
    c.groups()
    target = home.root / "uploads" / "photo.jpg"
    target.unlink()
    target.symlink_to(home.root / "notes" / "todo.md")
    out = c.remove(["uploads:photo.jpg"])
    assert out["removed"] == [] and (home.root / "notes" / "todo.md").read_bytes() == b"x" * 100


def test_update_backups_are_not_offered_while_an_update_is_writing_one(world):
    c, state, home = world
    (state / ".backups" / ".20261109-0000.part").mkdir()
    assert not any(i.startswith("backups:update/") for i in ids(c.groups(), "backups"))


async def test_the_agent_lists_freely_but_deletes_only_through_an_approval_card(world):
    c, state, home = world
    listing, delete = cleanup_tools(c)
    assert listing.read_only and not listing.requires_approval
    assert delete.requires_approval and delete.describe is not None and delete.validate is not None
    text = await listing.handler({})
    assert "uploads:old.pdf" in text and "[text kept]" in text and "assistant.db" not in text
    assert delete.validate({"items": ["uploads:old.pdf"]}) is None
    assert "not on the cleanup list" in delete.validate({"items": ["notes:todo.md"]})
    assert delete.validate({}) is not None
    line = await delete.describe({"items": ["uploads:old.pdf", "images:shoe-1.png"]})
    assert "2" in line and "old.pdf" in line
    assert (home.root / "uploads" / "old.pdf").exists()                     # describing and validating delete nothing
    out = await delete.handler({"items": ["uploads:old.pdf"]})
    assert "Deleted 1" in out and not (home.root / "uploads" / "old.pdf").exists()


async def test_run_agent_asks_before_the_cleanup_tool_runs(world):
    from assistant.agent.loop import Done, TextDelta, ToolCall, run_agent
    c, state, home = world
    tools = cleanup_tools(c)
    asked = []

    async def approve(req):
        asked.append(req)
        return False                                                          # the user says no

    steps = iter([[ToolCall("c1", "storage_cleanup", json.dumps({"items": ["uploads:old.pdf"]})), Done("tool_calls")],
                  [Done("stop")]])

    async def stream(provider, request, **kw):
        for ev in next(steps, [TextDelta("ok"), Done("stop")]):
            yield ev
    async for _ in run_agent("p", "m", [{"role": "user", "content": "free some space"}], tools, approve=approve, stream_fn=stream):
        pass
    assert asked and asked[0].name == "storage_cleanup" and "old.pdf" in asked[0].detail
    assert (home.root / "uploads" / "old.pdf").exists()                      # declined: nothing deleted


# ------------------------------------------------------------------------------------------------ the routes
def app_for(tmp_path, **kw):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "assistant.db"),
                               home_dir=str(tmp_path / "state" / "home"), **kw), tools=[])


async def test_the_routes_list_and_delete_only_with_confirm_and_only_for_the_owner(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        assert (await c.put("/api/uploads/old.pdf", content=b"%PDF" + b"0" * 5000)).status_code == 200
        age(tmp_path / "state" / "home" / "uploads" / "old.pdf", OLD_DAYS + 2)
        data = (await c.get("/api/storage/cleanup")).json()
        assert "uploads:old.pdf" in ids(data) and data["older_than_days"] == OLD_DAYS
        assert (await c.post("/api/storage/cleanup", json={"items": ["uploads:old.pdf"]})).status_code == 400      # no confirm
        assert (await c.post("/api/storage/cleanup", json={"items": [], "confirm": True})).status_code == 400
        assert (tmp_path / "state" / "home" / "uploads" / "old.pdf").exists()
        r = await c.post("/api/storage/cleanup", json={"items": ["uploads:old.pdf", "database:assistant.db"], "confirm": True})
        assert r.status_code == 200 and r.json()["removed"] == ["uploads:old.pdf"] and r.json()["skipped"] == ["database:assistant.db"]
        assert not (tmp_path / "state" / "home" / "uploads" / "old.pdf").exists() and (tmp_path / "state" / "assistant.db").exists()
    async with Running(app_for(tmp_path)) as srv2, httpx.AsyncClient(base_url=srv2.url) as anon:
        assert (await anon.get("/api/storage/cleanup")).status_code == 401


async def test_a_paired_phone_can_clean_up_there_are_no_owner_devices(tmp_path):
    app = app_for(tmp_path)
    store = app.state.storage_watch.store
    code = store.new_pairing(owner=False)
    _dev, token = store.redeem_pairing(code, "phone")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={"Authorization": f"Bearer {token}"}, timeout=20) as c:
        assert (await c.get("/api/storage/cleanup")).status_code == 200
        assert (await c.get("/api/resources")).json()["can_clean"] is True
