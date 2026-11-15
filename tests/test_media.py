"""Photos, videos and sound in the user's documents: uploads stream to disk (no whole-file buffering) up to 100 MB (99,000,000 bytes, under Cloudflare's body limit) within the storage cap
and the free disk, previews (upright photos, a video frame) are made once and kept, and files play in the app with Range requests."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

import assistant.home as home_mod
from assistant.home import Home, HomeError, HomeFull, UploadTooLarge
from assistant.media import MediaCache, kind_of, mime_of, thumb_size
from assistant.server import Settings, create_app
from assistant.tools.extract import run_extraction

from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running, scripted

av = pytest.importorskip("av")


def make_video(path: Path, seconds: float = 2.0, size=(64, 48)) -> Path:
    with av.open(str(path), "w") as box:
        s = box.add_stream("mpeg4", rate=10)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        for i in range(int(seconds * 10)):
            frame = av.VideoFrame.from_image(Image.new("RGB", size, (20, 200, 40)))
            for pkt in s.encode(frame):
                box.mux(pkt)
        for pkt in s.encode():
            box.mux(pkt)
    return path


def sideways_photo() -> bytes:
    """100x50 pixels stored, EXIF orientation 6: shown upright it is 50 wide and 100 tall."""
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (100, 50), (200, 30, 30)).save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


async def chunks(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def app_for(tmp_path, **kw):
    s = Settings(token=TOKEN, cdp_host=None, home_dir=str(tmp_path / "h"), content_dir=str(CONTENT), **kw)
    return create_app(s, tools=None, stream_fn=scripted())


def test_kinds_and_types():
    assert [kind_of(n) for n in ("a.JPG", "b.mov", "c.m4a", "d.pdf")] == ["image", "video", "audio", "document"]
    assert mime_of("x.mp4") == "video/mp4" and mime_of("x.mov") == "video/quicktime" and mime_of("x.mp3") == "audio/mpeg"
    assert thumb_size(100) == 128 and thumb_size(300) == 512 and thumb_size(99_999) == 2048


async def test_a_large_upload_streams_to_disk_and_lands_whole(tmp_path):
    home = Home(tmp_path / "h")
    data = bytes(range(256)) * 40_000                              # ~10 MB, arriving in 64 KB pieces
    path = await home.receive_upload("trip.mp4", chunks(data), declared=len(data))
    assert path == "uploads/trip.mp4" and (home.root / path).read_bytes() == data
    assert list((home.root / ".incoming").iterdir()) == []         # the part file was moved, not copied
    assert (await home.receive_upload("trip.mp4", chunks(b"again"))) == "uploads/trip-1.mp4"   # never overwrites
    assert [u["kind"] for u in home.uploads()] == ["video", "video"]


async def test_too_large_no_room_and_empty_uploads_leave_nothing_behind(tmp_path, monkeypatch):
    home = Home(tmp_path / "h")
    monkeypatch.setattr(home_mod, "MAX_UPLOAD", 100_000)
    with pytest.raises(UploadTooLarge):
        await home.receive_upload("a.mp4", chunks(b"x" * 100_001))              # found out while it streams (no length sent)
    with pytest.raises(UploadTooLarge):
        await home.receive_upload("a.mp4", chunks(b"x"), declared=100_001)       # refused before reading anything
    with pytest.raises(HomeError, match="empty"):
        await home.receive_upload("a.mp4", chunks(b""))
    with pytest.raises(HomeError, match="only"):
        await home.receive_upload("a.exe", chunks(b"MZ"))
    monkeypatch.setattr(home_mod, "MAX_UPLOAD", 99_000_000)
    monkeypatch.setattr(home_mod, "disk_free", lambda p: 250_000_000)            # 50 MB above the margin
    with pytest.raises(HomeFull, match="Kelo server"):
        await home.receive_upload("b.mp4", chunks(b"x"), declared=60_000_000)
    monkeypatch.setattr(home_mod, "CHECK_EVERY", 64 * 1024)
    monkeypatch.setattr(home_mod, "disk_free", lambda p: 1_000)                  # the disk fills up while the file arrives
    with pytest.raises(HomeFull, match="Kelo server"):
        await home.receive_upload("c.mp4", chunks(b"x" * 300_000))
    assert home.uploads() == [] and list((home.root / ".incoming").iterdir()) == []


async def test_the_storage_cap_holds_for_streamed_uploads(tmp_path, monkeypatch):
    from assistant.quota import Quota
    home = Home(tmp_path / "h")
    home.quota = Quota(tmp_path, 1_000_000)
    monkeypatch.setattr(home_mod, "CHECK_EVERY", 64 * 1024)
    with pytest.raises(HomeFull, match="Storage is full"):
        await home.receive_upload("big.mp4", chunks(b"x" * 1_500_000))           # no length sent: caught while it streams
    with pytest.raises(HomeFull):
        await home.receive_upload("big.mp4", chunks(b"x"), declared=1_500_000)
    assert home.uploads() == [] and list((home.root / ".incoming").iterdir()) == []
    assert await home.receive_upload("small.m4a", chunks(b"x" * 100_000)) == "uploads/small.m4a"


def test_previews_are_upright_cached_and_only_for_pictures_and_videos(tmp_path):
    home = Home(tmp_path / "h")
    home.save_upload("side.jpg", sideways_photo())
    src = home.upload_path("side.jpg")
    t1 = home.media.thumb(src, 256)
    with Image.open(t1) as im:
        assert im.format == "JPEG" and im.size == (50, 100)                      # turned by its EXIF orientation
    assert home.media.thumb(src, 200) == t1                                      # made once, then kept
    with Image.open(home.media.thumb(src, 64)) as im:
        assert max(im.size) <= 128
    video = make_video(home.root / "uploads" / "clip.mp4")
    with Image.open(home.media.thumb(video, 256)) as im:
        r, g, b = im.convert("RGB").getpixel((im.width // 2, im.height // 2))
        assert im.size == (64, 48) and g > 150 and r < 80                         # a real frame of the clip
    info = home.media.info(video)
    assert 1.5 <= info["duration"] <= 2.5 and (info["width"], info["height"]) == (64, 48)
    from assistant.media import MediaError
    (home.root / "uploads" / "song.mp3").write_bytes(b"ID3 not really")
    with pytest.raises(MediaError):
        home.media.thumb(home.upload_path("song.mp3"), 256)
    home.delete_upload("side.jpg")
    left = list((home.root / ".thumbs").iterdir())
    assert left and not any(p.read_bytes()[:2] == b"\xff\xd8" and Image.open(p).size == (50, 100) for p in left if p.suffix == ".jpg")


async def test_media_api_lists_streams_with_range_and_serves_previews(tmp_path):
    video = make_video(tmp_path / "clip.mp4")
    data = video.read_bytes()
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.put("/api/uploads/clip.mp4", content=data)).status_code == 401
        c.headers.update(AUTH)
        assert (await c.put("/api/uploads/clip.mp4", content=chunks(data, 1000))).json() == {"path": "uploads/clip.mp4", "ok": True}
        assert (await c.put("/api/uploads/side.jpg", content=sideways_photo())).status_code == 200
        rows = {r["path"]: r for r in (await c.get("/api/uploads")).json()}
        v = rows["uploads/clip.mp4"]
        assert v["kind"] == "video" and v["type"] == "video/mp4" and 1.5 <= v["duration"] <= 2.5 and v["width"] == 64
        assert rows["uploads/side.jpg"]["kind"] == "image" and "duration" not in rows["uploads/side.jpg"]
        assert (await c.get("/api/uploads/clip.mp4/file?inline=1")).status_code == 200
        part = await c.get("/api/uploads/clip.mp4/file?inline=1", headers={"Range": "bytes=0-99"})
        assert part.status_code == 206 and part.content == data[:100] and part.headers["content-type"] == "video/mp4"
        assert part.headers["content-disposition"].startswith("inline") and part.headers["x-content-type-options"] == "nosniff"
        assert (await c.get("/api/uploads/clip.mp4/file")).headers["content-disposition"].startswith("attachment")
        th = await c.get("/api/uploads/side.jpg/thumb?size=256")
        assert th.status_code == 200 and th.headers["content-type"] == "image/jpeg" and Image.open(io.BytesIO(th.content)).size == (50, 100)
        assert (await c.get("/api/uploads/clip.mp4/thumb")).status_code == 200
        assert (await c.get("/api/uploads/clip.mp4/text")).status_code == 422
        assert (await c.get("/api/uploads/nope.mp4/thumb")).status_code == 404
        async with httpx.AsyncClient(base_url=srv.url) as anon:
            assert (await anon.get("/api/uploads/side.jpg/thumb")).status_code == 401
            assert (await anon.get("/api/uploads/clip.mp4/file", headers={"Range": "bytes=0-9"})).status_code == 401


async def test_the_agent_knows_videos_are_for_the_user_and_cannot_read_them(tmp_path):
    home = Home(tmp_path / "h")
    make_video(tmp_path / "v.mp4")
    await home.receive_upload("v.mp4", chunks((tmp_path / "v.mp4").read_bytes()))
    block = home.context_block()
    assert "### Videos and sound files the user uploaded" in block and "uploads/v.mp4" in block
    assert "### Documents the user uploaded" not in block
    with pytest.raises(HomeError, match="no text"):
        await run_extraction(home, "v.mp4")


def test_media_cache_survives_a_missing_folder(tmp_path):
    cache = MediaCache(tmp_path / "nothing-here")
    assert cache.clear_stale([]) == 0 and cache.info(tmp_path / "x.pdf") == {}



async def test_the_limit_is_100_mb_and_says_so(tmp_path):
    assert home_mod.MAX_UPLOAD == 99_000_000
    home = Home(tmp_path / "h")
    with pytest.raises(UploadTooLarge, match="100 MB"):
        await home.receive_upload("big.mp4", chunks(b"x"), declared=99_000_001)
    with pytest.raises(UploadTooLarge, match="15 MB"):
        await home.receive_upload("big.jpg", chunks(b"x"), declared=15_000_001)
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.put("/api/uploads/big.mp4", content=b"x", headers={"Content-Length": "1"})
        assert r.status_code == 200
