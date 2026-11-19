"""One-time codes hidden from the model (docs/one-time-codes.md): found by shape, replaced by a handle in mail and phone texts,
typed by the browser once after approval, only into the sender's site, expiring."""

import pytest

from assistant.code_judge import CodeJudge
from assistant.code_shelf import SHELF, CodeShelf, code_spans
from assistant.tools.browser_more import BrowserExtras
from test_browser_more import FakeMail, FormPage, run_code_tool, session_for


@pytest.fixture(autouse=True)
def clean_shelf():
    SHELF.clear()
    SHELF.judge = None
    yield
    SHELF.clear()
    SHELF.judge = None


class FakeModel:
    """Stands in for the main model: says a marked token is a code when `is_code(token)`; counts requests; can fail."""

    def __init__(self, is_code=lambda tok: False, fail=None):
        self.is_code, self.fail, self.calls, self.seen = is_code, fail, 0, []

    async def ask(self, system, user):
        import json
        import re
        self.calls += 1
        self.seen.append(user)
        if self.fail == "raise":
            raise RuntimeError("down")
        if self.fail == "bad":
            return {"codes": "all"}
        toks = re.findall(r"\[\[#(\d+): ([^\]]*)\]\]", " ".join(m["text"] for m in json.loads(user)))
        return {"codes": [int(i) for i, tok in toks if self.is_code(tok)]}


def six(tok):
    return tok.isdigit() and len(tok) == 6


@pytest.mark.parametrize("text,codes", [
    ("Your sign-in code\n482913\nIt expires soon.", ["482913"]),
    ("Dein Code: 482913", ["482913"]),                                   # the only number of a short message, any language
    ("인증번호 [482913]를 입력하세요", ["482913"]),
    ("<#> 482 913 is your code\n@example.com #482913", ["482913", "482913"]),
    ("Use 7K4P9X to log in", ["7K4P9X"]),
    ("4821\n", ["4821"]),
    ("Order 1234567890 total 12 items, call 010-1234-5678.", []),      # long numbers are never candidates
    ("Price 482913 $ and 20 % off", []),                                 # beside a currency or percent sign
])
def test_codes_are_found_by_shape(text, codes):
    got = [text[a:b].replace(" ", "") for a, b in code_spans(text)]
    assert got == codes


async def test_mask_replaces_with_handles_and_holds_the_code():
    sh = CodeShelf()
    out = await sh.mask("Code:\n482913\nthanks", channel="mail", sender="Example <no-reply@example.com>")
    assert out == "Code:\n[verification code #1]\nthanks" and "482913" not in out
    h = sh.get(1)
    assert h.code == "482913" and h.site == "example.com" and h.sender == "no-reply@example.com"
    again = await sh.mask("482913", channel="mail", sender="no-reply@example.com")      # the same code keeps its number
    assert again == "[verification code #1]"
    assert await sh.mask("111222", channel="text", sender="+82101234567") == "[verification code #2]" and sh.get(2).site == ""


async def test_expiry_single_use_and_capacity():
    now = [1000.0]
    sh = CodeShelf(clock=lambda: now[0])
    await sh.mask("482913", channel="mail", sender="a@example.com")
    now[0] += 599
    assert sh.get(1) is not None
    now[0] += 2
    assert sh.get(1) is None                      # expired after 10 minutes
    await sh.mask("555666", channel="mail", sender="a@example.com")
    sh.forget(2)
    assert sh.get(2) is None                      # gone after one use
    for i in range(80):
        await sh.mask(f"{100000 + i}", channel="text", sender="x")
    assert len(sh._held) <= 64


async def test_a_failure_while_checking_withholds_the_text(monkeypatch):
    sh = CodeShelf()
    monkeypatch.setattr(CodeShelf, "_mask_many", lambda *a: 1 / 0)
    assert "482913" not in await sh.mask("482913", channel="mail") and "withheld" in await sh.mask("482913", channel="mail")


async def test_email_read_and_list_show_a_handle_not_the_code():
    from assistant.tools.mail_tools import mail_tools

    class M:
        async def list(self, *a):
            return [{"uid": 5, "unread": True, "date": "d", "from": "Example <no-reply@example.com>", "subject": "Your code is\n482913"}]

        async def read(self, account, uid, folder):
            return {"from": "Example <no-reply@example.com>", "to": "me@x.com", "cc": "", "date": "d", "subject": "Sign in",
                    "attachments": [], "text": "Hello,\nyour code:\n482913\nBye"}

    tools = {t.name: t for t in mail_tools(M())}
    out = await tools["email_read"].handler({"account": "a", "uid": "5"})
    assert "482913" not in out and "[verification code #1]" in out
    lst = await tools["email_list"].handler({"account": "a"})
    assert "482913" not in lst and "[verification code" in lst


async def browser_tool(page_url, boxes=1):
    page = FormPage(page_url, boxes=boxes)
    s = session_for(page)
    s.settle_seconds = 0
    s.sign_in_wait = 0
    await s.page()
    t = {x.name: x for x in BrowserExtras(s, mail=FakeMail([], accounts=False)).tools()}["find_login_code"]
    return page, t


