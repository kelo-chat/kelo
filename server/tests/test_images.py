import io

import httpx
import pytest
from PIL import Image

from assistant.netguard import NetGuardError, Reply
from assistant.server import Settings, create_app
from assistant.tools.image_tools import ImageStore, image_tools

from test_server import AUTH, TOKEN, Running


def png(w=40, h=100):
    b = io.BytesIO()
    im = Image.new("RGB", (w, h), "white")
    im.paste((255, 0, 0), (0, 0, w, h // 2))      # top half red, bottom half white
    im.save(b, "PNG")
    return b.getvalue()


class Fake:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if self.error:
            raise self.error
        return self.reply


def tools(tmp_path, fake):
    st = ImageStore(tmp_path / ".tryon")
    return {t.name: t for t in image_tools(st, fake)}, st


async def test_a_photo_is_saved_and_the_top_can_be_cropped_off(tmp_path):
    fake = Fake(Reply(200, {}, png(), False))
    t, st = tools(tmp_path, fake)
    out = await t["save_product_image"].handler({"url": "https://cdn.shop.example/p/1.png", "name": "Blue Shirt!", "crop_top_percent": 50})
    assert out.startswith("saved blue-shirt.png") and fake.calls[0]["allowed_hosts"] == ["cdn.shop.example"]
    im = Image.open(st.path("blue-shirt.png"))
    assert im.size == (40, 50) and im.getpixel((5, 5)) == (255, 255, 255)        # the red half is gone
    assert "blue-shirt.png" in await t["list_product_images"].handler({})


async def test_a_saved_photo_is_named_for_show_picture_not_by_its_try_on_path(tmp_path):
    t, _ = tools(tmp_path, Fake(Reply(200, {}, png(), False)))
    out = await t["save_product_image"].handler({"url": "https://cdn.shop.example/p/1.png"})
    assert out == "saved cdn.png (" + str(len(png())) + " bytes). Show it in the chat with show_picture name=cdn.png."
    assert "/api/tryon" not in out


async def test_without_a_crop_the_original_bytes_are_kept(tmp_path):
    t, st = tools(tmp_path, Fake(Reply(200, {}, png(), False)))
    await t["save_product_image"].handler({"url": "https://cdn.shop.example/p/1.png"})
    assert st.path("cdn.png").read_bytes() == png()


@pytest.mark.parametrize("reply,error,text", [
    (Reply(200, {}, b"<html>not an image</html>", False), None, "not a readable"),
    (Reply(200, {}, b"GIF89a" + b"\x00" * 50, False), None, "not a readable"),
    (Reply(404, {}, b"", False), None, "HTTP 404"),
    (Reply(200, {}, b"x", True), None, "too large"),
    (None, NetGuardError("a.example resolves to a non-public address (10.0.0.5); refused"), "non-public"),
])
async def test_bad_downloads_save_nothing(tmp_path, reply, error, text):
    t, st = tools(tmp_path, Fake(reply, error))
    assert text in await t["save_product_image"].handler({"url": "https://a.example/x.png"})
    assert st.names() == []


async def test_bad_crop_values_are_refused_before_downloading(tmp_path):
    fake = Fake(Reply(200, {}, png(), False))
    t, _ = tools(tmp_path, fake)
    for bad in (61, -1, "abc"):
        assert (await t["save_product_image"].handler({"url": "https://a.example/x.png", "crop_top_percent": bad})).startswith("error:")
    assert fake.calls == []


async def test_images_are_served_only_with_the_token_and_only_by_plain_names(tmp_path):
    home = tmp_path / "home"
    ImageStore(home / ".tryon").root.mkdir(parents=True)
    (home / ".tryon" / "shirt.png").write_bytes(png())
    (home / "secret.txt").write_text("nope")
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(home)))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/tryon/shirt.png")).status_code == 401
        c.headers.update(AUTH)
        r = await c.get("/api/tryon/shirt.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == png()
        for bad in ("nope.png", "..%2Fsecret.txt", "shirt.svg", "SHIRT.png"):
            assert (await c.get(f"/api/tryon/{bad}")).status_code == 404


# ---- generation
import base64
import json

from assistant.store import Store

KEY = "sk-img-ABCDEF123456"


def gen_tools(tmp_path, fake):
    st = Store()
    st.save_credential("imagen", "Images", ["api.openai.com"], {"type": "bearer_header"})
    st.set_credential_secret("imagen", KEY)
    store = ImageStore(tmp_path / ".tryon")
    return {t.name: t for t in image_tools(store, fake, creds=st)}, store


async def test_generation_uses_the_connected_service_and_keeps_the_picture(tmp_path):
    fake = Fake(Reply(200, {}, json.dumps({"data": [{"b64_json": base64.b64encode(png()).decode()}]}).encode(), False))
    t, store = gen_tools(tmp_path, fake)
    out = await t["generate_image"].handler({"credential": "imagen", "prompt": "a lake at night", "name": "Cover 1"})
    c = fake.calls[0]
    assert out.startswith("saved cover-1.png") and c["url"] == "https://api.openai.com/v1/images/generations" and c["headers"]["Authorization"] == f"Bearer {KEY}"
    assert json.loads(c["content"]) == {"model": "gpt-image-1", "prompt": "a lake at night", "size": "1024x1024", "n": 1}
    assert store.path("cover-1.png").read_bytes() == png() and t["generate_image"].requires_approval
    assert out == "saved cover-1.png. Show it in the chat with show_picture name=cover-1.png." and "/api/tryon" not in out


async def test_a_returned_link_is_downloaded_without_the_key_and_failures_hide_it(tmp_path):
    calls = []

    async def fetcher(method, url, **kw):
        calls.append((method, url, kw.get("headers")))
        if method == "POST":
            return Reply(200, {}, json.dumps({"data": [{"url": "https://cdn.img.example/x.png"}]}).encode(), False)
        return Reply(200, {}, png(), False)

    t, store = gen_tools(tmp_path, fetcher)
    assert (await t["generate_image"].handler({"credential": "imagen", "prompt": "p"})).startswith("saved generated.png")
    assert calls[1][1] == "https://cdn.img.example/x.png" and not calls[1][2]            # no Authorization to the download host

    t, store = gen_tools(tmp_path, Fake(Reply(401, {}, f"bad key {KEY}".encode(), False)))
    out = await t["generate_image"].handler({"credential": "imagen", "prompt": "p"})
    assert "401" in out and KEY not in out
    t, _ = gen_tools(tmp_path, Fake(Reply(200, {}, b'{"data":[{"b64_json":"!!!"}]}', False)))
    assert "not an image" in await t["generate_image"].handler({"credential": "imagen", "prompt": "p"})


async def test_generation_refuses_bad_input_before_spending(tmp_path):
    fake = Fake(Reply(200, {}, b"{}", False))
    t, _ = gen_tools(tmp_path, fake)
    for bad in ({"credential": "ghost", "prompt": "p"}, {"credential": "imagen", "prompt": ""}, {"credential": "imagen", "prompt": "p", "size": "huge"},
                {"credential": "imagen", "prompt": "p", "path": "/a/../b"}):
        assert (await t["generate_image"].handler(bad)).startswith("error:"), bad
    assert fake.calls == []
