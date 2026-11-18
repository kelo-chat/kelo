"""The operator link (docs/site-only.md): a main copy reads and answers the site copy's operator lists through a scoped link key,
keeps Firebase local, hears the site's events by polling, and the one-time CLI writes the two halves of the key (never printing it).
Two real servers (the site copy and the main copy) talk over a pretend network that routes the site's address to the site server."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from assistant import operator_link
from assistant.cli import read_env, write_env
from assistant.instances import Instances
from assistant.netguard import NetGuardError, Reply
from assistant.operator_events import OperatorEvents
from assistant.operator_notices import render
from assistant.operator_site import OperatorSite, OperatorSiteWatch
from assistant.server import Settings, create_app
from assistant.store import Store

from test_google_web import CLIENT, SECRET
from test_server import AUTH, TOKEN, Running

SITE = "https://kelo.example.org"
LINK_KEY = "k" * 43                  # a link key is 43 URL-safe characters (token_urlsafe(32)); the site keeps only its hash
LINK_HASH = hashlib.sha256(LINK_KEY.encode()).hexdigest()
LINK = {"Authorization": f"Bearer {LINK_KEY}"}


def never(*a, **k):
    raise AssertionError("no model may be asked anything here")


def site_app(tmp_path, name="site"):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / f"{name}.db"),
                               site_only=True, operator_link_sha256=LINK_HASH, public_url=SITE, oauth_broker=SITE),
                      tools=[], stream_fn=never)


async def sign_up_tester(site_url: str, email: str, *, model: str = "") -> None:
    """A kelo.chat/tester sign-up on the site copy. The site answers the sign-up page only when it is a sign-in broker (its Google
    app is set), as the real site copy is; the app is set here once per call (idempotent)."""
    async with httpx.AsyncClient(base_url=site_url) as c:
        await c.put("/api/oauth-apps/google", headers=AUTH, json={"client_id": CLIENT, "client_secret": SECRET})
        r = await c.post("/tester", data={"email": email, "model": model, "lang": "en", "consent": "yes"})
        assert r.status_code < 400, r.status_code


def main_app(tmp_path, fetch, *, linked=True, key=LINK_KEY):
    kw = {"operator_site_url": SITE, "operator_site_key": key} if linked else {}
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "main.db"),
                               public_url="https://main.example.net", **kw),
                      tools=[], stream_fn=never, operator_site_fetcher=fetch)


def via_site(site_url: str, seen: list):
    """The fetcher the main copy gets: the site's address goes to the local site server; anything else is refused (no network)."""
    async def fetch(method, url, *, allowed_hosts, headers=None, content=None, **_):
        assert urlsplit(url).hostname in allowed_hosts
        if not url.startswith(SITE):
            raise NetGuardError("this test has no network")
        seen.append((method, url.removeprefix(SITE), content))
        async with httpx.AsyncClient(base_url=site_url) as hop:
            r = await hop.request(method, url.removeprefix(SITE), headers=headers, content=content)
        return Reply(r.status_code, dict(r.headers), r.content, False)
    return fetch


async def test_link_key_opens_operator_routes_only(tmp_path):
    async with Running(site_app(tmp_path)) as s, httpx.AsyncClient(base_url=s.url) as c:
        for path in ("/api/operator/beta", "/api/operator/reports", "/api/operator/events?after=0", "/api/operator/status"):
            assert (await c.get(path, headers=LINK)).status_code == 200, path
        for path in ("/api/operator/firebase", "/api/profile", "/api/chat", "/api/devices"):
            r = await c.get(path, headers=LINK)
            assert r.status_code in (401, 404), path        # refused; a site copy answers 404 site_only on the paths it does not serve
        wrong = await c.get("/api/operator/beta", headers={"Authorization": "Bearer " + "x" * 43})
        assert wrong.status_code == 401
        # the link key is the operator's list key only: it is not an access key for anything else on the site
        assert (await c.get("/api/about", headers=LINK)).status_code == 401


async def test_wrong_link_key_is_counted_by_the_brute_force_guard(tmp_path):
    async with Running(site_app(tmp_path)) as s, httpx.AsyncClient(base_url=s.url) as c:
        codes = [(await c.get("/api/operator/beta", headers={"Authorization": f"Bearer {i:043d}"})).status_code for i in range(11)]
        assert codes[-1] == 429 and codes[0] == 401