async def test_the_browser_types_a_held_code_once_after_approval():
    await SHELF.mask("482913", channel="mail", sender="no-reply@example.com")
    page, t = await browser_tool("https://www.example.com/verify")
    out, card, extra = await run_code_tool(t, {"code_ref": 1})
    assert "482913" not in out and "482913" not in card and extra["login_code"]["code"] == "482913"
    assert page.filled == [("#otp-0", "482913")]
    assert SHELF.get(1) is None                                       # single use
    out, _, _ = await run_code_tool(t, {"code_ref": 1})
    assert out.startswith("error: there is no held verification code")


async def test_a_held_code_goes_only_to_its_senders_site_and_https():
    await SHELF.mask("482913", channel="mail", sender="no-reply@example.com")
    page, t = await browser_tool("https://www.other.com/verify")
    out, card, _ = await run_code_tool(t, {"code_ref": 1})
    assert out.startswith("error:") and "example.com" in out and card is None and page.filled == []
    assert SHELF.get(1) is not None                                   # a refusal does not use it up
    page, t = await browser_tool("http://www.example.com/verify")
    out, card, _ = await run_code_tool(t, {"code_ref": 1})
    assert "https" in out and page.filled == []


async def test_a_declined_code_is_not_typed():
    await SHELF.mask("111222", channel="text", sender="+82101234567")
    page, t = await browser_tool("https://www.example.com/verify")
    out, _, _ = await run_code_tool(t, {"code_ref": 1}, approve=False)
    assert "did not want" in out and page.filled == []


# ------------------------------------------------------------------------------------------------ the judge decides among candidates
def judged(model):
    SHELF.judge = CodeJudge(model.ask)
    return model


@pytest.mark.parametrize("text", [
    "택배가 2026-11-18 도착 예정입니다",
    "주문번호 20261116 결제 완료",
    "Total: 15000 KRW",
    "Invoice INV-2026 attached",
    "Receipt\n15000\n2026-11-18\nThanks",
])
async def test_ordinary_numbers_stay_readable(text):
    model = judged(FakeModel(six))
    assert code_spans(text)                                   # they are candidates by shape...
    assert await SHELF.mask(text, channel="mail", sender="shop@example.com") == text    # ...the judge says none is a code
    assert model.calls == 1 and SHELF.get(1) is None


async def test_a_real_code_message_is_masked_and_the_agent_never_sees_it():
    model = judged(FakeModel(six))
    out = await SHELF.mask("Your code is 482913. Order 20261116 shipped.", channel="mail", sender="no-reply@example.com")
    assert out == "Your code is [verification code #1]. Order 20261116 shipped." and "482913" not in out
    assert SHELF.get(1).code == "482913" and model.calls == 1


@pytest.mark.parametrize("fail", ["raise", "bad", "none"])
async def test_judge_down_masks_every_candidate(fail):
    if fail == "none":
        SHELF.judge = None
    else:
        judged(FakeModel(six, fail=fail))
    out = await SHELF.mask("Total: 15000 KRW", channel="mail", sender="a@example.com")
    assert "15000" not in out and "[verification code #1]" in out


async def test_no_candidates_means_no_judge_call():
    model = judged(FakeModel(six))
    assert await SHELF.mask("See you tomorrow at the cafe, thanks!", channel="mail", sender="a@example.com") == "See you tomorrow at the cafe, thanks!"
    assert model.calls == 0


async def test_one_request_per_message_and_per_list_and_it_is_cached():
    model = judged(FakeModel(six))
    items = [("Your code 482913", "a@example.com", "x"), ("Total: 15000 KRW", "b@example.com", "y"), ("no numbers", "c@example.com", "z")]
    out = await SHELF.mask_many(items, channel="mail")
    assert out[0] == "Your code [verification code #1]" and out[1] == "Total: 15000 KRW" and out[2] == "no numbers"
    assert model.calls == 1
    await SHELF.mask_many(items, channel="mail")
    assert model.calls == 1                                    # cached by the hash of the text


async def test_email_list_is_one_judge_call_for_all_subjects():
    from assistant.tools.mail_tools import mail_tools
    model = judged(FakeModel(six))

    class M:
        async def list(self, *a):
            return [{"uid": i, "unread": False, "date": "d", "from": f"s{i}@example.com", "subject": s}
                    for i, s in enumerate(["Code 482913", "Total: 15000 KRW", "Invoice INV-2026"])]

    lst = await {t.name: t for t in mail_tools(M())}["email_list"].handler({"account": "a"})
    assert model.calls == 1 and "482913" not in lst and "15000 KRW" in lst and "INV-2026" in lst


async def test_a_candidate_beyond_what_the_judge_reads_is_masked():
    judged(FakeModel(lambda tok: False))
    out = await SHELF.mask("word " * 6000 + "\n482913\n", channel="mail", sender="a@example.com")
    assert "482913" not in out and "[verification code #1]" in out
