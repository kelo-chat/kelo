"""The other ways to reach a copy: a free Kelo address, Tailscale Funnel, and an own domain (direct or through your proxy).

Everything runs against fakes: a fake command line (tailscale, docker), a fake broker / Cloudflare, fake DNS and port checks.
Nothing here touches this computer's ports, its Tailscale, its .env or any real service."""

import json
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant import connect
from assistant.cli import main, read_env
from assistant.connect import ConnectError
from assistant.manage import run_command

from test_connect_cli import STATUS_OK, Script, have, make, ns
from test_kelo_names import ACC, CF_TOKEN, ZONE, FakeCloudflare

TUNNEL_TOKEN = "eyJ-fake-tunnel-token-0123456789abcdefghij"


class Reply:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError


class Broker:
    """A fake Kelo broker reached with httpx.post-like calls."""

    def __init__(self, *, claim=None):
        self.calls, self.claim = [], claim

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if url.endswith("/claim"):
            if self.claim:
                return self.claim
            return Reply(200, {"name": json["name"], "hostname": f"{json['name']}.kelo.chat", "tunnel_token": TUNNEL_TOKEN})
        if url.endswith("/release"):
            return Reply(200, {"released": "x"})
        return Reply(404)


def no_health(url, **kw):
    raise httpx.ConnectError("offline in tests")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test in this file may reach the network or this computer's ports by accident."""
    monkeypatch.setattr(connect, "_get", no_health)
    monkeypatch.setattr(connect, "_post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline in tests")))
    monkeypatch.setattr(connect, "_port_open", lambda h, p: pytest.fail("a real port check ran"))
    monkeypatch.setattr(connect, "_resolve", lambda h: pytest.fail("a real DNS lookup ran"))
    monkeypatch.setattr(connect, "_public_ip", lambda *a: pytest.fail("a real public-address lookup ran"))
    monkeypatch.setattr(connect, "lan_ip", lambda: "192.168.1.20")


# ------------------------------------------------------------------------------------------------ free Kelo address
def test_kelo_writes_the_token_only_for_the_tunnel_and_sets_the_public_address(tmp_path):
    m, run, _ = make(tmp_path)
    m.create("alice")
    broker = Broker()
    url = connect.kelo_connect(m, m.load("alice"), broker="https://kelo.chat", post=broker)
    assert url == "https://alice.kelo.chat"
    (claim_url, body), = broker.calls
    assert claim_url == "https://kelo.chat/broker/tunnel/claim" and body["name"] == "alice"
    env = m.env_path("alice")
    install = read_env(env, "KELO_INSTALL_ID")
    assert len(install) >= 32 and body["install_id"] == install
    assert TUNNEL_TOKEN not in env.read_text()                                     # the server's settings never hold the tunnel token
    tok = tmp_path / "instances" / "alice" / "kelo" / "tunnel.env"
    assert read_env(tok, "TUNNEL_TOKEN") == TUNNEL_TOKEN and stat.S_IMODE(tok.stat().st_mode) == 0o600
    assert stat.S_IMODE(tok.parent.stat().st_mode) == 0o700
    assert read_env(env, "KELO_ENV_FILE") == "./instances/alice/kelo/tunnel.env" and read_env(env, "COMPOSE_PROFILES") == "kelo"
    assert (read_env(env, "CONNECT_MODE"), read_env(env, "ASSISTANT_PUBLIC_URL"), read_env(env, "CONNECT_HOST")) == ("kelo", url, "alice.kelo.chat")
    connect.kelo_connect(m, m.load("alice"), post=broker)                         # again: the same secret is reused
    assert broker.calls[-1][1]["install_id"] == install