async def test_main_shows_the_site_lists_unchanged(tmp_path):
    seen: list = []
    site = site_app(tmp_path)
    async with Running(site) as s, Running(main_app(tmp_path, via_site(s.url, seen))) as m, \
            httpx.AsyncClient(base_url=s.url, headers=AUTH) as sc, httpx.AsyncClient(base_url=m.url, headers=AUTH) as mc:
        await sign_up_tester(s.url, "tester.one@example.com", model="Pixel 8")
        assert (await mc.get("/api/operator/beta")).json() == (await sc.get("/api/operator/beta")).json()
        assert (await mc.get("/api/operator/reports")).json() == (await sc.get("/api/operator/reports")).json()
        assert any("/api/operator/beta" in u for _, u, _ in seen)


async def test_approve_and_decline_go_through_to_the_site(tmp_path):
    seen: list = []
    site = site_app(tmp_path)
    async with Running(site) as s, Running(main_app(tmp_path, via_site(s.url, seen))) as m, \
            httpx.AsyncClient(base_url=m.url, headers=AUTH) as mc, httpx.AsyncClient(base_url=s.url, headers=AUTH) as sc:
        await sign_up_tester(s.url, "tester.two@example.com")
        rid = (await sc.get("/api/operator/beta")).json()["requests"][0]["id"]
        got = await mc.post(f"/api/operator/beta/{rid}", json={"action": "approve"})
        assert got.status_code == 200 and got.json()["request"]["status"] == "approved"
        rows = (await sc.get("/api/operator/beta")).json()["requests"]
        assert rows[0]["status"] == "approved"
        gone = await mc.delete(f"/api/operator/beta/{rid}")
        assert gone.status_code == 200
        assert (await sc.get("/api/operator/beta")).json()["requests"] == []


async def test_firebase_stays_local_when_linked(tmp_path):
    seen: list = []
    async with Running(site_app(tmp_path)) as s, Running(main_app(tmp_path, via_site(s.url, seen))) as m, \
            httpx.AsyncClient(base_url=m.url, headers=AUTH) as mc:
        assert (await mc.get("/api/operator/firebase")).status_code == 200
        assert not any("/api/operator/firebase" in u for _, u, _ in seen)


async def test_site_down_and_bad_key_answer_502_with_a_code(tmp_path):
    async def down(*a, **k):
        raise NetGuardError("no route")
    async with Running(main_app(tmp_path, down)) as m, httpx.AsyncClient(base_url=m.url, headers=AUTH) as mc:
        r = await mc.get("/api/operator/beta")
        assert r.status_code == 502 and r.json()["code"] == "operator_site_down"
    async with Running(site_app(tmp_path, "site2")) as s:
        async with Running(main_app(tmp_path, via_site(s.url, []), key="z" * 43)) as m, httpx.AsyncClient(base_url=m.url, headers=AUTH) as mc:
            r = await mc.get("/api/operator/beta")
            assert r.status_code == 502 and r.json()["code"] == "operator_site_key"


async def test_no_open_proxy(tmp_path):
    seen: list = []
    async with Running(site_app(tmp_path)) as s, Running(main_app(tmp_path, via_site(s.url, seen))) as m, \
            httpx.AsyncClient(base_url=m.url, headers=AUTH) as mc:
        assert (await mc.get("/api/operator/reports?limit=9999")).status_code == 422
        assert (await mc.get("/api/operator/reports?handled=x")).status_code == 422
        assert (await mc.post("/api/operator/beta/1%2F..%2Fprofile", json={"action": "approve"})).status_code in (404, 422)
        assert seen == []                                     # nothing was forwarded for any of them


