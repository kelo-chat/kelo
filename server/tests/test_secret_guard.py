"""Secrets pasted into chat (secret_guard.py, server.py secret_check): found before the message reaches a model or storage, with few
false positives, and never logged."""

import logging

import httpx
import pytest

from assistant import secret_guard as sg
from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app
from assistant.store import Store
from test_server import AUTH, TOKEN, Running, read_events

ANTHROPIC = "sk-ant-api03-" + "Zq7xW2mK9pL4vN8rT1yB6cD3fG5hJ0sA"

FOUND = [
    # (text, the value that must be found, kind, (target type, id), label)
    (f"내 Anthropic 키는 {ANTHROPIC} 이거야", ANTHROPIC, "api_key", ("provider", "anthropic"), "Anthropic"),
    ("openai key sk-proj-AbC123dEf456GhI789jKl012MnO345pQr", "sk-proj-AbC123dEf456GhI789jKl012MnO345pQr", "api_key", ("provider", "openai"), "OpenAI"),
    ("ghp_aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5", "ghp_aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5", "token", ("vault", ""), "GitHub"),
    ("token github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyzABCDEF.",
     "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyzABCDEF", "token", ("vault", ""), "GitHub"),
    ("slack xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx", "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx", "token",
     ("connector", "slack"), "Slack"),
    ("AWS AKIAIOSFODNN7EXAMPLE please", "AKIAIOSFODNN7EXAMPLE", "api_key", ("vault", ""), "AWS"),
    ("AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "api_key", ("vault", ""), "Google"),
    ("glpat-xY12zA34bC56dE78fG90", "glpat-xY12zA34bC56dE78fG90", "token", ("vault", ""), "GitLab"),
    ("stripe sk_live_51HxYzAbCdEfGhIjKlMnOp", "sk_live_51HxYzAbCdEfGhIjKlMnOp", "api_key", ("vault", ""), "Stripe"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "token", ("vault", ""), ""),
    ("Authorization: Bearer abcDEF123ghiJKL456mnoPQR789", "abcDEF123ghiJKL456mnoPQR789", "token", ("vault", ""), ""),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n-----END OPENSSH PRIVATE KEY-----",
     "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n-----END OPENSSH PRIVATE KEY-----", "private_key",
     ("vault", ""), ""),
    ("Zq7xW2mK9pL4vN8rT1yB6cD3fG5hJ0sA", "Zq7xW2mK9pL4vN8rT1yB6cD3fG5hJ0sA", "token", ("vault", ""), ""),                 # one token alone
    ("the secret is Qm9vZ2xlR2VuZXJhdGVkS2V5X3hZMTIzNDU2Nzg5MEFCQ0RFRg9 ok", "Qm9vZ2xlR2VuZXJhdGVkS2V5X3hZMTIzNDU2Nzg5MEFCQ0RFRg9", "token",
     ("vault", ""), ""),                                                                                                    # a long one inside
    ("카드 4111 1111 1111 1111 로 결제해줘", "4111 1111 1111 1111", "card", ("wallet", ""), ""),
    ("my card 5555-5555-5555-4444 exp 12/28", "5555-5555-5555-4444", "card", ("wallet", ""), ""),
]


@pytest.mark.parametrize("text,value,kind,target,label", FOUND)
def test_known_shapes_tokens_cards_and_passwords_are_found(text, value, kind, target, label):
    hits = sg.find(text)
    assert len(hits) == 1, hits
    h = hits[0]
    assert text[h.start:h.end] == value and h.kind == kind and h.target == target and h.label == label