def test_kelo_main_copy_needs_a_name_and_a_new_name_gives_the_old_one_back(tmp_path):
    m, run, _ = make(tmp_path)
    broker = Broker()
    with pytest.raises(ConnectError, match="--name"):
        connect.kelo_connect(m, m.load(), post=broker)
    with pytest.raises(ConnectError, match="reserved"):
        connect.kelo_connect(m, m.load(), "admin", post=broker)
    connect.kelo_connect(m, m.load(), "mykelo", post=broker)
    assert read_env(tmp_path / ".env", "COMPOSE_PROFILES") == "claude,agy,kelo"
    connect.kelo_connect(m, m.load(), "other", post=broker)
    assert [u.rsplit("/", 1)[-1] for u, _ in broker.calls] == ["claim", "release", "claim"]
    assert m.load().connect_url == "https://other.kelo.chat"


@pytest.mark.parametrize("reply,text", [(Reply(409, {"detail": "alice.kelo.chat is taken; pick another name"}), "taken"),
                                        (Reply(503, {"detail": "no Kelo addresses are available right now."}), "no Kelo addresses"),
                                        (Reply(404, {"detail": "nope"}), "does not hand out"),
                                        (Reply(429, {"detail": "slow"}), "too many"),
                                        (Reply(200, {"hostname": "evil.example.com", "tunnel_token": TUNNEL_TOKEN}), "does not fit")])
def test_kelo_broker_refusals_are_explained_and_nothing_is_recorded(tmp_path, reply, text):
    m, run, _ = make(tmp_path)
    with pytest.raises(ConnectError, match=text):
        connect.kelo_connect(m, m.load(), "alice", post=Broker(claim=reply))
    assert m.load().connect_mode == "" and not (tmp_path / "connect" / "kelo").exists()


def test_kelo_broker_address_rules(tmp_path):
    m, _, _ = make(tmp_path)
    inst = m.load()
    assert connect.kelo_broker(inst) == "https://kelo.chat"
    (tmp_path / ".env").write_text((tmp_path / ".env").read_text() + "ASSISTANT_OAUTH_BROKER=https://broker.example.org/\n")
    assert connect.kelo_broker(m.load()) == "https://broker.example.org"
    with pytest.raises(ConnectError, match="https"):
        connect.kelo_broker(inst, "http://kelo.chat")


def test_kelo_off_releases_the_name_and_stops_the_tunnel(tmp_path):
    m, run, _ = make(tmp_path)
    broker = Broker()
    connect.kelo_connect(m, m.load(), "alice", post=broker)
    assert connect.kelo_off(m, m.load(), post=broker) is None
    assert broker.calls[-1][0].endswith("/broker/tunnel/release") and broker.calls[-1][1]["install_id"] == read_env(tmp_path / ".env", "KELO_INSTALL_ID")
    assert any("kelo-tunnel" in c and "rm" in c for c in run.calls) and not (tmp_path / "connect" / "kelo").exists()
    env = tmp_path / ".env"
    assert read_env(env, "COMPOSE_PROFILES") == "claude,agy" and read_env(env, "ASSISTANT_PUBLIC_URL") == "" and read_env(env, "CONNECT_MODE") == ""
    connect.kelo_connect(m, m.load(), "alice", post=broker)

    def down(*a, **k):
        raise httpx.ConnectError("no")

    assert "30 days" in connect.kelo_off(m, m.load(), post=down)                    # still turned off here; the name lapses later


def test_kelo_end_to_end_against_the_real_broker_with_a_fake_cloudflare(tmp_path):
    from assistant.server import Settings, create_app
    from test_server import AUTH, TOKEN

    cf = FakeCloudflare()
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, scheduler=False), tools=[],
                     cloudflare_transport=httpx.MockTransport(cf))
    with TestClient(app) as web:
        assert web.put("/api/operator/cloudflare", headers=AUTH,
                       json={"api_token": CF_TOKEN, "account_id": ACC, "zone_id": ZONE, "zone_name": "kelo.chat"}).status_code == 200
        m, run, _ = make(tmp_path)
        m.create("alice")
        url = connect.kelo_connect(m, m.load("alice"), broker="http://127.0.0.1", post=web.post)
        assert url == "https://alice.kelo.chat" and [t["name"] for t in cf.live()] == ["kelo-alice"]
        token = read_env(tmp_path / "instances" / "alice" / "kelo" / "tunnel.env", "TUNNEL_TOKEN")
        assert token == "eyJ" + cf.live()[0]["secret"]
        m.create("bobby")
        with pytest.raises(ConnectError, match="taken"):
            connect.kelo_connect(m, m.load("bobby"), "alice", broker="http://127.0.0.1", post=web.post)
        assert connect.kelo_off(m, m.load("alice"), post=web.post) is None
        assert cf.live() == [] and cf.records == {}


