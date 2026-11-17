import httpx
import pytest
from cryptography.fernet import Fernet

from assistant.netguard import Reply
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.browser import classify_sign_in
from assistant.tools.connect_tools import connect_tools
from assistant.tools.vault_tools import vault_tools
from assistant.vault import Transient, record_http

from test_server import AUTH, TOKEN, Running
from test_vault import PASSWORD, USER, Page, session_for

KEY = Fernet.generate_key().decode()
LOGIN = "https://github.com/login"


# ------------------------------------------------------------------------------------------------ reading the page after Enter
@pytest.mark.parametrize("pw,after,kw,want", [
    (False, "https://x.com/secure", {}, "success"),                                            # the sign-in page was left
    (False, "https://x.com/home", {}, "success"),
    (False, LOGIN, {}, "success"),                                                             # the password field is gone (checked again afterwards)
    (True, LOGIN, {"invalid_marked": True}, "failed"),                                         # the form is still there and a field is marked invalid
    (True, LOGIN, {}, "unknown"),                                                              # the form is still there and nothing marked
    (False, "https://x.com/2fa", {"otp_boxes": 6}, "code_needed"),                             # a one-time-code input by structure
    (False, "https://x.com/otp", {"otp_boxes": 1}, "code_needed"),
    (True, LOGIN, {"otp_boxes": 1, "invalid_marked": True}, "failed"),                         # the form is still there: it was refused
])
def test_the_page_after_a_sign_in_is_read_by_structure_as_success_failure_or_a_code_prompt(pw, after, kw, want):
    assert classify_sign_in(before_url=LOGIN, after_url=after, password_visible=pw, **kw)[0] == want


def test_the_words_of_the_page_decide_nothing():
    assert classify_sign_in(before_url=LOGIN, after_url=LOGIN, password_visible=True, text="Your password is invalid!")[0] == "unknown"
    assert classify_sign_in(before_url=LOGIN, after_url=LOGIN, password_visible=True, text="Welcome, Log out")[0] == "unknown"


def test_a_bot_check_found_by_structure_blocks_whatever_the_page_says():
    assert classify_sign_in(before_url=LOGIN, after_url=LOGIN, password_visible=True, text="Sign in", challenge="reCAPTCHA") == \
        ("blocked", "the site shows a human check (reCAPTCHA)")


