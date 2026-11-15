"""Google Calendar with the Google sign-in: incremental authorization with the Gmail client, tokens renewed like Gmail's, the agent's
read tools and the write tools behind an approval card in words. Against a pretend Google; nothing real is contacted."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from assistant.google_calendar import GoogleCalendar, calendar_tools, parse_when, when_text
from assistant.google_web import GoogleWeb
from assistant.store import Store

from test_google_web import ADDRESS, CLIENT, RELAY, SECRET, FakeGoogle, XoauthMail, app_with, id_token, j
from test_server import AUTH, Running

CAL_SCOPE = "openid email https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events"


class FakeCalendarGoogle(FakeGoogle):
    """Google's token endpoint (by the code asked for) and a small Calendar API with two calendars."""

    def __init__(self, scope=CAL_SCOPE):
        super().__init__()
        self.scope = scope
        self.api = []
        self.events = {"primary": [
            {"id": "e1", "summary": "치과", "start": {"dateTime": "2026-11-09T15:00:00+09:00"}, "end": {"dateTime": "2026-11-09T16:00:00+09:00"},
             "location": "강남역"},
            {"id": "e2", "summary": "Declined sync", "start": {"dateTime": "2026-11-09T17:00:00+09:00"}, "end": {"dateTime": "2026-11-09T18:00:00+09:00"},
             "attendees": [{"email": ADDRESS, "self": True, "responseStatus": "declined"}, {"email": "x@y.z"}]},
            {"id": "e3", "summary": "Weekly", "recurringEventId": "w", "start": {"date": "2026-11-10"}, "end": {"date": "2026-11-11"}}],
            "family@group.calendar.google.com": [
            {"id": "f1", "summary": "Dinner", "start": {"dateTime": "2026-11-09T10:00:00Z"}, "end": {"dateTime": "2026-11-09T11:00:00Z"},
             "attendees": [{"email": "a"}, {"email": "b"}]}]}

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        assert urlsplit(url).hostname in allowed_hosts
        if url == "https://oauth2.googleapis.com/token":
            f = parse_qs(content.decode())
            if f.get("grant_type") == ["authorization_code"] and f.get("code") == ["cal-code"]:
                self.calls.append(f)
                return j({"access_token": "ya29.cal", "refresh_token": "1//cal", "expires_in": 3599, "id_token": id_token(ADDRESS),
                          "scope": self.scope})
            if f.get("grant_type") == ["refresh_token"] and f.get("refresh_token") == ["1//cal"]:
                self.calls.append(f)
                return j({"access_token": "ya29.cal2", "expires_in": 3599, "scope": self.scope})
            return await super().fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)
        if url.startswith("https://www.googleapis.com/calendar/v3/"):
            self.api.append((method, url, headers.get("Authorization"), json.loads(content) if content else None))
            if headers.get("Authorization") not in ("Bearer ya29.cal", "Bearer ya29.cal2"):
                return j({"error": {"code": 401, "message": "Invalid Credentials"}}, 401)
            path = urlsplit(url).path.removeprefix("/calendar/v3")
            if path == "/users/me/calendarList":
                return j({"items": [{"id": ADDRESS, "summary": ADDRESS, "primary": True, "selected": True, "accessRole": "owner"},
                                    {"id": "family@group.calendar.google.com", "summary": "Family", "selected": True, "accessRole": "writer"},
                                    {"id": "holidays", "summary": "Holidays", "accessRole": "reader"}]})
            bits = path.split("/")
            cal = "primary" if bits[2] in ("primary", ADDRESS.replace("@", "%40")) else bits[2].replace("%40", "@")
            if len(bits) == 4 and method == "GET":
                q = parse_qs(urlsplit(url).query)
                assert q["singleEvents"] == ["true"] and q["timeZone"] == ["Asia/Seoul"]
                return j({"items": self.events.get(cal, [])})
            if len(bits) == 4 and method == "POST":
                return j({"id": "new1", **json.loads(content)})
            if len(bits) == 5:
                ev = next((e for e in self.events.get(cal, []) if e["id"] == bits[4]), None)
                if ev is None:
                    return j({"error": {"code": 404, "message": "Not Found"}}, 404)
                if method == "GET":
                    return j(ev)
                if method == "PATCH":
                    ev.update(json.loads(content))
                    return j(ev)
                if method == "DELETE":
                    self.events[cal].remove(ev)
                    return Reply(204, {}, b"", False)
        return j({}, 404)


from assistant.netguard import Reply  # noqa: E402


def sign_in_url_params(url):
    return parse_qs(urlsplit(url).query)


