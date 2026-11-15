"""Regression tests for a sign-in cut off by restarts (2026-11-10, owner's report; general rules, no site names):
- the reply that goes on after a login is saved keeps the owner's language and the original request, and settles the cut-off
  reply's Continue offer (one continuation, not two);
- resume notes carry the answer language;
- a verification hand-over ends "solved" only once the page is past signing in (a move to another verification step keeps it open);
- a saved login reads "checking" until the sign-in with it tells how it went."""

import asyncio
import json

import httpx

from assistant import server as server_mod
from assistant.agent import Tool, handover
from assistant.providers import Done, TextDelta, ToolCall
from assistant.resume import MODEL_NOTE, RESTART_NOTE, with_language
from assistant.secret_requests import SecretRequests
from assistant.server import create_app
from assistant.signin_state import still_signing_in
from assistant.store import Store

from test_agent_profile import app_settings
from test_browser_more import CheckPage, probe
from test_handover import READ_THEN_ANSWER, browsing, scripted, waiting_item
from test_secret_requests import PASSWORD, USER, Model, make_app, talk
from test_server import AUTH, Running, follow


def test_resume_notes_name_the_answer_language():
    for note in (RESTART_NOTE, MODEL_NOTE):
        got = with_language(note, "ko")
        assert got.startswith(note) and "answer in Korean" in got and "not the user's words" in got
    assert "answer in Simplified Chinese" in with_language(MODEL_NOTE, "zh-Hans")


def test_still_signing_in_needs_positive_evidence():
    assert still_signing_in(None) and still_signing_in({}) and still_signing_in({"kelo_check": 0})
    assert still_signing_in(probe(url="https://site.example.com/home", secret_box=True))           # a password box
    assert still_signing_in(probe(url="https://site.example.com/home", otp=1))                     # a one-time code box
    assert still_signing_in(probe(url="https://accounts.example.com/login/two-step", text="Approve"))   # a sign-in address
    assert still_signing_in(probe(url="https://site.example.com/x", title="2-Step Verification"))
    assert not still_signing_in(probe(url="https://site.example.com/gift/home", title="Gifts", text="Welcome back"))


async def test_a_verification_whose_step_only_moved_on_ends_progressed_never_solved(tmp_path):
    """The browser's `gone` (wait_verification_gone) is the one judge (2026-11-10): True only on signed-in evidence, "progressed"
    when the user's step left the page but the sign-in goes on. "progressed" closes the card without "signed in" and gives control
    back to the agent (an approved Kakao sign-in waited on its "continue" page while the card said nothing could be done)."""
    page, sess, _ = browsing()
    sess.raw_page = lambda key: page
    release = asyncio.Event()

    async def sign_in(args):
        async def gone():
            await release.wait()
            return "progressed"
        return await handover.get()({"tab": "owner", "site": "accounts.example.com", "vendor": "verification",
                                     "kind": "verification", "name": "Approve the sign-in on your phone"}, gone)

    tool = Tool("browser_read", "read the page", {"type": "object"}, sign_in)
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        tid = (await c.post("/api/turns", json={"text": "로그인해 줘"})).json()["id"]
        item = await waiting_item(c)
        await asyncio.sleep(0.3)
        assert (await c.get("/api/approvals/pending")).json()["items"][0]["approval_id"] == item["approval_id"]   # still open
        release.set()
        events = await follow(c, tid)
        assert [e["state"] for e in events if e["type"] == "human_check"][-1] == "progressed"


def test_a_saved_login_reads_checking_until_the_sign_in_tells(tmp_path):
    store = Store(tmp_path / "s.db")
    reqs = SecretRequests(store)
    store.save_credential("shop", "Shop", ["shop.example.com"], {}, kind="login")
    rid = reqs.create("login", name="shop", label="Shop", hosts=["shop.example.com"])["id"]
    reqs.answer(rid, "saved", {"mode": "after_success"})
    assert reqs.get(rid)["sign_in"] == "checking"
    store.set_credential_result("shop", "code_needed", "a verification step is open")
    assert reqs.get(rid)["sign_in"] == "code_needed"
    store.set_credential_result("shop", "ok", "")
    assert reqs.get(rid)["sign_in"] == "signed_in"
    other = reqs.create("key", name="k", label="K", hosts=["api.example.com"])["id"]
    reqs.answer(other, "saved", {"checked": "ok"})
    assert "sign_in" not in reqs.get(other)


