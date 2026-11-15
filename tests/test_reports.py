"""Reports of a bad AI reply: the phone -> its own server -> the operator's broker (/broker/reports), the operator's own copy
storing directly, the owner-only list, rate limits and size limits. Everything runs against local servers; nothing reaches a
real service and no model is ever asked anything."""

import json
from urllib.parse import urlsplit

import httpx

from assistant import reports as reports_mod
from assistant.netguard import NetGuardError, Reply
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running

BROKER = "https://broker.example.org"


def never_called(*a, **kw):
    raise AssertionError("no model may be asked anything here")


def make_app(tmp_path, *, name, public_url="", broker=BROKER, fetcher=None):
    s = Settings(token=TOKEN, provider="anthropic", model="claude-x", cdp_host=None, db_path=str(tmp_path / f"{name}.db"),
                 report_broker=broker, public_url=public_url)
    return create_app(s, tools=[], stream_fn=never_called, report_fetcher=fetcher)


def through(op_url, seen=None):
    """A fetcher that hands requests for the broker's address to the operator's local server."""
    async def fetch(method, url, *, allowed_hosts, headers=None, content=None):
        assert urlsplit(url).hostname in allowed_hosts and url.startswith(BROKER + "/broker/reports")
        if seen is not None:
            seen.append(json.loads(content))
        async with httpx.AsyncClient(base_url=op_url) as hop:
            r = await hop.request(method, url.removeprefix(BROKER), headers=headers, content=content)
        return Reply(r.status_code, dict(r.headers), r.content, False)
    return fetch


async def pair(c, name="phone"):
    code = (await c.post("/api/devices/pair", headers=AUTH, json={})).json()["code"]
    got = (await c.post("/api/pair", json={"code": code, "name": name})).json()
    return {"Authorization": f"Bearer {got['token']}"}


REPORT = {"reason": "harmful", "reply": "이건 해로운 답이에요.", "asked": "질문했어요", "comment": "불쾌해요", "app_version": "0.1.0"}


async def test_a_copy_forwards_a_report_to_the_broker_which_keeps_only_what_was_sent(tmp_path):
    op = make_app(tmp_path, name="op", public_url=BROKER)
    op_run = Running(op)
    seen: list = []
    friend = make_app(tmp_path, name="friend", fetcher=through(op_run.url, seen))
    async with op_run as o, httpx.AsyncClient(base_url=o.url) as oc, Running(friend) as f, httpx.AsyncClient(base_url=f.url) as c:
        phone = await pair(c)
        r = await c.post("/api/reports", headers=phone, json={**REPORT, "provider": "fake", "model": "fake", "extra": "dropped"})
        assert r.status_code == 200 and r.json()["to"] == "developer"
        # what left the copy: the install secret and the report fields, nothing else
        assert set(seen[0]) == {"install_id", "report"}
        assert set(seen[0]["report"]) == {"reason", "reply", "comment", "asked", "app_version", "provider", "model"}
        assert seen[0]["report"]["model"] == "claude-x" and seen[0]["report"]["provider"] == "anthropic"   # the server's own setting
        # the operator's list: owner token only; a paired phone of the operator gets 403
        listed = (await oc.get("/api/operator/reports", headers=AUTH)).json()
        assert listed["receiving"] is True and listed["open"] == 1 and listed["keep_days"] == 365
        rep = listed["reports"][0]
        assert rep["reply"] == REPORT["reply"] and rep["asked"] == REPORT["asked"] and rep["comment"] == "불쾌해요"
        assert rep["model"] == "claude-x" and rep["app_version"] == "0.1.0" and rep["handled"] is False
        install = friend.state.reports.install_id()
        row = op.state.report_box.store._all("SELECT * FROM operator_reports")[0]
        assert row["install"] != install and len(row["install"]) == 64           # only the hash
        assert "127.0.0.1" not in json.dumps(row)                                 # no address kept
        op_phone = await pair(oc)
        assert (await oc.get("/api/operator/reports", headers=op_phone)).status_code == 403
        assert (await oc.get("/api/operator/reports")).status_code == 401
        # mark handled, filter, then delete
        assert (await oc.post(f"/api/operator/reports/{rep['id']}", headers=AUTH, json={"handled": True})).json()["handled"] is True
        assert (await oc.get("/api/operator/reports?handled=false", headers=AUTH)).json()["reports"] == []
        assert (await oc.delete(f"/api/operator/reports/{rep['id']}", headers=AUTH)).json() == {"deleted": True}
        assert (await oc.delete(f"/api/operator/reports/{rep['id']}", headers=AUTH)).status_code == 404
        # the copy that is not the operator stores nothing itself
        assert (await c.get("/api/operator/reports", headers=AUTH)).json() == {"reports": [], "open": 0, "keep_days": 365, "receiving": False}


