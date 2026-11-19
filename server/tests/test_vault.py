import base64
import json
import sqlite3

import pytest
from cryptography.fernet import Fernet

from assistant import store as store_mod
from assistant.agent import ApprovalRequest, Tool, run_agent
from assistant.netguard import NetGuardError
from assistant.providers import Done, TextDelta, ToolCall
from assistant.store import Store
from assistant.tools.browser import BrowserPolicyError, BrowserSession, SignIn, browser_owner, classify_sign_in
from assistant.tools.vault_tools import site_host, vault_tools
from assistant.vault import LOG_KEEP, Sealer, Transient, VaultError, aad_for, mask_username, use_credential

KEY = Fernet.generate_key()
PASSWORD = "hunter2-Pa$$w0rd!"
USER = "nahyun.kim@example.com"


# ------------------------------------------------------------------------------------------------ sealing
def test_a_sealed_value_opens_only_where_it_was_sealed_for():
    s = Sealer(KEY)
    aad = aad_for("acme", "api", ["api.acme.com"], {"type": "bearer_header"})
    sealed = s.seal("sk-live-123", aad)
    assert sealed.startswith("v2:") and "sk-live-123" not in sealed and s.seal("sk-live-123", aad) != sealed     # a fresh nonce every time
    assert s.open(sealed, aad) == "sk-live-123"
    for other in (aad_for("acme2", "api", ["api.acme.com"], {"type": "bearer_header"}),            # another name
                  aad_for("acme", "login", ["api.acme.com"], {"type": "bearer_header"}),            # another kind
                  aad_for("acme", "api", ["evil.example.net"], {"type": "bearer_header"}),          # another host
                  aad_for("acme", "api", ["api.acme.com"], {"type": "query_param", "param": "k"})):  # another placement
        assert s.open(sealed, other) is None
    assert aad_for("a", "api", ["b.com", "a.com"], {}) == aad_for("a", "api", ["a.com", "b.com"], {})      # the order of hosts does not matter