def test_events_feed_kinds_and_retention(tmp_path):
    store = Store(str(tmp_path / "ev.db"))
    now = [1_000_000.0]
    ev = OperatorEvents(store, wall=lambda: now[0])
    ids = [ev.add("tester_signup", {"email": f"a{i}@example.com", "model": "P", "count": i}) for i in range(3)]
    ids.append(ev.add("report", {"id": 9, "reason": "harmful"}))
    assert ids == sorted(ids) and len(set(ids)) == 4
    page = ev.since(0, limit=2)
    assert [e["id"] for e in page["events"]] == ids[:2] and page["last"] == ids[-1]
    assert ev.since(ids[-1])["events"] == []
    assert set(page["events"][0]) == {"id", "at", "kind", "data"}
    now[0] += 31 * 86400
    ev.add("names_pool", {"kind": "near50", "used": 5, "cap": 10})
    assert [e["kind"] for e in ev.since(0)["events"]] == ["names_pool"]     # older than 30 days: gone
    ev.add("not_a_kind", {})                                                # an unknown kind is still kept (the poller skips it)
    assert ev.add("tester_signup", {"email": "x"}) > 0


async def test_watch_first_tick_sets_cursor_without_notifying(tmp_path):
    seen: list = []
    site = site_app(tmp_path)
    async with Running(site) as s:
        await sign_up_tester(s.url, "old.one@example.com")
        notes = []

        class Spy:
            def notify(self, type_, title, body="", **kw):
                notes.append((type_, title, body))
                return len(notes)
        watch = OperatorSiteWatch(OperatorSite(SITE, LINK_KEY, fetcher=via_site(s.url, seen)), Store(str(tmp_path / "w.db")), Spy(),
                                  tz=lambda: "UTC")
        assert await watch.tick() == 0 and notes == []         # first run: the 61 old sign-ups are not announced
        await sign_up_tester(s.url, "new.one@example.com", model="P")
        assert await watch.tick() == 1 and notes[0][0] == "beta_request"
        assert await watch.tick() == 0 and len(notes) == 1    # each event once


async def test_watch_notifies_each_new_event_once_in_the_owner_language(tmp_path):
    """The words are rendered on the main copy, in its owner's language (here Korean), from the event's data."""
    from assistant import i18n
    site = OperatorEvents(Store(str(tmp_path / "site-ev.db")))
    notes = []

    class Spy:
        def notify(self, type_, title, body="", **kw):
            notes.append((type_, title, body))
            return len(notes)
    site.add("tester_signup", {"email": "a@example.com", "model": "Pixel 8", "count": 3})

    async def events_only(method, url, *, allowed_hosts, headers=None, content=None, **_):
        after = int(urlsplit(url).query.split("after=")[1].split("&")[0])
        return Reply(200, {}, json.dumps(site.since(after)).encode(), False)

    watch = OperatorSiteWatch(OperatorSite(SITE, LINK_KEY, fetcher=events_only), Store(str(tmp_path / "w2.db")), Spy(),
                              tz=lambda: "UTC", lang=lambda: "ko")
    assert await watch.tick() == 0                             # the cursor is set; the old sign-up is not announced
    site.add("tester_signup", {"email": "b@example.com", "model": "", "count": 4})
    assert await watch.tick() == 1
    assert notes[-1] == ("beta_request", i18n.t("New tester sign-up: {email}", "ko", email="b@example.com"),
                         i18n.t("Phone: {model} · {count} signed up so far", "ko", model="-", count=4))
    assert await watch.tick() == 0                             # each event once


async def test_watch_survives_site_restore_and_errors(tmp_path):
    notes = []

    class Spy:
        def notify(self, *a, **k):
            notes.append(a)

    async def broken(*a, **k):
        raise NetGuardError("offline")
    store = Store(str(tmp_path / "w3.db"))
    store.set_meta("operator_site:cursor", "50")
    watch = OperatorSiteWatch(OperatorSite(SITE, LINK_KEY, fetcher=broken), store, Spy(), tz=lambda: "UTC")
    assert await watch.tick() == 0 and store.meta("operator_site:cursor") == "50"

    async def restored(*a, **k):   # the site's database came back older: its last id is below the cursor
        return Reply(200, {}, json.dumps({"events": [], "last": 3}).encode(), False)
    watch = OperatorSiteWatch(OperatorSite(SITE, LINK_KEY, fetcher=restored), store, Spy(), tz=lambda: "UTC")
    assert await watch.tick() == 0 and store.meta("operator_site:cursor") == "3" and notes == []


