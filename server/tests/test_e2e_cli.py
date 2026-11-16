"""`assistant phone fingerprint` and `assistant e2e rotate` (KE2E): they talk to the running copy with its access key."""

from types import SimpleNamespace

from assistant import cli, connect, manage

from test_connect_cli import Resp, make

INFO = {"v": 1, "kid": "63682d9925e9da86", "pk": "OFJ52Ces7PFBXbc107iFdZtEkPYt3bDrO1FM3Msw01k",
        "fingerprint": "MNUC-3GJF-5HNI-MPYR-YGRR-VPKK"}


def test_connect_reads_and_rotates_the_key(tmp_path):
    m, _, _ = make(tmp_path)
    inst = m.load(None)
    seen = []

    def get(url, **kw):
        seen.append(("GET", url, kw["headers"]["Authorization"].startswith("Bearer ")))
        return Resp(200, INFO)

    def post(url, **kw):
        seen.append(("POST", url, True))
        return Resp(200, {**INFO, "kid": "0011223344556677", "pair_again": True})

    assert connect.e2e_info(m, inst, get=get)["fingerprint"] == INFO["fingerprint"]
    assert connect.e2e_rotate(m, inst, post=post)["pair_again"] is True
    assert [s[0] for s in seen] == ["GET", "POST"] and seen[0][1].endswith("/api/e2e/info") and seen[1][1].endswith("/api/e2e/rotate")


def test_the_commands(tmp_path, monkeypatch):
    m, _, msgs = make(tmp_path)
    monkeypatch.setattr(connect, "e2e_info", lambda m, inst: INFO)
    rotated = []
    monkeypatch.setattr(connect, "e2e_rotate", lambda m, inst: rotated.append(1) or {**INFO, "fingerprint": "AAAA-BBBB"})
    assert manage.run_command(SimpleNamespace(cmd="phone", pcmd="fingerprint", instance=None), manager=m) == 0
    assert any(INFO["fingerprint"] in x for x in msgs)
    assert manage.run_command(SimpleNamespace(cmd="e2e", ecmd="rotate", instance=None, yes=False), manager=m,
                              ask=lambda q: "n") == 0
    assert rotated == [] and "Nothing was changed" in msgs[-1]
    assert manage.run_command(SimpleNamespace(cmd="e2e", ecmd="rotate", instance=None, yes=True), manager=m) == 0
    assert rotated == [1] and any("AAAA-BBBB" in x for x in msgs) and "pair again" in msgs[-1]


def test_the_cli_parses_them(monkeypatch):
    got = []
    monkeypatch.setattr(manage, "run_command", lambda args, **k: got.append(args) or 0)
    cli.main(["phone", "fingerprint"])
    cli.main(["e2e", "rotate", "--yes", "--instance", "alice"])
    assert got[0].pcmd == "fingerprint" and (got[1].cmd, got[1].ecmd, got[1].yes, got[1].instance) == ("e2e", "rotate", True, "alice")
