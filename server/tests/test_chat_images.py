"""chat_images.py: a screenshot of the browser tab shown in the chat. Secure fields are covered before the picture is kept, the
picture shows at once (an `image` turn event) and stays with the reply's message, and clearing the chat removes it."""

import io

import pytest
from PIL import Image

from assistant.agent import ToolOutput
from assistant.agent.loop import current_call, ui_sink
from assistant.chat_images import PER_TURN, ChatImages, cover
from assistant.netguard import NetGuardError, Reply
from assistant.store import Store
from assistant.tools.image_tools import ImageStore


def png(w=400, h=300, color=(255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_secure_fields_are_covered_scaled_to_the_picture_and_a_part_can_be_cropped():
    # a 2x device picture of a 200-px-wide page with a password field at (20, 40) 100x20 CSS px
    jpeg, w, h = cover(png(400, 300), [[20, 40, 100, 20]], 200)
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert (w, h) == (400, 300)
    assert max(img.getpixel((140, 100))) < 80          # inside the field (x 40..240, y 80..120 in device px): covered
    assert min(img.getpixel((300, 250))) > 200         # elsewhere: the page as it was
    part, pw, ph = cover(png(400, 300), [], 200, crop=(100, 100, 50, 25))
    assert (pw, ph) == (100, 50)


async def test_the_screenshot_shows_at_once_and_stays_with_the_reply(tmp_path):
    store = Store()
    images = ChatImages(store, tmp_path / "chat_images")
    events = []

    async def sink(item):
        events.append(item)

    async def grab(selector):
        jpeg, w, h = cover(png(), [], 400)
        return jpeg, w, h, ""

    async def no_page(selector):
        return None, 0, 0, "no page is open in your browser tab: open the page first"
    tool = images.tool(grab, lambda: {"turn_id": "t1"})
    tok, tok2 = ui_sink.set(sink), current_call.set("c1")
    try:
        out = await tool.handler({"caption": "예치금 화면"})
        assert "in the chat now" in out and "covered" in out
        assert (await images.tool(no_page, lambda: {"turn_id": "t1"}).handler({})).startswith("Error: no page")
        assert (await images.tool(grab, lambda: None).handler({})).startswith("Error:")
    finally:
        ui_sink.reset(tok)
        current_call.reset(tok2)
    ev = events[0]
    assert ev["type"] == "image" and ev["url"].startswith("/api/chat-images/img_") and ev["caption"] == "예치금 화면"
    assert images.path(ev["image_id"]).read_bytes()[:2] == b"\xff\xd8"
    mid = store.add_message("assistant", "이 화면이에요.")
    images.attach("t1", mid)
    rows = images.for_messages([{"id": mid, "role": "assistant", "content": "x"}, {"id": mid + 1, "role": "user", "content": "y"}])
    assert rows[0]["images"][0]["id"] == ev["image_id"] and "images" not in rows[1]
    assert images.path("../etc") is None
    images.clear()
    assert images.path(ev["image_id"]) is None and store._all("SELECT COUNT(*) AS n FROM chat_images")[0]["n"] == 0


class Fetch:
    """A fake public fetch: records each call and answers with `reply` (or raises `error`)."""
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if self.error:
            raise self.error
        return self.reply


class Library:
    """Stands in for the library (Deliverables): path() raises for a name it does not hold."""
    def __init__(self, files=None):
        self.files = files or {}

    def path(self, name):
        if name not in self.files:
            raise LookupError(f"no file named {name!r} in your library")
        return self.files[name]


def picture_tool(images, *, library=None, fetch=None, store=None):
    return images.library_tool(library or Library(), lambda: {"turn_id": "t1"}, images=store, fetcher=fetch)


async def run(tool, args):
    """Calls the tool the way a turn does; returns (result, the chat events it emitted)."""
    events = []

    async def sink(item):
        events.append(item)
    tok, tok2 = ui_sink.set(sink), current_call.set("c1")
    try:
        return await tool.handler(args), events
    finally:
        ui_sink.reset(tok)
        current_call.reset(tok2)


async def test_a_web_picture_is_shown_once_and_kept_nowhere(tmp_path):
    store = Store()
    images = ChatImages(store, tmp_path / "chat_images")
    saved = ImageStore(tmp_path / ".tryon")
    fetch = Fetch(Reply(200, {}, png(40, 30), False))
    out, events = await run(picture_tool(images, fetch=fetch, store=saved),
                            {"url": "https://hotels.example/rooms/suite.png", "caption": "the suite"})
    assert isinstance(out, ToolOutput) and out.text.startswith("The picture is in the chat now")
    assert "did NOT see it" in out.no_vision and len(out.images) == 1 and out.images[0].startswith("data:image/jpeg;base64,")
    c = fetch.calls[0]
    assert c["url"] == "https://hotels.example/rooms/suite.png" and c["allowed_hosts"] == ["hotels.example"]
    assert c["max_body"] == 10 * 1024 * 1024 and c["headers"]["Accept"] == "image/png,image/jpeg,image/webp"
    assert len(events) == 1 and events[0]["type"] == "image" and events[0]["caption"] == "the suite"
    assert images.count_for("t1") == 1 and images.path(events[0]["image_id"]) is not None
    assert saved.names() == [] and sorted(p.name for p in tmp_path.iterdir()) == ["chat_images"]   # nothing kept but the chat's copy


@pytest.mark.parametrize("url", ["http://hotels.example/room.png", "https://10.0.0.5/room.png", "https://user@hotels.example/room.png"])
async def test_only_public_https_addresses_are_fetched(tmp_path, url):
    images = ChatImages(Store(), tmp_path / "chat_images")
    out, events = await run(picture_tool(images), {"url": url})   # the real netguard fetch: refused before any connection
    assert isinstance(out, str) and out.startswith("Error:") and events == [] and images.count_for("t1") == 0


@pytest.mark.parametrize("reply,text", [
    (Reply(200, {}, b"<html>not a picture</html>", False), "does not hold a JPG, PNG or WebP picture"),
    (Reply(200, {}, b"GIF89a" + b"\x00" * 50, False), "does not hold a JPG, PNG or WebP picture"),
    (Reply(404, {}, b"", False), "HTTP 404"),
    (Reply(200, {}, png(), True), "larger than 10 MB"),
])
async def test_a_bad_web_answer_shows_nothing(tmp_path, reply, text):
    images = ChatImages(Store(), tmp_path / "chat_images")
    out, events = await run(picture_tool(images, fetch=Fetch(reply)), {"url": "https://hotels.example/room.png"})
    assert text in out and out.startswith("Error:") and events == []


async def test_a_refused_fetch_shows_its_reason_and_nothing(tmp_path):
    images = ChatImages(Store(), tmp_path / "chat_images")
    fetch = Fetch(error=NetGuardError("hotels.example resolves to a non-public address (10.0.0.5); refused"))
    out, events = await run(picture_tool(images, fetch=fetch), {"url": "https://hotels.example/room.png"})
    assert out.startswith("Error: hotels.example resolves") and events == []


async def test_a_saved_photo_is_shown_by_name_and_an_unknown_name_lists_the_saved_ones(tmp_path):
    images = ChatImages(Store(), tmp_path / "chat_images")
    saved = ImageStore(tmp_path / ".tryon")
    saved.save("hotel-room.png", png(40, 30))
    tool = picture_tool(images, store=saved)
    out, events = await run(tool, {"name": "hotel-room.png", "caption": "room 1204"})
    assert isinstance(out, ToolOutput) and events[0]["caption"] == "room 1204" and images.count_for("t1") == 1
    out, events = await run(tool, {"name": "nope.png"})
    assert out == "Error: no picture named nope.png in the library or the saved photos. Saved photos: ['hotel-room.png']"
    assert events == [] and saved.names() == ["hotel-room.png"]


async def test_a_library_file_is_shown_by_name(tmp_path):
    door = tmp_path / "door.png"
    door.write_bytes(png(40, 30))
    note = tmp_path / "note.txt"
    note.write_bytes(b"hello")
    images = ChatImages(Store(), tmp_path / "chat_images")
    tool = picture_tool(images, library=Library({"door.png": door, "note.txt": note}))
    out, events = await run(tool, {"name": "door.png"})
    assert isinstance(out, ToolOutput) and len(events) == 1 and images.count_for("t1") == 1
    out, events = await run(tool, {"name": "note.txt"})
    assert out.startswith("Error: that is not a picture") and events == []


async def test_name_or_url_means_exactly_one(tmp_path):
    images = ChatImages(Store(), tmp_path / "chat_images")
    fetch = Fetch(Reply(200, {}, png(), False))
    tool = picture_tool(images, fetch=fetch)
    for args in ({}, {"name": "a.png", "url": "https://a.example/a.png"}, {"name": " ", "url": ""}):
        assert (await run(tool, args))[0] == "Error: give name or url (exactly one of them)."
    assert fetch.calls == []


async def test_the_per_reply_cap_counts_web_pictures_and_refuses_before_fetching(tmp_path):
    images = ChatImages(Store(), tmp_path / "chat_images")
    fetch = Fetch(Reply(200, {}, png(), False))
    tool = picture_tool(images, fetch=fetch)
    for _ in range(PER_TURN):
        out, _events = await run(tool, {"url": "https://a.example/p.png"})
        assert isinstance(out, ToolOutput)
    out, events = await run(tool, {"url": "https://a.example/p.png"})
    assert out == f"Error: at most {PER_TURN} pictures in one reply." and events == [] and len(fetch.calls) == PER_TURN
