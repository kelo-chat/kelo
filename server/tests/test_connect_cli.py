import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant import connect
from assistant.cli import main, read_env
from assistant.connect import ConnectError
from assistant.instances import Instances
from assistant.manage import run_command

STATUS_OK = json.dumps({"BackendState": "Running", "Self": {"DNSName": "mac.tail1234.ts.net."}})


class Script:
    """A fake command line: each rule is (prefix of the command, result). The first matching rule answers; everything is recorded."""

    def __init__(self, *rules):
        self.rules, self.calls = list(rules), []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        for prefix, result in self.rules:
            if cmd[:len(prefix)] == list(prefix):
                if isinstance(result, Exception):
                    raise result
                rc, out = result if isinstance(result, tuple) else (0, result)
                return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def make(tmp_path, *rules):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chromium\nASSISTANT_TOKEN=main-token-main-token-1234567\nCOMPOSE_PROFILES=claude,agy\n")
    run = Script(*rules)
    msgs = []
    return Instances(tmp_path, run=run, port_free=lambda p: True, out=msgs.append), run, msgs


have = lambda name: f"/usr/bin/{name}"          # noqa: E731
missing = lambda name: None                     # noqa: E731


# ------------------------------------------------------------------------------------------------ Tailscale
def test_tailscale_main_copy_uses_https_443_and_an_extra_copy_its_own_port(tmp_path):
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], "{}"))
    m.create("alice")
    main_url = connect.tailscale_connect(m, m.load(), which=have)
    alice_url = connect.tailscale_connect(m, m.load("alice"), which=have)
    assert main_url == "https://mac.tail1234.ts.net" and alice_url == "https://mac.tail1234.ts.net:8100"
    assert run.ran("tailscale", "serve", "--bg") == [["tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8080"],
                                                      ["tailscale", "serve", "--bg", "--https=8100", "http://127.0.0.1:8100"]]
    assert (m.load().connect_mode, m.load().connect_url) == ("tailscale", main_url) and m.load("alice").connect_url == alice_url
    assert read_env(tmp_path / ".env", "COMPOSE_PROFILES") == "claude,agy"                          # nothing else in .env was touched


def test_many_copies_each_get_a_distinct_address_on_one_tailscale_name(tmp_path):
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], "{}"))
    urls = []
    for i in range(30):
        m.create(f"user{i:02d}")
        urls.append(connect.tailscale_connect(m, m.load(f"user{i:02d}"), which=have))
    assert len(set(urls)) == 30 and all(u.startswith("https://mac.tail1234.ts.net:") for u in urls)


