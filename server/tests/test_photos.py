"""Photos from the phone: JPEG / PNG / WebP / HEIC uploads (size cap, GPS removed, HEIC to JPEG or a clear refusal), and how the model
looks at one (view_image: the picture for a model that sees images, a plain explanation for one that does not), including the
transports that carry pictures."""

import io
import json

import httpx
import pytest
from PIL import Image

from assistant import photos
from assistant.agent import ToolOutput, ToolResult, run_agent
from assistant.home import Home, HomeError
from assistant.providers import Done, TextDelta, ToolCall
from assistant.providers.transports import anthropic_messages, bedrock_converse, responses
from assistant.providers.vision import supports_images
from assistant.server import Settings, create_app
from assistant.tools.extract import extract_tool
from assistant.tools.home_tools import NO_VISION, home_tools

from test_server import AUTH, TOKEN, Running, scripted
from test_extract import CONTENT

GPS = 0x8825


def picture(fmt="JPEG", gps=True, size=(64, 48)):
    img = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "TestMake"
    if gps:
        exif[GPS] = {1: "N", 2: (37.0, 33.0, 0.0), 3: "E", 4: (126.0, 58.0, 0.0)}
    buf = io.BytesIO()
    img.save(buf, fmt, exif=exif.tobytes())
    return buf.getvalue()


def exif_of(data):
    return Image.open(io.BytesIO(data)).getexif()


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path / "h")


def test_photos_are_kept_without_their_location(home):
    for fmt, name in (("JPEG", "beach.jpeg"), ("PNG", "screen.png"), ("WEBP", "cat.webp")):
        raw = picture(fmt)
        assert GPS in exif_of(raw)
        path = home.save_upload(name, raw)
        kept = (home.root / path).read_bytes()
        assert GPS not in exif_of(kept) and exif_of(kept).get(0x010F) == "TestMake", fmt   # the rest of the EXIF stays
        assert Image.open(io.BytesIO(kept)).format == fmt
    assert home.save_upload("beach.jpeg", picture()) == "uploads/beach-1.jpg"            # .jpeg kept as .jpg, never overwritten
    clean = picture("PNG", gps=False)
    assert (home.root / home.save_upload("plain.png", clean)).read_bytes() == clean          # nothing to remove: the file as sent
    kinds = {u["path"]: u["kind"] for u in home.uploads()}
    assert kinds["uploads/beach.jpg"] == "image"
    home.save_upload("r.pdf", b"%PDF-1.4")
    assert {u["path"]: u["kind"] for u in home.uploads()}["uploads/r.pdf"] == "document"


def test_heic_becomes_jpeg_when_the_library_is_there_and_is_refused_clearly_otherwise(home, monkeypatch):
    if photos.heic_supported():
        import pillow_heif
        pillow_heif.register_heif_opener()
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (10, 200, 10)).save(buf, "HEIF")
        path = home.save_upload("IMG_0001.HEIC", buf.getvalue())
        assert path == "uploads/IMG_0001.jpg" and Image.open(home.root / path).format == "JPEG"
    monkeypatch.setattr(photos, "heic_supported", lambda: False)
    with pytest.raises(HomeError, match="HEIC photos cannot be read on this server. Send the photo as JPEG"):
        home.save_upload("IMG_0002.heic", b"\x00\x00\x00\x18ftypheic")


def test_broken_or_oversized_photos_are_refused(home):
    for name, data in (("fake.jpg", b"<html>not a picture</html>"), ("empty.png", b""), ("gif.webp", b"GIF89a\x01\x00")):
        with pytest.raises(HomeError):
            home.save_upload(name, data)
    with pytest.raises(HomeError, match="15 MB"):
        home.save_upload("huge.jpg", b"\xff" * (photos.MAX_IMAGE + 1))
    gif = io.BytesIO()
    Image.new("RGB", (4, 4)).save(gif, "GIF")
    with pytest.raises(HomeError, match="only JPEG, PNG, WebP and HEIC"):
        home.save_upload("anim.jpg", gif.getvalue())                                         # decoded, not trusted by its name
    assert home.uploads() == []


