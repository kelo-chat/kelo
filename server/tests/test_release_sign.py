"""Signed app releases (release_sign.py, release_keys.py, downloads.py): the shared test vector the Kotlin apps read too, the key file's
rules, publish / sign-app with a fake docker, and that the public keys here equal the ones built into the apps (ReleaseKeys.kt)."""

import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistant import cli, downloads, release_keys, release_sign
from assistant.downloads import Downloads, Publisher, PublishError, _entry, run_publish, run_release_key, run_sign
from assistant.release_sign import SigningKeyError, key_id, load_key, new_key, sign_row, statement, verify_row

from test_downloads import APK, DMG, FakeDocker

REPO = Path(__file__).resolve().parents[2]
VECTOR = REPO / "apps/shared/src/desktopTest/resources/release_signature.json"
KOTLIN_KEYS = REPO / "apps/shared/src/commonMain/kotlin/chat/kelo/app/update/ReleaseKeys.kt"
COMMIT = "a" * 40


def _flip(sig: str) -> str:
    raw = bytearray(base64.b64decode(sig))
    raw[0] ^= 1
    return base64.b64encode(bytes(raw)).decode()


# ---------------------------------------------------------------------------------------------------------------- the vector
@pytest.mark.skipif(not VECTOR.is_file(), reason="the apps' test vector is not in this checkout")
def test_the_shared_vector_has_the_same_text_and_verifies_and_breaks_on_any_change():
    v = json.loads(VECTOR.read_text(encoding="utf-8"))
    row, platform, keys = v["row"], v["platform"], {v["key_id"]: v["public_key"]}
    assert statement(platform, row).decode() == v["statement"]
    assert key_id(base64.b64decode(v["public_key"])) == v["key_id"]
    signed = {**row, "sig": v["sig"], "key": v["key_id"]}
    assert verify_row(platform, signed, keys) is True
    changed = {"file": "kelo-1.2.3-4243.exe", "version": "1.2.4", "version_code": 4243, "size": 1234568,
               "sha256": "0" * 64, "commit": "0" * 40}
    assert set(changed) == set(row)                                        # every field of the vector is tried
    for field, value in changed.items():
        assert verify_row(platform, {**signed, field: value}, keys) is False, field
    for field in ("version_code", "commit"):                               # and leaving one out
        assert verify_row(platform, {k: v2 for k, v2 in signed.items() if k != field}, keys) is False, field
    for other in ("android", "windows-msi", "mac"):
        assert verify_row(other, signed, keys) is False, other
    assert verify_row(platform, {**signed, "key": "0123456789abcdef"}, keys) is False
    assert verify_row(platform, signed, {}) is False
    assert verify_row(platform, signed, release_keys.RELEASE_KEYS) is False   # the real key list does not know the test key
    for bad in (v["sig"][:-4] + "!!!!", v["sig"][:-2], v["sig"] + "AAAA", "", _flip(v["sig"])):
        assert verify_row(platform, {**signed, "sig": bad}, keys) is False, bad


def test_verify_row_never_raises_on_malformed_rows():
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keys = {key_id(pub): base64.b64encode(pub).decode()}
    base = {"file": "kelo-1.0.exe", "version": "1.0", "size": 5, "sha256": "b" * 64}
    sig = base64.b64encode(key.sign(statement("windows", base))).decode()
    good = {**base, "sig": sig, "key": key_id(pub)}
    assert verify_row("windows", good, keys) is True
    for bad in ({**good, "size": "5"}, {**good, "size": True}, {**good, "size": -5}, {**good, "sha256": "B" * 64}, {**good, "version": ""},
                {**good, "file": "../x.exe"}, {**good, "version_code": "7"}, {**good, "version_code": True}, {**good, "commit": "xyz"},
                {**good, "commit": "A" * 40}, {**good, "sig": 5}, {**good, "key": None}, {k: v for k, v in good.items() if k != "sha256"}):
        assert verify_row("windows", bad, keys) is False, bad
    assert verify_row("ios", good, keys) is False
    assert verify_row("windows", good, {key_id(pub): "not base64"}) is False
    with pytest.raises(ValueError):
        statement("windows", {**base, "size": 0})


