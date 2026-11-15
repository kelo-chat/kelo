"""chat_images.py: a screenshot of the browser tab shown in the chat. Secure fields are covered before the picture is kept, the
picture shows at once (an `image` turn event) and stays with the reply's message, and clearing the chat removes it."""

import io

from PIL import Image

from assistant.agent.loop import current_call, ui_sink
from assistant.chat_images import ChatImages, cover
from assistant.store import Store


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