async def test_photo_upload_api(tmp_path):
    s = Settings(token=TOKEN, cdp_host=None, home_dir=str(tmp_path / "h"), content_dir=str(CONTENT))
    async with Running(create_app(s, tools=None, stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.put("/api/uploads/a.jpg", content=picture())).status_code == 401
        c.headers.update(AUTH)
        r = await c.put("/api/uploads/a.jpg", content=picture())
        assert r.json() == {"path": "uploads/a.jpg", "ok": True}
        assert (await c.put("/api/uploads/big.jpg", content=b"x" * (photos.MAX_IMAGE + 10))).status_code == 413
        assert (await c.put("/api/uploads/x.png", content=b"nope")).status_code == 422
        listed = (await c.get("/api/uploads")).json()
        assert [(u["path"], u["kind"]) for u in listed] == [("uploads/a.jpg", "image")]
        f = await c.get("/api/uploads/a.jpg/file")
        assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg" and GPS not in exif_of(f.content)
        assert (await c.get("/api/uploads/a.jpg/text")).status_code == 422
        assert (await c.delete("/api/uploads/a.jpg")).json() == {"ok": True}


async def test_the_model_reads_about_photos_and_documents_tools_point_to_view_image(home):
    home.save_upload("receipt.jpg", picture())
    assert "### Photos the user uploaded\nLook with view_image: uploads/receipt.jpg" in home.context_block()
    tools = {t.name: t for t in home_tools(home, None)}
    assert "view_image" in await tools["read_file"].handler({"path": "uploads/receipt.jpg"})
    assert "photo from the user: view_image uploads/receipt.jpg" in await tools["list_files"].handler({})
    assert "view_image" in await extract_tool(home).handler({"path": "receipt.jpg"})


async def test_view_image_returns_a_scaled_picture(home):
    home.save_upload("big.png", picture("PNG", gps=False, size=(3000, 2000)))
    home.save_upload("r.pdf", b"%PDF-1.4")
    view = {t.name: t for t in home_tools(home, None)}["view_image"]
    out = await view.handler({"name": "uploads/big.png"})
    assert isinstance(out, ToolOutput) and "3000x2000" in out.text and "1568x1045" in out.text and out.no_vision == NO_VISION
    shown = Image.open(io.BytesIO(__import__("base64").b64decode(out.images[0].split(",", 1)[1])))
    assert out.images[0].startswith("data:image/jpeg;base64,") and max(shown.size) == 1568
    assert (await view.handler({"name": "r.pdf"})).startswith("error: r.pdf is a document")
    assert (await view.handler({"name": "missing.jpg"})).startswith("error: no uploaded document")


def test_view_bytes_makes_a_jpeg_url_from_picture_bytes_and_refuses_what_is_not_a_picture():
    url, w, h, sw, sh = photos.view_bytes(picture(fmt="PNG", gps=False, size=(64, 48)))
    assert url.startswith("data:image/jpeg;base64,") and (w, h, sw, sh) == (64, 48, 64, 48)
    with pytest.raises(photos.PhotoError):
        photos.view_bytes(b"hello, not a picture")


def recording(*turns):
    seen = []
    it = iter(turns)

    async def fn(provider, request, **kw):
        seen.append(request.messages)
        for ev in next(it):
            yield ev
    return fn, seen


async def test_the_loop_shows_the_picture_only_to_a_model_that_sees_images(home):
    home.save_upload("cat.jpg", picture(gps=False))
    tools = home_tools(home, None)
    for images_ok in (True, False):
        fn, seen = recording([ToolCall("c1", "view_image", '{"name": "cat.jpg"}'), Done("tool_calls")], [TextDelta("a red square"), Done("stop")])
        results = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "what is in my photo?"}], tools, stream_fn=fn,
                                                 images_ok=images_ok) if isinstance(ev, ToolResult)]
        second = seen[1]
        tool_msg = next(m for m in second if m["role"] == "tool")
        if images_ok:
            pic = second[-1]
            assert pic["role"] == "user" and pic["content"][0]["text"].startswith("[Not from the user")
            assert pic["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            assert NO_VISION not in tool_msg["content"]
        else:
            assert all(isinstance(m.get("content"), (str, type(None))) for m in second)        # no picture went anywhere
            assert NO_VISION in tool_msg["content"] and NO_VISION in results[0].output
        assert "data:image" not in results[0].output                                         # the chat never gets the bytes
        assert results[0].pictures == (1 if images_ok else 0)                                 # what the model was shown, for the steps line


def test_which_providers_get_pictures():
    assert supports_images("claude-cli") and supports_images("anthropic") and supports_images("openai") and supports_images("gemini")
    assert not supports_images("agy") and not supports_images("deepseek") and not supports_images("no-such-provider")


IMG = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}
MSGS = [{"role": "user", "content": "look"}, {"role": "user", "content": [{"type": "text", "text": "[picture]"}, IMG]}]


def test_transports_carry_pictures_in_their_own_shape():
    _, out = anthropic_messages.convert_messages(MSGS)
    assert out[0]["content"][-1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    assert out[0]["content"][-2] == {"type": "text", "text": "[picture]"}
    _, out = bedrock_converse.convert_messages(MSGS)
    assert out[0]["content"][-1]["image"]["format"] == "png" and out[0]["content"][-1]["image"]["source"]["bytes"].startswith(b"\x89PNG")
    _, items = responses.convert_input(MSGS)
    assert items[1]["content"] == [{"type": "input_text", "text": "[picture]"}, {"type": "input_image", "image_url": IMG["image_url"]["url"]}]
    json.dumps(items)


async def test_a_picture_from_an_earlier_turn_can_be_opened_again(home):
    """The next turn reads the steps line of the last reply: it names the call that showed the picture, and that call (with a fresh
    tool set, as the next turn has) gives the picture again. The photo stays in the home context too."""
    from assistant.reply_text import ReplyText
    home.save_upload("cat.jpg", picture(gps=False))
    said = ReplyText()
    said.feed({"type": "tool_started", "id": "c1", "name": "view_image", "arguments": '{"name": "cat.jpg"}'})
    said.feed({"type": "tool_result", "id": "c1", "name": "view_image", "output": "a photo", "pictures": 1})
    said.feed({"type": "text", "text": "A cat."})
    said.feed({"type": "done"})
    note = said.steps_text()
    assert "not attached any more" in note
    args = json.loads(note[note.index("{"):])
    fresh = {t.name: t for t in home_tools(home, None)}
    out = await fresh["view_image"].handler(args)
    assert isinstance(out, ToolOutput) and len(out.images) == 1 and out.images[0].startswith("data:image/jpeg;base64,")
    assert "### Photos the user uploaded" in home.context_block() and "cat.jpg" in home.context_block()


def test_a_tool_result_says_how_many_pictures_it_showed_only_when_there_were_some():
    from assistant.server import serialize
    shown = serialize(ToolResult("1", "view_image", "a photo", pictures=2))
    assert shown["pictures"] == 2
    assert "pictures" not in serialize(ToolResult("2", "view_image", "a photo"))

