"""Sign-ups for the Google Play closed test (backlog 86): kelo.chat/tester."""

import httpx
import pytest

from assistant.server import create_app
from assistant.store import Store
from assistant.testers import KEEP_DAYS, SignupError, TesterDesk

from test_server import Running, scripted, settings


async def test_tester_desk_sign_up(tmp_path):
    store = Store(str(tmp_path / "testers.db"))
    calls: list[tuple[str, str, str, str]] = []

    async def fake_mailer(to: str, subject: str, body: str, reply_to: str) -> str:
        calls.append((to, subject, body, reply_to))
        return "ok"

    wall_time = [1_000_000.0]
    desk = TesterDesk(store, fake_mailer, wall=lambda: wall_time[0])

    # 1. New email -> True, mail to support@kelo.chat, subject starting "[Kelo Pre-Test]", reply_to tester's address, mailed = 1
    ok = await desk.sign_up("Tester.One@Example.com", "Pixel 8", "en", True, "1.2.3.4")
    assert ok is True
    assert len(calls) == 1
    to, subject, body, reply_to = calls[0]
    assert to == "support@kelo.chat"
    assert subject.startswith("[Kelo Pre-Test]")
    assert "Tester.One@Example.com" in body
    assert reply_to == "Tester.One@Example.com"
    rows = store._all("SELECT * FROM tester_signups WHERE lower(email) = lower(?)", ("tester.one@example.com",))
    assert len(rows) == 1 and rows[0]["mailed"] == 1 and rows[0]["model"] == "Pixel 8"

    # 2. Same email again (different case) -> False, no second mail, model updated
    ok2 = await desk.sign_up("tester.one@example.com", "Galaxy S24", "ko", True, "1.2.3.4")
    assert ok2 is False
    assert len(calls) == 1
    rows = store._all("SELECT * FROM tester_signups WHERE lower(email) = lower(?)", ("tester.one@example.com",))
    assert len(rows) == 1 and rows[0]["model"] == "Galaxy S24"

    # 3. Invalid email -> SignupError("invalid")
    with pytest.raises(SignupError) as exc_invalid:
        await desk.sign_up("not-an-email", "Pixel 8", "en", True, "1.2.3.4")
    assert exc_invalid.value.code == "invalid"

    # 4. Consent False -> SignupError("consent")
    with pytest.raises(SignupError) as exc_consent:
        await desk.sign_up("tester.two@example.com", "Pixel 8", "en", False, "1.2.3.4")
    assert exc_consent.value.code == "consent"

    # 5. 11 tries from one IP within an hour -> 11th is SignupError("limited")
    test_ip = "10.0.0.99"
    for i in range(10):
        await desk.sign_up(f"limit{i}@example.com", "model", "en", True, test_ip)
    with pytest.raises(SignupError) as exc_limited:
        await desk.sign_up("limit10@example.com", "model", "en", True, test_ip)
    assert exc_limited.value.code == "limited"

    # 6. Trap filled -> True and nothing stored, nothing mailed
    calls_count = len(calls)
    db_count = desk.count()
    trap_ok = await desk.sign_up("bot@example.com", "bot", "en", True, "1.1.1.1", trap="spam")
    assert trap_ok is True
    assert len(calls) == calls_count
    assert desk.count() == db_count

    # 7. Mailer raises -> still True and stored, mailed = 0
    async def broken_mailer(to: str, subject: str, body: str, reply_to: str) -> str:
        raise RuntimeError("SMTP broken")

    desk_broken = TesterDesk(store, broken_mailer, wall=lambda: wall_time[0])
    broken_ok = await desk_broken.sign_up("failedmail@example.com", "model", "en", True, "1.2.3.5")
    assert broken_ok is True
    row_failed = store._all("SELECT * FROM tester_signups WHERE email = ?", ("failedmail@example.com",))
    assert len(row_failed) == 1 and row_failed[0]["mailed"] == 0

    # 8. Rows older than KEEP_DAYS go away on next sign-up (fake wall clock)
    old_time = wall_time[0] - (KEEP_DAYS + 10) * 86400
    store._run("INSERT INTO tester_signups(email, model, lang, created_at, mailed) VALUES(?,?,?,?,?)",
               ("ancient@example.com", "old", "en", old_time, 1))
    assert store._all("SELECT 1 FROM tester_signups WHERE email = ?", ("ancient@example.com",))
    await desk.sign_up("newcomer@example.com", "new", "en", True, "1.2.3.6")
    assert not store._all("SELECT 1 FROM tester_signups WHERE email = ?", ("ancient@example.com",))
    assert store._all("SELECT 1 FROM tester_signups WHERE email = ?", ("newcomer@example.com",))


