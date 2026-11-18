"""`assistant setup --web`: the wizard's questions over real HTTP on 127.0.0.1, with a scripted page client (no browser, no Docker)."""

import html
import http.client
import json
import logging
import re
import tempfile
import threading
import time
import types
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from assistant import i18n, setup_web
from assistant.setup_web import WebUI, serve
from test_wizard import Docker, Http, make

FINAL = ("summary", "fail")


def link_in(page):
    """The link a page file sends the browser to (read the way the browser reads the page)."""
    return html.unescape(re.search(r'url=([^"]+)"', Path(page).read_text(encoding="utf-8")).group(1))


class Page:
    """What the page does: asks for events with the token header, answers with POST /answer."""

    def __init__(self, srv, token=None):
        self.port = srv.server_port
        self.token = srv.token if token is None else token

    def request(self, method, path, *, body=None, token=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=40)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None or self.token:
            headers["X-Kelo-Setup"] = self.token if token is None else token
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, raw

    def events(self, after, token=None, host=None):
        status, raw = self.request("GET", f"/events?after={after}", token=token, host=host)
        return status, (json.loads(raw) if status == 200 else raw)

    def answer(self, qid, value, token=None):
        return self.request("POST", "/answer", body={"id": qid, "value": value}, token=token)[0]


def drive(page, answers, *, until=FINAL, limit=200):
    """Reads events as the page does and answers each question with answers[type] (a value or a callable(event) -> value).
    Returns every event seen, in order."""
    seen, after = [], 0
    for _ in range(limit):
        status, evs = page.events(after)
        assert status == 200, evs
        for ev in evs:
            after = ev["id"]
            seen.append(ev)
            if ev["type"] in ("choose", "ask", "confirm"):
                got = answers.get(ev["type"], ev["default"])
                value = got(ev) if callable(got) else got
                assert page.answer(ev["id"], value) == 200, ev
            if ev["type"] in until:
                return seen
    raise AssertionError("the run did not finish")


def scripted_wizard(ui):
    ui.banner(False)
    ui.step(1, "Check the Kelo server")
    ui.ok("Docker 28.4.0")
    size = ui.choose("How much may this Kelo use?", [("1", "Minimum", "RAM 2 GB"), ("2", "Recommended", "RAM 4 GB")], "2")
    name = ui.ask("Name for your address", "pat")
    ui.step(2, "Start Kelo")
    code = ui.live("Building Kelo", lambda on_line: (on_line("\x1b[32m#5 DONE\x1b[0m\n"), on_line("\n"), 0)[2])
    ui.warn("careful")
    ui.info("  a sub line")
    ui.qr("https://pat.kelo.chat/pair#one-time")
    go = ui.confirm("Sign a phone in now?", True)
    ui.summary("https://pat.kelo.chat", [("kelo status", "status of every copy")], "Choose the AI model in the Kelo app.")
    RESULT.update(size=size, name=name, go=go, code=code)
    return 0


RESULT: dict = {}


@pytest.fixture
def running():
    """A server with the scripted wizard, a 0.3 s grace after the final event, and a stop handle for every test."""
    started = []

    def start(run_wizard=scripted_wizard, **kw):
        kw.setdefault("grace", 0.3)
        url, srv = serve(run_wizard, lang=kw.pop("lang", "en"), **kw)
        started.append(srv)
        return url, srv

    yield start
    for srv in started:
        srv.stop()
        srv.closed.wait(5)


def token_of(url):
    return url.split("#t=", 1)[1]