# ------------------------------------------------------------------------------------------------ what the tool does with it
def setup(tmp_path, state=None, found=None, url=LOGIN, wait=0.0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    st = Store(tmp_path / "a.db", KEY)
    st.save_credential("github", "GitHub", ["github.com"], {}, kind="login")
    t = Transient()
    page = Page(url=url, state=state, found=found)
    session = session_for(page)
    session.sign_in_wait = wait
    tools = {x.name: x for x in vault_tools(st, session, t)}
    return st, t, tools, page


WRONG = {"pw": True, "text": "Your password is invalid!", "invalid": True}
BLOCKED = {"pw": True, "text": "Please complete the CAPTCHA",
           "probe": {"kelo_check": 1, "title": "Sign in", "marks": {}, "frames": [
               {"src": "https://www.google.com/recaptcha/api2/bframe?k=x", "w": 400, "h": 580, "shown": True}]}}
CODE = {"pw": False, "text": "Enter the verification code we sent to your phone", "otp": 6}
UNSURE = {"pw": True, "text": "Sign in to GitHub"}


async def test_a_saved_login_the_site_accepts_is_remembered_as_working(tmp_path):
    st, t, tools, page = setup(tmp_path)
    st.set_login("github", USER, PASSWORD)
    out = await tools["browser_login"].handler({"name": "github"})
    assert out.startswith("[sign-in result: success") and PASSWORD not in out
    item = st.credentials()[0]
    assert item["last_result"] == "ok" and item["last_result_at"] and st.credential("github")["secret"] == PASSWORD


async def test_a_saved_login_the_site_turns_down_is_marked_and_not_tried_again_until_the_user_says_so(tmp_path):
    st, t, tools, page = setup(tmp_path, state=WRONG)
    st.set_login("github", USER, PASSWORD)
    first = await tools["browser_login"].handler({"name": "github"})
    assert first.startswith("[sign-in result: failed") and "will not be tried again until the user checks it" in first
    assert st.credentials()[0]["last_result"] == "failed" and len(page.filled) == 2
    again = await tools["browser_login"].handler({"name": "github"})
    assert again.startswith("error:") and "Do NOT try again" in again and len(page.filled) == 2                       # nothing was typed a second time
    assert st.vault_log(1)[0]["action"] == "denied"
    st.set_credential_result("github", None)                                                                         # the user pressed "try again"
    await tools["browser_login"].handler({"name": "github"})
    assert len(page.filled) == 4
    st.set_credential_result("github", "failed", "x")
    st.set_login("github", USER, PASSWORD + "-new")                                                                  # a new password has not failed yet
    assert st.credentials()[0]["last_result"] is None


@pytest.mark.parametrize("state,result", [(CODE, "code_needed"), (BLOCKED, "blocked"), (UNSURE, "unknown")])
async def test_other_outcomes_are_reported_to_the_assistant_and_do_not_stop_it_from_trying_again(tmp_path, state, result):
    st, t, tools, page = setup(tmp_path, state=state)
    st.set_login("github", USER, PASSWORD)
    out = await tools["browser_login"].handler({"name": "github"})
    assert out.startswith(f"[sign-in result: {'verification_pending' if result == 'code_needed' else result}") and st.credentials()[0]["last_result"] == result
    assert not (await tools["browser_login"].handler({"name": "github"})).startswith("error:")


async def test_a_login_held_until_it_works_is_saved_only_after_the_site_accepts_it(tmp_path):
    st, t, tools, page = setup(tmp_path)
    t.put("github", USER, PASSWORD, promote=True)
    assert "will be saved once a sign-in with it works" in await tools["vault_status"].handler({})
    assert st.credentials()[0]["has_secret"] is False
    out = await tools["browser_login"].handler({"name": "github"})
    assert "was saved to the keychain" in out and PASSWORD not in out
    item = st.credential("github")
    assert (item["username"], item["secret"], item["last_result"]) == (USER, PASSWORD, "ok") and not t.has("github")
    assert (st.vault_log(1)[0]["action"], st.vault_log(1)[0]["detail"]) == ("saved", "kept because the sign-in worked")


async def test_a_login_the_site_rejects_is_not_saved_and_the_assistant_is_told_not_to_retry(tmp_path):
    st, t, tools, page = setup(tmp_path, state=WRONG)
    t.put("github", USER, PASSWORD, promote=True)
    out = await tools["browser_login"].handler({"name": "github"})
    assert out.startswith("[sign-in result: failed") and "NOT saved" in out and "Do NOT try again" in out and PASSWORD not in out
    assert st.credentials()[0]["has_secret"] is False and not t.has("github")
    assert st.vault_log(1)[0]["action"] == "failed" and st.vault_log(1)[0]["ok"] is False
    gone = await tools["browser_login"].handler({"name": "github"})
    assert gone.startswith("error:") and "has not entered" in gone                                                       # nothing wrong was kept


async def test_a_login_held_until_it_works_survives_an_unclear_answer_and_a_code_prompt_counts_as_accepted(tmp_path):
    st, t, tools, page = setup(tmp_path, state=UNSURE)
    t.put("github", USER, PASSWORD, promote=True)
    await tools["browser_login"].handler({"name": "github"})
    assert t.has("github") and st.credentials()[0]["has_secret"] is False                                              # not proven either way: still held
    st2, t2, tools2, page2 = setup(tmp_path / "b", state=CODE)
    t2.put("github", USER, PASSWORD, promote=True)
    out = await tools2["browser_login"].handler({"name": "github"})
    assert "verification code" in out and st2.credential("github")["secret"] == PASSWORD and st2.credentials()[0]["last_result"] == "code_needed"


async def test_a_login_for_one_use_is_gone_after_the_password_step_whatever_happened(tmp_path):
    for state in (None, WRONG):
        st, t, tools, page = setup(tmp_path / f"d{bool(state)}", state=state)
        t.put("github", USER, PASSWORD)
        await tools["browser_login"].handler({"name": "github"})
        assert not t.has("github") and st.credentials()[0]["has_secret"] is False


async def test_a_username_only_step_proves_nothing_and_keeps_what_was_held(tmp_path):
    st, t, tools, page = setup(tmp_path, found={"u": True, "p": False})
    t.put("github", USER, PASSWORD, promote=True)
    out = await tools["browser_login"].handler({"name": "github", "step": "username"})
    assert out.startswith("[sign-in result: unknown") and t.has("github") and st.credentials()[0]["has_secret"] is False


async def test_the_status_the_assistant_sees_says_what_the_last_result_was_and_nothing_more(tmp_path):
    st, t, tools, page = setup(tmp_path)
    st.set_login("github", USER, PASSWORD)
    st.set_credential_result("github", "failed", "the page still shows the sign-in form and says the user name or password is wrong")
    out = await tools["vault_status"].handler({})
    assert "last result: failed (the page still shows" in out and PASSWORD not in out and USER not in out


# ------------------------------------------------------------------------------------------------ keys for services
def test_what_a_service_says_to_a_key_is_remembered_and_a_refusal_comes_with_advice(tmp_path):
    st = Store(tmp_path / "a.db", KEY)
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    assert record_http(st, "acme", 200, "connector_get") == "" and st.credentials()[0]["last_result"] == "ok"
    hint = record_http(st, "acme", 401, "connector_get")
    assert "refused the key (HTTP 401)" in hint and "Do not keep trying" in hint
    assert (st.credentials()[0]["last_result"], st.credentials()[0]["last_result_detail"]) == ("rejected", "HTTP 401")
    assert record_http(st, "acme", 500, "x") == "" and st.credentials()[0]["last_result"] == "rejected"           # a server error says nothing about the key
    assert st.vault_log(1)[0]["action"] == "rejected" and st.vault_log(1)[0]["ok"] is False
    st.set_credential_secret("acme", "sk-live-NEW123456789")
    assert st.credentials()[0]["last_result"] is None


class Fake:
    def __init__(self, status):
        self.status, self.calls = status, []

    async def __call__(self, method, url, **kw):
        self.calls.append(url)
        return Reply(self.status, {"content-type": "application/json"}, b"{}", False)


async def test_the_connector_tools_record_the_answer_and_tell_the_assistant_when_the_key_is_refused(tmp_path):
    st = Store(tmp_path / "a.db", KEY)
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    ok = {t.name: t for t in connect_tools(st, None, fetcher=Fake(200))}
    assert "refused" not in await ok["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1"}) and st.credentials()[0]["last_result"] == "ok"
    bad = {t.name: t for t in connect_tools(st, None, fetcher=Fake(401))}
    out = await bad["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1"})
    assert out.startswith("HTTP 401") and "refused the key" in out and st.credentials()[0]["last_result"] == "rejected"
    out = await bad["connector_send"].handler({"name": "acme", "url": "https://api.acme.com/v1", "json": {}})
    assert "refused the key" in out