# ------------------------------------------------------------------------------------------------ Tailscale Funnel
def funnel_status(**ports):
    return json.dumps({"Web": {f"mac.tail1234.ts.net:{p}": {"Handlers": {"/": {"Proxy": t}}} for p, t in ports.items()}})


def test_funnel_picks_443_when_free_and_sets_a_public_address(tmp_path):
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "funnel", "status"], "{}"))
    url = connect.funnel_connect(m, m.load(), which=have)
    assert url == "https://mac.tail1234.ts.net"
    assert run.ran("tailscale", "funnel", "--bg") == [["tailscale", "funnel", "--bg", "--https=443", "http://127.0.0.1:8080"]]
    env = tmp_path / ".env"
    assert (read_env(env, "CONNECT_MODE"), read_env(env, "ASSISTANT_PUBLIC_URL")) == ("funnel", url)


def test_funnel_skips_ports_used_by_other_things_and_reuses_its_own(tmp_path):
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK),
                     (["tailscale", "funnel", "status"], funnel_status(**{"443": "http://127.0.0.1:3000"})))
    m.create("alice")
    assert connect.funnel_connect(m, m.load("alice"), which=have) == "https://mac.tail1234.ts.net:8443"
    m.run = Script((["tailscale", "status"], STATUS_OK),
                   (["tailscale", "funnel", "status"], funnel_status(**{"443": "http://127.0.0.1:3000", "8443": "http://127.0.0.1:9999",
                                                                        "10000": "http://127.0.0.1:8100"})))
    assert connect.funnel_connect(m, m.load("alice"), which=have).endswith(":10000")      # already its own


def test_funnel_explains_when_all_three_ports_are_taken_or_a_bad_port_is_asked(tmp_path):
    busy = funnel_status(**{"443": "http://127.0.0.1:1", "8443": "http://127.0.0.1:2", "10000": "http://127.0.0.1:3"})
    m, run, _ = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "funnel", "status"], busy))
    with pytest.raises(ConnectError, match="all three") as e:
        connect.funnel_connect(m, m.load(), which=have)
    assert "assistant connect tailscale" in str(e.value) and "assistant connect domain" in str(e.value)
    with pytest.raises(ConnectError, match="443, 8443 or 10000"):
        connect.funnel_connect(m, m.load(), which=have, https_port=8100)
    with pytest.raises(ConnectError, match="already serves"):
        connect.funnel_connect(m, m.load(), which=have, https_port=8443)
    m.run = Script((["tailscale", "status"], STATUS_OK), (["tailscale", "funnel", "status"], "{}"), (["tailscale", "funnel", "--bg"], (1, "Funnel not enabled")))
    with pytest.raises(ConnectError, match="funnel` attribute"):
        connect.funnel_connect(m, m.load(), which=have)
    assert m.load().connect_mode == "" and not run.ran("tailscale", "funnel", "--bg")


def test_switching_from_private_tailscale_to_funnel_turns_serve_off_first(tmp_path, monkeypatch):
    m, run, msgs = make(tmp_path, (["tailscale", "status"], STATUS_OK), (["tailscale", "serve", "status"], "{}"),
                        (["tailscale", "funnel", "status"], "{}"))
    monkeypatch.setattr(connect.shutil, "which", have)
    assert run_command(ns(cmd="connect", mode="tailscale", instance=None, https_port=None, force=False, public=False), m) == 0
    assert run_command(ns(cmd="connect", mode="tailscale", instance=None, https_port=None, force=False, public=True), m) == 0
    off = run.calls.index(["tailscale", "serve", "--https=443", "off"])
    on = run.calls.index(["tailscale", "funnel", "--bg", "--https=443", "http://127.0.0.1:8080"])
    assert off < on and m.load().connect_mode == "funnel"
    assert run_command(ns(cmd="connect", mode="off", instance=None), m) == 0 and ["tailscale", "funnel", "--https=443", "off"] in run.calls
    assert read_env(tmp_path / ".env", "ASSISTANT_PUBLIC_URL") == ""