def test_tailscale_problems_are_explained(tmp_path):
    m, run, _ = make(tmp_path, (["tailscale", "status"], (1, "")), (["tailscale", "serve", "status"], "{}"))
    with pytest.raises(ConnectError, match="download"):
        connect.tailscale_connect(m, m.load(), which=missing)
    with pytest.raises(ConnectError, match="tailscale up"):
        connect.tailscale_connect(m, m.load(), which=have)
    m.run = Script((["tailscale", "status"], json.dumps({"BackendState": "NeedsLogin"})))
    with pytest.raises(ConnectError, match="tailscale up"):
        connect.tailscale_connect(m, m.load(), which=have)
    m.run = Script((["tailscale", "status"], json.dumps({"BackendState": "Running", "Self": {}})))
    with pytest.raises(ConnectError, match="MagicDNS"):
        connect.tailscale_connect(m, m.load(), which=have)
    m.run = Script((["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "--bg"], (1, "")))
    with pytest.raises(ConnectError, match="HTTPS certificates"):
        connect.tailscale_connect(m, m.load(), which=have)
    m.run = Script((["tailscale", "status"], STATUS_OK), (["tailscale", "serve"], subprocess.TimeoutExpired("tailscale", 60)))
    with pytest.raises(ConnectError, match="waiting for you"):
        connect.tailscale_connect(m, m.load(), which=have)
    assert m.load().connect_mode == ""                                  # nothing was recorded for a failed attempt


def test_an_https_port_already_serving_something_else_is_not_overwritten_unless_forced(tmp_path):
    busy = json.dumps({"TCP": {"443": {"HTTPS": True}}, "Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}})
    mine = json.dumps({"Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}}}})
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], busy))
    with pytest.raises(ConnectError, match="already serves something else"):
        connect.tailscale_connect(m, m.load(), which=have)
    assert not run.ran("tailscale", "serve", "--bg")
    assert connect.tailscale_connect(m, m.load(), which=have, force=True).endswith("ts.net")
    m.run = Script((["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], mine))
    assert connect.tailscale_connect(m, m.load(), which=have)                       # re-running for the same target is fine
    assert connect.tailscale_connect(m, m.load(), which=have, https_port=8443).endswith(":8443")


def test_tailscale_off_removes_only_its_own_mapping(tmp_path):
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], "{}"))
    m.create("alice")
    connect.tailscale_connect(m, m.load("alice"), which=have)
    connect.tailscale_off(m, m.load("alice"))
    assert run.ran("tailscale", "serve", "--https=8100", "off") and m.load("alice").connect_url == "" and m.load("alice").connect_mode == ""
    assert read_env(m.env_path("alice"), "SERVER_PORT") == "8100"                   # other settings stay


# ------------------------------------------------------------------------------------------------ Cloudflare
def cf_home(tmp_path):
    home = tmp_path / "cfhome"
    home.mkdir()
    (home / "cert.pem").write_text("cert")
    (home / "11111111-2222-3333-4444-555555555555.json").write_text(json.dumps({"TunnelID": "11111111-2222-3333-4444-555555555555"}))
    return home


def tunnels(*names):
    return json.dumps([{"id": "11111111-2222-3333-4444-555555555555", "name": n} for n in names])


def test_cloudflare_creates_a_tunnel_and_dns_name_per_copy_and_writes_the_sidecar_files(tmp_path):
    home = cf_home(tmp_path)
    m, run, _ = make(tmp_path, (["cloudflared", "tunnel", "list"], tunnels("assistant-alice")))
    m.create("alice")
    url = connect.cloudflare_connect(m, m.load("alice"), "example.com", which=have, cf_home=home)
    assert url == "https://alice.example.com"
    assert run.ran("cloudflared", "tunnel", "route", "dns") == [["cloudflared", "tunnel", "route", "dns", "assistant-alice", "alice.example.com"]]
    d = tmp_path / "instances" / "alice" / "cloudflared"
    cfg = (d / "config.yml").read_text()
    assert "tunnel: 11111111-2222-3333-4444-555555555555" in cfg and "hostname: alice.example.com" in cfg and "service: http://server:8080" in cfg
    assert (d / "credentials.json").read_text() == (home / "11111111-2222-3333-4444-555555555555.json").read_text()
    assert oct(d.stat().st_mode & 0o777) == "0o700" and oct((d / "credentials.json").stat().st_mode & 0o777) == "0o600"
    env = m.env_path("alice")
    assert read_env(env, "CONNECT_MODE") == "cloudflare" and read_env(env, "COMPOSE_PROFILES") == "cloudflare"
    assert read_env(env, "CLOUDFLARED_DIR") == "./instances/alice/cloudflared" and read_env(env, "ASSISTANT_PUBLIC_URL") == url


def test_the_main_copy_keeps_its_existing_profiles_and_a_second_run_changes_nothing_else(tmp_path):
    home = cf_home(tmp_path)
    m, run, _ = make(tmp_path, (["cloudflared", "tunnel", "list"], tunnels("assistant-main")),
                     (["cloudflared", "tunnel", "route"], (1, "record already exists")))
    for _ in range(2):
        url = connect.cloudflare_connect(m, m.load(), "example.com", which=have, cf_home=home)
    assert url == "https://assistant.example.com"
    assert read_env(tmp_path / ".env", "COMPOSE_PROFILES") == "claude,agy,cloudflare" and (tmp_path / ".env").read_text().count("COMPOSE_PROFILES") == 1
    assert read_env(tmp_path / ".env", "CLOUDFLARED_DIR") == "./connect/cloudflared" and not run.ran("cloudflared", "tunnel", "create")


def test_a_missing_tunnel_is_created_first(tmp_path):
    home = cf_home(tmp_path)
    seq = iter([tunnels(), tunnels("assistant-alice")])
    m, run, _ = make(tmp_path)

    def fake(cmd, **kw):
        run.calls.append(cmd)
        out = next(seq) if cmd[:3] == ["cloudflared", "tunnel", "list"] else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    m.run = fake
    m.create("alice")
    connect.cloudflare_connect(m, m.load("alice"), "example.com", which=have, cf_home=home)
    assert run.ran("cloudflared", "tunnel", "create") == [["cloudflared", "tunnel", "create", "assistant-alice"]]


def test_cloudflare_problems_are_explained_and_nothing_is_recorded(tmp_path):
    home = cf_home(tmp_path)
    m, run, _ = make(tmp_path, (["cloudflared", "tunnel", "list"], tunnels("assistant-main")))
    inst = m.load()
    with pytest.raises(ConnectError, match="not a domain"):
        connect.cloudflare_connect(m, inst, "nope", which=have, cf_home=home)
    with pytest.raises(ConnectError, match="under example.com"):
        connect.cloudflare_connect(m, inst, "example.com", hostname="evil.other.org", which=have, cf_home=home)
    with pytest.raises(ConnectError, match="not installed"):
        connect.cloudflare_connect(m, inst, "example.com", which=missing, cf_home=home)
    with pytest.raises(ConnectError, match="tunnel login"):
        connect.cloudflare_connect(m, inst, "example.com", which=have, cf_home=tmp_path / "empty")
    (home / "11111111-2222-3333-4444-555555555555.json").unlink()
    with pytest.raises(ConnectError, match="credentials file"):
        connect.cloudflare_connect(m, inst, "example.com", which=have, cf_home=home)
    (home / "11111111-2222-3333-4444-555555555555.json").write_text("{}")
    m.run = Script((["cloudflared", "tunnel", "list"], tunnels("assistant-main")), (["cloudflared", "tunnel", "route"], (1, "zone not found")))
    with pytest.raises(ConnectError, match="zone not found"):
        connect.cloudflare_connect(m, inst, "example.com", which=have, cf_home=home)
    assert m.load().connect_mode == "" and not (tmp_path / "connect").exists()


def test_cloudflare_off_removes_the_sidecar_files_and_the_profile(tmp_path):
    home = cf_home(tmp_path)
    m, run, _ = make(tmp_path, (["cloudflared", "tunnel", "list"], tunnels("assistant-main")))
    connect.cloudflare_connect(m, m.load(), "example.com", which=have, cf_home=home)
    connect.cloudflare_off(m, m.load())
    env = tmp_path / ".env"
    assert read_env(env, "COMPOSE_PROFILES") == "claude,agy" and read_env(env, "CONNECT_MODE") == "" and read_env(env, "ASSISTANT_PUBLIC_URL") == ""
    assert not (tmp_path / "connect" / "cloudflared").exists() and any("rm" in c and "cloudflared" in c for c in run.calls)


# ------------------------------------------------------------------------------------------------ phones
class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text = status, body or {}, json.dumps(body or {})

    def json(self):
        return self._body


def test_adding_a_phone_asks_the_running_server_and_draws_a_qr(tmp_path):
    m, _, _ = make(tmp_path)
    m.create("alice")
    inst = m.load("alice")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return Resp(200, {"link": "https://alice.example.com/#pair=CODE", "code": "CODE"})

    link, qr = connect.phone_add(m, inst, address="https://alice.example.com", post=post)
    assert link.endswith("#pair=CODE") and len(qr.splitlines()) > 10
    assert seen["url"] == "http://127.0.0.1:8100/api/devices/pair" and seen["body"] == {"origin": "https://alice.example.com"}
    assert seen["auth"] == f"Bearer {m.token('alice')}"
    for status, text in ((401, "not accepted"), (500, "refused")):
        with pytest.raises(ConnectError, match=text):
            connect.phone_add(m, inst, post=lambda *a, **k: Resp(status))
    import httpx

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    with pytest.raises(ConnectError, match="assistant instance start alice"):
        connect.phone_add(m, inst, post=down)


def test_only_outside_addresses_count_as_reachable_from_a_phone():
    assert connect.local_only("http://127.0.0.1:8080") and connect.local_only("http://localhost:8100") and connect.local_only("http://192.168.0.5:80")
    assert not connect.local_only("https://mac.tail1234.ts.net") and not connect.local_only("https://alice.example.com")


# ------------------------------------------------------------------------------------------------ the commands
def ns(**kw):
    return argparse.Namespace(**kw)


def test_the_commands_tell_the_next_step_and_never_print_a_token(tmp_path, monkeypatch):
    m, run, msgs = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], "{}"))
    assert run_command(ns(cmd="instance", icmd="create", name="alice", no_browser=True, domain=None, no_start=False), m) == 0
    text = "\n".join(msgs)
    assert "assistant connect tailscale --instance alice" in text and "assistant phone add --instance alice" in text
    assert m.token("alice") not in text and any("up" in c for c in run.calls)

    msgs.clear()
    connect_args = ns(cmd="connect", mode="tailscale", instance="alice", https_port=None, force=False)
    monkeypatch.setattr(connect.shutil, "which", have)
    assert run_command(connect_args, m) == 0
    assert "https://mac.tail1234.ts.net:8100" in "\n".join(msgs) and "Tailscale app" in "\n".join(msgs)

    msgs.clear()
    assert run_command(ns(cmd="connect", mode="status", instance="alice", no_check=True), m) == 0
    assert "tailscale -> https://mac.tail1234.ts.net:8100" in msgs[-1]
    assert run_command(ns(cmd="connect", mode="off", instance="alice"), m) == 0 and run.ran("tailscale", "serve", "--https=8100", "off")
    assert run_command(ns(cmd="connect", mode="off", instance="alice"), m) == 0 and msgs[-1] == "Nothing to turn off."
    assert run_command(ns(cmd="connect", mode="status", instance="ghost"), m) == 1


def test_list_shows_the_main_copy_and_every_extra_one_with_how_they_are_reached(tmp_path):
    m, run, msgs = make(tmp_path)
    m.create("alice")
    run.rules.append((["docker", "compose"], subprocess.CompletedProcess([], 0, stdout="server\n", stderr="")))
    m.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="server\n" if "ps" in cmd else "", stderr="")
    assert run_command(ns(cmd="instance", icmd="list"), m) == 0
    assert msgs[0].startswith("main") and "running" in msgs[0] and msgs[1].startswith("alice") and "no browser" not in msgs[1]


