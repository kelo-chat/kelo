"""Choice cards (goals.py clean_cards / quick_replies, chat_images.card_pictures): validation, pictures fetched as thumbnails and
stored as chat images that go with the message, the emitted event and the stored shape, and old-shape questions unchanged."""

import io
import json

import pytest
from PIL import Image

from assistant.agent.loop import current_call, ui_sink
from assistant.chat_images import CARD_SIDE, ChatImages
from assistant.goals import GoalBook, GoalError
from assistant.netguard import Reply
from assistant.store import Store


def png(w=1000, h=500) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def fetcher(bad=()):
    async def fetch(method, url, **kw):
        if any(b in url for b in bad):
            return Reply(404, {}, b"", False)
        return Reply(200, {}, png(), False)
    return fetch


def setup(tmp_path, bad=()):
    store = Store()
    book = GoalBook(store, None)
    images = ChatImages(store, tmp_path / "ci")
    got = []

    async def card_images(tid, urls):
        return await images.card_pictures(tid, urls, fetcher(bad))
    tool = next(t for t in book.tools(lambda: {"turn_id": "t1"}, lambda t, q, g: got.append((t, q, g)), card_images=card_images)
                if t.name == "quick_replies")
    return store, book, images, tool, got


async def call(tool, args):
    events = []

    async def sink(item):
        events.append(item)
    tok, tok2 = ui_sink.set(sink), current_call.set("c1")
    try:
        return await tool.handler(args), events
    finally:
        ui_sink.reset(tok)
        current_call.reset(tok2)


def q(**kw):
    return {"question": "Which?", "options": ["A", "B", "C"], **kw}


def test_cards_are_validated_and_clipped():
    clean = GoalBook.clean_questions
    out = clean([q(cards=[{"image": "https://x.test/a.jpg", "detail": "d" * 200, "recommended": True}, {}, {"detail": "  1,000 · Tue "}])])
    cards = out[0]["cards"]
    assert cards[0] == {"image": "https://x.test/a.jpg", "detail": "d" * 80, "recommended": True}
    assert cards[1] == {} and cards[2] == {"detail": "1,000 · Tue"}
    assert out[0]["options"] == ["A", "B", "C"] and "kind" not in out[0]
    for bad in ([{}], [{}, {}, {}, {}], [{"image": "http://x.test/a.jpg"}, {}, {}], [{"recommended": True}, {"recommended": True}, {}],
                ["x", {}, {}]):
        with pytest.raises(GoalError):
            clean([q(cards=bad)])
    with pytest.raises(GoalError):
        clean([{"question": "Q", "kind": "text", "cards": [{}]}])
    with pytest.raises(GoalError):
        clean([{"question": "Q", "options": list("abcdefg"), "cards": [{}] * 7}])
    assert "cards" not in clean([q(cards=[{}, {}, {}])])[0]            # nothing to show: plain buttons
    assert clean([q(cards=None)]) == clean([q()]) == [{"question": "Which?", "options": ["A", "B", "C"]}]   # old shape unchanged


def test_cards_follow_options_after_cleaning():
    out = GoalBook.clean_questions([{"question": "Q", "options": ["A", "A", "B"], "cards": [{"detail": "x"}, {"detail": "y"}, {"detail": "z"}]}])
    assert out[0]["options"] == ["A", "B"] and [c["detail"] for c in out[0]["cards"]] == ["x", "z"]


async def test_pictures_become_thumbnails_stored_with_the_message(tmp_path):
    store, book, images, tool, got = setup(tmp_path, bad=("bad",))
    text, events = await call(tool, {"questions": [q(cards=[
        {"image": "https://x.test/a.jpg", "detail": "9,900 · Tue", "recommended": True}, {"image": "https://x.test/bad.jpg"}, {"detail": "no picture"}])]})
    assert "could not be loaded" in text and "B" in text
    tid, qs, _ = got[0]
    cards = qs[0]["cards"]
    pic = cards[0]["image"]
    assert set(pic) == {"image_id", "url", "width", "height"} and pic["url"] == f"/api/chat-images/{pic['image_id']}"
    assert max(pic["width"], pic["height"]) == CARD_SIDE
    assert Image.open(io.BytesIO(images.path(pic["image_id"]).read_bytes())).size == (pic["width"], pic["height"])
    assert cards[1] == {} and cards[2] == {"detail": "no picture"} and cards[0]["recommended"] is True
    assert events[0]["type"] == "replies" and events[0]["questions"][0]["cards"] == cards
    assert images.count_for("t1") == 0                                   # card thumbnails do not count toward PER_TURN
    mid = store.add_message("assistant", "Pick one")
    images.attach("t1", mid)
    assert store._all("SELECT message_id FROM chat_images WHERE id = ?", (pic["image_id"],))[0]["message_id"] == mid
    assert "images" not in images.for_messages([{"id": mid, "role": "assistant", "content": "x"}])[0]   # not the message's own pictures
    book.attach_replies(mid, qs, None)
    shown = book.replies_for([{"id": mid, "role": "assistant", "content": "x"}])[0]["replies"]["questions"][0]
    assert shown["cards"][0]["image"]["url"] == pic["url"] and json.dumps(shown)   # what GET /api/messages returns
    images.clear()
    assert images.path(pic["image_id"]) is None


async def test_all_pictures_failing_leaves_plain_buttons_with_details(tmp_path):
    _, _, _, tool, got = setup(tmp_path, bad=("x.test",))
    text, _ = await call(tool, {"questions": [q(cards=[{"image": "https://x.test/1.jpg"}, {"image": "https://x.test/2.jpg"}, {}])]})
    assert "cards" not in got[0][1][0] and "A, B" in text


async def test_a_question_without_cards_is_as_before(tmp_path):
    _, _, _, tool, got = setup(tmp_path)
    text, events = await call(tool, {"questions": [q()]})
    assert got[0][1] == [{"question": "Which?", "options": ["A", "B", "C"]}] and "could not be loaded" not in text
    assert events[0]["questions"] == got[0][1]
