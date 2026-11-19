"""Release manifests (release.py): what a server accepts from the release list, and how images are checked before use."""

import json
import subprocess

import pytest

from assistant import release as rel
from assistant.downloads import Downloads, server_entry

D = {c: "sha256:" + f"{i:x}" * 64 for i, c in enumerate(rel.COMPONENTS, start=1)}


def manifest(**kw):
    m = rel.build_manifest("0.5.0", D, date="2026-11-09T00:00:00Z", min_app={"android": "402400"},
                           notes={"en": "Faster replies.", "ko": "답장이 빨라졌어요."})
    m.update(kw)
    return m


def test_a_good_manifest_round_trips_and_picks_notes_by_language():
    r = rel.parse(manifest())
    assert r.version == "0.5.0" and r.channel == "stable" and r.images["server"].ref == f"ghcr.io/kelo-chat/server@{D['server']}"
    assert r.note("ko-KR") == "답장이 빨라졌어요." and r.note("de") == "Faster replies." and r.note(None) == "Faster replies."
    assert rel.parse(r.to_json()) == r
    assert rel.from_latest({"android": {}, "server": manifest()}).version == "0.5.0"
    with pytest.raises(rel.ReleaseError, match="no server release"):
        rel.from_latest({"android": {}})


@pytest.mark.parametrize("change, why", [
    ({"images": {**manifest()["images"], "server": {"repo": "docker.io/evil/server", "digest": D["server"]}}}, "configured registry"),
    ({"images": {**manifest()["images"], "server": {"repo": "ghcr.io/kelo-chat/server", "digest": "latest"}}}, "sha256"),
    ({"images": {**manifest()["images"], "server": {"repo": "ghcr.io/kelo-chat/server", "digest": D["server"], "cmd": "rm -rf /"}}}, "not readable"),
    ({"images": {**manifest()["images"], "shell": {"repo": "ghcr.io/kelo-chat/shell", "digest": D["server"]}}}, "unknown image"),
    ({"images": {**manifest()["images"], "cli-claude": {"repo": "ghcr.io/kelo-chat/cli-claude", "digest": D["server"]}}}, "never published"),
    ({"images": {**manifest()["images"], "cli-agy": {"repo": "ghcr.io/kelo-chat/cli-agy", "digest": D["server"]}}}, "never published"),
    ({"images": {c: v for c, v in manifest()["images"].items() if c != "updater"}}, "missing images: updater"),
    ({"registry": "ghcr.io/someone-else"}, "another registry"),
    ({"version": "latest"}, "version"),
    ({"version": "1.0.0; rm -rf /"}, "version"),
    ({"schema": 2}, "format"),
    ({"min_app": {"android": "$(id)"}}, "minimum app"),
    ({"compose_level": "2"}, "setup file"),
])
def test_anything_odd_in_a_manifest_is_refused(change, why):
    with pytest.raises(rel.ReleaseError, match=why):
        rel.parse(manifest(**change))


def test_versions_compare_numerically_and_prereleases_come_first():
    assert rel.newer("0.10.0", "0.9.9") and rel.newer("0.5.0", "0.5.0-rc.2") and rel.newer("0.5.0-rc.10", "0.5.0-rc.9")
    assert not rel.newer("0.5.0", "0.5.0") and not rel.newer("0.4.9", "0.5.0") and not rel.newer(None, "0.1.0")
    assert rel.newer("0.1.0", None) and rel.newer("0.1.0", "dev")


def test_fetch_reads_https_only_and_bounds_the_size():
    body = json.dumps({"server": manifest()}).encode()
    assert rel.fetch("https://kelo.chat/download/latest.json", get=lambda url, t: body).version == "0.5.0"
    assert rel.fetch("http://127.0.0.1:18930/latest.json", get=lambda url, t: body).version == "0.5.0"
    for url in ("http://kelo.chat/download/latest.json", "file:///etc/passwd", "ftp://x/y"):
        with pytest.raises(rel.ReleaseError, match="https"):
            rel.fetch(url, get=lambda url, t: body)
    with pytest.raises(rel.ReleaseError, match="too large"):
        rel.fetch("https://kelo.chat/x", get=lambda url, t: b" " * 1_000_001)
    with pytest.raises(rel.ReleaseError, match="not readable"):
        rel.fetch("https://kelo.chat/x", get=lambda url, t: b"<html>")


