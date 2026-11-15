"""New Google sign-ins wait for Google's review (google_web.OPEN_META, off by default): the Connectors rows say "pending_review" with the
note, the start and the broker's exchange are refused with it (409), and accounts already connected keep working and stay disconnectable.
Only the owner flips the switch. Against a pretend Google; nothing real is contacted."""

import json
import time

import httpx

from assistant.connect_ways import ways
from assistant.google_calendar import GoogleCalendar, calendar_tools
from assistant.google_web import SUPPORT_META, GoogleReviewPending, GoogleWeb
from assistant.mail import Mail
from assistant.store import Store
from assistant.tools.oauth_tools import oauth_tools

from test_google_calendar import CAL_SCOPE, FakeCalendarGoogle
from test_google_web import ADDRESS, CLIENT, RELAY, SECRET, XoauthMail, app_with
from test_server import AUTH, Running

NOTE_KO = "Google 검토가 끝나야 열 수 있어요. Kelo를 응원해 주시면 더 빨리 열 수 있어요."


async def test_closed_by_default_and_a_connected_calendar_keeps_working(tmp_path):
    g, m = FakeCalendarGoogle(), XoauthMail()
    st = Store(tmp_path / "s.db")
    st.set_profile(timezone="Asia/Seoul")
    gw = GoogleWeb(st, relay=RELAY, fetcher=g.fetch)
    gw.set_app(CLIENT, SECRET)
    assert not gw.open_to_all()
    try:
        await gw.start("https://kelo.example.org", "calendar")
        raise AssertionError("a new sign-in must wait")
    except GoogleReviewPending as e:
        assert "Google" in str(e)
    # the owner's calendar connected before the switch was closed: still connected, tokens still renewed
    cal = GoogleCalendar(st, gw, fetcher=g.fetch)
    await cal.connect(ADDRESS, {"access_token": "ya29.cal", "refresh_token": "1//cal", "expires_in": 3599, "scope": CAL_SCOPE})
    data = json.loads(st.credential("google-calendar")["secret"])
    data["oauth"]["expires_at"] = time.time() - 1
    st.set_credential_secret("google-calendar", json.dumps(data))
    tools = {t.name: t for t in calendar_tools(cal)}
    assert "치과" in await tools["gcal_events"].handler({"start": "2026-11-09", "end": "2026-11-09"})
    assert g.calls[-1]["grant_type"] == ["refresh_token"]
    row = ways(st, Mail(st), browser=True)["google_calendar"]
    assert row["connected"] and row["method"] == "google" and "connect_state" not in row and "fallback" not in row


async def test_rows_carry_the_note_and_the_support_link_only_when_set(tmp_path):
    st = Store(tmp_path / "s.db")
    mail = Mail(st)
    t = ways(st, mail, browser=True)
    assert t["gmail"]["connect_state"] == "pending_review" and t["gmail"]["connect"] == "mail" and "support_url" not in t["gmail"]
    assert t["google_calendar"]["connect_state"] == "pending_review" and "fallback" not in t["google_calendar"]
    assert "connect_state" not in t["imap_email"]
    st.set_meta(SUPPORT_META, "javascript:alert(1)")                  # never a link that is not https
    assert "support_url" not in ways(st, mail, browser=True)["gmail"]
    st.set_meta(SUPPORT_META, "https://kelo.chat/support")
    assert ways(st, mail, browser=True)["gmail"]["support_url"] == "https://kelo.chat/support"
    opened = ways(st, mail, browser=True, google_open=True)
    assert "connect_state" not in opened["gmail"] and opened["google_calendar"]["fallback"]["connect"] == "key"
    # the assistant is told to offer the phone calendar instead of a sign-in card
    tool = oauth_tools(lambda: ways(st, mail, browser=True), None, mail)[0]
    said = await tool.handler({"service": "google_calendar"})
    assert "review" in said and "phone" in said and "card" not in said


async def test_only_the_owner_opens_it_and_the_broker_follows_its_switch(tmp_path):
    from test_devices import add_phone, bearer
    g, m = FakeCalendarGoogle(), XoauthMail()
    async with Running(app_with(tmp_path / "op", g, m)) as op, httpx.AsyncClient(base_url=op.url, headers=AUTH) as c:
        await c.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
        assert (await c.get("/api/oauth-apps/google")).json()["open_to_all"] is False
        _, r = await add_phone(c)
        phone = bearer(r.json()["token"])
        assert (await c.get("/api/oauth-apps/google", headers=phone)).status_code == 200   # a phone is allowed too: no owner devices
        rows = {x["id"]: x for x in (await c.get("/api/connectors", headers={"Accept-Language": "ko-KR"})).json()}
        assert rows["gmail"]["note"] == NOTE_KO and rows["google_calendar"]["note"] == NOTE_KO
        assert (await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org", "product": "calendar"})).status_code == 409
        async with httpx.AsyncClient(base_url=op.url) as anon:
            assert (await anon.get("/broker/google/client")).json() == {"client_id": CLIENT, "open": False}
            r = await anon.post("/broker/google/exchange", json={"code": "g-code", "redirect_uri": RELAY, "code_verifier": "v"})
            assert r.status_code == 409
        assert (await c.put("/api/oauth-apps/google/open", json={"open": True})).json()["open_to_all"] is True
        async with httpx.AsyncClient(base_url=op.url) as anon:
            assert (await anon.get("/broker/google/client")).json()["open"] is True
        assert "connect_state" not in next(x for x in (await c.get("/api/connectors")).json() if x["id"] == "gmail")
        assert (await c.put("/api/oauth-apps/google/open", json={"open": False})).json()["open_to_all"] is False