def next_event(ui, kind, after=0):
    """The first event of `kind` the page would get after `after` (waits for the wizard thread to ask)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for ev in ui.events(after, timeout=0.2):
            after = ev["id"]
            if ev["type"] == kind:
                return ev
    raise AssertionError(f"no {kind} event")


def drive_url(url):
    """What the browser does with the printed link: reads and answers with defaults until the run ends."""
    parts = urlsplit(url)
    drive(Page(types.SimpleNamespace(server_port=parts.port, token=token_of(url))), {})


# ------------------------------------------------------------------------------------------------------------- the bind and the guards
def test_listens_on_loopback_only_and_refuses_other_addresses(running):
    url, srv = running()
    assert srv.server_address[0] == "127.0.0.1" and url.startswith(f"http://127.0.0.1:{srv.server_port}/#t=")
    with pytest.raises(ValueError):
        serve(scripted_wizard, lang="en", host="0.0.0.0")


def test_token_is_only_in_the_fragment_and_never_in_a_request_line(running):
    url, srv = running()
    assert "?" not in url.split("#", 1)[0] and token_of(url)
    status, page = Page(srv).request("GET", "/")
    assert status == 200 and token_of(url).encode() not in page      # the page itself holds no secret


def test_wrong_or_missing_token_is_403_on_every_endpoint(running):
    _url, srv = running()
    page = Page(srv)
    assert page.request("GET", "/events?after=0", token="nope")[0] == 403
    assert page.request("GET", "/events?after=0", token="")[0] == 403
    assert page.request("POST", "/answer", body={"id": 1, "value": True}, token="nope")[0] == 403


def test_wrong_host_is_403_and_localhost_is_accepted(running):
    _url, srv = running()
    page = Page(srv)
    assert page.request("GET", "/", host="evil.example:%d" % srv.server_port)[0] == 403
    assert page.request("GET", "/events?after=0", host="evil.example:%d" % srv.server_port)[0] == 403
    assert page.request("GET", "/", host="127.0.0.1")[0] == 403          # the port is part of the name
    assert page.request("GET", "/", host="localhost:%d" % srv.server_port)[0] == 200
    assert page.events(0, host="localhost:%d" % srv.server_port)[0] == 200


def test_unknown_paths_and_bad_answers_are_refused(running):
    _url, srv = running()
    page = Page(srv)
    assert page.request("GET", "/nope")[0] == 404
    assert page.request("POST", "/nope", body={})[0] == 404
    assert page.request("GET", "/events?after=abc")[0] == 400
    assert page.request("POST", "/answer", body=b"not json")[0] == 400
    assert page.request("POST", "/answer", body=[1, 2])[0] == 400
    assert page.request("POST", "/answer", body={"id": True, "value": True})[0] == 400
    assert page.request("POST", "/answer", body=b"x" * 5000)[0] == 413
    assert page.request("POST", "/answer", body={"id": 999, "value": True})[0] == 409     # no question waiting


def test_security_headers_on_the_page(running):
    _url, srv = running()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    conn.request("GET", "/", headers={"Host": f"127.0.0.1:{srv.server_port}"})
    resp = conn.getresponse()
    resp.read()
    assert "default-src 'none'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("Referrer-Policy") == "no-referrer" and resp.getheader("Cache-Control") == "no-store"
    conn.close()


# ------------------------------------------------------------------------------------------------------------- a whole run over HTTP
def test_full_run_over_http_answers_every_question_and_stops_after_the_summary(running):
    RESULT.clear()
    url, srv = running()
    page = Page(srv, token_of(url))
    seen = drive(page, {"choose": "1", "ask": "alice", "confirm": False})
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "hello" and kinds[-1] == "summary"
    assert RESULT == {"size": "1", "name": "alice", "go": False, "code": 0}
    assert {"banner", "step", "ok", "warn", "info", "live", "log", "live_end", "qr", "choose", "ask", "confirm"} <= set(kinds)
    qr = next(e for e in seen if e["type"] == "qr")
    assert qr["link"] == "https://pat.kelo.chat/pair#one-time"
    assert all(set(row) <= {"0", "1"} and len(row) == len(qr["matrix"]) for row in qr["matrix"])
    logs = [e["text"] for e in seen if e["type"] == "log"]
    assert logs == ["#5 DONE"]                                              # the ANSI colour is stripped, the blank line skipped
    assert any(e["type"] == "ok" and e["text"].startswith("Building Kelo") for e in seen)
    assert srv.wait() == 0                                                   # exit code = the wizard's
    assert srv.closed.is_set()


def test_a_failed_run_ends_with_the_fail_screen_and_exit_1(running):
    def failing(ui):
        ui.step(1, "Check the Kelo server")
        ui.fail("Docker is not running", ["Start Docker Desktop, then run the command again."])
        return 1

    url, srv = running(failing)
    seen = drive(Page(srv, token_of(url)), {})
    assert seen[-1]["type"] == "fail" and seen[-1]["lines"] == ["Start Docker Desktop, then run the command again."]
    assert srv.wait() == 1


def test_an_exception_in_the_wizard_still_gives_the_page_a_last_word(running, caplog):
    def broken(ui):
        ui.step(1, "Check the Kelo server")
        raise RuntimeError("secret detail that must not reach the page")

    with caplog.at_level(logging.ERROR, logger="assistant.setup_web"):
        url, srv = running(broken)
        seen = drive(Page(srv, token_of(url)), {})
    assert seen[-1]["type"] == "fail" and seen[-1]["title"] == "Setup stopped"
    assert "secret detail" not in json.dumps(seen)
    assert srv.wait() == 1


def test_the_real_wizard_runs_through_the_page(tmp_path, running):
    """The Wizard itself, with the test_wizard fakes for Docker and the Kelo address service."""
    docker, http_fake = Docker(), Http()

    def wizard_run(ui):
        w, _, _ = make(tmp_path, ui, docker=docker, http=http_fake)
        return w()

    url, srv = running(wizard_run, lang="en")
    seen = drive(Page(srv, token_of(url)), {"ask": "alice"})
    assert seen[-1]["type"] == "summary"
    assert srv.wait() == 0
    env = (tmp_path / ".env").read_text()
    assert "CONNECT_URL=https://alice.kelo.chat" in env
    assert any(e["type"] == "qr" and e["link"] == "https://alice.kelo.chat/pair#one-time" for e in seen)
    assert docker.streamed and docker.streamed[0][-4:] == ["up", "-d", "--build", "--remove-orphans"]


def test_the_page_speaks_the_wizard_language(running):
    url, srv = running(lang="ko")
    seen = drive(Page(srv, token_of(url)), {})
    hello = seen[0]
    assert hello["type"] == "hello" and hello["lang"] == "ko"
    assert hello["strings"]["Continue"] == i18n.t("Continue", lang="ko") and hello["strings"]["Continue"] != "Continue"
    assert set(hello["strings"]) == set(setup_web.page_strings("en"))


# ------------------------------------------------------------------------------------------------------------- the questions
def test_a_question_waits_for_the_page_and_gets_the_answer():
    ui = WebUI("en", answer_timeout=5)
    got = {}
    t = threading.Thread(target=lambda: got.update(v=ui.choose("Pick", [("a", "A", ""), ("b", "B", "")], "a")))
    t.start()
    ev = next_event(ui, "choose")
    assert ev["options"] == [{"key": "a", "label": "A", "note": ""}, {"key": "b", "label": "B", "note": ""}]
    with pytest.raises(ValueError):
        ui.answer(ev["id"], "zzz")                # not one of the options
    ui.answer(ev["id"], "b")
    t.join(5)
    assert got["v"] == "b"
    with pytest.raises(LookupError):              # answered: nothing is waiting any more
        ui.answer(ev["id"], "a")


def test_answers_are_checked_by_kind():
    ui = WebUI("en", answer_timeout=5)
    threading.Thread(target=lambda: ui.confirm("Go?", True)).start()
    qid = next_event(ui, "confirm")["id"]
    with pytest.raises(ValueError):
        ui.answer(qid, "yes")                     # a confirm takes true or false
    ui.answer(qid, False)


def test_no_answer_in_time_stops_the_setup_and_takes_no_default(caplog):
    ui = WebUI("en", answer_timeout=0.2)
    with caplog.at_level(logging.WARNING, logger="assistant.setup_web"):
        with pytest.raises(setup_web.SetupTimedOut):
            ui.confirm("Sign a phone in now?", True)      # the suggested "yes" is never taken for the owner
    evs = ui.events(0, timeout=0)
    assert ui.finished and [e["type"] for e in evs].count("resolved") == 1
    fails = [e for e in evs if e["type"] == "fail"]
    assert len(fails) == 1 and fails[0]["lines"] == [
        i18n.t("No answer for {minutes} minutes, so setup stopped. What you answered is kept. Run `{command}` to finish.",
               lang="en", minutes=0, command="kelo setup")]
    assert "got no answer" in caplog.text


def test_a_setup_nobody_answers_ends_before_anything_is_chosen_for_the_owner(running):
    chosen = []

    def wizard(ui):
        chosen.append(ui.choose("Where should your phone reach Kelo?", [("1", "Kelo address", ""), ("3", "Skip for now", "")], "1"))
        return 0

    url, srv = running(run_wizard=wizard, answer_timeout=0.2)     # the page never answers and never reads the events
    deadline = time.monotonic() + 5
    while srv.exit_code is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert srv.exit_code == 1 and chosen == []
    assert [e["type"] for e in srv.ui.events(0, timeout=0)][-1] == "fail"


def test_events_are_kept_in_order_and_an_empty_poll_returns_nothing():
    ui = WebUI("en")
    ui.info("one")
    ui.ok("two")
    evs = ui.events(0, timeout=0)
    assert [e["id"] for e in evs] == [1, 2, 3] and [e["type"] for e in evs] == ["hello", "info", "ok"]
    assert ui.events(3, timeout=0.05) == []


def test_qr_sends_the_link_and_the_modules_the_terminal_draws():
    ui = WebUI("en")
    ui.qr("https://alice.kelo.chat/pair#one-time")
    ev = ui.events(1, timeout=0)[0]
    assert ev["type"] == "qr" and ev["link"] == "https://alice.kelo.chat/pair#one-time"
    assert len(ev["matrix"]) >= 21 and all(len(r) == len(ev["matrix"]) for r in ev["matrix"])


# ------------------------------------------------------------------------------------------------------------- the strings
def test_every_string_the_setup_page_uses_is_translated_in_every_language():
    import ast
    import json
    from pathlib import Path

    tree = ast.parse(Path(setup_web.__file__).read_text(encoding="utf-8"))
    used = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str) and getattr(n.func, "attr", getattr(n.func, "id", "")) in ("t", "N_")}
    assert len(used) >= 15
    for f in sorted(i18n.LOCALES.glob("*.json")):
        have = json.loads(f.read_text(encoding="utf-8"))
        assert not [k for k in used if k not in have], f.name


# ------------------------------------------------------------------------------------------------------------- the command
def test_run_web_prints_the_link_opens_it_and_returns_the_wizard_code(tmp_path, monkeypatch):
    from assistant import wizard

    class QuickWizard:
        def __init__(self, env_file, ui, **kw):
            self.ui, self.kw = ui, kw

        def __call__(self):
            self.ui.summary("https://x.kelo.chat", [], "next")
            return 0

    monkeypatch.setattr(wizard, "Wizard", QuickWizard)
    monkeypatch.setattr(setup_web, "GRACE", 0.2)
    printed, opened = [], []

    def opener(uri):                     # the browser: opens the page file and reads the link in it until the run ends
        opened.append(uri)
        drive_url(link_in(uri.removeprefix("file://")))

    code = setup_web.run_web(tmp_path / ".env", start=False, clis=["claude"], open_browser=True,
                             out=printed.append, opener=opener)
    assert code == 0
    assert printed and printed[0].startswith("http://127.0.0.1:") and "#t=" in printed[0]
    assert len(opened) == 1 and opened[0].startswith("file://") and "#t=" not in opened[0]   # the token is never a command-line argument
    assert not Path(opened[0].removeprefix("file://")).exists()                              # the page file is removed when the run ends


def test_run_web_page_file_prints_a_private_path_and_no_link(tmp_path, monkeypatch):
    from assistant import wizard

    class QuickWizard:
        def __init__(self, env_file, ui, **kw):
            self.ui = ui

        def __call__(self):
            self.ui.fail("Nothing to do", [])
            return 1

    monkeypatch.setattr(wizard, "Wizard", QuickWizard)
    monkeypatch.setattr(setup_web, "GRACE", 0.2)
    printed, opened = [], []

    def out(line):                       # the installer: gets a path, opens it itself; a reader is started by hand here
        printed.append(line)
        page = Path(line)
        assert page.parent == Path(tempfile.gettempdir()) and page.stat().st_mode & 0o777 == 0o600
        threading.Thread(target=drive_url, args=(link_in(page),), daemon=True).start()

    assert setup_web.run_web(tmp_path / ".env", open_browser=False, page_file=True, out=out, opener=opened.append) == 1
    assert len(printed) == 1 and "#t=" not in printed[0] and opened == []
    assert not Path(printed[0]).exists()


def test_page_file_escapes_the_link_and_is_readable_only_by_this_account():
    page = setup_web.write_page('http://127.0.0.1:1/#t=abc"><script>x()</script>')
    try:
        text = page.read_text(encoding="utf-8")
        assert "<script>" not in text and page.stat().st_mode & 0o777 == 0o600
        assert link_in(page) == 'http://127.0.0.1:1/#t=abc"><script>x()</script>'
    finally:
        page.unlink()


def test_run_web_with_no_open_only_prints(tmp_path, monkeypatch):
    from assistant import wizard

    class QuickWizard:
        def __init__(self, env_file, ui, **kw):
            self.ui = ui

        def __call__(self):
            self.ui.fail("Nothing to do", [])
            return 1

    monkeypatch.setattr(wizard, "Wizard", QuickWizard)
    monkeypatch.setattr(setup_web, "GRACE", 0.2)
    printed, opened = [], []

    def out(url):                        # nothing opens the browser here: a reader of the link is started by hand
        printed.append(url)
        threading.Thread(target=drive_url, args=(url,), daemon=True).start()

    assert setup_web.run_web(tmp_path / ".env", open_browser=False, out=out, opener=opened.append) == 1
    assert len(printed) == 1 and opened == []


def test_cli_accepts_web_and_no_open(monkeypatch, tmp_path):
    from assistant import cli

    calls = []
    monkeypatch.setattr(setup_web, "run_web", lambda env_file, **kw: calls.append((env_file, kw)) or 7)
    assert cli.main(["setup", "--web", "--no-open", "--env-file", str(tmp_path / ".env"), "--no-start"]) == 7
    env_file, kw = calls[0]
    assert kw["open_browser"] is False and kw["start"] is False and kw["page_file"] is False
    assert cli.main(["setup", "--web", "--no-open", "--page-file", "--env-file", str(tmp_path / ".env"), "--no-start"]) == 7
    assert calls[1][1]["page_file"] is True


def test_watch_stops_the_server_once_the_grace_has_passed(running):
    url, srv = running(grace=0.2)
    drive(Page(srv, token_of(url)), {})
    assert srv.closed.wait(5)
    with pytest.raises(OSError):
        Page(srv).request("GET", "/", token="")


def test_a_fixed_port_is_bound_and_the_default_stays_random(running):
    """`setup --web --web-port N` (the Mac server's setup page, whose Lima forward is static) binds N on 127.0.0.1 only."""
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    url, srv = running(port=port)
    assert srv.server_address[0] == "127.0.0.1" and srv.server_port == port
    assert url == f"http://127.0.0.1:{port}/#t={url.rsplit('#t=', 1)[1]}"
    _, other = running()
    assert other.server_port != 0 and other.server_port != port


def test_cli_passes_the_web_port_and_refuses_it_without_web(monkeypatch, tmp_path):
    from assistant import cli

    calls = []
    monkeypatch.setattr(setup_web, "run_web", lambda env_file, **kw: calls.append(kw) or 7)
    env = ["--env-file", str(tmp_path / ".env"), "--no-start", "--no-open"]
    assert cli.main(["setup", "--web", "--web-port", "18081", *env]) == 7
    assert calls[-1]["port"] == 18081
    assert cli.main(["setup", "--web", *env]) == 7
    assert calls[-1]["port"] == 0, "the default is a random port, as before"
    with pytest.raises(SystemExit):
        cli.main(["setup", "--web", "--web-port", "70000", *env])
    with pytest.raises(SystemExit):
        cli.main(["setup", "--web-port", "18081", *env])