class FakeDocker:
    """docker pull / image inspect / cosign verify, with switches for each way an image can be wrong."""

    def __init__(self):
        self.calls = []
        self.repo_digests_ok = self.signed = True
        self.signed_digest = None
        self.version = "0.5.0"

    def __call__(self, cmd, capture_output=False, text=False, **kw):
        self.calls.append(cmd)
        ok = lambda out="", rc=0: subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="" if rc == 0 else "no")  # noqa: E731
        ref = cmd[-1]
        if cmd[:2] == ["docker", "pull"]:
            return ok()
        if "{{json .RepoDigests}}" in cmd:
            return ok(json.dumps([ref] if self.repo_digests_ok else ["ghcr.io/kelo-chat/server@sha256:" + "f" * 64]))
        if "{{json .Config.Labels}}" in cmd:
            return ok(json.dumps({rel.VERSION_LABEL: self.version, rel.RELEASE_LABEL: self.version}))
        if cmd[0] == "cosign":
            if not self.signed:
                return ok(rc=1)
            digest = self.signed_digest or ref.split("@")[1]
            return ok(json.dumps([{"critical": {"image": {"docker-manifest-digest": digest}}}]))
        raise AssertionError(cmd)


def test_an_image_is_pulled_by_digest_and_checked_three_ways():
    d = FakeDocker()
    img = rel.parse(manifest()).images["server"]
    steps = []
    rel.Verifier(d).fetch_and_check(img, "0.5.0", steps.append)
    assert steps == ["download", "verify"]
    assert d.calls[0] == ["docker", "pull", "--quiet", img.ref]
    cosign = next(c for c in d.calls if c[0] == "cosign")
    assert cosign[cosign.index("--certificate-identity-regexp") + 1] == rel.CERT_IDENTITY
    assert cosign[cosign.index("--certificate-oidc-issuer") + 1] == "https://token.actions.githubusercontent.com"
    with_key = FakeDocker()
    rel.Verifier(with_key, key="/keys/cosign.pub").fetch_and_check(img, "0.5.0")
    assert "--key" in next(c for c in with_key.calls if c[0] == "cosign")


@pytest.mark.parametrize("flip, why", [("repo_digests_ok", "digest the release names"), ("signed", "official Kelo release")])
def test_a_wrong_digest_or_a_missing_signature_is_refused(flip, why):
    d = FakeDocker()
    setattr(d, flip, False)
    with pytest.raises(rel.ReleaseError, match=why):
        rel.Verifier(d).fetch_and_check(rel.parse(manifest()).images["server"], "0.5.0")


def test_a_signature_for_another_digest_or_an_older_image_relabelled_is_refused():
    d = FakeDocker()
    d.signed_digest = "sha256:" + "e" * 64
    with pytest.raises(rel.ReleaseError, match="not for this digest"):
        rel.Verifier(d).fetch_and_check(rel.parse(manifest()).images["server"], "0.5.0")
    d = FakeDocker()
    d.version = "0.4.0"                     # a validly signed older release named as 0.5.0 in the list
    with pytest.raises(rel.ReleaseError, match="another version"):
        rel.Verifier(d).fetch_and_check(rel.parse(manifest()).images["server"], "0.5.0")


def test_compose_level_and_the_shipped_compose_file():
    assert rel.compose_level("services: {}\n") == 1 and rel.compose_level("x-kelo-compose-level: 3\nservices: {}\n") == 3
    from pathlib import Path
    assert rel.compose_level((Path(__file__).resolve().parents[2] / "compose.yml").read_text()) >= 1


def test_latest_json_carries_the_server_release_only_when_it_passes(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "server": manifest()}))
    assert Downloads(tmp_path).latest()["server"]["version"] == "0.5.0"
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "server": manifest(version="nope")}))
    assert "server" not in Downloads(tmp_path).latest()
    assert server_entry(None) is None