# ------------------------------------------------------------------------------------------------ own domain
@pytest.mark.parametrize("given,want", [
    ("me.duckdns.org", ("me.duckdns.org", None, "https://me.duckdns.org")),
    ("kelo.me.duckdns.org", ("kelo.me.duckdns.org", None, "https://kelo.me.duckdns.org")),
    ("https://me.duckdns.org:8443", ("me.duckdns.org", 8443, "https://me.duckdns.org:8443")),
    ("Me.Example.com:443/", ("me.example.com", 443, "https://me.example.com")),
    ("example.com", ("example.com", None, "https://example.com")),
])
def test_any_host_is_accepted_exactly_as_given(given, want):
    assert connect.parse_address(given) == want


@pytest.mark.parametrize("bad", ["http://me.duckdns.org", "me", "https://me.duckdns.org/path", "me.duckdns.org:99999", "https://u@me.org", ""])
def test_bad_addresses_are_refused(bad):
    with pytest.raises(ConnectError):
        connect.parse_address(bad)


def test_bare_names_are_told_apart_from_sub_names():
    assert connect.is_bare("me.duckdns.org") and connect.is_bare("example.com")
    assert not connect.is_bare("kelo.me.duckdns.org") and not connect.is_bare("kelo.example.com")


def direct(m, inst, host, *, open_ports=(), ips=("203.0.113.5",), mine="203.0.113.5", **kw):
    return connect.domain_direct(m, inst, host, port_open=lambda h, p: p in open_ports, resolve=lambda h: set(ips),
                                 public_ip=lambda: mine, **kw)


def test_direct_refuses_when_port_80_or_443_is_already_used_here(tmp_path):
    m, run, _ = make(tmp_path)
    for p in (80, 443):
        with pytest.raises(ConnectError, match=f"port {p} is already in use") as e:
            direct(m, m.load(), "me.duckdns.org", open_ports=(p,))
        assert "--proxy" in str(e.value)
    assert m.load().connect_mode == "" and read_env(tmp_path / ".env", "DIRECT_HOST") == "" and run.calls == []


def test_direct_checks_that_the_name_points_here(tmp_path):
    m, run, _ = make(tmp_path)
    with pytest.raises(ConnectError, match="does not resolve"):
        direct(m, m.load(), "me.duckdns.org", ips=())
    with pytest.raises(ConnectError, match="points to 198.51.100.9") as e:
        direct(m, m.load(), "me.duckdns.org", ips=("198.51.100.9",))
    assert "203.0.113.5" in str(e.value) and "--duckdns" in str(e.value)
    with pytest.raises(ConnectError, match="inside your home network"):
        direct(m, m.load(), "me.duckdns.org", ips=("192.168.1.20",))
    with pytest.raises(ConnectError, match="ports 80 and 443"):
        direct(m, m.load(), "https://me.duckdns.org:8443")
    url, notes = direct(m, m.load(), "me.duckdns.org", ips=("198.51.100.9",), check_dns=False)
    assert url == "https://me.duckdns.org"


def test_direct_sets_up_caddy_for_the_exact_host_and_off_removes_it(tmp_path):
    m, run, _ = make(tmp_path)
    url, notes = direct(m, m.load(), "me.duckdns.org")
    env = tmp_path / ".env"
    assert url == "https://me.duckdns.org" and read_env(env, "DIRECT_HOST") == "me.duckdns.org"
    assert read_env(env, "COMPOSE_PROFILES") == "claude,agy,direct" and read_env(env, "CONNECT_HOST") == "me.duckdns.org"
    assert read_env(env, "ASSISTANT_PUBLIC_URL") == url and any("forward TCP ports 80 and 443" in n and "192.168.1.20" in n for n in notes)
    direct(m, m.load(), "me.duckdns.org", open_ports=(80, 443))                    # its own Caddy holds them now: fine to re-run
    m.create("alice")
    with pytest.raises(ConnectError, match="only one copy per computer"):
        direct(m, m.load("alice"), "alice.duckdns.org")
    connect.domain_off(m, m.load())
    assert read_env(env, "COMPOSE_PROFILES") == "claude,agy" and read_env(env, "DIRECT_HOST") == "" and read_env(env, "CONNECT_MODE") == ""
    assert any("caddy" in c and "rm" in c for c in run.calls)


