import httpx
import pytest

from assistant.home import Home, HomeError, HomeFull
from assistant.quota import Quota, QuotaError, cgroup_limits, fmt_size, parse_size
from assistant.server import Settings, create_app
from assistant.tools.image_tools import ImageStore, image_tools

from test_server import AUTH, TOKEN, Running


# ------------------------------------------------------------------------------------------------ sizes
@pytest.mark.parametrize("text,n", [("512m", 512 * 1024**2), ("2g", 2 * 1024**3), ("1.5G", int(1.5 * 1024**3)), ("100MB", 100 * 1024**2),
                                    ("64k", 64 * 1024), ("1024", 1024), (2048, 2048), ("5 GiB", 5 * 1024**3), ("1t", 1024**4),
                                    ("0", 0), ("", 0), (None, 0), ("none", 0), ("Unlimited", 0), ("off", 0)])
def test_sizes_are_read_the_way_people_write_them(text, n):
    assert parse_size(text) == n


@pytest.mark.parametrize("bad", ["lots", "-1g", "1x", "g", "1..5g", "5 gigs"])
def test_odd_sizes_are_refused_with_an_example(bad):
    with pytest.raises(ValueError, match="examples"):
        parse_size(bad)


def test_sizes_are_shown_briefly():
    assert [fmt_size(n) for n in (0, 900, 2048, 5 * 1024**2, 1536 * 1024**2, 20 * 1024**3)] == ["0B", "900B", "2.0KB", "5.0MB", "1.5GB", "20.0GB"]


# ------------------------------------------------------------------------------------------------ the cap
def fill(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * n)


def test_usage_is_counted_by_area_and_symlinks_are_ignored(tmp_path):
    fill(tmp_path / "assistant.db", 1000)
    fill(tmp_path / "home" / "uploads" / "a.pdf", 5000)
    fill(tmp_path / "home" / ".podcasts" / "e.mp3", 7000)   # left by an older version: still takes room, counted as "other"
    fill(tmp_path / "home" / ".tryon" / "p.png", 300)
    fill(tmp_path / "home" / "notes" / "n.md", 20)
    fill(tmp_path / "home" / "memory" / "m.md", 30)
    fill(tmp_path / "home" / "MEMORY.md", 40)
    (tmp_path / "home" / "link").symlink_to(tmp_path / "assistant.db")
    q = Quota(tmp_path, limit=0)
    assert q.used() == 1000 + 5000 + 7000 + 300 + 20 + 30 + 40
    assert q.parts() == {"other": 7040, "uploads": 5000, "database": 1000, "images": 300, "notes": 50}


def test_a_write_that_would_pass_the_cap_is_refused_and_a_rewrite_only_counts_what_it_adds(tmp_path):
    fill(tmp_path / "assistant.db", 600)
    q = Quota(tmp_path, limit=1000, ttl=0)
    q.check(400)                                    # exactly fits
    with pytest.raises(QuotaError, match="Storage is full"):
        q.check(401)
    q.check(900, replacing=600)                     # replacing a 600 byte file with a 900 byte one adds 300
    with pytest.raises(QuotaError):
        q.check(1100, replacing=600)
    Quota(tmp_path, limit=0).check(10**12)           # no cap, no refusal


def test_the_cap_covers_notes_uploads_and_images_but_never_blocks_reading(tmp_path):
    root = tmp_path / "state"
    home = Home(root / "home")
    q = Quota(root, limit=10_000, ttl=0)
    home.quota = q
    home.write("notes/a.md", "x" * 500)
    fill(root / "pad", 9_000)                        # something else already uses most of the room
    with pytest.raises(HomeFull, match="Storage is full"):
        home.write("notes/b.md", "y" * 2000)
    home.write("notes/a.md", "z" * 600)              # a small rewrite of an existing file still fits
    assert isinstance(HomeFull("x"), HomeError)
    with pytest.raises(HomeFull):
        home.save_upload("big.pdf", b"%PDF" + b"0" * 3000)
    assert home.read("notes/a.md").startswith("z")    # reading always works

    store = ImageStore(root / "home" / ".tryon")
    store.quota = q
    with pytest.raises(QuotaError):
        store.save("p.png", b"i" * 5000)
    fill(root / "pad", 10)                           # room again
    store.save("p.png", b"i" * 5000)
    assert store.path("p.png") and store.delete("p.png") and not store.delete("p.png")