# Ordinary messages that must pass: Korean and English text, links with long ids, order / tracking / phone numbers, hashes, ids...
NORMAL = [
    "내일 오후 3시에 강남역 근처 회의실 예약해줘",
    "Can you summarize this: The quick brown fox jumps over the lazy dog. 이 문장은 모든 알파벳을 포함합니다.",
    "주문번호 2024110912345678 배송 언제 와?",
    "송장번호 6892 1234 5678 조회해줘",
    "Tracking number 1Z999AA10123456784 from UPS",
    "Amazon order #112-4567890-1234567 hasn't arrived",
    "4111 1111 1111 1112",                                         # fails the Luhn check
    "제 번호는 010-1234-5678 이에요",
    "Call me at +82 10 1234 5678 or (415) 555-0123.",
    "Meeting code: 845 2391 0042",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
    "https://www.google.com/maps/place/Seoul+Station/@37.5546788,126.9706069,17z/data=!3m1!4b1!4m6!3m5!1s0x357ca2154e3b1b0f:0x4f1b6e5b0b4b5e8",
    "https://drive.google.com/file/d/1aBcD3fGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing",
    "https://docs.google.com/document/d/1Xy2Zz3AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit",
    "Instagram post https://www.instagram.com/p/C8xYz12AbCd/?igsh=MWQ1ZGUxMzBkMA==",
    "Spotify link https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=1a2b3c4d5e6f7a8b",
    "naver blog https://blog.naver.com/PostView.naver?blogId=abc123&logNo=223456789012",
    "쿠팡 https://link.coupang.com/a/bXyZ12",
    "The commit is 3f9a1c07d2b8e4f6a1c3e5b7d9f1a3c5e7b9d1f3 and the build id 550e8400-e29b-41d4-a716-446655440000",
    "Send the report to kim.minsu+work@example.co.kr please",
    "파일은 /Users/kim/Documents/2024_세금_신고서_final_v2.pdf 에 있어",
    "My password is stored in the keychain already",
    "비밀번호는 보관함에 저장해뒀어",
    "비밀번호 바꿔야 하는데 어떻게 해?",
    "Reset my password please, I forgot it",
    "PIN 번호 바꾸는 방법 알려줘",
    "OTP 앱 추천해줘",
    "The price went from ₩1,299,000 to ₩1,099,000 on 2024-11-09",
    "Flight KE017 departs at 10:30, booking ref XJ7K2P",
    "use the function getUserAccountSettingsFromServer2024 in utils.ts",
    "set ASSISTANT_MEMORY_BUDGET=3000 in the env file",
    "The wifi network is KT_GiGA_5G_Wave2_ABCD",
    "Run `uv run pytest -o faulthandler_timeout=600 server/tests/test_fence.py`",
    "My employee ID is EMP-2024-00123 and room B1-204",
    "ISBN 978-89-01-23456-7",
    "Version 1.0.3-beta.2+build.20241109",
    "hashtag #KeloAssistantLaunch2024",
    "123456 이 숫자가 뭐야?",
    "東京の天気を教えて。明日は雨ですか？",
    "Wie spät ist es in Berlin? Bitte antworte kurz.",
]


@pytest.mark.parametrize("text", NORMAL)
def test_ordinary_text_passes(text):
    assert sg.find(text) == []


def test_a_card_number_is_a_card_even_when_a_word_next_to_it_calls_it_an_order_number():
    # (hc-privacy: the order-number words that used to let a Luhn-valid number pass are gone; the safe side wins)
    for text in ("order 4532015112830366 tracking?", "4111111111111111 확인 부탁해"):
        assert [h.kind for h in sg.find(text)] == ["card"]


# ---- candidates and the judge (hc-privacy): no password or code WORD decides anything
class Judge:
    """secret_judge.SecretJudge stand-in: says that the candidates whose token is in `secret` are secrets; None = cannot tell."""
    def __init__(self, secret=(), fail=False):
        self.secret, self.fail, self.asked = set(secret), fail, []

    async def secrets(self, text, spans):
        self.asked.append([text[s:e] for s, e in spans])
        return None if self.fail else {i for i, (s, e) in enumerate(spans) if text[s:e] in self.secret}


CANDIDATES = [
    ("password: Tr0ub4dor&3", ["Tr0ub4dor&3"]),
    ("비밀번호는 Hunter2!x 야", ["Hunter2!x"]),
    ("인증번호 482913", ["482913"]),
    ("my router code is secret1234", ["secret1234"]),
    ("cvc: 123", ["123"]),
    ("482913", ["482913"]),
    ("Meeting code: 845 2391 0042", ["845", "2391", "0042"]),
]


@pytest.mark.parametrize("text,found", CANDIDATES)
def test_candidates_are_found_by_shape_in_any_language(text, found):
    assert [text[h.start:h.end] for h in sg.candidates(text)] == found


@pytest.mark.parametrize("text", ["see https://example.com/a?x=1 and kim@example.org", "file_v2.pdf and /usr/local/bin", "price $1500 only", "Timezone: Asia/Seoul",
                                  "주문번호 2024110912345678", "2024년11월12일에 만나요", "12,500 won at 7:30", "version 1.2.3-beta"])
def test_ordinary_shapes_are_no_candidates(text):
    assert sg.candidates(text) == []


async def test_the_judge_names_the_secrets_and_the_rest_goes_through():
    j = Judge(secret={"482913"})
    hits = await sg.screen("인증번호 482913, 방 번호 2391", judge=j)
    assert [h.kind for h in hits] == ["password"] and j.asked == [["482913", "2391"]]
    assert await sg.screen("방 번호 2391", judge=Judge()) == []