def test_proxy_mode_expresses_the_owners_npm_setup_and_restores_the_old_binding(tmp_path):
    m, run, _ = make(tmp_path)
    env = tmp_path / ".env"
    env.write_text(env.read_text() + "SERVER_BIND=10.0.0.2\n")
    url, settings, notes = connect.domain_proxy(m, m.load(), "kelo.me.duckdns.org", get=no_health)
    assert url == "https://kelo.me.duckdns.org"
    assert "http://192.168.1.20:8080" in settings and "proxy_buffering off" in settings and "client_max_body_size 0;" in settings
    assert "300s" in settings and "websockets" in settings
    assert (read_env(env, "SERVER_BIND"), read_env(env, "CONNECT_MODE"), read_env(env, "ASSISTANT_PUBLIC_URL")) == ("0.0.0.0", "proxy", url)
    assert read_env(env, "CONNECT_HOST") == "kelo.me.duckdns.org"
    connect.domain_off(m, m.load())
    assert read_env(env, "SERVER_BIND") == "10.0.0.2" and read_env(env, "ASSISTANT_PUBLIC_URL") == ""


def test_proxy_mode_keeps_a_port_and_suggests_a_sub_name_when_the_bare_name_is_busy(tmp_path):
    m, run, _ = make(tmp_path)
    m.create("alice")
    busy = lambda url, **kw: Reply(200, text="<html>my other app</html>", body="html")   # noqa: E731
    url, settings, notes = connect.domain_proxy(m, m.load("alice"), "me.duckdns.org", get=busy, bind="192.168.1.20")
    assert any("kelo.me.duckdns.org" in n and "8443" in n for n in notes) and "http://192.168.1.20:8100" in settings
    url, settings, notes = connect.domain_proxy(m, m.load("alice"), "https://me.duckdns.org:8443", get=lambda u, **k: Reply(200, {"ok": True}))
    assert url == "https://me.duckdns.org:8443" and read_env(m.env_path("alice"), "CONNECT_HOST") == "https://me.duckdns.org:8443"
    assert any("already reaches a Kelo" in n for n in notes)
    connect.domain_off(m, m.load("alice"))
    assert read_env(m.env_path("alice"), "SERVER_BIND") == ""                     # it had none before
    with pytest.raises(ConnectError, match="--bind"):
        connect.domain_proxy(m, m.load("alice"), "me.duckdns.org", bind="lan", get=no_health)


def test_duckdns_token_is_sealed_and_only_used_when_asked(tmp_path):
    from cryptography.fernet import Fernet

    from assistant import ddns

    m, run, _ = make(tmp_path)
    env = tmp_path / ".env"
    tok = "1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f"
    with pytest.raises(ConnectError, match="ASSISTANT_SECRET_KEY"):
        connect.domain_proxy(m, m.load(), "me.duckdns.org", duckdns_token=tok, get=no_health)
    key = Fernet.generate_key().decode()
    env.write_text(env.read_text() + f"ASSISTANT_SECRET_KEY={key}\n")
    with pytest.raises(ConnectError, match="duckdns.org"):
        connect.domain_proxy(m, m.load(), "kelo.example.com", duckdns_token=tok, get=no_health)
    seen = []

    def get(url, params=None, **kw):
        seen.append((url, params))
        return Reply(200, text="OK") if "duckdns" in url else no_health(url)

    connect.domain_proxy(m, m.load(), "kelo.me.duckdns.org", duckdns_token=tok, get=get)
    assert seen[0] == (ddns.UPDATE, {"domains": "me", "token": tok, "ip": ""})
    assert tok not in env.read_text() and ddns.unseal(key, read_env(env, "DUCKDNS_TOKEN_ENC")) == tok and read_env(env, "DUCKDNS_DOMAIN") == "me"
    with pytest.raises(ConnectError, match="did not accept"):
        connect.domain_proxy(m, m.load(), "kelo.me.duckdns.org", duckdns_token=tok, get=lambda u, **k: Reply(200, text="KO"))
    connect.domain_off(m, m.load())
    assert read_env(env, "DUCKDNS_TOKEN_ENC") == "" and read_env(env, "DUCKDNS_DOMAIN") == ""


