"""A reply never names the model's sandbox folder: a sandbox path that Kelo's home maps to one of Kelo's files becomes that file's
relative name in backticks (reply_paths.py). Streamed in pieces, a path is held back until it is complete."""

import httpx

from assistant.home import Home
from assistant.providers import Done, TextDelta
from assistant.reply_paths import ReplyPaths
from assistant.server import create_app

from test_server import AUTH, BODY, Running, read_events, settings

SANDBOX = "/tmp/hermes_agy_8ylvkxb7/notes/lotto.md"


def mapped(tmp_path):
    home = Home(tmp_path / "home")
    home.write("notes/lotto.md", "# lotto")
    return ReplyPaths(home.relativize)


def stream(paths, pieces):
    out = "".join(paths.feed(p) for p in pieces)
    return out + paths.flush()


def test_a_markdown_link_to_a_sandbox_file_is_kelos_file_name_in_code(tmp_path):
    paths = mapped(tmp_path)
    text = f"번호는 [lotto.md](file://{SANDBOX})에 기록해 두었어요."
    assert stream(paths, [text]) == "번호는 `notes/lotto.md`에 기록해 두었어요."


def test_a_bare_sandbox_path_is_kelos_file_name_in_code(tmp_path):
    paths = mapped(tmp_path)
    assert stream(paths, [f"기록은 {SANDBOX} 에 있어요."]) == "기록은 `notes/lotto.md` 에 있어요."
    assert stream(paths, [f"Saved at {SANDBOX}."]) == "Saved at `notes/lotto.md`."     # the sentence's full stop is not in the name


def test_a_path_already_in_a_code_span_keeps_one_code_span(tmp_path):
    paths = mapped(tmp_path)
    assert stream(paths, [f"파일: `{SANDBOX}`"]) == "파일: `notes/lotto.md`"


def test_a_path_split_across_deltas_is_rewritten_like_the_whole_text(tmp_path):
    text = f"번호는 [lotto.md](file://{SANDBOX})에 기록. 경로 {SANDBOX} 도요."
    want = "번호는 `notes/lotto.md`에 기록. 경로 `notes/lotto.md` 도요."
    assert stream(mapped(tmp_path), [text]) == want
    assert stream(mapped(tmp_path), list(text)) == want                       # one character per delta
    assert stream(mapped(tmp_path), ["번호는 [lotto.md](file:///tmp/herm", "es_agy_8ylvkxb7/notes/lo", "tto.md)에"]) == \
        "번호는 `notes/lotto.md`에"


def test_text_that_is_not_yet_a_path_is_passed_on_at_once(tmp_path):
    paths = mapped(tmp_path)
    assert paths.feed("안녕하세요") == "안녕하세요"
    assert paths.feed(" 확인") == " 확인"


def test_ordinary_links_and_urls_are_untouched(tmp_path):
    paths = mapped(tmp_path)
    text = ("[kelo](https://kelo.chat) and https://kelo.chat/tmp/hermes_agy_8ylvkxb7/notes/lotto.md and "
            "[docs](https://kelo.chat/notes/lotto.md) plus /tmp/other/lotto.md")
    assert stream(paths, list(text)) == text


def test_a_sandbox_path_that_is_not_kelos_file_is_left_alone(tmp_path):
    paths = mapped(tmp_path)
    text = "[x](file:///tmp/hermes_agy_8ylvkxb7/other/y.md) and /tmp/hermes_agy_8ylvkxb7/other/y.md"
    assert stream(paths, list(text)) == text


def test_without_a_home_nothing_is_rewritten():
    paths = ReplyPaths(None)
    assert paths.feed(f"[a](file://{SANDBOX})") == f"[a](file://{SANDBOX})"
    assert paths.flush() == ""


async def test_the_reply_text_as_it_is_sent_has_no_sandbox_path(tmp_path):
    home_dir = tmp_path / "home"
    Home(home_dir).write("notes/lotto.md", "# lotto")

    async def fn(provider, request, **kw):
        for piece in ["번호는 [lotto.md](file:///tmp/hermes_agy_8ylvkxb7/notes/", "lotto.md)에 기록해 두었어요.", Done("stop")]:
            yield TextDelta(piece) if isinstance(piece, str) else piece

    async with Running(create_app(settings(home_dir=str(home_dir)), tools=[], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY) as r:
            ev = await read_events(r)
    text = "".join(e["text"] for e in ev if e["type"] == "text")
    assert text == "번호는 `notes/lotto.md`에 기록해 두었어요."
    assert ev[-1]["type"] == "done"