@pytest.mark.parametrize("judge", [None, Judge(fail=True)])
async def test_no_judge_or_a_judge_that_cannot_tell_makes_every_candidate_a_hit(judge):
    text = "방 번호 2391 and abc123x"
    hits = await sg.screen(text, judge=judge)
    assert [text[h.start:h.end] for h in hits] == ["2391", "abc123x"]
    assert await sg.screen("nothing to judge here", judge=judge) == []


async def test_a_known_shape_is_not_asked_about_again():
    j = Judge()
    hits = await sg.screen(f"key {ANTHROPIC}", judge=j)
    assert [h.kind for h in hits] == ["api_key"] and j.asked == []


def test_the_secret_judge_asks_once_and_reads_only_strict_json():
    import asyncio
    from assistant.secret_judge import SecretJudge, parse
    assert parse({"secrets": [0]}, 2) == {0} and parse({"secrets": []}, 2) == set()
    for bad in (None, {}, {"secrets": [2]}, {"secrets": ["0"]}, {"secrets": [True]}, {"secrets": 0}, [0]):
        assert parse(bad, 2) is None
    asked = []

    async def ask(system, user):
        asked.append(user)
        return {"secrets": [1]}
    j = SecretJudge(ask)
    text = "a 1234 b 5678"
    spans = [(2, 6), (9, 13)]
    assert asyncio.run(j.secrets(text, spans)) == {1} and asyncio.run(j.secrets(text, spans)) == {1} and len(asked) == 1
    assert "[[1234]]" in asked[0]


def test_an_installed_connectors_key_shape_is_found_and_names_it():
    hits = sg.find("key acme_AbCdEfGhIjKlMnOpQrStUvWx12 here", [("acme", "acme_[A-Za-z0-9]{26}")], lambda c: "Acme Cloud")
    assert [(h.kind, h.target, h.label) for h in hits] == [("api_key", ("connector", "acme"), "Acme Cloud")]
    assert sg.target_of(hits) == {"type": "connector", "id": "acme"} and sg.label_of(hits) == "Acme Cloud"
    assert sg.find("acme_short", [("acme", "acme_[A-Za-z0-9]{26}")]) == []


@pytest.mark.parametrize("pattern", ["(a|b)+", "abc.*", "ab[A-Z]{30}", "x" * 300, "abc[A-Z]+", "abc[A-Z]{300}", "abc[A-Z]{2}"])
def test_a_connectors_key_pattern_must_be_a_fixed_prefix_and_bounded_classes(pattern):
    with pytest.raises(ValueError):
        sg.check_key_pattern(pattern)
    assert sg.find("abc" + "Q" * 40, [("bad", pattern)]) == []                         # a bad pattern is skipped, never run


def test_offsets_are_utf16_and_the_detail_never_holds_the_value():
    text = f"🙂 key {ANTHROPIC}"
    hits = sg.find(text)
    detail = sg.describe(text, hits, "[hidden key]")
    span = detail["spans"][0]
    assert text.encode("utf-16-le")[span["start"] * 2:span["end"] * 2].decode("utf-16-le") == ANTHROPIC   # what the apps cut out
    assert ANTHROPIC not in str(detail) and detail["code"] == "secret_in_message" and detail["label"] == "Anthropic"
    assert sg.mask(text, hits, "[hidden key]") == "🙂 key [hidden key]"