# ------------------------------------------------------------------------------------------------ status, switching, the commands
def test_status_shows_method_exact_host_and_health_for_every_copy(tmp_path, monkeypatch):
    m, run, msgs = make(tmp_path)
    m.create("alice")
    connect.domain_proxy(m, m.load("alice"), "https://alice.duckdns.org:8443", get=no_health)
    monkeypatch.setattr(connect, "_get", lambda url, **kw: Reply(200, {"ok": True}) if "alice" in url else no_health(url))
    assert run_command(ns(cmd="connect", mode="status", instance=None, no_check=False), m) == 0
    text = "\n".join(msgs)
    assert "the main copy: not open to the outside" in text
    assert "alice: proxy -> https://alice.duckdns.org:8443" in text and "own reverse proxy" in text and "health: ok" in text


def test_status_of_an_address_written_by_hand_is_shown_too(tmp_path):
    m, _, _ = make(tmp_path)
    env = tmp_path / ".env"
    env.write_text(env.read_text() + "ASSISTANT_PUBLIC_URL=https://kelo.me.duckdns.org\n")
    st = connect.status(m, m.load(), get=lambda u, **k: Reply(502, text="bad gateway", body="x"))
    assert st["mode"] == "manual" and st["address"] == "https://kelo.me.duckdns.org" and "something else answers (HTTP 502)" in st["health"]


def test_switching_from_kelo_to_own_domain_releases_the_name_first(tmp_path, monkeypatch):
    m, run, msgs = make(tmp_path)
    broker = Broker()
    monkeypatch.setattr(connect, "_post", broker)
    assert run_command(ns(cmd="connect", mode="kelo", instance=None, name="alice", broker=None), m) == 0
    assert "https://alice.kelo.chat" in "\n".join(msgs) and "passes through Cloudflare" in "\n".join(msgs)
    assert TUNNEL_TOKEN not in "\n".join(msgs) and read_env(tmp_path / ".env", "KELO_INSTALL_ID") not in "\n".join(msgs)
    msgs.clear()
    assert run_command(ns(cmd="connect", mode="domain", instance=None, host="kelo.me.duckdns.org", direct=False, proxy=True,
                          bind=None, duckdns=False), m) == 0
    assert broker.calls[-1][0].endswith("/release") and m.load().connect_mode == "proxy"
    text = "\n".join(msgs)
    assert "Turning off Kelo address" in text and "http://192.168.1.20:8080" in text and "is open at https://kelo.me.duckdns.org" in text
    assert read_env(tmp_path / ".env", "COMPOSE_PROFILES") == "claude,agy"


def test_direct_through_the_command_uses_the_checks_and_starts_caddy(tmp_path, monkeypatch):
    m, run, msgs = make(tmp_path)
    monkeypatch.setattr(connect, "_port_open", lambda h, p: False)
    monkeypatch.setattr(connect, "_resolve", lambda h: {"203.0.113.5"})
    monkeypatch.setattr(connect, "_public_ip", lambda *a: "203.0.113.5")
    args = ns(cmd="connect", mode="domain", instance=None, host="me.duckdns.org", direct=True, proxy=False, skip_dns_check=False, duckdns=False)
    assert run_command(args, m) == 0 and m.load().connect_mode == "direct"
    assert any("up" in c for c in run.calls)
    monkeypatch.setattr(connect, "_port_open", lambda h, p: True)
    m.create("alice")
    assert run_command(ns(**{**vars(args), "instance": "alice", "host": "alice.duckdns.org"}), m) == 1 and "only one copy" in msgs[-1]