async def test_tools_report_a_full_disk_clearly(tmp_path):
    from assistant.netguard import Reply
    root = tmp_path / "state"
    fill(root / "pad", 9_990)
    q = Quota(root, limit=10_000, ttl=0)
    images = ImageStore(root / "home" / ".tryon")
    images.quota = q
    import io

    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (200, 200), "red").save(b, "PNG")

    async def img(*a, **k):
        return Reply(200, {}, b.getvalue(), False)

    t2 = {t.name: t for t in image_tools(images, img)}
    assert "Storage is full" in await t2["save_product_image"].handler({"url": "https://cdn.example.com/x.png"})


# ------------------------------------------------------------------------------------------------ what Docker gave the container
def cg(tmp_path, **files):
    for name, text in files.items():
        p = tmp_path / name.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_cgroup_v2_limits_are_read_and_file_cache_is_not_counted_as_use(tmp_path):
    root = cg(tmp_path, **{"memory.current": "300000000", "memory.max": "314572800", "cpu.max": "150000 100000",
                           "memory.stat": "anon 1\ninactive_file 100000000\nactive_file 5\n"})
    assert cgroup_limits(root) == {"memory_used": 200_000_000, "memory_limit": 314572800, "cpus": 1.5}


def test_no_limits_show_as_none_and_missing_files_do_not_crash(tmp_path):
    root = cg(tmp_path, **{"memory.current": "5000", "memory.max": "max", "cpu.max": "max 100000"})
    assert cgroup_limits(root) == {"memory_used": 5000, "memory_limit": None, "cpus": None}
    assert cgroup_limits(tmp_path / "nothing") == {"memory_used": None, "memory_limit": None, "cpus": None}


def test_cgroup_v1_is_understood_too(tmp_path):
    root = cg(tmp_path, **{"memory__memory.usage_in_bytes": "4096", "memory__memory.limit_in_bytes": "2147483648",
                           "cpu__cpu.cfs_quota_us": "50000", "cpu__cpu.cfs_period_us": "100000"})
    assert cgroup_limits(root) == {"memory_used": 4096, "memory_limit": 2147483648, "cpus": 0.5}
    unlimited = cg(tmp_path / "u", **{"memory__memory.usage_in_bytes": "1", "memory__memory.limit_in_bytes": str(2**63 - 4096),
                                      "cpu__cpu.cfs_quota_us": "-1", "cpu__cpu.cfs_period_us": "100000"})
    assert cgroup_limits(unlimited)["memory_limit"] is None and cgroup_limits(unlimited)["cpus"] is None


# ------------------------------------------------------------------------------------------------ the app
def app_for(tmp_path, **kw):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "s.db"),
                               home_dir=str(tmp_path / "state" / "home"), **kw), tools=[])


