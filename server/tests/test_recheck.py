"""What is left of rechecks (recheck.py): the topic key and the source rule, the old hidden mark, the one-time retirement of the
waiting rows, and that no recheck service, tool, route, switch, budget or nightly extraction is left."""

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.providers import Done
from assistant.recheck import RECHECK_MARK, RETIRED_REASON, SCHEMA, SourceRule, TopicKey, retire_rechecks, topic_key
from assistant.reflection import AUTO_PREFIXES, PROMPT, Reflection
from assistant.store import Store

SEOUL = ZoneInfo("Asia/Seoul")
HOW = "출입국·외국인정책본부 hikorea.go.kr, 법무부 moj.go.kr"


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


def official(url="https://www.hikorea.go.kr/info", **kw):
    return {"url": url, "publisher": "HiKorea", "official": True, "date": "2026-11-10", **kw}


def news(url="https://news.example.com/a", **kw):
    return {"url": url, "publisher": "News", "official": False, "date": "2026-11-11", **kw}


# ---------------------------------------------------------------------------------------------------------- the topic key
def test_topic_key_is_ascii_hyphens_and_a_month_and_the_same_wording_merges():
    now = ts(2026, 11, 13, 9, 0)
    assert topic_key("Visa Extension Rules!!", now, SEOUL) == "visa-extension-rules:2026-11"
    assert topic_key("  visa_extension--RULES ", now, SEOUL) == topic_key("visa extension rules", now, SEOUL)
    assert len(TopicKey.slug("a" * 200)) <= 60 and "--" not in TopicKey.slug("a - - b")
    assert TopicKey.slug("café-menü") == "cafe-menu"
    korean = TopicKey.slug("비자 연장")
    assert korean.startswith("topic-") and korean == TopicKey.slug("비자 연장") != TopicKey.slug("체류 기간")   # no ascii: a stable hash
    assert TopicKey.slug_of("visa-rules:2026-11") == "visa-rules" and TopicKey.slug("visa-rules:2026-11") == "visa-rules"
    assert topic_key("x", ts(2026, 12, 1, 0, 0), SEOUL) != topic_key("x", now, SEOUL)          # a new month, a new key


# ---------------------------------------------------------------------------------------------------------- the source rule
def test_two_domains_pass_one_official_passes_one_news_domain_fails():
    rule = SourceRule("출입국 hikorea.go.kr")
    assert rule.judge([news("https://a.example.com/x"), news("https://b.example.org/y")]).ok                   # two different sites
    assert rule.judge([official()]).ok                                                                       # one official site
    out = rule.judge([news()])
    assert not out.ok and out.reason == "one unofficial source"
    assert not rule.judge([news("https://a.example.com/x"), news("https://www.example.com/y")]).ok            # the same site twice
    assert not rule.judge([news("https://a.example.co.kr/x"), news("https://b.example.co.kr/y")]).ok           # a registrable domain, not a host
    assert not rule.judge([]).ok and not rule.judge("nope").ok and not rule.judge([{"url": "javascript:alert(1)"}, {"url": ""}]).ok


def test_the_models_official_flag_is_not_trusted_alone():
    rule = SourceRule(HOW)
    liar = rule.judge([news(official=True)])                                  # says official: a news domain that `how` does not name
    assert not liar.ok and liar.sources[0]["official"] is False and liar.sources[0]["claimed_official"] is True
    assert not rule.judge([official("https://www.hikorea.go.kr.evil.com/x")]).ok                              # a look-alike host
    assert not rule.judge([{**official("https://hikorea.go.kr/x"), "official": False}, ]).sources[0]["official"] is False   # a government site is official by its domain
    assert not SourceRule("some site").judge([official("https://news.example.com/x")]).ok
    named = SourceRule("the company's own site acme.com").judge([news("https://www.acme.com/pricing", official=True)])
    assert named.ok and named.sources[0]["official"]                           # named in `how`, and the model says so
    assert not SourceRule("the company's own site acme.com").judge([news("https://www.acme.com/pricing", official=False)]).ok
    for gov in ("https://www.irs.gov/x", "https://www.gov.uk/x", "https://moj.go.kr/x"):
        assert SourceRule("").judge([{"url": gov}]).ok
    assert not SourceRule("").judge([{"url": "https://fake.gov.evil.com/x"}]).ok and not SourceRule("").judge([{"url": "https://evilgov.com/x"}]).ok


def test_a_recheck_handoff_is_not_the_owners_words_for_the_look_back():
    assert any(RECHECK_MARK.startswith(p) for p in AUTO_PREFIXES)


def _rows(store, *states):
    store._db.executescript(SCHEMA)
    for n, state in enumerate(states):
        store._run("INSERT INTO rechecks(topic_key, created, next_at, state, origin) VALUES(?, 0, 0, ?, ?)",
                   (f"t{n}:2026-11", state, "owner_watch" if n == 0 else "agent"))


def test_retiring_closes_every_waiting_row_once_and_leaves_the_rest():
    store = Store()
    _rows(store, "open", "unconfirmed", "drafted", "sent", "stopped", "closed")
    assert retire_rechecks(store, 1000.0) == 3
    got = {r["topic_key"]: (r["state"], r["skip_reason"]) for r in store._all("SELECT topic_key, state, skip_reason FROM rechecks")}
    assert [got[f"t{n}:2026-11"][0] for n in range(6)] == ["closed", "closed", "closed", "sent", "stopped", "closed"]
    assert got["t0:2026-11"][1] == RETIRED_REASON and got["t4:2026-11"][1] == ""
    store._run("INSERT INTO rechecks(topic_key, created, next_at, state) VALUES('late:2026-11', 0, 0, 'open')")
    assert retire_rechecks(store, 2000.0) == 0                                # once per server


def test_retiring_works_on_a_server_that_never_had_the_table():
    store = Store()
    assert retire_rechecks(store, 1000.0) == 0 and store._all("SELECT * FROM rechecks") == []


def test_the_nightly_look_back_asks_for_no_rechecks():
    assert "recheck" not in PROMPT.lower()
    assert "rechecks" not in inspect.getsource(Reflection)


async def test_the_app_has_no_recheck_service_tool_route_switch_or_budget(tmp_path):
    from assistant.server import Settings, create_app
    import httpx
    from test_server import AUTH, TOKEN, Running

    async def fn(*a, **kw):
        yield Done("stop")

    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home")), stream_fn=fn)
    assert not [t.name for t in app.state.tools if t.name.startswith("recheck")]
    assert getattr(app.state, "rechecks", None) is None
    assert getattr(app.state.briefing, "corrections", None) is not None
    assert getattr(app.state.proactive.situation, "corrections", None) is not None
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        c.headers.update(AUTH)
        assert (await c.get("/api/rechecks")).status_code == 404
        got = (await c.get("/api/proactive")).json()
        assert "rechecks" not in got and "recheck_per_day" not in got
        put = await c.put("/api/proactive", json={"rechecks": False, "recheck_per_day": 2})   # an old client: ignored
        assert put.status_code == 200 and "rechecks" not in put.json() and "recheck_per_day" not in put.json()
