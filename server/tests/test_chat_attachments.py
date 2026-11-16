"""Files the user sends with a chat message (chat_attachments.py, backlog 26): kept with the message, returned in GET /api/messages
and as the reply's first event, thumbnails behind the API's auth, the auto "read and summarise" sentence marked for the apps."""

import io

import httpx
from PIL import Image

from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, follow


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def app_for(tmp_path):
    async def reply(provider, request, **kw):
        yield TextDelta("빨간 사진이에요.")
        yield Done("stop")
    home = tmp_path / "home"
    home.mkdir()
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(home)),
                      tools=[], stream_fn=reply)


async def test_a_photo_and_a_file_go_with_the_message_and_survive_a_reload(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        photo = (await c.put("/api/uploads/red.jpg", content=jpeg())).json()["path"]
        doc = (await c.put("/api/uploads/notes.pdf", content=b"%PDF-1.4\n%%EOF\n")).json()["path"]
        tid = (await c.post("/api/turns", json={"text": "red.jpg, notes.pdf 내용을 읽고 요약해줘.", "auto_text": True,
                                               "attachments": [{"path": photo}, {"path": doc}]})).json()["id"]
        events = await follow(c, tid)
        first = events[0]
        assert first["type"] == "user_message" and first["auto_text"] is True
        assert [(a["name"], a["kind"]) for a in first["attachments"]] == [("red.jpg", "image"), ("notes.pdf", "document")]
        assert first["attachments"][0]["thumb"] == "/api/uploads/red.jpg/thumb" and first["attachments"][1]["thumb"] is None
        msgs = (await c.get("/api/messages")).json()
        user = [m for m in msgs if m["role"] == "user"][-1]
        assert user["id"] == first["message_id"] and user["attachments"] == first["attachments"] and user["auto_text"] is True
        assert user["content"].startswith("red.jpg")                      # the agent still gets the sentence with the names
        assert all("attachments" not in m for m in msgs if m["role"] == "assistant")
        thumb = await c.get(first["attachments"][0]["thumb"])
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
        async with httpx.AsyncClient(base_url=srv.url) as anon:
            assert (await anon.get(first["attachments"][0]["thumb"])).status_code == 401   # behind the API's auth
        # a message without files: no event, empty list
        events = await follow(c, (await c.post("/api/turns", json={"text": "고마워"})).json()["id"])
        assert all(e["type"] != "user_message" for e in events)
        last = [m for m in (await c.get("/api/messages")).json() if m["role"] == "user"][-1]
        assert last["attachments"] == [] and last["auto_text"] is False
        # clearing the chat drops the lists (the files stay)
        await c.delete("/api/messages")
        assert (await c.get("/api/uploads/red.jpg/file")).status_code == 200


async def test_something_that_is_not_an_upload_is_refused_before_the_message_is_kept(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        for bad in ("uploads/missing.jpg", "../s.db", "uploads/../../s.db", ".hidden"):
            r = await c.post("/api/turns", json={"text": "이거 봐", "attachments": [{"path": bad}]})
            assert r.status_code == 422, bad
        r = await c.post("/api/turns", json={"text": "x", "attachments": [{"path": "uploads/a.jpg"}] * 11})
        assert r.status_code == 422
        assert (await c.get("/api/messages")).json() == []