async def test_the_app_refuses_writes_past_the_cap_with_507_and_shows_usage(tmp_path):
    limit = 900_000   # an empty assistant's database (every module's tables) is about 0.6 MB and grows with new modules
    async with Running(app_for(tmp_path, disk_limit=limit)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        base = (await c.get("/api/resources")).json()["disk"]
        assert base["limit"] == limit and 0 < base["used"] < limit - 100_000          # an empty assistant (database, notes) is far below the cap
        room = limit - base["used"]
        assert (await c.put("/api/uploads/a.pdf", content=b"%PDF" + b"0" * 1000)).status_code == 200
        too_big = await c.put("/api/uploads/b.pdf", content=b"%PDF" + b"0" * (room + 1000))
        assert too_big.status_code == 507 and "Storage is full" in too_big.json()["detail"]
        filler = await c.put("/api/uploads/c.pdf", content=b"%PDF" + b"0" * (room - 1000 - 20_000))
        assert filler.status_code == 200                                              # fills it to within 20 KB of the cap
        res = (await c.get("/api/resources")).json()
        assert res["disk"]["parts"]["uploads"] > room - 22_000 and res["disk"]["free_on_host"] > 0 and set(res) == {"memory", "cpus", "disk", "host", "profile", "minimum", "recommended", "alert", "can_clean"}
        assert (await c.put("/api/files/notes/x.md", json={"content": "k" * 30_000})).status_code == 507
        assert (await c.put("/api/files/notes/x.md", json={"content": "k" * 1_000})).status_code == 200      # small notes still fit
        assert (await c.delete("/api/uploads/c.pdf")).status_code == 200
        assert (await c.put("/api/files/notes/x.md", json={"content": "k" * 30_000})).status_code == 200     # deleting frees room at once


async def test_without_a_cap_nothing_is_refused_and_the_limit_shows_as_none(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        assert (await c.put("/api/uploads/a.pdf", content=b"%PDF" + b"0" * 700_000)).status_code == 200
        assert (await c.get("/api/resources")).json()["disk"]["limit"] is None
    async with Running(app_for(tmp_path)) as srv2, httpx.AsyncClient(base_url=srv2.url) as anon:
        assert (await anon.get("/api/resources")).status_code == 401


def test_the_cap_can_be_written_the_way_people_write_sizes_and_a_typo_stops_the_start():
    base = {"ASSISTANT_TOKEN": "t" * 32}
    assert Settings.from_env({**base, "ASSISTANT_DISK_LIMIT": "5g"}).disk_limit == 5 * 1024**3
    assert Settings.from_env({**base}).disk_limit == 0 and Settings.from_env({**base, "ASSISTANT_DISK_LIMIT": "none"}).disk_limit == 0
    with pytest.raises(RuntimeError, match="ASSISTANT_DISK_LIMIT"):
        Settings.from_env({**base, "ASSISTANT_DISK_LIMIT": "plenty"})


def test_room_freed_by_a_delete_counts_at_once_even_while_the_numbers_are_cached(tmp_path):
    fill(tmp_path / "big", 900)
    q = Quota(tmp_path, limit=1000, ttl=3600)           # numbers stay cached for an hour
    assert q.used() == 900
    with pytest.raises(QuotaError):
        q.check(500)
    (tmp_path / "big").unlink()                           # deleted by anything, without telling the quota
    q.check(500)                                          # looks again before refusing, so this fits


def test_what_the_computer_has_is_read_so_that_no_limit_can_be_shown_as_a_real_number(monkeypatch):
    from assistant.hosting import capacity
    from assistant.quota import host_capacity
    cap = host_capacity()
    assert cap["memory"] > 0 and cap["cpus"] >= 1
    monkeypatch.setattr(capacity, "machine_memory", lambda: None)
    assert host_capacity()["memory"] is None


async def test_the_resources_answer_always_carries_the_computers_capacity_even_when_nothing_is_limited(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        res = (await c.get("/api/resources")).json()
        assert res["disk"]["limit"] is None
        host = res["host"]
        assert host["cpus"] >= 1 and host["disk_free"] > 0 and host["disk_total"] >= host["disk_free"]
        assert host["memory"] is None or host["memory"] > 100 * 1024**2


async def test_the_minimum_and_recommended_sizes_go_out_as_whole_numbers_of_bytes(tmp_path):
    # 536870912.0 (a float) made the phone app's integer fields fail to read and left the page blank
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        res = (await c.get("/api/resources")).json()
        for size in ("minimum", "recommended"):
            for key in ("memory", "disk"):
                assert type(res[size][key]) is int and res[size][key] > 0, (size, key, res[size][key])
            assert isinstance(res[size]["cpus"], (int, float)) and res[size]["cpus"] > 0
        assert all(type(n) is int for n in res["disk"]["parts"].values()) and type(res["disk"]["used"]) is int


async def test_without_limits_the_hints_are_the_whole_computers_requirement_and_with_a_cap_the_servers_share(tmp_path):
    # the owner's resources page (2026-11-15) showed "recommended 12.6 GB" under a disk that holds 11.4 GB at most: that was the
    # server container's data share of the recommended size, read as a need of the computer
    from assistant import profiles
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        res = (await c.get("/api/resources")).json()
    assert res["disk"]["limit"] is None
    assert res["minimum"]["disk"] == profiles.TOTALS["minimum"][2] == 10 * 1024**3
    assert res["recommended"]["disk"] == profiles.TOTALS["recommended"][2] == 20 * 1024**3
    if res["memory"]["limit"] is None:
        assert (res["minimum"]["memory"], res["recommended"]["memory"]) == (2 * 1024**3, 4 * 1024**3)
    if res["cpus"] is None:
        assert (res["minimum"]["cpus"], res["recommended"]["cpus"]) == (2.0, 4.0)
    async with Running(app_for(tmp_path, disk_limit=900_000)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        capped = (await c.get("/api/resources")).json()
    assert capped["minimum"]["disk"] == int(profiles.caps("minimum")["disk"])
    assert capped["recommended"]["disk"] == int(profiles.caps("recommended")["disk"])
