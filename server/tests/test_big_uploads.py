"""Uploads: no per-file limit except through Cloudflare (CF-Ray), and big files in parts (start, parts in any order, finish)."""

import hashlib
import os
import time

import httpx
import pytest

from assistant import home as home_mod
from assistant.home import Home, HomeError, HomeFull
from assistant.photos import MAX_IMAGE
from assistant.quota import Quota
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, scripted

CF = {"cf-ray": "8a1b2c3d4e5f-AMS"}


def app(tmp_path):
    s = Settings(token=TOKEN, cdp_host=None, home_dir=str(tmp_path / "h"), content_dir=str(__import__("test_extract").CONTENT))
    return Running(create_app(s, tools=None, stream_fn=scripted()))


async def chunks(data, n=7000):
    for i in range(0, len(data), n):
        yield data[i:i + n]


def left_behind(root):
    inc = root / ".incoming"
    return sorted(p.name for p in inc.iterdir()) if inc.exists() else []


async def test_one_request_limit_depends_on_the_route(tmp_path, monkeypatch):
    monkeypatch.setattr(home_mod, "MAX_UPLOAD", 1000)
    async with app(tmp_path) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        body = b"\x00" * 5000
        r = await c.put("/api/uploads/v.mp4", content=body, headers=CF)
        assert r.status_code == 413 and "in parts" in r.json()["detail"] and "100 MB" in r.json()["detail"]
        r = await c.put("/api/uploads/v.mp4", content=body)
        assert r.status_code == 200 and r.json()["path"] == "uploads/v.mp4"
        assert (await c.put("/api/uploads/s.mp4", content=b"x" * 900, headers=CF)).status_code == 200
        # a photo keeps its own limit on every route
        big = b"x" * (MAX_IMAGE + 10)
        assert (await c.put("/api/uploads/b.jpg", content=big)).status_code == 413
        assert (await c.put("/api/uploads/b.jpg", content=big, headers=CF)).status_code == 413
        lim = (await c.get("/api/uploads/limits")).json()
        assert lim == {"single_max": None, "part_size": 90_000_000, "image_max": MAX_IMAGE}
        assert (await c.get("/api/uploads/limits", headers=CF)).json()["single_max"] == 1000


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(home_mod, "PART_SIZE", 1000)
    monkeypatch.setattr(home_mod, "CHECK_EVERY", 500)


async def test_parts_in_any_order_are_joined_and_checked(tmp_path, small):
    data = os.urandom(2500)
    sha = hashlib.sha256(data).hexdigest()
    async with app(tmp_path) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/uploads/trip.mp4/parts", json={"size": 2500, "sha256": sha})
        assert r.status_code == 200
        st = r.json()
        assert st["part_size"] == 1000 and st["parts"] == 3
        uid = st["upload_id"]
        assert (await c.put(f"/api/uploads/parts/{uid}/2", content=data[2000:])).json() == {"ok": True, "received": 1}
        assert (await c.put(f"/api/uploads/parts/{uid}/0", content=b"z" * 1000)).status_code == 200
        assert (await c.put(f"/api/uploads/parts/{uid}/0", content=data[:1000])).json()["received"] == 2   # re-sent: replaced
        info = (await c.get(f"/api/uploads/parts/{uid}")).json()
        assert info == {"name": "trip.mp4", "size": 2500, "part_size": 1000, "received": [0, 2]}
        r = await c.post(f"/api/uploads/parts/{uid}/finish")
        assert r.status_code == 422                                                  # part 1 is missing; the upload stays open
        assert (await c.put(f"/api/uploads/parts/{uid}/1", content=data[1000:2000])).status_code == 200
        r = await c.post(f"/api/uploads/parts/{uid}/finish")
        assert r.status_code == 200 and r.json() == {"path": "uploads/trip.mp4", "ok": True}
        assert (tmp_path / "h" / "uploads" / "trip.mp4").read_bytes() == data
        assert left_behind(tmp_path / "h") == []
        assert (await c.get(f"/api/uploads/parts/{uid}")).status_code == 404