def test_deleting_a_copy_that_is_open_to_the_outside_closes_it_first(tmp_path):
    m, run, msgs = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], "{}"))
    m.create("alice")
    connect.tailscale_connect(m, m.load("alice"), which=have)
    assert run_command(ns(cmd="instance", icmd="delete", name="alice", yes=True), m) == 0
    calls = run.calls
    off = next(i for i, c in enumerate(calls) if c[:2] == ["tailscale", "serve"] and c[-1] == "off")
    down = next(i for i, c in enumerate(calls) if "down" in c)
    assert off < down and m.names() == []


def _ask(*answers):
    it, seen = iter(answers), []

    def ask(prompt):
        seen.append(prompt)
        return next(it)

    ask.seen = seen
    return ask


def test_delete_says_what_goes_asks_once_and_keeps_the_copy_unless_the_name_is_typed(tmp_path):
    m, run, msgs = make(tmp_path)
    m.create("alice")
    from assistant.cli import write_env
    write_env(m.env_path("alice"), {"CONNECT_MODE": "kelo", "CONNECT_URL": "https://alice.kelo.chat"})
    ask = _ask("no")
    assert run_command(ns(cmd="instance", icmd="delete", name="alice", yes=False), m, ask=ask) == 1
    said = next(x for x in msgs if x.startswith("This deletes alice for good"))
    assert "every chat, key, file and phone sign-in" in said and str(tmp_path / "instances" / "alice") in said
    assert "its address https://alice.kelo.chat is given back" in said
    assert ask.seen == ["Type alice to delete it (anything else keeps it): "] and m.names() == ["alice"]
    assert "not deleted; alice is unchanged" in msgs[-1] and not any("down" in c for c in run.calls)

    assert run_command(ns(cmd="instance", icmd="delete", name="alice", yes=False), m) == 1          # no terminal and no --yes
    assert "needs a terminal" in msgs[-1] and m.names() == ["alice"]
    write_env(m.env_path("alice"), {"CONNECT_MODE": "", "CONNECT_URL": ""})          # giving a name back is tested with `connect off`

    assert run_command(ns(cmd="instance", icmd="delete", name="alice", yes=False), m, ask=_ask("alice")) == 0
    assert m.names() == [] and msgs[-1] == "alice is deleted."