def test_tampering_a_wrong_key_and_foreign_formats_never_open():
    s = Sealer(KEY)
    aad = aad_for("x", "api", ["x.example.com"], {})
    sealed = s.seal("secret", aad)
    blob = bytearray(base64.urlsafe_b64decode(sealed[3:]))
    blob[len(blob) // 2] ^= 0x01                                                       # one bit of the ciphertext
    flipped = "v2:" + base64.urlsafe_b64encode(bytes(blob)).decode()
    assert s.open(flipped, aad) is None and s.open(sealed[:-8], aad) is None and s.open("", aad) is None
    assert Sealer(Fernet.generate_key()).open(sealed, aad) is None
    assert s.open(Fernet(KEY).encrypt(b"secret").decode(), aad) is None             # an older-format token is not a v2 value


@pytest.mark.parametrize("raw,shown", [("nahyun.kim@example.com", "na***@example.com"), ("bob", "bo***"), ("ab", "a***"), ("a@b.co", "a***@b.co"), ("", "")])
def test_a_user_name_is_masked_enough_to_tell_accounts_apart_and_no_more(raw, shown):
    assert mask_username(raw) == shown


def test_a_login_typed_for_one_use_lives_in_memory_ten_minutes_and_is_used_up():
    now = [100.0]
    t = Transient(ttl=600, clock=lambda: now[0])
    t.put("github", "u", "p")
    assert t.has("github") and t.peek("github") == ("u", "p") and t.peek("github") == ("u", "p")     # a two-page sign-in can look twice
    assert t.take("github") == ("u", "p") and not t.has("github") and t.take("github") is None
    t.put("github", "u", "p")
    now[0] += 601
    assert not t.has("github") and t.take("github") is None
    t.put("a", "u", "p")
    t.discard("a")
    assert t.peek("a") is None


# ------------------------------------------------------------------------------------------------ what is stored
def raw_db(path):
    return path.read_bytes() + b"".join(p.read_bytes() for p in path.parent.glob("assistant.db-*"))


def test_values_are_sealed_never_returned_and_bound_to_their_hosts(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db, KEY.decode())
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    st.save_credential("github", "GitHub", ["github.com"], {}, kind="login")
    assert st.set_login("github", USER, PASSWORD) and not st.set_login("acme", USER, PASSWORD)          # a login can only go into a login item
    assert b"sk-live-ABCDEF123456" not in raw_db(db) and PASSWORD.encode() not in raw_db(db) and USER.encode() not in raw_db(db)
    listing = json.dumps(st.credentials())
    assert "sk-live" not in listing and PASSWORD not in listing and USER not in listing and "na***@example.com" in listing
    got = st.credential("github")
    assert (got["username"], got["secret"], got["kind"]) == (USER, PASSWORD, "login") and st.credential("acme")["secret"] == "sk-live-ABCDEF123456"


def test_changing_the_allowed_host_in_the_database_makes_the_value_unreadable_instead_of_sending_it_elsewhere(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db, KEY.decode())
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    con = sqlite3.connect(db)
    con.execute("UPDATE credentials SET hosts = ? WHERE name = 'acme'", (json.dumps(["evil.example.net"]),))
    con.commit()
    con.close()
    assert st.credential("acme")["secret"] is None and st.credential("acme")["has_secret"] is True


def test_a_value_copied_from_one_item_to_another_does_not_open_there(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db, KEY.decode())
    st.save_credential("good", "Good", ["api.good.com"], {"type": "bearer_header"})
    st.save_credential("evil", "Evil", ["api.evil.example.net"], {"type": "bearer_header"})
    st.set_credential_secret("good", "sk-good-ABCDEF123456")
    con = sqlite3.connect(db)
    con.execute("UPDATE credentials SET secret = (SELECT secret FROM credentials WHERE name='good') WHERE name='evil'")
    con.commit()
    con.close()
    assert st.credential("evil")["secret"] is None and st.credential("good")["secret"] == "sk-good-ABCDEF123456"


def test_re_registering_keeps_the_value_only_when_nothing_it_is_bound_to_changed(tmp_path):
    st = Store(tmp_path / "a.db", KEY.decode())
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    st.save_credential("acme", "Acme Inc", ["api.acme.com"], {"type": "bearer_header"})       # a new label only
    assert st.credential("acme")["secret"] == "sk-live-ABCDEF123456" and st.credential("acme")["label"] == "Acme Inc"
    st.save_credential("acme", "Acme Inc", ["api.other.com"], {"type": "bearer_header"})      # a new host
    assert st.credential("acme")["secret"] is None and st.credential("acme")["has_secret"] is False
    with pytest.raises(VaultError, match="already used"):
        st.save_credential("acme", "x", ["github.com"], {}, kind="login")


def test_a_value_in_the_older_format_still_opens_and_is_sealed_again_in_the_new_one(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db, KEY.decode())
    st.save_credential("old", "Old", ["api.old.com"], {"type": "bearer_header"})
    legacy = Fernet(KEY).encrypt(b"sk-from-before").decode()
    con = sqlite3.connect(db)
    con.execute("UPDATE credentials SET secret = ? WHERE name = 'old'", (legacy,))
    con.commit()
    con.close()
    assert st.credential("old")["secret"] == "sk-from-before"
    con = sqlite3.connect(db)
    now = con.execute("SELECT secret FROM credentials WHERE name='old'").fetchone()[0]
    con.close()
    assert now.startswith("v2:") and st.credential("old")["secret"] == "sk-from-before"


def test_a_database_from_before_the_vault_gains_its_columns_and_keeps_its_rows(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE credentials (name TEXT PRIMARY KEY, label TEXT NOT NULL, hosts TEXT NOT NULL, placement TEXT NOT NULL, "
                      "endpoint TEXT NOT NULL DEFAULT '', secret TEXT, created_at REAL NOT NULL);")
    con.execute("INSERT INTO credentials VALUES('acme','Acme','[\"api.acme.com\"]','{\"type\":\"bearer_header\"}','',NULL,1.0)")
    con.commit()
    con.close()
    st = Store(db, KEY.decode())
    only = st.credentials()[0]
    assert (only["name"], only["kind"], only["auto_allow"], only["last_used"]) == ("acme", "api", False, None)


def test_the_master_key_is_shared_so_a_different_key_opens_nothing(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db, KEY.decode())
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    again = Store(db, Fernet.generate_key().decode())
    assert again.credential("acme")["secret"] is None and again.credential("acme")["has_secret"] is True


def test_settings_for_a_login_and_the_use_marker_and_the_label(tmp_path):
    st = Store(tmp_path / "a.db", KEY.decode())
    st.save_credential("github", "GitHub", ["github.com"], {}, kind="login")
    assert st.set_auto_allow("github", True) and st.credential("github")["auto_allow"] is True and not st.set_auto_allow("nope", True)
    st.touch_credential("github")
    assert st.credentials()[0]["last_used"] > 1_700_000_000 and st.set_credential_label("github", "My GitHub") and st.credentials()[0]["label"] == "My GitHub"


# ------------------------------------------------------------------------------------------------ the log and the lock
def test_everything_is_logged_newest_first_and_only_the_latest_entries_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "LOG_KEEP", 25)
    st = Store(tmp_path / "a.db", KEY.decode())
    for i in range(40):
        st.add_vault_log(f"item{i}", "used", "x" * 400, ok=i % 2 == 0)
    log = st.vault_log(100)
    assert len(log) == 25 and log[0]["item"] == "item39" and log[-1]["item"] == "item15" and len(log[0]["detail"]) == 300
    assert log[0]["ok"] is False and log[1]["ok"] is True and st.vault_log(3)[2]["item"] == "item37"
    assert LOG_KEEP == 1000


def test_the_lock_is_remembered(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db, KEY.decode())
    assert st.vault_locked() is False
    st.set_vault_locked(True)
    assert Store(db, KEY.decode()).vault_locked() is True
    st.set_vault_locked(False)
    assert st.vault_locked() is False


# ------------------------------------------------------------------------------------------------ one use by the agent
def filled(tmp_path):
    st = Store(tmp_path / "a.db", KEY.decode())
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    st.save_credential("github", "GitHub", ["github.com"], {}, kind="login")
    st.set_login("github", USER, PASSWORD)
    st.save_credential("empty", "Empty", ["api.empty.com"], {"type": "bearer_header"})
    return st


def test_a_use_is_allowed_logged_and_marked(tmp_path):
    st = filled(tmp_path)
    cred = use_credential(st, "acme", tool="connector_get", detail="GET api.acme.com/v1")
    assert cred["secret"] == "sk-live-ABCDEF123456"
    assert st.vault_log(1)[0] == {**st.vault_log(1)[0], "item": "acme", "action": "used", "detail": "connector_get GET api.acme.com/v1", "ok": True}
    assert st.credential("acme")["last_used"]


@pytest.mark.parametrize("name,kind,text", [("acme", "login", "service key"), ("github", "api", "website login"), ("empty", "api", "has not entered"), ("ghost", "api", "nothing named")])
def test_the_vault_refuses_the_wrong_kind_an_empty_item_and_a_missing_one_and_logs_the_refusal(tmp_path, name, kind, text):
    st = filled(tmp_path)
    with pytest.raises(VaultError, match=text):
        use_credential(st, name, tool="t", kind=kind)
    assert st.vault_log(1)[0]["action"] == "denied" and st.vault_log(1)[0]["ok"] is False


def test_a_locked_vault_refuses_everything(tmp_path):
    st = filled(tmp_path)
    st.set_vault_locked(True)
    with pytest.raises(VaultError, match="locked"):
        use_credential(st, "acme", tool="connector_get")
    assert issubclass(VaultError, NetGuardError)                                       # the tools already report these as errors
    assert st.vault_log(1)[0]["action"] == "denied" and st.credential("acme")["last_used"] is None


# ------------------------------------------------------------------------------------------------ the tools
def tool_map(st, session=None, transient=None):
    return {t.name: t for t in vault_tools(st, session, transient)}


async def test_the_status_lists_what_exists_and_never_a_value_a_user_name_or_even_a_hint(tmp_path):
    st = filled(tmp_path)
    t = Transient()
    t.put("empty", "u", "p")
    out = await tool_map(st, transient=t)["vault_status"].handler({})
    assert "acme (Acme): service key for api.acme.com; stored" in out and "github (GitHub): website login for github.com; stored" in out
    assert "empty (Empty): service key for api.empty.com; entered for one use" in out and "asks the user each time" in out
    for leaked in ("sk-live", PASSWORD, USER, "na***", "nahyun"):
        assert leaked not in out
    st.set_vault_locked(True)
    assert (await tool_map(st)["vault_status"].handler({})).startswith("The user has LOCKED the keychain")
    assert "The keychain is empty" in await tool_map(Store(tmp_path / "e.db", KEY.decode()))["vault_status"].handler({})


async def test_asking_for_a_login_registers_a_slot_without_a_value_and_forbids_chat(tmp_path):
    st = Store(tmp_path / "a.db", KEY.decode())
    t = tool_map(st)
    assert not t["request_login"].requires_approval and not t["vault_status"].requires_approval   # the secure form is the user's choice
    out = await t["request_login"].handler({"site": "https://GitHub.com/login", "label": "GitHub"})
    assert "NEVER ask them to type the password in chat" in out and "browser_login(name='login-github-com')" in out
    [item] = st.credentials()
    assert (item["name"], item["kind"], item["hosts"], item["has_secret"]) == ("login-github-com", "login", ["github.com"], False)
    assert st.vault_log(1)[0]["action"] == "requested"
    st.set_login("login-github-com", USER, PASSWORD)
    assert "already has a saved login" in await t["request_login"].handler({"site": "github.com"})
    assert st.credential("login-github-com")["secret"] == PASSWORD                                 # asking again did not wipe it


@pytest.mark.parametrize("site", ["http://github.com/login", "https://127.0.0.1/", "https://localhost/x", "https://user:pw@github.com/", "https://github.com:8443/", "github", "", "ftp://x.com"])
async def test_logins_can_only_be_asked_for_on_public_https_sites(tmp_path, site):
    st = Store(tmp_path / "a.db", KEY.decode())
    assert (await tool_map(st)["request_login"].handler({"site": site})).startswith("error:") and st.credentials() == []


def test_the_site_can_be_given_as_an_address_or_a_host():
    assert site_host("https://Accounts.Example.com/signin?x=1") == "accounts.example.com" and site_host(" Example.com ") == "example.com"


async def test_a_name_that_is_already_a_service_key_is_not_reused_for_a_login(tmp_path):
    st = filled(tmp_path)
    assert (await tool_map(st)["request_login"].handler({"site": "github.com", "name": "acme"})).startswith("error:")
    assert st.credential("acme")["kind"] == "api"


# ------------------------------------------------------------------------------------------------ typing a login into the page
class Locator:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    async def fill(self, value, timeout=None):
        if self.page.fail:
            raise RuntimeError(f"locator.fill: Timeout while filling {value!r}")
        self.page.filled.append((self.sel, value))

    async def focus(self, timeout=None):
        return None

    async def click(self, timeout=None):
        self.page.clicked.append(self.sel)

    async def count(self):
        return int(bool(self.page.state.get("pw"))) if "password" in self.sel else 1

    async def press(self, key, timeout=None):
        self.page.pressed.append((self.sel, key))
        if self.page.after_press:
            self.page.url = self.page.after_press


class Page:
    def __init__(self, url="https://github.com/login", found=None, fail=False, after_press=None, body="Welcome nahyun.kim@example.com, password hunter2-Pa$$w0rd!",
                 state=None, field=None, links=None, options=None):
        self.field = field or {}
        self.links, self.options, self.clicked = list(links or []), list(options or []), []
        self.url, self.fail, self.after_press, self.body = url, fail, after_press, body
        self.found = {"u": True, "p": True, "p_count": 1, "u_path": "#vault-u", "p_path": "#vault-p", **(found or {})}
        self.state = state or {"pw": False, "text": "Signed in. Logout"}             # what the page shows after Enter
        self.filled, self.pressed, self.untagged, self.goto_calls = [], [], 0, []

    async def evaluate(self, js, arg=None):
        if "hosts.includes" in js:                              # SIGN_IN_LINKS_JS: links of the page to the login's own host
            return self.links
        if "role=switch" in js:                                 # OPTIONS_JS: the page's unticked boxes and switches
            return self.options
        if "kelo_check" in js:                                  # the structure of the page (bot-check reading): from the state after Enter
            return self.state.get("probe")
        if "webkitTextSecurity" in js:                           # the password field's state at the moment of typing (lengths only)
            typed = [v for sel, v in self.filled if sel == "#vault-p"]
            return {"n": 1, "input": True, "type": "password", "tagged": "password", "masked": True,
                    "length": len(typed[-1]) if typed else 0, **self.field}
        if "pw:" in js:
            return self.state
        if "u_path" in js:                                     # the login fields: their CSS paths
            return self.found
        if "removeAttribute" in js:
            self.untagged += 1
            return None
        return self.body

    def locator(self, sel):
        return Locator(self, sel)

    async def wait_for_load_state(self, *a, **k):
        return None

    async def title(self):
        return "Sign in"

    async def goto(self, url, **k):
        self.goto_calls.append(url)
        self.url = url

    def is_closed(self):
        return False


def session_for(page):
    async def connect():
        return page

    def resolver(host, port, *a, **k):                         # a real lookup of an address that is already numbers answers with itself
        return [(2, 1, 6, "", (host if host[0].isdigit() else "140.82.112.3", port))]

    session = BrowserSession(connect=connect, resolver=resolver)
    session.settle_seconds = 0
    session.sign_in_wait = 0
    return session


async def test_the_login_goes_to_the_page_and_comes_back_only_as_the_page_with_both_values_removed():
    page = Page()
    result = await session_for(page).fill_login(USER, PASSWORD)
    assert page.filled == [("#vault-u", USER), ("#vault-p", PASSWORD)] and page.pressed == [("#vault-p", "Enter")]
    out = result.text
    assert PASSWORD not in out and USER not in out and "[hidden]" in out and "Sign in" in out
    assert isinstance(result, SignIn) and result.outcome == "success"


async def test_a_two_page_sign_in_can_be_done_in_two_steps():
    first, second = Page(found={"u": True, "p": False}), Page(found={"u": False, "p": True})
    await session_for(first).fill_login(USER, PASSWORD, step="username", submit=True)
    assert first.filled == [("#vault-u", USER)] and first.pressed == [("#vault-u", "Enter")]
    await session_for(second).fill_login(USER, PASSWORD, step="password", submit=False)
    assert second.filled == [("#vault-p", PASSWORD)] and second.pressed == []
    third = Page(found={"u": False, "p": True})
    await session_for(third).fill_login(USER, PASSWORD)                          # "both" on a password-only page fills just the password
    assert third.filled == [("#vault-p", PASSWORD)]


async def test_a_page_without_a_sign_in_form_gets_nothing_and_is_cleaned_up():
    page = Page(found={"u": False, "p": False})
    with pytest.raises(BrowserPolicyError, match="no sign-in form"):
        await session_for(page).fill_login(USER, PASSWORD)
    assert page.filled == []


async def test_a_driver_error_that_quotes_the_typed_value_is_replaced_by_its_kind():
    page = Page(fail=True)
    with pytest.raises(BrowserPolicyError) as e:
        await session_for(page).fill_login(USER, PASSWORD)
    assert PASSWORD not in str(e.value) and "RuntimeError" in str(e.value)


async def test_a_sign_in_that_lands_on_a_forbidden_address_is_dropped():
    page = Page(after_press="http://10.0.0.5/admin")
    with pytest.raises(BrowserPolicyError):
        await session_for(page).fill_login(USER, PASSWORD)
    assert page.goto_calls == ["about:blank"]


def with_browser(st, page, transient=None):
    return tool_map(st, session_for(page), transient)


async def test_the_login_is_typed_only_on_the_site_it_was_saved_for_and_every_try_is_logged(tmp_path):
    st = filled(tmp_path)
    tool = with_browser(st, Page(url="https://github.com/login"))["browser_login"]
    assert tool.requires_approval and tool.untrusted
    out = await tool.handler({"name": "github"})
    assert out.startswith("[sign-in result: success") and "Sign in" in out and PASSWORD not in out and USER not in out
    assert any(r["detail"] == "browser_login on github.com: success" and r["ok"] for r in st.vault_log(5)) and st.credential("github")["last_used"]
    for url, why in (("https://evil.example.net/login", "evil.example.net"), ("http://github.com/login", "over https"), ("https://github.com.evil.net/", "github.com.evil.net"),
                     ("https://github.io/", "github.io"), ("about:blank", "blank page")):
        page = Page(url=url)
        got = await with_browser(st, page)["browser_login"].handler({"name": "github"})
        assert got.startswith("error:") and why in got and page.filled == [], url
        assert st.vault_log(1)[0]["action"] == "denied"
    # another address of the same site (a separate sign-in host) is the same site
    other = Page(url="https://login.github.com/session")
    assert (await with_browser(st, other)["browser_login"].handler({"name": "github"})).startswith("[sign-in result: success")


@pytest.mark.parametrize("name,text", [("acme", "no website login"), ("ghost", "no website login"), ("login-new", "has not entered")])
async def test_browser_login_refuses_other_kinds_missing_items_and_empty_slots(tmp_path, name, text):
    st = filled(tmp_path)
    st.save_credential("login-new", "New", ["github.com"], {}, kind="login")
    page = Page()
    got = await with_browser(st, page)["browser_login"].handler({"name": name})
    assert got.startswith("error:") and text in got and page.filled == []


async def test_a_locked_vault_blocks_logins_even_ones_allowed_without_asking(tmp_path):
    st = filled(tmp_path)
    st.set_auto_allow("github", True)
    st.set_vault_locked(True)
    tool = with_browser(st, Page())["browser_login"]
    assert tool.auto_approve({"name": "github"}) is False
    got = await tool.handler({"name": "github"})
    assert got.startswith("error:") and "locked" in got


async def test_a_login_typed_for_one_use_works_for_the_whole_sign_in_and_then_is_gone(tmp_path):
    st = Store(tmp_path / "a.db", KEY.decode())
    st.save_credential("github", "GitHub", ["github.com"], {}, kind="login")                 # nothing stored
    t = Transient()
    t.put("github", USER, PASSWORD)
    first, second = Page(found={"u": True, "p": False}), Page(found={"u": False, "p": True})
    await with_browser(st, first, t)["browser_login"].handler({"name": "github", "step": "username"})
    assert t.has("github") and first.filled == [("#vault-u", USER)]                       # still there for the password page
    await with_browser(st, second, t)["browser_login"].handler({"name": "github", "step": "password"})
    assert second.filled == [("#vault-p", PASSWORD)] and not t.has("github")
    again = await with_browser(st, Page(), t)["browser_login"].handler({"name": "github"})
    assert again.startswith("error:") and "has not entered" in again


async def test_the_user_can_allow_a_login_to_sign_in_without_asking_and_the_card_says_where_it_goes(tmp_path):
    st = filled(tmp_path)
    tool = with_browser(st, Page(url="https://github.com/login"))["browser_login"]
    assert tool.auto_approve({"name": "github"}) is False and tool.auto_approve({"name": "acme"}) is False and tool.auto_approve({"name": "ghost"}) is False
    assert tool.auto_approve({"name": 5}) is False and tool.auto_approve({}) is False
    st.set_auto_allow("github", True)
    assert tool.auto_approve({"name": "github"}) is True
    words = await tool.describe({"name": "github"})
    assert "GitHub" in words and "na***@example.com" in words and "github.com" in words and PASSWORD not in words and "nahyun" not in words


async def test_the_browser_tool_exists_only_when_there_is_a_browser(tmp_path):
    st = Store(tmp_path / "a.db", KEY.decode())
    assert set(tool_map(st)) == {"vault_status", "request_login"}
    assert set(tool_map(st, session_for(Page()))) == {"vault_status", "request_login", "browser_login"}


# ------------------------------------------------------------------------------------------------ the approval the user sees
def scripted(*turns):
    it = iter(turns)

    async def fn(provider, request, **kw):
        for ev in next(it):
            yield ev

    return fn


async def run(tools, approve):
    fn = scripted([ToolCall("c1", "browser_login", '{"name":"github"}'), Done("tool_calls")], [TextDelta("ok"), Done("stop")])
    return [e async for e in run_agent("p", "m", [{"role": "user", "content": "sign in"}], tools, stream_fn=fn, approve=approve)]


async def test_the_user_is_asked_unless_the_login_was_allowed_before_and_the_card_carries_the_destination(tmp_path):
    st = filled(tmp_path)
    tools = list(with_browser(st, Page(url="https://github.com/login")).values())
    asked = []

    async def approve(req: ApprovalRequest) -> bool:
        asked.append(req)
        return True

    events = await run(tools, approve)
    assert len(asked) == 1 and asked[0].name == "browser_login" and "github.com" in asked[0].detail and "GitHub" in asked[0].detail
    assert any(type(e).__name__ == "ToolResult" and e.approved for e in events)

    async def refuse(req):
        raise AssertionError("must not ask: the user already allowed this login")

    st.set_auto_allow("github", True)
    await run(list(with_browser(st, Page(url="https://github.com/login")).values()), refuse)

    async def no(req):
        return False

    st.set_auto_allow("github", False)
    page = Page()
    declined = await run(list(with_browser(st, page).values()), no)
    assert page.filled == [] and any(type(e).__name__ == "ToolResult" and e.approved is False for e in declined)


async def test_a_tool_with_a_broken_helper_still_asks_the_user():
    async def handler(args):
        return "ran"

    def bad(args):
        raise RuntimeError("boom")

    async def bad_describe(args):
        raise RuntimeError("boom")

    t = Tool("t", "d", {"type": "object", "properties": {}}, handler, requires_approval=True, auto_approve=bad, describe=bad_describe)
    asked = []

    async def approve(req):
        asked.append(req.detail)
        return True

    fn = scripted([ToolCall("c1", "t", "{}"), Done("tool_calls")], [TextDelta("ok"), Done("stop")])
    [e async for e in run_agent("p", "m", [{"role": "user", "content": "x"}], [t], stream_fn=fn, approve=approve)]
    assert asked == [""]


# ---- a login works across one site's addresses; a login typed in just now does not ask a second time

def test_login_site_matches_the_registrable_domain():
    from assistant.tools.vault_tools import login_site_ok

    assert login_site_ok("member.one.musinsa.com", ["www.musinsa.com"])
    assert login_site_ok("musinsa.com", ["www.musinsa.com"])
    assert login_site_ok("www.musinsa.com", ["www.musinsa.com"])
    assert login_site_ok("nid.naver.com", ["www.naver.com"])
    assert login_site_ok("login.shop.example.co.kr", ["www.example.co.kr"])
    assert not login_site_ok("musinsa.com.evil.net", ["www.musinsa.com"])
    assert not login_site_ok("musinsa.net", ["www.musinsa.com"])
    assert not login_site_ok("other.co.kr", ["www.example.co.kr"])
    assert not login_site_ok("someone.github.io", ["me.github.io"])     # a shared host: each name is its own site
    assert not login_site_ok("", ["www.musinsa.com"])


def test_a_login_typed_in_just_now_counts_as_asked(tmp_path):
    from assistant.store import Store
    from assistant.tools.vault_tools import JUST_ENTERED_SECONDS, just_entered

    s = Store(tmp_path / "a.db")
    assert not just_entered(s, "musinsa")
    s.add_vault_log("musinsa", "added", "website login for www.musinsa.com")
    assert not just_entered(s, "musinsa")                    # asked for, not typed in yet
    s.add_vault_log("musinsa", "saved", "website login, not tried yet")
    assert just_entered(s, "musinsa")
    import time
    assert not just_entered(s, "musinsa", now=time.time() + JUST_ENTERED_SECONDS + 5)
    s.add_vault_log("musinsa", "used", "browser_login on member.one.musinsa.com")
    assert not just_entered(s, "musinsa")                    # used once: the next sign-in asks as usual


async def test_a_sign_in_that_stops_at_a_verification_step_is_logged_as_not_ok_and_says_it_is_not_finished(tmp_path):
    # the site accepted the password and waits for an approval on the phone: the vault log and the model's text must not say success
    st = filled(tmp_path)
    verify = {"kelo_check": 1, "text": "2-Step Verification. Check your phone and tap Yes to sign in.", "text_len": 62,
              "url": "https://github.com/login/two-step", "title": "Verify", "otp": 0, "short_inputs": 0, "secret_box": False}
    page = Page(state={"pw": False, "text": "2-Step Verification. Check your phone and tap Yes to sign in.", "probe": verify})
    out = await with_browser(st, page)["browser_login"].handler({"name": "github"})
    assert out.startswith("[sign-in result: verification_pending") and "NOT finished" in out and "Do not tell the user they are signed in" in out
    used = [r for r in st.vault_log(10) if r["action"] == "used"]
    assert len(used) == 1 and used[0]["ok"] is False and used[0]["detail"] == "browser_login on github.com: code_needed"
    assert all(not r["ok"] for r in st.vault_log(10) if r["action"] == "result")
    assert st.credential("github")["last_result"] == "code_needed"


async def test_a_sign_in_that_left_the_form_for_another_sign_in_step_is_not_counted_as_signed_in(tmp_path):
    st = filled(tmp_path)
    # the page judge read it and said "not a verification step", but the address carries the page to return to: still the sign-in flow (structure)
    still = {"kelo_check": 1, "text": "Choose how to continue", "text_len": 22, "judged_step": "none", "title": "Continue", "otp": 0,
             "short_inputs": 0, "secret_box": False, "url": "https://github.com/session/challenge?return_to=https%3A%2F%2Fgithub.com%2Fhome"}
    page = Page(after_press="https://github.com/session/challenge?return_to=https%3A%2F%2Fgithub.com%2Fhome", state={"pw": False, "text": "Choose how to continue", "probe": still})
    out = await with_browser(st, page)["browser_login"].handler({"name": "github"})
    assert out.startswith("[sign-in result: unknown") and "still shows a sign-in or verification step" in out
    assert [r["ok"] for r in st.vault_log(10) if r["action"] == "used"] == [False]


async def test_a_sign_in_whose_form_fails_is_logged_as_used_and_not_ok(tmp_path):
    st = filled(tmp_path)
    got = await with_browser(st, Page(fail=True))["browser_login"].handler({"name": "github"})
    assert got.startswith("error:")
    row = st.vault_log(1)[0]
    assert row["action"] == "used" and row["ok"] is False and row["detail"].startswith("browser_login on github.com: failed")


async def test_a_successful_browser_login_records_the_site_and_a_failed_one_does_not(tmp_path):
    st = filled(tmp_path)
    await with_browser(st, Page(url="https://login.github.com/session"))["browser_login"].handler({"name": "github"})
    assert [(r["profile"], r["site"], r["how"]) for r in st.browser_sign_ins()] == [("", "github.com", "login")]
    st2 = filled(tmp_path / "second")
    out = await with_browser(st2, Page(url="https://github.com/login", fail=True))["browser_login"].handler({"name": "github"})
    assert not out.startswith("[sign-in result: success") and st2.browser_sign_ins() == []


def site_login(tmp_path, hosts=("accounts.example.com",)):
    st = Store(tmp_path / "a.db", KEY.decode())
    st.save_credential("shop", "Shop", list(hosts), {}, kind="login")
    st.set_login("shop", USER, PASSWORD)
    return st


async def test_a_saved_login_of_the_site_answers_a_request_for_another_address_even_with_a_new_name(tmp_path):
    st = site_login(tmp_path)
    out = await tool_map(st)["request_login"].handler({"site": "https://gift.example.com/box", "name": "login-gift-example-com"})
    assert out.startswith("shop already has a saved login for example.com") and "browser_login(name='shop')" in out
    assert [c["name"] for c in st.credentials()] == ["shop"] and st.credential("shop")["hosts"] == ["accounts.example.com"]


async def test_another_account_on_purpose_gets_its_own_card(tmp_path):
    st = site_login(tmp_path)
    out = await tool_map(st)["request_login"].handler({"site": "gift.example.com", "name": "shop-work", "another_account": True})
    assert "fill-in card" in out and st.credential("shop-work")["hosts"] == ["gift.example.com"]


async def test_a_shared_host_name_is_not_one_site(tmp_path):
    st = site_login(tmp_path, hosts=("me.github.io",))
    out = await tool_map(st)["request_login"].handler({"site": "someone.github.io"})
    assert "fill-in card" in out


async def test_the_status_says_a_login_works_on_its_whole_site(tmp_path):
    out = await tool_map(site_login(tmp_path))["vault_status"].handler({})
    assert "website login for accounts.example.com (every address of example.com)" in out


async def test_a_wrong_login_name_is_answered_with_the_logins_and_their_sites(tmp_path):
    st = site_login(tmp_path)
    got = await with_browser(st, Page(url="https://gift.example.com/"))["browser_login"].handler({"name": "login-gift-example-com"})
    assert got.startswith("error:") and "shop (accounts.example.com)" in got


def off_form():
    return {"u": False, "p": False}


async def test_with_no_form_and_no_via_nothing_is_typed_and_the_way_is_a_click(tmp_path):
    st = site_login(tmp_path)
    page = Page(url="https://gift.example.com/error", found=off_form(), links=["Sign in"])
    out = await with_browser(st, page)["browser_login"].handler({"name": "shop"})
    assert out.startswith("[No sign-in form on this page (gift.example.com)") and "via=" in out and "Sign in" in out
    assert "accounts.example.com" in out and page.filled == [] and page.goto_calls == []
    assert "no sign-in form" in st.vault_log(1)[0]["detail"]


async def test_via_clicks_the_sites_link_then_fills_the_form_it_opens(tmp_path):
    st = site_login(tmp_path)
    page = Page(url="https://gift.example.com/box", found=off_form())
    session = session_for(page)
    tools = tool_map(st, session)
    clicked = []

    async def open_sign_in(via):
        clicked.append(via)
        page.url, page.found = "https://accounts.example.com/login", {"u": True, "p": True, "p_count": 1, "u_path": "#vault-u", "p_path": "#vault-p"}
        return "[reading]"

    session.open_sign_in = open_sign_in
    out = await tools["browser_login"].handler({"name": "shop", "via": "e7"})
    assert clicked == ["e7"] and out.startswith("[sign-in result: success")
    assert page.filled == [("#vault-u", USER), ("#vault-p", PASSWORD)] and page.goto_calls == []
    assert "Before signing in you were on https://gift.example.com/box" in out


async def test_a_via_that_leads_off_the_site_types_nothing(tmp_path):
    st = site_login(tmp_path)
    page = Page(url="https://gift.example.com/box", found=off_form())
    session = session_for(page)
    tools = tool_map(st, session)

    async def open_sign_in(via):
        page.url, page.found = "https://login.example.net/", {"u": True, "p": True, "p_count": 1, "u_path": "#vault-u", "p_path": "#vault-p"}
        return ""

    session.open_sign_in = open_sign_in
    out = await tools["browser_login"].handler({"name": "shop", "via": "e7"})
    assert out.startswith("error:") and "login.example.net" in out and "nothing was typed" in out and page.filled == []


async def test_the_card_names_the_link_it_clicks(tmp_path):
    st = site_login(tmp_path)
    session = session_for(Page(url="https://gift.example.com/box", found=off_form()))
    session.ref_label = lambda sel: "Sign in"
    tools = tool_map(st, session)
    words = await tools["browser_login"].describe({"name": "shop", "via": "e7"})
    assert "Opens the sign-in page through “Sign in”" in words and "gift.example.com" in words and PASSWORD not in words


OPTS = [{"path": "#keep", "label": "Stay signed in"}, {"path": "#news", "label": "Send me offers"}]


async def test_the_forms_options_come_first_and_nothing_is_typed(tmp_path):
    st = filled(tmp_path)
    page = Page(url="https://github.com/login", options=OPTS)
    tool = with_browser(st, page)["browser_login"]
    out = await tool.handler({"name": "github"})
    assert "unticked options" in out and "- `#keep`:" in out and "- `#news`:" in out and 'keep="none"' in out
    assert page.filled == [] and page.pressed == [] and page.clicked == []
    assert tool.auto_approve({"name": "github", "keep": "#keep"}) is True and tool.auto_approve({"name": "github"}) is False


async def test_keep_ticks_only_the_chosen_option_then_signs_in_and_is_used_up(tmp_path):
    st = filled(tmp_path)
    page = Page(url="https://github.com/login", options=OPTS)
    tool = with_browser(st, page)["browser_login"]
    await tool.handler({"name": "github"})
    out = await tool.handler({"name": "github", "keep": "#keep"})
    assert out.startswith("[sign-in result: success") and page.clicked == ["#keep"] and len(page.filled) == 2
    assert tool.auto_approve({"name": "github", "keep": "#keep"}) is False          # the choice was used


async def test_keep_none_ticks_nothing_and_an_unlisted_selector_types_nothing(tmp_path):
    st = filled(tmp_path)
    page = Page(url="https://github.com/login", options=OPTS)
    tool = with_browser(st, page)["browser_login"]
    await tool.handler({"name": "github"})
    bad = await tool.handler({"name": "github", "keep": "#other"})
    assert bad.startswith("error:") and "keep must be one of" in bad and page.filled == []
    out = await tool.handler({"name": "github", "keep": "none"})
    assert out.startswith("[sign-in result: success") and page.clicked == []


async def test_a_form_without_options_signs_in_at_once(tmp_path):
    st = filled(tmp_path)
    page = Page(url="https://github.com/login")
    out = await with_browser(st, page)["browser_login"].handler({"name": "github"})
    assert out.startswith("[sign-in result: success") and page.clicked == []