def test_marked_messages_are_read_without_the_secret(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    a = store.add_message("user", f"use {ANTHROPIC} for it")
    b = store.add_message("user", "plain")
    sg.mark(store, a)
    rows = sg.scrub_rows(store, [{"id": a, "content": f"use {ANTHROPIC} for it"}, {"id": b, "content": "plain"}])
    assert rows[0]["content"] == "use [removed] for it" and rows[1]["content"] == "plain"
    store.set_meta("secret_message_ids", str(a))                                        # no judge here: every candidate of a marked message goes
    assert sg.scrub_rows(store, [{"id": a, "content": "code 4821 ok"}])[0]["content"] == "code [removed] ok"


# ------------------------------------------------------------------------------------------------------------------ in the server
def recording():
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        yield TextDelta("ok")
        yield Done("stop")
    return fn, calls


class Lines(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


async def test_a_message_with_a_key_is_held_before_the_model_and_storage_and_never_logged(tmp_path):
    fn, calls = recording()
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn)
    text = f"🙂 내 Anthropic 키는 {ANTHROPIC} 이거야. 연결해줘"
    seen, logger = Lines(), logging.getLogger("uvicorn.error")   # the server's own log lines
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(seen)
        for path in ("/api/talk", "/api/turns"):
            r = await c.post(path, json={"text": text})
            assert r.status_code == 409, r.text
            d = r.json()["detail"]
            assert d["code"] == "secret_in_message" and d["target"] == {"type": "provider", "id": "anthropic"}
            assert [s["kind"] for s in d["spans"]] == ["api_key"] and ANTHROPIC not in r.text
        assert calls == []                                                                 # no model saw it
        assert (await c.get("/api/messages")).json() == []                                 # nothing was stored

        # "mask": the key is replaced before anything is stored or sent
        async with c.stream("POST", "/api/talk", json={"text": text, "secrets": "mask"}) as r:
            assert r.status_code == 200
            await read_events(r)
        assert ANTHROPIC not in str(calls[-1].messages) and ANTHROPIC not in str((await c.get("/api/messages")).json())

        # "allow" (Send as is, only after the owner's tap): sent as written, and the message is marked for the lasting passes
        async with c.stream("POST", "/api/talk", json={"text": text, "secrets": "allow"}) as r:
            assert r.status_code == 200
            await read_events(r)
        assert ANTHROPIC in calls[-1].messages[-1]["content"]
        mine = [m for m in (await c.get("/api/messages")).json() if m["role"] == "user" and ANTHROPIC in m["content"]]
        assert len(mine) == 1
        store = Store(str(tmp_path / "a.db"))
        assert mine[0]["id"] in sg.marked(store)
        assert ANTHROPIC not in str(sg.scrub_rows(store, store.messages(limit=50)))

        # an ordinary message goes straight through
        async with c.stream("POST", "/api/talk", json={"text": "주문번호 2024110912345678 배송 언제 와?"}) as r:
            assert r.status_code == 200
        logger.removeHandler(seen)
        logger.setLevel(level)
    assert any("held back" in line for line in seen.lines) and any("after the warning" in line for line in seen.lines)
    assert not [line for line in seen.lines if ANTHROPIC in line or "Zq7xW2mK9pL4" in line]   # the value is never logged


async def test_a_password_without_its_word_is_held_when_the_model_names_it_or_cannot_answer(tmp_path):
    from assistant.secret_judge import SYSTEM as JUDGE_SYSTEM
    verdict = {"mode": "secret"}
    calls = []

    async def fn(provider, request, **kw):
        if request.messages[0]["content"] == JUDGE_SYSTEM:
            calls.append(request)
            yield TextDelta({"secret": '{"secrets": [0]}', "none": '{"secrets": []}', "down": "no json"}[verdict["mode"]])
        else:
            yield TextDelta("ok")
        yield Done("stop")
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn)
    def said(token):
        return f"공유기 코드: {token}"                                           # no password word in English: the shape and the judge decide
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/turns", json={"text": said("secret1234")})
        assert r.status_code == 409 and [s["kind"] for s in r.json()["detail"]["spans"]] == ["password"] and "secret1234" not in r.text
        verdict["mode"] = "down"                                                       # the judge cannot answer: held all the same
        assert (await c.post("/api/turns", json={"text": said("secret5678")})).status_code == 409
        verdict["mode"] = "none"                                                       # the model says it is no secret
        assert (await c.post("/api/turns", json={"text": said("secret9999")})).status_code != 409
        assert (await c.post("/api/turns", json={"text": said("secret9999")})).status_code != 409 and len(calls) == 3   # the answer is cached
        assert (await c.post("/api/turns", json={"text": said("secret5678"), "secrets": "mask"})).status_code != 409


# ---- chat path: few candidates, a 3 s judge (a message with no candidate never waits)
class Counting(Judge):
    pass


async def test_an_ordinary_chat_message_sends_with_no_judge_call():
    j = Counting()
    assert await sg.screen("제 아이폰15 가격이 129만원이고 2026년에 샀어요", judge=j, chat=True) == [] and j.asked == []
    assert await sg.screen("I paid 1500 in 2026 for room 4821", judge=j, chat=True) == [] and j.asked == []


@pytest.mark.parametrize("text,found", [("비밀번호 Tr0ub4dor&3", "Tr0ub4dor&3"), ("wifi=abc12345", "abc12345"), ("482913", "482913"),
                                        ("router: secret1234", "secret1234")])
def test_chat_candidates_are_the_three_structural_cases(text, found):
    assert [text[h.start:h.end] for h in sg.chat_candidates(text)] == [found]


async def test_an_unanswered_chat_judge_holds_after_the_timeout(monkeypatch):
    import asyncio
    monkeypatch.setattr(sg, "CHAT_TIMEOUT", 0.05)

    class Slow:
        async def secrets(self, text, spans):
            await asyncio.sleep(5)
    text = "wifi=abc12345"
    assert [text[h.start:h.end] for h in await sg.screen(text, judge=Slow(), chat=True)] == ["abc12345"]