async def test_the_operator_stores_its_own_reports_directly(tmp_path):
    def no_fetch(*a, **kw):
        raise AssertionError("the operator does not call itself")
    op = make_app(tmp_path, name="op", public_url=BROKER + "/", fetcher=no_fetch)
    async with Running(op) as o, httpx.AsyncClient(base_url=o.url) as c:
        r = await c.post("/api/reports", headers=AUTH, json={"reason": "inaccurate", "reply": "틀린 답"})
        assert r.json()["to"] == "here"
        assert (await c.get("/api/operator/reports", headers=AUTH)).json()["reports"][0]["reason"] == "inaccurate"


async def test_unreachable_broker_gives_a_readable_error(tmp_path):
    async def down(*a, **kw):
        raise NetGuardError("connection refused")
    app = make_app(tmp_path, name="c", fetcher=down)
    async with Running(app) as s, httpx.AsyncClient(base_url=s.url) as c:
        r = await c.post("/api/reports", headers={**AUTH, "Accept-Language": "ko"}, json=REPORT)
        assert r.status_code == 502 and "다시 보내" in r.json()["detail"]
        assert (await c.post("/api/reports", json=REPORT)).status_code == 401
    off = make_app(tmp_path, name="d", broker="")
    async with Running(off) as s, httpx.AsyncClient(base_url=s.url) as c:
        assert (await c.post("/api/reports", headers=AUTH, json=REPORT)).status_code == 503


async def test_checks_and_size_limits(tmp_path):
    app = make_app(tmp_path, name="c", fetcher=never_called)
    async with Running(app) as s, httpx.AsyncClient(base_url=s.url) as c:
        assert (await c.post("/api/reports", headers=AUTH, json={**REPORT, "reason": "spam"})).status_code == 422
        assert (await c.post("/api/reports", headers=AUTH, json={**REPORT, "reply": "  "})).status_code == 422
        assert (await c.post("/api/reports", headers=AUTH, json={**REPORT, "comment": "x" * 1001})).status_code == 413
        assert (await c.post("/api/reports", headers=AUTH, content=b"{" + b"x" * 60_000)).status_code == 413
        # the broker checks the same things, without any user token
        assert (await c.post("/broker/reports", json={"install_id": "short", "report": REPORT})).status_code == 403
        assert (await c.post("/broker/reports", json={"install_id": "a" * 40, "report": {**REPORT, "reply": ""}})).status_code == 422
        assert (await c.post("/broker/reports", content=b"x" * 60_000)).status_code == 413


def test_broker_rate_limits_per_install_and_per_address(tmp_path):
    from assistant.store import Store
    now = [1000.0]
    box = reports_mod.ReportBox(Store(str(tmp_path / "b.db")), wall=lambda: now[0])
    broker = reports_mod.ReportBroker(box, clock=lambda: now[0])
    body = {"install_id": "i" * 40, "report": REPORT}
    for _ in range(reports_mod.PER_INSTALL_HOUR):
        broker.handle(body, "1.2.3.4")
    try:
        broker.handle(body, "1.2.3.4")
        raise AssertionError("should be limited")
    except reports_mod.ReportError as e:
        assert e.status == 429 and e.retry_after and e.retry_after > 0
    now[0] += 3601                                         # an hour later the copy may send again
    broker.handle(body, "1.2.3.4")
    # many new copies from one address
    for n in range(reports_mod.NEW_INSTALLS_PER_IP_DAY - 1):
        broker.handle({"install_id": f"{n:02d}" + "n" * 40, "report": REPORT}, "5.6.7.8")
    try:
        broker.handle({"install_id": "z" * 40, "report": REPORT}, "5.6.7.8")
        broker.handle({"install_id": "y" * 40, "report": REPORT}, "5.6.7.8")
        raise AssertionError("should be limited")
    except reports_mod.ReportError as e:
        assert e.status == 429
    # a year later the old reports are gone
    assert box.list()["reports"]
    now[0] += 366 * 86400
    assert box.list()["reports"] == []