def test_a_copy_without_a_browser_starts_its_kelo_tunnel(tmp_path):
    m, run, _ = make(tmp_path)
    m.create("alice", browser=False)
    connect.kelo_connect(m, m.load("alice"), post=Broker())
    m.start("alice", build_if_missing=False)
    assert [c for c in run.calls if "up" in c][-1][-6:] == ["--no-deps", "server", "kelo-tunnel", "workroom", "vpn", "voice"]


def test_the_real_command_line_knows_the_new_ways(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=x" * 5 + "\n")
    assert main(["connect", "status", "--no-check"]) == 0 and "not open to the outside" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["connect", "domain", "--host", "me.duckdns.org"])                   # --direct or --proxy is required
    with pytest.raises(SystemExit):
        main(["connect", "domain", "--host", "me.duckdns.org", "--direct", "--proxy"])
    assert main(["connect", "kelo"]) == 1 and "--name" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ `instance create` ends like setup
class Server(Broker):
    """The Kelo broker and the new copy's own server (the one-time phone link), both answered locally."""

    def __init__(self, taken=()):
        super().__init__()
        self.taken = set(taken)

    def __call__(self, url, json=None, timeout=None, headers=None):
        if url.endswith("/claim") and json["name"] in self.taken:
            self.calls.append((url, json))
            return Reply(409, {"detail": "that name is taken"})
        if url.endswith("/api/devices/pair"):
            self.calls.append((url, json))
            return Reply(200, {"link": f"{json['origin']}/pair#one-time"})
        return super().__call__(url, json=json, timeout=timeout)


def create(name="alice", **kw):
    return ns(cmd="instance", icmd="create", name=name, no_browser=True, domain=None, no_start=False, yes=False, **kw)


def test_create_in_a_terminal_goes_on_to_the_address_and_the_qr_code(tmp_path, monkeypatch):
    from test_wizard import FakeUI
    monkeypatch.setenv("KELO_LANG", "en")
    m, run, msgs = make(tmp_path)
    server = Server(taken={"bob"})
    monkeypatch.setattr(connect, "_post", server)
    ui = FakeUI("1",          # default address
                "bob",        # taken
                True,         # try another name
                "",           # Enter: the suggestion, which is the copy's name
                True)         # sign a phone in now
    assert run_command(create(), m, ui=ui) == 0
    assert any("ask: Name for your address" in q for q in ui.asked) and any("that name is taken" in s for s in ui.shown)
    alice = m.load("alice")
    assert (alice.connect_mode, alice.connect_url) == ("kelo", "https://alice.kelo.chat")
    assert ui.qr_links == ["https://alice.kelo.chat/pair#one-time"]
    text = "\n".join(msgs)
    assert 'Kelo "alice" is running: https://alice.kelo.chat' in text
    assert msgs[-2:] == ["Connect another phone (shows a QR code):", "    assistant phone add --instance alice"]
    assert "connect kelo" not in text and TUNNEL_TOKEN not in text + "\n".join(ui.shown)


def test_create_in_a_terminal_not_now_prints_each_command_on_its_own_line(tmp_path, monkeypatch):
    from test_wizard import FakeUI
    monkeypatch.setenv("KELO_LANG", "en")
    monkeypatch.setattr("assistant.wizard.platform.system", lambda: "Darwin")
    m, _, msgs = make(tmp_path)
    ui = FakeUI("3")                                       # not now
    assert run_command(create(), m, ui=ui) == 0
    assert ui.qr_links == [] and m.load("alice").connect_mode == ""
    tail = msgs[msgs.index('Kelo "alice" is running: http://127.0.0.1:8100 (this Mac only)'):]
    assert tail == ['Kelo "alice" is running: http://127.0.0.1:8100 (this Mac only)', "",
                    "Reach it from your phone (pick one):",
                    "    Kelo address (offered for now)   assistant connect kelo --instance alice --name <name>",
                    "    " + "Tailscale".ljust(33) + "assistant connect tailscale --instance alice",
                    "    " + "Own domain".ljust(33) + "assistant connect domain --instance alice --host <address> --proxy", "",
                    "Then connect a phone (shows a QR code):",
                    "    assistant phone add --instance alice"]
    assert not any("Next:" in line for line in msgs)