async def test_calendar_signs_in_with_only_calendar_scopes_and_says_when_the_operator_has_not_set_google_up(tmp_path):
    g, m = FakeCalendarGoogle(), XoauthMail()
    async with Running(app_with(tmp_path, g, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        ko = {"Accept-Language": "ko-KR"}
        st = (await c.get("/api/google/calendar", headers=ko)).json()
        assert st["available"] is False and st["reason"] == "운영자가 Google 로그인을 아직 설정하지 않았어요." and st["accounts"] == []
        r = await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org", "product": "calendar"}, headers=ko)
        assert r.status_code == 422 and r.json()["detail"] == "운영자가 Google 로그인을 아직 설정하지 않았어요."
        row = next(x for x in (await c.get("/api/connectors")).json() if x["id"] == "google_calendar")
        # new Google sign-ins wait for Google's review: the row says so and the service account stays out of the way
        assert row["connect"] == "google" and row["status"] == "available" and row["connect_state"] == "pending_review"
        assert "fallback" not in row and row["note"] and row["featured"] is True

        await c.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
        st = (await c.get("/api/google/calendar", headers=ko)).json()
        assert st["available"] is False and st["pending_review"] is True and st["reason"].startswith("Google 검토가 끝나야")
        r = await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org", "product": "calendar"}, headers=ko)
        assert r.status_code == 409 and r.json()["detail"].startswith("Google 검토가 끝나야")
        await c.put("/api/oauth-apps/google/open", json={"open": True})
        assert (await c.get("/api/google/calendar")).json()["available"] is True
        row = next(x for x in (await c.get("/api/connectors")).json() if x["id"] == "google_calendar")
        assert "connect_state" not in row and row["fallback"]["connect"] == "key"
        url = (await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org", "product": "calendar"})).json()["url"]
        q = sign_in_url_params(url)
        scopes = q["scope"][0].split()
        assert "https://www.googleapis.com/auth/calendar.readonly" in scopes and "https://www.googleapis.com/auth/calendar.events" in scopes
        assert "https://mail.google.com/" not in scopes
        assert q["include_granted_scopes"] == ["true"] and q["access_type"] == ["offline"] and q["redirect_uri"] == [RELAY]
        done = await c.get("/oauth/callback", params={"state": q["state"][0], "code": "cal-code"}, headers={"Accept-Language": "en"})
        assert "Google Calendar connected" in done.text, done.text
        st = (await c.get("/api/google/calendar")).json()
        assert st["accounts"] == [{"name": "google-calendar", "address": ADDRESS, "can_write": True, "problem": False}]
        row = next(x for x in (await c.get("/api/connectors")).json() if x["id"] == "google_calendar")
        assert row["status"] == "connected" and row["item"] == "google-calendar" and row["method"] == "google" and row["account"] == ADDRESS
        assert "ya29" not in str((await c.get("/api/vault")).json())
        # Gmail is still its own sign-in with only the mail scope
        gurl = (await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org"})).json()["url"]
        assert sign_in_url_params(gurl)["scope"] == ["openid email https://mail.google.com/"]
    assert b"1//cal" not in (tmp_path / "s.db").read_bytes()


async def test_a_paired_phone_cannot_see_or_change_the_operators_google_client(tmp_path):
    from test_devices import add_phone, bearer
    g, m = FakeCalendarGoogle(), XoauthMail()
    async with Running(app_with(tmp_path, g, m)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, r = await add_phone(c)
        phone = bearer(r.json()["token"])
        assert (await c.get("/api/oauth-apps/google", headers=phone)).status_code == 200   # every device has the same rights
        assert (await c.get("/api/oauth-apps/github", headers=phone)).status_code == 200
        assert (await c.get("/api/oauth-apps/google", headers=AUTH)).status_code == 200
        assert (await c.get("/api/google/calendar", headers=phone)).status_code == 200    # the phone still connects its calendar


async def test_a_refused_calendar_sign_in_saves_nothing(tmp_path):
    g, m = FakeCalendarGoogle(scope="openid email"), XoauthMail()   # the calendar box left unticked
    async with Running(app_with(tmp_path, g, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
        await c.put("/api/oauth-apps/google/open", json={"open": True})
        url = (await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org", "product": "calendar"})).json()["url"]
        done = await c.get("/oauth/callback", params={"state": sign_in_url_params(url)["state"][0], "code": "cal-code"},
                           headers={"Accept-Language": "ko"})
        assert "캘린더" in done.text and (await c.get("/api/google/calendar")).json()["accounts"] == []


async def connected(tmp_path, scope=CAL_SCOPE):
    g = FakeCalendarGoogle(scope)
    st = Store(tmp_path / "s.db")
    st.set_profile(timezone="Asia/Seoul")
    gw = GoogleWeb(st, relay=RELAY, fetcher=g.fetch)
    gw.set_app(CLIENT, SECRET)
    cal = GoogleCalendar(st, gw, fetcher=g.fetch)
    await cal.connect(ADDRESS, {"access_token": "ya29.cal", "refresh_token": "1//cal", "expires_in": 3599, "scope": scope})
    return g, st, cal, {t.name: t for t in calendar_tools(cal)}


async def test_the_agent_reads_events_in_the_users_time_zone_and_skips_declined_ones(tmp_path):
    g, st, cal, tools = await connected(tmp_path)
    out = await tools["gcal_events"].handler({"start": "2026-11-09", "end": "2026-11-10"})
    assert "2026-11-09 15:00–16:00 · 치과 · at 강남역" in out
    assert "2026-11-09 19:00–20:00 · Dinner · 2 people · Family" in out          # 10:00 UTC shown in Seoul time
    assert "2026-11-10, all day · Weekly · repeats" in out
    assert "Declined" not in out and "Asia/Seoul" in out
    assert "Holidays" in await tools["gcal_calendars"].handler({})
    # an hour later the token is renewed (here: this server holds the secret) and saved
    data = json.loads(st.credential("google-calendar")["secret"])
    data["oauth"]["expires_at"] = time.time() - 1
    st.set_credential_secret("google-calendar", json.dumps(data))
    assert "치과" in await tools["gcal_events"].handler({"start": "2026-11-09", "end": "2026-11-09"})
    assert g.api[-1][2] == "Bearer ya29.cal2" and g.calls[-1]["grant_type"] == ["refresh_token"]
    assert json.loads(st.credential("google-calendar")["secret"])["oauth"]["refresh_token"] == "1//cal"


async def test_changes_need_approval_and_the_card_says_what_changes_in_words(tmp_path):
    g, st, cal, tools = await connected(tmp_path)
    create, update, delete = tools["gcal_create_event"], tools["gcal_update_event"], tools["gcal_delete_event"]
    assert create.requires_approval and update.requires_approval and delete.requires_approval
    args = {"title": "팀 점심", "start": "2026-11-12T12:00", "end": "2026-11-12T13:00", "location": "을지로"}
    assert await create.describe(args) == "Add to Google Calendar: 팀 점심 · 2026-11-12 12:00–13:00 · Place: 을지로"
    assert "{" not in await create.describe(args)
    out = await create.handler(args)
    assert out.startswith("Added: 팀 점심 · 2026-11-12 12:00–13:00")
    sent = g.api[-1][3]
    assert sent["start"] == {"dateTime": "2026-11-12T12:00:00+09:00", "timeZone": "Asia/Seoul"}
    assert create.validate({"title": "x", "start": "2026-11-12T12:00", "end": "2026-11-12T11:00"}) == "the end is before the start"
    allday = {"title": "여행", "start": "2026-11-20", "end": "2026-11-22"}
    assert await create.describe(allday) == "Add to Google Calendar: 여행 · 2026-11-20 – 2026-11-22, all day"
    await create.handler(allday)
    assert g.api[-1][3]["end"] == {"date": "2026-11-23"}                        # the last day is included

    line = await update.describe({"event_id": "e1", "start": "2026-11-09T17:00", "end": "2026-11-09T18:00"})
    assert line == "Change in Google Calendar: 치과 (2026-11-09 15:00–16:00) → time: 2026-11-09 17:00–18:00"
    assert "e1" not in line
    assert "Changed: 치과 · 2026-11-09 17:00–18:00" in await update.handler({"event_id": "e1", "start": "2026-11-09T17:00", "end": "2026-11-09T18:00"})
    assert update.validate({"title": "x"}) == "event_id is required (from gcal_events)"
    assert await delete.describe({"event_id": "e1"}) == "Delete from Google Calendar: 치과 (2026-11-09 17:00–18:00)"
    assert await delete.handler({"event_id": "e1"}) == "Deleted."
    assert all(e["id"] != "e1" for e in g.events["primary"])


async def test_an_account_that_allowed_reading_only_cannot_change_events(tmp_path):
    g, st, cal, tools = await connected(tmp_path, scope="openid email https://www.googleapis.com/auth/calendar.readonly")
    assert cal.accounts()[0]["can_write"] is False
    out = await tools["gcal_create_event"].handler({"title": "x", "start": "2026-11-12T12:00"})
    assert out.startswith("error:") and "reading the calendar only" in out
    assert all(m == "GET" for m, *_ in g.api)


async def test_without_a_calendar_the_tools_say_how_to_connect(tmp_path):
    st = Store(tmp_path / "s.db")
    cal = GoogleCalendar(st, GoogleWeb(st, relay=RELAY))
    out = await {t.name: t for t in calendar_tools(cal)}["gcal_events"].handler({})
    assert "connect_service(service='google_calendar')" in out


def test_times_are_read_in_the_users_zone():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Seoul")
    dt, date_only = parse_when("2026-11-08 09:30", tz)
    assert dt.isoformat() == "2026-11-08T09:30:00+09:00" and not date_only
    assert parse_when("2026-11-08", tz, end_of_day=True)[0].isoformat() == "2026-11-09T00:00:00+09:00"
    assert when_text(dt, dt.replace(hour=11, minute=0), False) == "2026-11-08 09:30–11:00"