async def test_site_tester_router(tmp_path, monkeypatch):
    # With is_broker false: GET /tester -> 404, POST /tester -> 404
    broker_app = create_app(settings(db_path=str(tmp_path / "broker.db")),
                            tools=[], stream_fn=scripted())
    monkeypatch.setattr(broker_app.state.appctx.google_web, "is_broker", lambda: False)
    async with Running(broker_app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        get_res = await c.get("/tester")
        assert get_res.status_code == 404
        post_res = await c.post("/tester", data={"email": "user@example.com", "consent": "yes"})
        assert post_res.status_code == 404

    # With is_broker true:
    op_app = create_app(settings(db_path=str(tmp_path / "op.db")), tools=[], stream_fn=scripted())
    monkeypatch.setattr(op_app.state.appctx.google_web, "is_broker", lambda: True)
    async with Running(op_app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        # GET /tester -> 200, HTML has form, CSP header has form-action 'self'
        get_ok = await c.get("/tester")
        assert get_ok.status_code == 200
        assert '<form class="tester-form"' in get_ok.text
        assert "form-action 'self'" in get_ok.headers["content-security-policy"]

        # GET /support keeps form-action 'none'
        sup_res = await c.get("/support")
        assert sup_res.status_code == 200
        assert "form-action 'none'" in sup_res.headers["content-security-policy"]

        # POST valid -> 303 with sent=sent
        p1 = await c.post("/tester", data={"email": "tester@example.com", "consent": "yes", "lang": "ko"},
                          follow_redirects=False)
        assert p1.status_code == 303
        assert p1.headers["location"] == "/tester?lang=ko&sent=sent#form"

        # POST again -> 303 with sent=again
        p2 = await c.post("/tester", data={"email": "tester@example.com", "consent": "yes", "lang": "ko"},
                          follow_redirects=False)
        assert p2.status_code == 303
        assert p2.headers["location"] == "/tester?lang=ko&sent=again#form"

        # POST without consent -> 303 with sent=consent
        p3 = await c.post("/tester", data={"email": "tester2@example.com", "lang": "ko"},
                          follow_redirects=False)
        assert p3.status_code == 303
        assert p3.headers["location"] == "/tester?lang=ko&sent=consent#form"


async def test_a_new_sign_up_also_notifies_the_operator_and_a_repeat_does_not(tmp_path):
    """Gmail hides a mail its own account sent to an address that forwards back to it, so the operator also gets an app notification."""
    store = Store(str(tmp_path / "testers.db"))
    notes: list[tuple[str, str]] = []

    async def mailer(to: str, subject: str, body: str, reply_to: str) -> str:
        return "ok"

    desk = TesterDesk(store, mailer, notify=lambda title, body: notes.append((title, body)))
    assert await desk.sign_up("a@example.com", "Galaxy S24", "ko", True, "1.1.1.1") is True
    assert len(notes) == 1 and "a@example.com" in notes[0][0] and "Galaxy S24" in notes[0][1]
    assert await desk.sign_up("a@example.com", "Galaxy S25", "ko", True, "1.1.1.2") is False   # a repeat: no second notice
    assert len(notes) == 1

    def broken(title: str, body: str) -> None:
        raise RuntimeError("push down")

    desk2 = TesterDesk(store, mailer, notify=broken)
    assert await desk2.sign_up("b@example.com", "", "en", True, "1.1.1.3") is True   # a failing notice never fails the sign-up


async def test_the_operator_notice_is_in_the_owners_language_not_the_visitors(tmp_path):
    from assistant import i18n
    store = Store(str(tmp_path / "testers.db"))
    notes: list[tuple[str, str]] = []

    async def mailer(to: str, subject: str, body: str, reply_to: str) -> str:
        return "ok"

    i18n.set_fallback(lambda: "ko")
    token = i18n.use("en")   # an English visitor's page
    try:
        desk = TesterDesk(store, mailer, notify=lambda title, body: notes.append((title, body)))
        await desk.sign_up("en@example.com", "Pixel", "en", True, "2.2.2.2")
    finally:
        i18n.reset(token)
        i18n.set_fallback(None)
    assert notes and notes[0][0].startswith("새 테스터 신청")


def test_a_tester_sign_up_notice_may_go_out_as_a_push():
    from assistant.push import check_data
    assert check_data({"type": "beta_request", "id": "79"}) == {"type": "beta_request", "id": "79"}


async def test_operator_beta_list_carries_the_tester_sign_ups(tmp_path):
    from test_server import AUTH
    app = create_app(settings(db_path=str(tmp_path / "op.db")), tools=[], stream_fn=scripted())
    desk = app.state.tester_desk
    desk.mailer = None
    await desk.sign_up("one@example.com", "Pixel 8", "en", True, "1.1.1.1")
    await desk.sign_up("two@example.com", "Galaxy", "ko", True, "1.1.1.2")
    one, two = sorted(desk.rows(), key=lambda r: r["id"])
    assert [r["email"] for r in desk.rows()] == ["two@example.com", "one@example.com"]   # newest first
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        got = (await c.get("/api/operator/beta")).json()
        rows = got["requests"]
        assert got["open"] == 2 and [r["id"] for r in rows] == [-two["id"], -one["id"]]   # waiting first, then newest
        r0 = rows[0]
        assert r0["kind"] == "play_tester" and r0["products"] == ["play_test"] and r0["app_version"] == "Galaxy"
        assert r0["language"] == "ko" and r0["status"] == "requested" and r0["handled_at"] is None and r0["notice"] == "failed"
        # approve = marked added: no longer waiting, sorts after the open one
        ans = (await c.post(f"/api/operator/beta/{-two['id']}", json={"action": "approve"})).json()
        assert ans["request"]["status"] == "approved" and ans["mailed"] is False and ans["mail_error"] == ""
        got = (await c.get("/api/operator/beta")).json()
        assert got["open"] == 1 and [r["id"] for r in got["requests"]] == [-one["id"], -two["id"]]
        # decline removes, and answers the row as it was
        ans = (await c.post(f"/api/operator/beta/{-one['id']}", json={"action": "decline"})).json()
        assert ans["request"]["status"] == "declined" and ans["request"]["email"] == "one@example.com"
        assert desk.row(one["id"]) is None
        # delete removes
        assert (await c.delete(f"/api/operator/beta/{-two['id']}")).json() == {"deleted": True}
        assert (await c.get("/api/operator/beta")).json()["requests"] == []
        # unknown ids
        assert (await c.post("/api/operator/beta/-999", json={"action": "approve"})).status_code == 404
        assert (await c.post("/api/operator/beta/-999", json={"action": "decline"})).status_code == 404
        assert (await c.delete("/api/operator/beta/-999")).status_code == 404
    # the list is the owner's only
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as anon:
        assert (await anon.get("/api/operator/beta")).status_code == 401
        assert (await anon.post("/api/operator/beta/-1", json={"action": "approve"})).status_code == 401
        assert (await anon.delete("/api/operator/beta/-1")).status_code == 401


async def test_notices_whose_mail_failed_are_sent_again_once_the_mailbox_works(tmp_path):
    store = Store(str(tmp_path / "testers.db"))
    working = [False]
    sent: list[str] = []

    async def mailer(to: str, subject: str, body: str, reply_to: str) -> str:
        if not working[0]:
            raise RuntimeError("the mailbox sign-in expired")
        sent.append(reply_to)
        return "ok"

    desk = TesterDesk(store, mailer)
    for i in range(3):
        assert await desk.sign_up(f"t{i}@example.com", "phone", "ko", True, f"1.2.3.{i}")
    assert sent == [] and all(not r["mailed"] for r in desk.rows())
    assert await desk.retry_unsent() == 0                 # still broken: nothing changes, nothing raises
    working[0] = True
    assert await desk.retry_unsent() == 3
    assert sorted(sent) == ["t0@example.com", "t1@example.com", "t2@example.com"] and all(r["mailed"] for r in desk.rows())
    assert await desk.retry_unsent() == 0 and len(sent) == 3   # never twice