async def test_wrong_part_size_bad_hash_bad_index_and_bad_ids(tmp_path, small):
    async with app(tmp_path) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        uid = (await c.post("/api/uploads/a.mp4/parts", json={"size": 1500})).json()["upload_id"]
        assert (await c.put(f"/api/uploads/parts/{uid}/0", content=b"x" * 999)).status_code == 422
        assert (await c.put(f"/api/uploads/parts/{uid}/0", content=b"x" * 1001)).status_code == 422
        assert (await c.put(f"/api/uploads/parts/{uid}/1", content=b"x" * 1000)).status_code == 422   # the last part is 500
        assert (await c.put(f"/api/uploads/parts/{uid}/2", content=b"x" * 500)).status_code == 422     # no such index
        assert (await c.put(f"/api/uploads/parts/{uid}/0", content=b"x" * 1000)).status_code == 200
        assert (await c.put(f"/api/uploads/parts/{uid}/1", content=b"x" * 500)).status_code == 200
        assert (await c.post("/api/uploads/a.mp4/parts", json={"size": 10, "sha256": "nothex"})).status_code == 422
        assert (await c.post("/api/uploads/a.exe/parts", json={"size": 10})).status_code == 422
        assert (await c.post("/api/uploads/a.mp4/parts", json={"size": 0})).status_code == 422
        for bad in ("x" * 16 + "%2F..", "short", "a.b" * 8):
            assert (await c.get(f"/api/uploads/parts/{bad}")).status_code in (404, 405, 422)
        assert (await c.get("/api/uploads/parts/" + "A" * 20)).status_code == 404
        # the whole file's hash is wrong: refused and dropped, nothing kept
        sha = hashlib.sha256(b"other").hexdigest()
        v = (await c.post("/api/uploads/h.mp4/parts", json={"size": 600, "sha256": sha})).json()["upload_id"]
        assert (await c.put(f"/api/uploads/parts/{v}/0", content=b"y" * 600)).status_code == 200
        assert (await c.post(f"/api/uploads/parts/{v}/finish")).status_code == 422
        assert (await c.get(f"/api/uploads/parts/{v}")).status_code == 404
        assert not (tmp_path / "h" / "uploads" / "h.mp4").exists()


async def test_cancel_removes_and_three_open_is_the_most(tmp_path, small):
    async with app(tmp_path) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        ids = []
        for i in range(3):
            ids.append((await c.post(f"/api/uploads/f{i}.mp4/parts", json={"size": 2000})).json()["upload_id"])
        assert (await c.put(f"/api/uploads/parts/{ids[0]}/0", content=b"x" * 1000)).status_code == 200
        assert (await c.post("/api/uploads/f9.mp4/parts", json={"size": 2000})).status_code == 409
        assert (await c.delete(f"/api/uploads/parts/{ids[0]}")).json() == {"ok": True}
        assert (await c.delete(f"/api/uploads/parts/{ids[0]}")).status_code == 404
        assert not (tmp_path / "h" / ".incoming" / ids[0]).exists()
        assert (await c.post("/api/uploads/f9.mp4/parts", json={"size": 2000})).status_code == 200


async def test_a_photo_over_the_image_limit_is_refused_at_start(tmp_path):
    async with app(tmp_path) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/uploads/a.jpg/parts", json={"size": MAX_IMAGE + 1})).status_code == 413
        assert left_behind(tmp_path / "h") == []


async def test_storage_cap_holds_for_every_part_and_nothing_is_left(tmp_path, small, monkeypatch):
    home = Home(tmp_path / "h")
    home.quota = Quota(home.root, 3000)
    # refused at the start: the whole file would not fit
    with pytest.raises(HomeFull):
        home.start_parts("big.mp4", 5000)
    assert left_behind(home.root) == []
    st = home.start_parts("v.mp4", 2500)
    uid = st["upload_id"]
    await home.receive_part(uid, 0, chunks(b"a" * 1000))
    # something else fills the storage meanwhile: the next part is refused, the earlier part stays, no stray temp file
    (home.root / "notes").mkdir(exist_ok=True)
    (home.root / "notes" / "fill.txt").write_bytes(b"f" * 1800)
    home.quota.invalidate()
    with pytest.raises(HomeFull):
        await home.receive_part(uid, 1, chunks(b"b" * 1000))
    assert sorted(p.name for p in (home.root / ".incoming" / uid).iterdir()) == ["0.part", "meta.json"]
    # a cap crossed while the part streams in (no room found at the start is simulated by a lying size check)
    (home.root / "notes" / "fill.txt").write_bytes(b"f" * 1000)
    home.quota.invalidate()
    monkeypatch.setattr(Home, "room_for", lambda self, size: None)
    with pytest.raises(HomeFull):
        await home.receive_part(uid, 1, chunks(b"b" * 1000))
    assert sorted(p.name for p in (home.root / ".incoming" / uid).iterdir()) == ["0.part", "meta.json"]
    home.cancel_parts(uid)
    assert left_behind(home.root) == []


async def test_disk_margin_is_checked_per_part(tmp_path, small, monkeypatch):
    home = Home(tmp_path / "h")
    uid = home.start_parts("v.mp4", 2000)["upload_id"]
    monkeypatch.setattr(home_mod, "disk_free", lambda root: 100_000_000)       # less than DISK_MARGIN
    with pytest.raises(HomeFull):
        await home.receive_part(uid, 0, chunks(b"a" * 1000))
    assert not list((home.root / ".incoming" / uid).glob("*.part"))


async def test_the_sweep_removes_part_folders_after_a_day(tmp_path, small):
    home = Home(tmp_path / "h")
    old = home.start_parts("o.mp4", 2000)["upload_id"]
    new = home.start_parts("n.mp4", 2000)["upload_id"]
    await home.receive_part(old, 0, chunks(b"a" * 1000))
    d = home.root / ".incoming" / old
    past = time.time() - 25 * 3600
    for p in [*d.iterdir(), d]:
        os.utime(p, (past, past))
    home.start_parts("x.mp4", 2000)            # sweeping happens whenever an upload starts
    assert not d.exists() and (home.root / ".incoming" / new).is_dir()
    with pytest.raises(HomeError):
        home.parts_status(old)