def test_render_matches_the_old_words():   # the same words the server sent before this change (English is the msgid)
    title, body = render("tester_signup", {"email": "a@example.com", "model": "Pixel 8", "count": 3}, tz="UTC")[1:]
    assert title == "New tester sign-up: a@example.com" and body == "Phone: Pixel 8 · 3 signed up so far"
    typ, title, body = render("beta_request", {"email": "a@example.com", "products": ["gmail"]}, tz="UTC")
    assert typ == "beta_request" and title == "Google sign-in beta request" and body.startswith("a@example.com · ")
    assert render("report", {"id": 4, "reason": "harmful"}, tz="UTC")[0] == "operator_report"
    assert render("report", {"id": 4, "reason": "harmful"}, tz="UTC")[1] == "New report: harmful or offensive"
    assert render("names_busy", {"count": 2, "until": 0}, tz="UTC")[0] == "operator_busy"
    assert render("names_pool", {"kind": "near50", "used": 5, "cap": 10}, tz="UTC")[0] == "operator_pool"
    assert render("made_up", {}, tz="UTC") is None


def test_operator_link_cli_writes_hash_and_key_never_prints_it(tmp_path):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    m = Instances(tmp_path, run=lambda *a, **k: None, port_free=lambda p: True, out=lambda s: None)
    m.create("operator", browser=False)
    write_env(m.env_path("operator"), {"ASSISTANT_SITE_ONLY": "1", "ASSISTANT_PUBLIC_URL": SITE})
    out: list[str] = []
    operator_link.link(m, "operator", out=out.append)
    key = read_env(m.env_path("main"), "KELO_OPERATOR_SITE_KEY")
    assert len(key) >= 32 and read_env(m.env_path("main"), "KELO_OPERATOR_SITE_URL") == SITE
    assert read_env(m.env_path("operator"), "KELO_OPERATOR_LINK_SHA256") == hashlib.sha256(key.encode()).hexdigest()
    assert key not in "\n".join(out)
    operator_link.unlink(m, "operator", out=out.append)
    for path in (m.env_path("main"), m.env_path("operator")):
        assert "KELO_OPERATOR_SITE_KEY" not in path.read_text() and "KELO_OPERATOR_LINK_SHA256" not in path.read_text()
    assert "KELO_OPERATOR_SITE_URL" not in m.env_path("main").read_text()


def test_unlinked_main_is_exactly_as_today():
    s = Settings.from_env({"ASSISTANT_TOKEN": "t" * 32})
    assert s.operator_site_url == "" and s.operator_site_key == "" and s.operator_link_sha256 == "" and s.site_only is False


def test_bad_link_settings_are_refused_with_the_setting_named():
    with pytest.raises(RuntimeError, match="KELO_OPERATOR_SITE_URL"):
        Settings.from_env({"ASSISTANT_TOKEN": "t" * 32, "KELO_OPERATOR_SITE_URL": "http://kelo.chat/path"})
    with pytest.raises(RuntimeError, match="KELO_OPERATOR_SITE_KEY"):
        Settings.from_env({"ASSISTANT_TOKEN": "t" * 32, "KELO_OPERATOR_SITE_KEY": "short"})
    with pytest.raises(RuntimeError, match="KELO_OPERATOR_LINK_SHA256"):
        Settings.from_env({"ASSISTANT_TOKEN": "t" * 32, "KELO_OPERATOR_LINK_SHA256": "xyz"})


def test_operator_status_says_when_nothing_is_linked(tmp_path):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    m = Instances(tmp_path, run=lambda *a, **k: None, port_free=lambda p: True, out=lambda s: None)
    out: list[str] = []
    assert operator_link.status(m, out=out.append) is False
    assert out == ["not linked: no site copy is set (assistant operator link <name> makes the link)"]


def test_cli_routes_the_operator_command_to_manage(monkeypatch):
    """`assistant operator ...` reaches manage.run_command (it used to fall through to the setup branch and crash)."""
    from assistant import cli, manage
    seen = {}
    monkeypatch.setattr(manage, "run_command", lambda args, ui=None: seen.setdefault("cmd", (args.cmd, args.ocmd if hasattr(args, "ocmd") else None)) and 0)
    cli.main(["operator", "status"])
    assert seen["cmd"][0] == "operator"