def test_the_old_remove_command_changes_nothing_and_points_to_stop_or_delete(tmp_path, monkeypatch, capsys):
    m, run, msgs = make(tmp_path)
    m.create("alice")
    for extra in ({}, {"delete_data": True, "yes": True}):
        assert run_command(ns(cmd="instance", icmd="remove", name="alice", **extra), m) == 1
        assert "instance stop alice" in msgs[-1] and "instance delete alice" in msgs[-1] and "nothing was changed" in msgs[-1]
    assert m.names() == ["alice"] and not any("down" in c or "stop" in c for c in run.calls)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["instance", "--help"])
    out = capsys.readouterr().out
    assert "delete" in out and "stop" in out and "remove" not in out and "delete-data" not in out


def test_the_real_command_line_reaches_these_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=x" * 5 + "\n")
    assert main(["connect", "status"]) == 0 and "not open to the outside" in capsys.readouterr().out
    assert main(["connect", "status", "--instance", "ghost"]) == 1
    with pytest.raises(SystemExit):
        main(["connect", "cloudflare"])                           # --domain is required


def test_a_copy_without_a_browser_still_starts_its_tunnel(tmp_path):
    home = cf_home(tmp_path)
    m, run, _ = make(tmp_path, (["cloudflared", "tunnel", "list"], tunnels("assistant-alice")))
    m.create("alice", browser=False)
    connect.cloudflare_connect(m, m.load("alice"), "example.com", which=have, cf_home=home)
    m.start("alice", build_if_missing=False)
    up = [c for c in run.calls if "up" in c][-1]
    assert up[-6:] == ["--no-deps", "server", "cloudflared", "workroom", "vpn", "voice"]


@pytest.mark.parametrize("link", ["https://alice.kelo.chat/#pair=ABCDEFGHJKLMNP", "https://kelo.me.duckdns.org/#pair=ABCDEFGHJKLMNPQRSTUV12"])
def test_the_terminal_qr_is_compact_half_blocks_with_a_small_quiet_zone(link):
    rows = connect.qr_matrix(link)
    assert len(rows) in (33, 37) and all(len(r) == len(rows) for r in rows)      # version 3-4 plus 2 modules of quiet zone
    assert not any(rows[0]) and not any(rows[1]) and not any(r[0] or r[1] for r in rows)
    lines = connect.qr_lines(link)
    assert len(lines) == (len(rows) + 1) // 2 and 17 <= len(lines) <= 19                 # two module rows per line
    assert all(len(line) == len(rows) for line in lines) and set("".join(lines)) <= set(" ▀▄█")
    assert lines[0] == "█" * len(rows)                                                  # the quiet zone is light (drawn) all round
    painted = connect.qr_lines(link, color=True)
    assert len(painted) == len(lines) and "\x1b[30;107m▀" in painted[1] and painted[0].endswith("\x1b[0m")
    assert connect.qr_text(link) == "\n".join(lines) + "\n"