def test_the_release_manifest_tool_writes_a_checked_file(tmp_path):
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    dig = tmp_path / "digests"
    dig.mkdir()
    for c in ("server", "workroom", "updater"):
        (dig / c).write_text(D[c] + "\n")
    (dig / "cli-claude").write_text(D["server"] + "\n")                       # a stray file: proprietary sidecars are never published
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "en.md").write_text("New things\n")
    (notes / "ko.md").write_text("새로운 점\n")
    out = tmp_path / "release.json"
    r = subprocess.run([sys.executable, str(root / "tools" / "release_manifest.py"), "--version", "v0.6.0", "--digests-dir", str(dig),
                        "--notes-dir", str(notes), "--min-app", "desktop=1.0.3", "--out", str(out)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = rel.parse(json.loads(out.read_text()))
    assert got.version == "0.6.0" and set(got.images) == {"server", "workroom", "updater"}
    assert got.notes == {"en": "New things", "ko": "새로운 점"} and got.min_app == {"desktop": "1.0.3"}
    (dig / "updater").unlink()
    r = subprocess.run([sys.executable, str(root / "tools" / "release_manifest.py"), "--version", "0.6.0", "--digests-dir", str(dig),
                        "--out", str(out)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "missing images: updater" in r.stderr


# ------------------------------------------------------------------------------------------------------------- one exact version
class TagDocker(FakeDocker):
    """A registry that has the tags of the versions in `published`: a tag pull works, and the tag's digest comes from RepoDigests."""

    def __init__(self, published=("0.4.0",)):
        super().__init__()
        self.published = set(published)

    def __call__(self, cmd, capture_output=False, text=False, **kw):
        ref = cmd[-1]
        if cmd[:2] == ["docker", "pull"] and "@" not in ref:
            self.calls.append(cmd)
            ok = ref.rsplit(":", 1)[1] in self.published
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, stdout="", stderr="" if ok else "manifest unknown")
        if "{{json .RepoDigests}}" in cmd and "@" not in ref:
            self.calls.append(cmd)
            repo = ref.rsplit(":", 1)[0]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([f"{repo}@sha256:" + "a" * 64]), stderr="")
        return super().__call__(cmd, capture_output, text, **kw)


def test_one_exact_version_is_found_by_its_tags_and_still_checked_by_digest():
    d = TagDocker()
    r = rel.by_version("v0.4.0", "ghcr.io/kelo-chat", d)
    assert r.version == "0.4.0" and set(r.images) == set(rel.REQUIRED)
    assert r.images["server"].ref == "ghcr.io/kelo-chat/server@sha256:" + "a" * 64
    assert ["docker", "pull", "--quiet", "ghcr.io/kelo-chat/workroom:0.4.0"] in d.calls
    d.version = "0.4.0"
    rel.Verifier(d).fetch_and_check(r.images["server"], r.version)          # the same three checks as for the newest release
    d.version = "0.5.0"                                                      # a tag that points at another release's image
    with pytest.raises(rel.ReleaseError, match="another version"):
        rel.Verifier(d).fetch_and_check(r.images["server"], r.version)


def test_a_version_with_no_images_or_no_version_form_is_refused_in_one_line():
    with pytest.raises(rel.ReleaseError, match=r"release 9\.9\.9 has no published image server"):
        rel.by_version("9.9.9", "ghcr.io/kelo-chat", TagDocker())
    for bad in ("", "latest", "1.2", "1.2.3; rm -rf /", "../0.4.0"):
        d = TagDocker()
        with pytest.raises(rel.ReleaseError, match="not a release version"):
            rel.by_version(bad, "ghcr.io/kelo-chat", d)
        assert d.calls == []                                                 # nothing was pulled for it


def test_parse_reads_notes_i18n_before_notes():
    got = rel.parse({**manifest(), "notes": "Plain", "notes_i18n": {"en": "E", "ko": "K"}})
    assert got.notes == {"en": "E", "ko": "K"}
    assert rel.parse({**manifest(), "notes": "Plain"}).notes == {"en": "Plain"}