async def test_the_reply_after_a_saved_login_continues_the_original_request_in_the_owners_language(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.ENTRY_WAIT_SECONDS", 0.2)
    model = Model([ToolCall("c1", "request_login", json.dumps({"site": "https://shop.example.com/login", "label": "Example shop"})),
                   Done("tool_calls")], [TextDelta("아래에 넣어 주세요."), Done("stop")], [TextDelta("이어서 할게요."), Done("stop")])
    app = make_app(tmp_path, model)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await c.put("/api/language", json={"language": "ko"})
        await talk(c, "선물하기 체험 페이지에서 쿠폰 받아 줘")
        req = (await c.get("/api/secret-requests")).json()["items"][0]
        store = app.state.secret_requests.store
        from assistant.activity import Activity
        act = Activity(store)
        last = store._all("SELECT MAX(id) AS m FROM messages")[0]["m"]
        act.add_offer(int(last), int(req["task_id"]), "restart", "이어서 하기")   # the asking reply was cut off by a restart
        r = (await c.put(f"/api/vault/{req['name']}/login", json={"username": USER, "password": PASSWORD, "mode": "after_success"})).json()
        assert r["reply"] == "started"
        await follow(c, r["turn_id"])
        note = model.seen[-1][-1]["content"]
        assert "선물하기 체험 페이지에서 쿠폰 받아 줘" in note and "answer in Korean" in note
        assert act.offer_for_task(int(req["task_id"])) is None                    # the Continue offer is used up: one continuation
        assert (await c.get(f"/api/secret-requests")).json()["items"] == [] 
        got = app.state.secret_requests.get(req["id"])
        assert got["state"] == "saved" and got["sign_in"] == "checking"         # saved, not yet signed in


def test_the_prompt_asks_to_remember_the_browser_on_the_owners_own_sign_ins(tmp_path):
    """(e) Remember this browser / device on the owner's own sign-ins: described by its effect (any wording, any language), with
    what it must not be taken for, and never on someone else's account. Since the prompt diet (2026-11-11) the rule is said once, on
    browser_login (always shown, and the only way the owner's sign-in is made); the keychain paragraph only points to browser_login."""
    from pathlib import Path
    from assistant.server import Settings, create_app
    from test_server import TOKEN
    root = Path(__file__).resolve().parents[1]
    text = (root / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    para = next(p for p in text.split("\n\n") if p.startswith("**Logins and keys"))
    assert "browser_login" in para and "remember this browser" not in text
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host="127.0.0.1", home_dir=str(tmp_path / "home"),
                              content_dir=str(root / "content")))
    desc = next(t.description for t in app.state.tools if t.name == "browser_login")
    for must in ("keep this browser signed in", "any wording, any language", "marketing or cookie consent", "recovery phone",
                 "security settings", "never someone else's", "skip verification next time", "not saving the password"):
        assert must in desc, must


def test_the_browser_profile_lives_on_a_kept_volume():
    """Remembering a browser is useless if its profile is lost: the profile (cookies, local storage) is under /data, a named volume
    that a restart or `assistant update` (compose up) keeps; the agent works in that profile's default context."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "--user-data-dir=/data/profile" in (root / "docker" / "entrypoint.sh").read_text()
    compose = (root / "compose.yml").read_text()
    assert '"browser-data:/data"' in compose and "\n  browser-data:" in compose
    assert "self._browser.contexts[0] if self._browser.contexts" in (root / "src" / "assistant" / "tools" / "browser.py").read_text()


async def test_a_click_the_request_covered_is_in_the_approval_log(tmp_path):
    from assistant.activity import DECISIONS
    assert "progressed" in DECISIONS
    app = create_app(app_settings(tmp_path), tools=[])
    app.state.orders.on_request_use("browser_click", "apply", "event.example.com", "체험단 신청해 줘")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        hist = (await c.get("/api/approvals/history")).json()["items"]
    assert hist[0]["tool"] == "browser_click" and hist[0]["decision"] == "allowed_by_rule" and hist[0]["site"] == "event.example.com"
    assert "체험단 신청해 줘" in hist[0]["detail"]


async def test_a_solved_sign_in_verification_marks_the_login_that_stopped_at_it_as_ok(tmp_path):
    """fill_login said code_needed (a verification step was open); the hand-over that follows ends solved on signed-in evidence:
    the login's result, its vault log and its fill-in card follow (ok / used / sign_in "signed_in"). Another site's login is left."""
    async def sign_in(args):
        async def gone():
            return True
        return await handover.get()({"tab": "owner", "site": "accounts.example.com", "vendor": "verification", "kind": "verification",
                                     "name": "Approve the sign-in on your phone", "step": "approve"}, gone)

    tool = Tool("browser_read", "read the page", {"type": "object"}, sign_in)
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=scripted(*READ_THEN_ANSWER))
    store = app.state.secret_requests.store
    reqs = app.state.secret_requests
    for name, host in (("ex", "login.example.com"), ("other", "other.test")):
        store.save_credential(name, name, [host], {}, kind="login")
        store.set_login(name, USER, PASSWORD)
        store.set_credential_result(name, "code_needed", "a verification step is open")
    rid = reqs.create("login", name="ex", label="Example", hosts=["login.example.com"], source="keychain")["id"]   # no carry-on reply
    reqs.answer(rid, "saved", {"mode": "after_success", "sign_in": "code_needed"})
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "로그인해 줘"})).json()["id"])
    assert [e["state"] for e in events if e["type"] == "human_check"][-1:] == ["solved"], [e["type"] for e in events]
    got = {c["name"]: c["last_result"] for c in store.credentials()}
    assert got == {"ex": "ok", "other": "code_needed"}
    assert reqs.get(rid)["sign_in"] == "signed_in"
    log = store._all("SELECT item, action, ok FROM vault_log WHERE item = 'ex' ORDER BY id DESC LIMIT 1")[0]
    assert (log["action"], log["ok"]) == ("used", 1)