# ---------------------------------------------------------------------------------------------------------------- the key file
def test_new_key_is_private_never_overwritten_and_never_shows_its_private_part(tmp_path):
    path = tmp_path / "keys" / "signing.key"
    kid, pub = new_key(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    private = serialization.load_pem_private_key(path.read_bytes(), password=None)
    raw_private = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    assert key_id(base64.b64decode(pub)) == kid and len(kid) == 16
    for shown in (kid, pub):
        assert base64.b64encode(raw_private).decode() not in shown and raw_private.hex() not in shown
    before = path.read_bytes()
    with pytest.raises(SigningKeyError, match="already exists"):
        new_key(path)
    assert path.read_bytes() == before
    lines: list[str] = []
    assert run_release_key(SimpleNamespace(path=str(tmp_path / "k2" / "a.key")), lines.append, tmp_path / "root") == 0
    text = "\n".join(lines)
    assert "key id" in text and "public key" in text and "offline copy" in text and "BEGIN" not in text
    assert run_release_key(SimpleNamespace(path=str(tmp_path / "k2" / "a.key")), lines.append, tmp_path / "root") == 1


def test_load_key_refuses_a_key_where_a_container_could_reach_it_or_others_can_read_it(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "compose.yml").write_text("services:\n  server:\n    volumes:\n      - ./state:/state\n      - /srv/kelo-data:/data:ro\n")
    outside = tmp_path / "safe" / "signing.key"
    new_key(outside)
    assert load_key(outside, root) is not None
    for inside in (root / "signing.key", root / "state" / "k.key", Path("/srv/kelo-data/k.key"), Path("/state/k.key")):
        with pytest.raises(SigningKeyError, match="outside|elsewhere|operator's own"):
            load_key(inside, root)
    with pytest.raises(SigningKeyError, match="no signing key"):
        load_key(tmp_path / "safe" / "missing.key", root)
    outside.chmod(0o644)
    with pytest.raises(SigningKeyError, match="chmod 600"):
        load_key(outside, root)
    outside.chmod(0o640)
    with pytest.raises(SigningKeyError, match="chmod 600"):
        load_key(outside, root)
    outside.chmod(0o600)
    (tmp_path / "safe" / "other.key").write_text("not a key")
    (tmp_path / "safe" / "other.key").chmod(0o600)
    with pytest.raises(SigningKeyError, match="not an unencrypted Ed25519"):
        load_key(tmp_path / "safe" / "other.key", root)
    with pytest.raises(SigningKeyError, match="inside"):
        release_sign.check_location(root / "x.key", root)                     # `release-key new` uses the same rule


# ---------------------------------------------------------------------------------------------------------------- publish / sign-app
@pytest.fixture
def signing(tmp_path, monkeypatch):
    """A test key listed as the one key the apps know, and a root (compose folder) the key is not inside."""
    root = tmp_path / "root"
    root.mkdir()
    path = tmp_path / "keys" / "signing.key"
    kid, pub = new_key(path)
    monkeypatch.setitem(release_keys.RELEASE_KEYS, kid, pub)
    return SimpleNamespace(root=root, path=path, kid=kid, pub=pub)


def test_publish_signs_the_row_and_latest_json_carries_sig_key_and_commit(tmp_path, signing):
    volume, lines = tmp_path / "vol", []
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    got = Publisher(signing.root, run=FakeDocker(volume), out=lines.append).publish("mac", dmg, "1.0.8", commit=COMMIT.upper(),
                                                                                    key_path=signing.path)
    assert got["key"] == signing.kid and got["commit"] == COMMIT and len(got["sig"]) == 88
    row = Downloads(volume).latest("https://kelo.chat")["mac"]
    assert (row["sig"], row["key"], row["commit"]) == (got["sig"], signing.kid, COMMIT)
    assert verify_row("mac", row) is True                                  # latest.json as served checks against RELEASE_KEYS
    assert not any("WITHOUT" in line for line in lines)
    # without a commit the text has an empty commit line and the row has no commit
    apk = tmp_path / "a.apk"
    apk.write_bytes(APK)
    Publisher(signing.root, run=FakeDocker(volume), out=lambda s: None).publish("android", apk, "0.9.0+402500", key_path=signing.path)
    android = Downloads(volume).latest()["android"]
    assert "commit" not in android and verify_row("android", android) is True and verify_row("mac", android) is False
    assert b"commit=\n" in statement("android", android)
    with pytest.raises(PublishError, match="40-character"):
        Publisher(signing.root, run=FakeDocker(volume), out=lambda s: None).publish("mac", dmg, "1.0.9", commit="abc", key_path=signing.path)


def test_a_key_the_apps_do_not_know_is_refused_before_anything_is_copied(tmp_path):
    volume = tmp_path / "vol"
    path = tmp_path / "keys" / "signing.key"
    new_key(path)                                                          # not in RELEASE_KEYS
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    docker = FakeDocker(volume)
    with pytest.raises(PublishError, match="not one the apps know: add its public key to release_keys.py and ReleaseKeys.kt first"):
        Publisher(tmp_path / "root", run=docker, out=lambda s: None).publish("mac", dmg, "1.0.8", key_path=path)
    assert not volume.exists()


def test_no_key_is_an_error_and_unsigned_publishes_an_unsigned_row_with_a_warning(tmp_path, signing):
    volume, lines = tmp_path / "vol", []
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    pub = Publisher(signing.root, run=FakeDocker(volume), out=lines.append)
    with pytest.raises(PublishError, match="assistant release-key new"):
        pub.publish("mac", dmg, "1.0.8")
    assert not volume.exists()
    with pytest.raises(PublishError, match="no signing key"):
        pub.publish("mac", dmg, "1.0.8", key_path=tmp_path / "keys" / "none.key")
    got = pub.publish("mac", dmg, "1.0.8", unsigned=True, commit=COMMIT)
    assert "sig" not in got and "key" not in got
    assert any("WITHOUT a signature" in line and "will not offer" in line for line in lines)
    row = Downloads(volume).latest()["mac"]
    assert "sig" not in row and verify_row("mac", row) is False


def test_a_key_inside_the_compose_folder_is_refused_when_publishing(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    kid, pub = new_key(root / "signing.key")
    monkeypatch.setitem(release_keys.RELEASE_KEYS, kid, pub)
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    with pytest.raises(PublishError, match="inside"):
        Publisher(root, run=FakeDocker(tmp_path / "vol"), out=lambda s: None).publish("mac", dmg, "1.0.8", key_path=root / "signing.key")


def test_sign_app_signs_the_live_row_and_refuses_a_file_that_differs(tmp_path, signing):
    volume = tmp_path / "vol"
    docker = FakeDocker(volume)
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    Publisher(signing.root, run=docker, out=lambda s: None).publish("mac", dmg, "1.0.8", unsigned=True)
    live = Downloads(volume).latest()["mac"]
    assert "sig" not in live
    lines: list[str] = []
    pub = Publisher(signing.root, run=docker, out=lines.append)
    got = pub.sign("mac", COMMIT, signing.path)
    after = Downloads(volume).latest()["mac"]
    assert verify_row("mac", after) is True and after["commit"] == COMMIT and got["key"] == signing.kid
    assert {k: v for k, v in after.items() if k not in ("sig", "key", "commit")} == live        # nothing else changed (date included)
    assert (volume / live["file"]).read_bytes() == DMG                                           # the file was not touched
    assert any(line.startswith("Signed macOS 1.0.8") for line in lines)
    pub.sign("mac", None, signing.path)                                                          # signing again keeps the commit
    assert Downloads(volume).latest()["mac"]["commit"] == COMMIT
    with pytest.raises(PublishError, match="no published windows"):
        pub.sign("windows", None, signing.path)
    with pytest.raises(PublishError, match="no signing key"):
        pub.sign("mac", None, None)
    # a different file on the server (same size, other bytes): the manifest's sha256 no longer matches, so nothing is signed
    before = (volume / "manifest.json").read_bytes()
    (volume / live["file"]).write_bytes(bytes(len(DMG)))
    with pytest.raises(PublishError, match="does not match"):
        pub.sign("mac", COMMIT, signing.path)
    assert (volume / "manifest.json").read_bytes() == before


def test_sign_app_and_release_key_are_cli_commands_and_run_only_on_the_operators_server(tmp_path, signing, monkeypatch):
    seen = {}
    monkeypatch.setattr(downloads, "run_sign", lambda a: seen.setdefault("sign", a) and 0)
    monkeypatch.setattr(downloads, "run_release_key", lambda a: seen.setdefault("key", a) and 0)
    monkeypatch.setattr(downloads, "run_publish", lambda a: seen.setdefault("pub", a) and 0)
    assert cli.main(["sign-app", "windows", "--commit", COMMIT, "--sign-key", "/k"]) == 0
    assert (seen["sign"].platform, seen["sign"].commit, seen["sign"].sign_key) == ("windows", COMMIT, "/k")
    assert cli.main(["release-key", "new", "--path", "/x/k"]) == 0 and seen["key"].path == "/x/k"
    assert cli.main(["publish-app", "mac", "/x/k.dmg", "--commit", COMMIT, "--sign-key", "/k", "--unsigned"]) == 0
    assert (seen["pub"].commit, seen["pub"].sign_key, seen["pub"].unsigned) == (COMMIT, "/k", True)
    with pytest.raises(SystemExit):
        cli.main(["sign-app", "ios"])
    root = signing.root
    (root / "compose.yml").write_text("services: {}\n")
    (root / ".env").write_text("ASSISTANT_PUBLIC_URL=https://me.kelo.chat\nASSISTANT_KELO_BROKER=https://kelo.chat\n")
    lines: list[str] = []
    docker = FakeDocker(tmp_path / "vol")
    args = SimpleNamespace(platform="mac", commit=None, sign_key=str(signing.path))
    assert run_sign(args, root, run=docker, out=lines.append) == 1 and "operator's server" in lines[-1] and not docker.calls
    (root / ".env").write_text("ASSISTANT_PUBLIC_URL=https://kelo.chat\nASSISTANT_OAUTH_BROKER=https://kelo.chat\n")
    assert run_sign(args, root, run=docker, out=lines.append) == 1 and "no published mac" in lines[-1]
    dmg = tmp_path / "Kelo-1.0.0.dmg"
    dmg.write_bytes(DMG)
    pargs = SimpleNamespace(platform="mac", file=str(dmg), version=None, sign_key=str(signing.path), commit=COMMIT, unsigned=False)
    assert run_publish(pargs, root, run=docker, out=lines.append) == 0
    assert verify_row("mac", Downloads(tmp_path / "vol").latest()["mac"]) is True
    assert run_publish(SimpleNamespace(**{**vars(pargs), "sign_key": str(root / "signing.key")}), root, run=docker, out=lines.append) == 1


# ---------------------------------------------------------------------------------------------------------------- the manifest
def test_entry_keeps_a_well_formed_signature_key_and_commit_and_drops_the_rest():
    good = {"file": "kelo-1.0.dmg", "version": "1.0", "size": 5, "sha256": "a" * 64, "date": "d"}
    sig = base64.b64encode(bytes(64)).decode()
    kept = _entry("mac", {**good, "sig": sig, "key": "0123456789abcdef", "commit": COMMIT})
    assert (kept["sig"], kept["key"], kept["commit"]) == (sig, "0123456789abcdef", COMMIT)
    assert len(sig) == 88
    for field, bad in (("sig", sig[:-2]), ("sig", sig + "A"), ("sig", "x" * 88), ("sig", 5), ("key", "0123456789ABCDEF"), ("key", "0123"),
                       ("key", None), ("commit", COMMIT.upper()), ("commit", "a" * 39), ("commit", 7)):
        assert field not in _entry("mac", {**good, "sig": sig, "key": "0123456789abcdef", "commit": COMMIT, field: bad}), (field, bad)
    assert "sig" not in _entry("mac", good) and "commit" not in _entry("mac", good)


def test_the_public_keys_here_equal_the_ones_built_into_the_apps():
    if not KOTLIN_KEYS.is_file():
        pytest.skip("the apps are not in this checkout")
    text = KOTLIN_KEYS.read_text(encoding="utf-8")
    body = re.search(r"val ALL: Map<String, String> = mapOf\((.*?)\)\s*\n\}", text, re.S)
    assert body is not None, "ReleaseKeys.kt: `val ALL: Map<String, String> = mapOf(...)` not found"
    in_apps = dict(re.findall(r"\"([0-9a-f]{16})\"\s+to\s+\"([A-Za-z0-9+/=]+)\"", body.group(1)))
    assert in_apps == release_keys.RELEASE_KEYS
    for kid, pub in release_keys.RELEASE_KEYS.items():
        assert key_id(base64.b64decode(pub)) == kid


def test_sign_row_returns_the_row_plus_sig_and_key_and_leaves_the_input_alone(tmp_path):
    path = tmp_path / "k" / "signing.key"
    kid, pub = new_key(path)
    row = {"file": "kelo-1.0.exe", "version": "1.0", "size": 5, "sha256": hashlib.sha256(b"x").hexdigest(), "date": "d"}
    signed = sign_row("windows", row, path, tmp_path / "root")
    assert "sig" not in row and signed["key"] == kid and {k: v for k, v in signed.items() if k not in ("sig", "key")} == row
    assert verify_row("windows", signed, {kid: pub}) is True
    assert os.stat(path).st_mode & 0o077 == 0


def test_the_server_msi_row_has_its_own_signed_statement():
    from assistant.release_sign import statement
    row = {"file": "kelo-server-0.10.2.msi", "version": "0.10.2", "size": 1234, "sha256": "a" * 64}
    text = statement("windows-server-msi", row).decode()
    assert text.splitlines()[1] == "platform=windows-server-msi"
    assert statement("windows-server-msi", row) != statement("windows-msi", row)
    with pytest.raises(ValueError):
        statement("windows-server", row)


def test_the_mac_server_bundle_row_has_its_own_signed_statement():
    from assistant.release_sign import PLATFORM_KEYS, statement
    row = {"file": "kelo-server-0.10.2.zip", "version": "0.10.2", "size": 1234, "sha256": "a" * 64}
    text = statement("mac-server-bundle", row).decode()
    assert text.splitlines()[1] == "platform=mac-server-bundle"
    assert statement("mac-server-bundle", row) != statement("mac", row)
    assert "mac-server-bundle" in PLATFORM_KEYS


# ---------------------------------------------------------------------------------------------------------------- store rows (kelo-store-v1)
STORE = {"channel": "play", "version": "0.10.3", "build": 402600, "date": "2026-11-18"}


def test_store_row_signs_and_verifies(tmp_path):
    path = tmp_path / "k" / "signing.key"
    kid, pub = new_key(path)
    signed = release_sign.sign_store_row("android", STORE, path, tmp_path / "root")
    assert signed["key"] == kid and set(signed) == set(STORE) | {"sig", "key"}
    assert release_sign.verify_store_row("android", signed, {kid: pub}) is True
    assert release_sign.verify_store_row("android", signed) is False      # the real key list does not know this key


def test_store_row_with_a_changed_field_fails(tmp_path):
    path = tmp_path / "k" / "signing.key"
    kid, pub = new_key(path)
    keys = {kid: pub}
    signed = release_sign.sign_store_row("android", {**STORE, "min_server": "0.10.0"}, path, tmp_path / "root")
    assert release_sign.verify_store_row("android", signed, keys) is True
    for field, value in {"version": "0.10.4", "build": 402601, "min_server": "0.10.1", "date": "2026-11-19", "channel": "appstore"}.items():
        assert release_sign.verify_store_row("android", {**signed, field: value}, keys) is False, field
    assert release_sign.verify_store_row("android", {k: v for k, v in signed.items() if k != "min_server"}, keys) is False
    assert release_sign.verify_store_row("ios", signed, keys) is False


def test_store_channel_must_match_platform():
    with pytest.raises(ValueError):
        release_sign.store_statement("android", {**STORE, "channel": "appstore"})
    with pytest.raises(ValueError):
        release_sign.store_statement("ios", STORE)
    with pytest.raises(ValueError):
        release_sign.store_statement("mac", {**STORE, "channel": "download"})
    for bad in ({"build": True}, {"build": 0}, {"date": "2026-13-01"}, {"version": "1.2"}, {"min_server": "x"}):
        with pytest.raises(ValueError):
            release_sign.store_statement("android", {**STORE, **bad})


def test_store_signature_never_verifies_as_a_file_row_or_notices(tmp_path):
    path = tmp_path / "k" / "signing.key"
    kid, pub = new_key(path)
    keys = {kid: pub}
    store = release_sign.sign_store_row("android", STORE, path, tmp_path / "root")
    file_row = {"file": "kelo-0.10.3.apk", "version": "0.10.3", "size": 5, "sha256": "c" * 64}
    as_file = sign_row("android", file_row, path, tmp_path / "root")
    assert verify_row("android", {**file_row, "sig": store["sig"], "key": kid}, keys) is False
    assert release_sign.verify_notices([], store["sig"], kid, keys) is False
    assert release_sign.verify_store_row("android", {**STORE, "sig": as_file["sig"], "key": kid}, keys) is False
    notices_sig, notices_kid = release_sign.sign_notices([], path, tmp_path / "root")
    assert release_sign.verify_store_row("android", {**STORE, "sig": notices_sig, "key": notices_kid}, keys) is False


def test_store_statement_bytes():
    text = release_sign.store_statement("desktop", {"channel": "msstore", "version": "1.0.2", "build": 100000002,
                                                    "min_server": "0.10.1", "date": "2026-11-18"})
    assert text == (b"kelo-store-v1\nplatform=desktop\nchannel=msstore\nversion=1.0.2\nbuild=100000002\n"
                    b"min_server=0.10.1\ndate=2026-11-18\n")
    assert release_sign.store_statement("ios", {"channel": "appstore", "version": "0.10.4", "build": 26111411, "date": "2026-11-18"}) == (
        b"kelo-store-v1\nplatform=ios\nchannel=appstore\nversion=0.10.4\nbuild=26111411\nmin_server=\ndate=2026-11-18\n")


def test_sign_app_keeps_store_rows(tmp_path, signing):
    volume = tmp_path / "vol"
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    store = {"android": {"play": {"channel": "play", "version": "0.10.3", "build": 402600, "date": "2026-11-18", "sig": "A" * 86 + "==",
                                 "key": "0123456789abcdef"}}}
    Publisher(signing.root, run=FakeDocker(volume), out=lambda s: None).publish("mac", dmg, "1.0.8", unsigned=True)
    raw = json.loads((volume / "manifest.json").read_text())
    (volume / "manifest.json").write_text(json.dumps({**raw, "store": store}))
    Publisher(signing.root, run=FakeDocker(volume), out=lambda s: None).sign("mac", COMMIT, signing.path)
    assert json.loads((volume / "manifest.json").read_text())["store"] == store


def _store_record():
    from assistant.app_releases import AppReleases

    def e(version, build, channel):
        return {"version": version, "build": build, "channel": channel, "date": "2026-11-18"}
    return AppReleases.parse({"android": [e("0.10.2", 402500, "play"), e("0.10.3", 402600, "play")],
                              "ios": [e("0.10.1", 26111401, "appstore")], "desktop": [e("1.0.2", 100000002, "download")]})


def test_publish_store_signs_the_newest_store_release_per_platform(tmp_path, signing):
    from assistant.downloads import StorePublisher

    volume, lines = tmp_path / "vol", []
    StorePublisher(signing.root, run=FakeDocker(volume), out=lines.append).publish_store(_store_record(), signing.path)
    store = json.loads((volume / "manifest.json").read_text())["store"]
    assert set(store) == {"android", "ios"} and set(store["android"]) == {"play"} and set(store["ios"]) == {"appstore"}
    assert store["android"]["play"]["version"] == "0.10.3" and store["ios"]["appstore"]["version"] == "0.10.1"
    assert release_sign.verify_store_row("android", store["android"]["play"]) and release_sign.verify_store_row("ios", store["ios"]["appstore"])
    assert Downloads(volume).latest()["store"] == store                                  # the readers see the same rows
    assert any(line.startswith("Published store releases: android 0.10.3 (play), ios 0.10.1 (appstore)") for line in lines)


def test_publish_store_refuses_without_a_key_and_on_a_non_operator_copy(tmp_path, signing):
    from assistant.downloads import PublishError, StorePublisher, run_publish_store

    volume = tmp_path / "vol"
    with pytest.raises(PublishError, match="no signing key"):
        StorePublisher(signing.root, run=FakeDocker(volume), out=lambda s: None).publish_store(_store_record(), None)
    assert not (volume / "manifest.json").exists()
    copy, lines, docker = tmp_path / "copy", [], FakeDocker(volume)
    copy.mkdir()
    assert run_publish_store(SimpleNamespace(sign_key=str(signing.path)), copy, run=docker, out=lines.append, record=_store_record()) == 1
    assert lines and not docker.calls and not (volume / "manifest.json").exists()