def test_create_in_a_terminal_later_lists_the_other_ways_for_this_copy(tmp_path, monkeypatch):
    from test_wizard import FakeUI
    m, _, _ = make(tmp_path)
    ui = FakeUI("2")
    assert run_command(create(), m, ui=ui) == 0
    shown = "\n".join(ui.shown)
    assert "assistant connect tailscale --instance alice" in shown and "assistant connect funnel --instance alice" in shown
    assert ui.qr_links == []


def test_ctrl_c_in_the_phone_step_keeps_the_new_copy(tmp_path, monkeypatch):
    from test_wizard import FakeUI

    class Interrupt(FakeUI):
        def choose(self, question, options, default):
            raise KeyboardInterrupt
    m, _, msgs = make(tmp_path)
    assert run_command(create(), m, ui=Interrupt()) == 0
    assert m.names() == ["alice"] and "Cancelled" not in "\n".join(msgs)
    assert msgs[-1] == "    assistant phone add --instance alice"


def test_create_without_a_terminal_asks_nothing_and_speaks_the_system_language(tmp_path, monkeypatch):
    monkeypatch.setenv("KELO_LANG", "ko")
    m, _, msgs = make(tmp_path)
    assert run_command(create(), m) == 0                   # no ui: a script, or --yes
    text = "\n".join(msgs)
    assert "휴대폰에서 접속할 방법 (하나 선택):" in text and "그다음 휴대폰 연결 (QR 코드 표시):" in text
    assert "    Kelo 주소 (현재 한정 제공)   assistant connect kelo --instance alice --name <name>" in msgs
    assert "    Tailscale" + " " * 20 + "assistant connect tailscale --instance alice" in msgs   # lined up: Korean letters take two cells
    assert msgs[-1] == "    assistant phone add --instance alice"
    assert "내 컴퓨터" not in text and "your computer" not in text


def test_create_with_no_start_says_how_to_start_it(tmp_path, monkeypatch):
    monkeypatch.setenv("KELO_LANG", "en")
    m, run, msgs = make(tmp_path)
    assert run_command(ns(**{**vars(create()), "no_start": True}), m) == 0
    assert 'Kelo "alice" is ready but not started. Start it with:' in msgs and "    assistant instance start alice" in msgs
    assert not any("up" in c for c in run.calls)


def test_only_a_terminal_without_yes_gets_the_phone_step(monkeypatch):
    import sys

    from assistant import cli, wizard
    args = lambda **kw: ns(**{"cmd": "instance", "icmd": "create", "yes": False, "no_start": False, **kw})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert isinstance(cli._create_ui(args()), wizard.RichUI)
    assert cli._create_ui(args(yes=True)) is None and cli._create_ui(args(no_start=True)) is None
    assert cli._create_ui(ns(cmd="instance", icmd="list")) is None
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli._create_ui(args()) is None


def test_create_in_a_real_looking_terminal_renders_the_menu(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    from assistant import wizard
    monkeypatch.setenv("KELO_LANG", "en")
    buf = io.StringIO()
    ui = wizard.RichUI("en", console=Console(file=buf, width=80, no_color=True, force_terminal=False), input_fn=lambda _: "3")
    m, _, msgs = make(tmp_path)
    assert run_command(create(), m, ui=ui) == 0
    screen = buf.getvalue()
    assert "Where should your phone reach Kelo?" in screen and "\x1b[" not in screen
    assert "1) Kelo address (offered for now): alice.kelo.chat ready in a minute; works from anywhere" in " ".join(screen.split())
    assert "2) Tailscale or my own domain  needs setup on your side" in screen
    assert "3) Skip for now  the phone can't connect until an address is set" in screen
    assert "Type 1-3 and press Enter (Enter alone = 1):" in screen
    assert msgs[-1] == "    assistant phone add --instance alice"