# ------------------------------------------------------------------------------------------------ the app
def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home")), tools=[])


async def test_the_app_can_hold_a_login_until_it_works_and_can_let_the_assistant_try_again(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})
        r = await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD, "mode": "after_success"})
        assert r.json() == {"ok": True, "saved": False, "mode": "after_success", "reply": "none", "request": None}
        v = (await c.get("/api/vault")).json()
        assert v["verifying"] == ["github"] and v["entered_once"] == ["github"] and v["items"] == [] and [i["name"] for i in v["pending"]] == ["github"]
        assert (await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD, "mode": "once"})).json()["mode"] == "once"
        assert (await c.get("/api/vault")).json()["verifying"] == []
        assert (await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD, "mode": "sometimes"})).status_code == 422
        await c.put("/api/vault/github/login", json={"username": USER, "password": PASSWORD, "mode": "now"})
        assert (await c.get("/api/vault")).json()["items"][0]["has_secret"] is True
        # the user clears a failure
        store_items = (await c.patch("/api/vault/github", json={"clear_result": True})).json()
        assert store_items[0]["last_result"] is None
        assert (await c.get("/api/vault/log")).json()[0]["detail"] == "the user allowed trying again"


async def test_a_held_login_whose_sign_in_was_unclear_is_saved_once_the_browser_is_signed_in_on_the_site(tmp_path, monkeypatch):
    import asyncio
    from assistant.tools import vault_tools as vt
    monkeypatch.setattr(vt, "CONFIRM_DELAYS", (0.0, 0.0))
    st, t, tools, page = setup(tmp_path, state=UNSURE)
    t.put("github", USER, PASSWORD, promote=True)
    await tools["browser_login"].handler({"name": "github"})
    page.url, page.state = "https://github.com/settings/profile", {"pw": False}   # the site went on to a signed-in page
    for _ in range(5):
        await asyncio.sleep(0)
    assert st.credential("github")["secret"] == PASSWORD and not t.has("github")
    assert st.vault_log(1)[0]["detail"] == "kept because the browser was signed in on the site afterwards"


async def test_a_held_login_still_on_the_sign_in_page_is_not_saved(tmp_path, monkeypatch):
    import asyncio
    from assistant.tools import vault_tools as vt
    monkeypatch.setattr(vt, "CONFIRM_DELAYS", (0.0, 0.0))
    st, t, tools, page = setup(tmp_path, state=UNSURE)
    t.put("github", USER, PASSWORD, promote=True)
    await tools["browser_login"].handler({"name": "github"})
    for _ in range(5):
        await asyncio.sleep(0)
    assert st.credentials()[0]["has_secret"] is False and t.has("github")



async def test_a_sign_in_that_redirects_for_a_few_seconds_is_read_when_it_lands(tmp_path):
    """A site still on its sign-in page right after Enter (redirecting) is watched until it lands, not called unclear."""
    import asyncio
    st, t, tools, page = setup(tmp_path, state=UNSURE, wait=3.0)
    t.put("github", USER, PASSWORD, promote=True)

    async def land():
        await asyncio.sleep(0.6)
        page.url, page.state = "https://github.com/", {"pw": False, "text": "Signed in. Logout"}

    lander = asyncio.create_task(land())
    out = await tools["browser_login"].handler({"name": "github"})
    await lander
    assert "was saved to the keychain" in out and st.credential("github")["secret"] == PASSWORD
