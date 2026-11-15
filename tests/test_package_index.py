"""Package sources, content-bound approval and the catalog index (packages.py, package_index.py). Nothing reaches the network: the
fetcher is a fake that serves archives built here."""

import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from assistant.package_index import git_archive_url, install_entry, load_index, parse_entry, parse_index, unpack
from assistant.packages import MANIFEST, SOURCES_FILE, PackageError, Registry, content_hash, file_hashes

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


def manifest(pid="acme_notes", version="1.0.0", connect=None, **extra):
    return {"id": pid, "kind": "connector", "version": version, "name": "Acme Notes", "description": "Notes in Acme", "author": "Someone",
            "connector": {"connect": connect or {"type": "key", "hosts": ["api.acme-notes.example.com"], "placement": "bearer_header"}},
            **extra}


def files_of(m=None, extra=None):
    out = {MANIFEST: json.dumps(m or manifest()).encode()}
    out.update(extra or {})
    return out


def tar_gz(files: dict, *, top="repo-" + COMMIT[:7], commit=COMMIT, modes=None, links=()):
    buf = io.BytesIO()
    pax = {"comment": commit} if commit else {}
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=pax) as tf:
        for name, body in files.items():
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size, info.mode = len(body), (modes or {}).get(name, 0o644)
            tf.addfile(info, io.BytesIO(body))
        for name, target in links:
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    return buf.getvalue()


def zip_bytes(files: dict, *, modes=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | (modes or {}).get(name, 0o644)) << 16
            zf.writestr(info, body)
    return buf.getvalue()


class FakeFetcher:
    def __init__(self, bodies: dict):
        self.bodies, self.urls = bodies, []

    async def __call__(self, url, max_bytes):
        self.urls.append(url)
        if url not in self.bodies:
            raise PackageError("no such download")
        return self.bodies[url]


def git_entry(commit=COMMIT, version="1.0.0", url="https://github.com/someone/acme-notes", reviewed=True, **src):
    return parse_entry({"id": "acme_notes", "kind": "connector", "name": "Acme Notes", "description": "d", "author": "a",
                        "version": version, "reviewed": reviewed, "source": {"type": "git", "url": url, "commit": commit, **src}})


def write(folder: Path, files: dict) -> Path:
    for name, body in files.items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    return folder


def later(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


# ------------------------------------------------------------------------------------------------ content hash and source records
def test_content_hash_covers_every_path_and_byte(tmp_path):
    a = write(tmp_path / "a", files_of(extra={"notes.md": b"hello", "sub/more.md": b"x"}))
    h = content_hash(file_hashes(a))
    assert list(file_hashes(a)) == sorted(file_hashes(a)) and "sub/more.md" in file_hashes(a)
    (a / "sub" / "more.md").write_bytes(b"y")
    assert content_hash(file_hashes(a)) != h
    (a / "sub" / "more.md").write_bytes(b"x")
    assert content_hash(file_hashes(a)) == h
    (a / "sub" / "more.md").rename(a / "sub" / "other.md")             # same bytes under another name: another hash
    assert content_hash(file_hashes(a)) != h
    (a / ".hidden").write_bytes(b"")                                    # nothing is left out silently
    assert ".hidden" in file_hashes(a)


def test_installing_records_the_source_and_approval_unreviewed(tmp_path):
    src = write(tmp_path / "src", files_of())
    reg = Registry(installed=tmp_path / "packages")
    p = reg.install_from_dir(src)
    rec = reg.source_record("acme_notes")
    assert rec["source"] == {"type": "local"} and rec["reviewed"] is False and rec["version"] == "1.0.0" and rec["installed_at"]
    assert rec["approved_hash"] == p.content_hash == content_hash(file_hashes(src))
    assert p.public()["review"] == "unreviewed"
    assert reg.statuses() == [{"id": "acme_notes", "version": "1.0.0", "source": {"type": "local"}, "installed_at": rec["installed_at"],
                               "review": "unreviewed", "state": "approved"}]
    assert not (tmp_path / "packages" / "acme_notes" / SOURCES_FILE).exists()   # kept beside the packages, not inside one
    assert reg.remove("acme_notes") and reg.source_record("acme_notes") is None


def test_changed_files_are_held_until_approved_again_with_what_changed(tmp_path):
    inst = tmp_path / "packages"
    reg = Registry(installed=inst)
    reg.install_from_dir(write(tmp_path / "src", files_of(extra={"notes.md": b"one"})))
    assert reg.key_preset("acme_notes") is not None
    # someone edits the installed copy: new hosts, another placement
    m = manifest(connect={"type": "key", "hosts": ["api.acme-notes.example.com", "collect.example.net"],
                          "placement": {"type": "query_param", "param": "key"}})
    (inst / "acme_notes" / MANIFEST).write_text(json.dumps(m))
    (inst / "acme_notes" / "notes.md").write_bytes(b"two")
    later(inst / "acme_notes" / MANIFEST)
    assert reg.installed_connector("acme_notes") is None and reg.key_preset("acme_notes") is None
    held = reg.pending["acme_notes"]
    assert held["sensitive"] is True
    by = {c.get("field") or c.get("path"): c for c in held["changes"]}
    assert by["hosts"]["before"] == ["api.acme-notes.example.com"] and "collect.example.net" in by["hosts"]["after"]
    assert by["placement"]["sensitive"] and by["notes.md"]["change"] == "changed" and by[MANIFEST]["sensitive"]
    assert any("approved again" in x for x in reg.problems)
    assert reg.statuses()[0]["state"] == "changed"
    with pytest.raises(PackageError, match="changed again"):
        reg.approve("acme_notes", expected_hash="0" * 64)
    p = reg.approve("acme_notes", expected_hash=held["content_hash"])
    assert p.connect["hosts"] == ("api.acme-notes.example.com", "collect.example.net") and not reg.pending


def test_an_mcp_address_change_is_shown(tmp_path):
    inst = tmp_path / "packages"
    reg = Registry(installed=inst)
    reg.install_from_dir(write(tmp_path / "src", files_of(manifest(connect={"type": "mcp", "url": "https://mcp.acme.example.com/mcp"}))))
    (inst / "acme_notes" / MANIFEST).write_text(json.dumps(manifest(connect={"type": "mcp", "url": "https://mcp.evil.example.net/mcp"})))
    later(inst / "acme_notes" / MANIFEST)
    reg.refresh()
    ch = {c["field"]: c for c in reg.pending["acme_notes"]["changes"] if c["what"] == "connect"}
    assert ch["url"]["after"] == "https://mcp.evil.example.net/mcp" and ch["hosts"]["after"] == ["mcp.evil.example.net"]


def test_a_folder_put_in_by_hand_is_adopted_as_unreviewed(tmp_path):
    inst = tmp_path / "packages"
    write(inst / "acme_notes", files_of())
    reg = Registry(installed=inst)
    assert reg.installed_connector("acme_notes") is not None and reg.source_record("acme_notes")["source"] == {"type": "local"}
    assert not reg.refresh()        # writing the record does not cause another reload


def test_updates_never_switch_source_silently(tmp_path):
    reg = Registry(installed=tmp_path / "packages")
    src = write(tmp_path / "src", files_of())
    reg.install_from_dir(src, source={"type": "git", "url": "https://github.com/someone/acme-notes", "commit": COMMIT})
    with pytest.raises(PackageError, match="Switching where a package comes from"):
        reg.install_from_dir(src, replace=True)
    with pytest.raises(PackageError, match="Switching"):
        reg.install_from_dir(src, replace=True, source={"type": "git", "url": "https://github.com/other/acme-notes", "commit": COMMIT})
    reg.install_from_dir(src, replace=True, source={"type": "git", "url": "https://github.com/someone/acme-notes", "commit": OTHER})
    assert reg.source_record("acme_notes")["source"]["commit"] == OTHER
    reg.install_from_dir(src, replace=True, switch_source=True)
    assert reg.source_record("acme_notes")["source"] == {"type": "local"}


# ------------------------------------------------------------------------------------------------ the index
def test_the_index_is_checked_strictly(tmp_path):
    good = {"format": 1, "name": "Kelo picks", "packages": [git_entry().public(),
            {"id": "picnic", "kind": "skill", "name": "Picnic", "version": "0.1.0", "reviewed": False,
             "source": {"type": "archive", "url": "https://example.org/picnic.zip", "sha256": "a" * 64}}]}
    got = parse_index(json.dumps(good))
    assert [e.id for e in got] == ["acme_notes", "picnic"] and got[0].reviewed and got[1].source["sha256"] == "a" * 64
    f = tmp_path / "index.json"
    f.write_text(json.dumps(good))
    assert len(load_index(f)) == 2

    def bad(entry_change=None, src_change=None, top=None):
        e = git_entry().public()
        e.update(entry_change or {})
        e["source"] = {**e["source"], **(src_change or {})}
        e["source"] = {k: v for k, v in e["source"].items() if v is not None}
        return {"format": 1, "packages": [e], **(top or {})}

    cases = [
        (bad(src_change={"commit": "v1.0.0"}), "40-character"),
        (bad(src_change={"commit": COMMIT[:12]}), "40-character"),
        (bad(src_change={"commit": COMMIT.upper()}), "40-character"),
        (bad(src_change={"tag": "v1"}), "tags and branches"),
        (bad(src_change={"branch": "main"}), "tags and branches"),
        (bad(src_change={"url": "http://github.com/a/b"}), "https"),
        (bad(src_change={"url": "https://git.internal.lan/a/b"}), "public host"),
        (bad(src_change={"url": "https://example.org/a/b"}), "github.com"),
        (bad(src_change={"path": "../up"}), "relative folder"),
        (bad(entry_change={"extra": 1}), "unknown fields"),
        (bad(entry_change={"reviewed": "yes"}), "true or false"),
        (bad(entry_change={"id": "Bad Id"}), "not a valid"),
        (bad(entry_change={"version": "latest"}), "1.2.3"),
        (bad(top={"mirror": "x"}), "unknown fields"),
        ({"format": 2, "packages": []}, "format"),
        ({"format": 1, "packages": [{"id": "picnic", "kind": "skill", "name": "P", "version": "1.0.0",
                                     "source": {"type": "archive", "url": "https://example.org/p.zip", "sha256": "xyz"}}]}, "sha256"),
        ({"format": 1, "packages": [git_entry().public(), git_entry().public()]}, "listed twice"),
    ]
    for doc, why in cases:
        with pytest.raises(PackageError, match=why):
            parse_index(doc)


def test_git_sources_become_commit_archives_of_known_forges():
    assert git_archive_url("https://github.com/a/b.git", COMMIT) == f"https://codeload.github.com/a/b/tar.gz/{COMMIT}"
    assert git_archive_url("https://codeberg.org/a/b", COMMIT) == f"https://codeberg.org/a/b/archive/{COMMIT}.tar.gz"
    assert git_archive_url("https://gitlab.com/g/s/b", COMMIT) == f"https://gitlab.com/g/s/b/-/archive/{COMMIT}/b-{COMMIT}.tar.gz"


# ------------------------------------------------------------------------------------------------ installing from the index
async def test_install_from_index_git_verifies_the_commit(tmp_path):
    reg = Registry(installed=tmp_path / "packages")
    url = git_archive_url("https://github.com/someone/acme-notes", COMMIT)
    fetch = FakeFetcher({url: tar_gz({"README.md": b"repo", **{f"package/{k}": v for k, v in files_of().items()}})})
    p = await reg.install_from_index(git_entry(path="package"), fetcher=fetch)
    assert fetch.urls == [url] and p.reviewed and p.public()["review"] == "reviewed"
    rec = reg.source_record("acme_notes")
    assert rec["source"] == {"type": "git", "url": "https://github.com/someone/acme-notes", "commit": COMMIT, "path": "package",
                             "version": "1.0.0"}
    assert not (tmp_path / "packages" / "acme_notes" / "README.md").exists()    # only the package folder is kept
    # an archive that says it is another commit (or says nothing) is refused
    fetch.bodies[url] = tar_gz(files_of(), commit=OTHER)
    with pytest.raises(PackageError, match="not of the catalog's commit"):
        await install_entry(reg, git_entry(), fetcher=fetch, replace=True)
    fetch.bodies[url] = tar_gz(files_of(), commit="")
    with pytest.raises(PackageError, match="not of the catalog's commit"):
        await install_entry(reg, git_entry(), fetcher=fetch, replace=True)


async def test_install_from_index_archive_checks_sha256_id_and_version(tmp_path):
    reg = Registry(installed=tmp_path / "packages")
    body = zip_bytes(files_of())
    url = "https://example.org/acme-notes-1.0.0.zip"

    def entry(digest, version="1.0.0", pid="acme_notes"):
        return parse_entry({"id": pid, "kind": "connector", "name": "Acme Notes", "version": version,
                            "source": {"type": "archive", "url": url, "sha256": digest}})

    fetch = FakeFetcher({url: body})
    with pytest.raises(PackageError, match="sha256"):
        await reg.install_from_index(entry("b" * 64), fetcher=fetch)
    digest = hashlib.sha256(body).hexdigest()
    with pytest.raises(PackageError, match="catalog lists 2.0.0"):
        await reg.install_from_index(entry(digest, "2.0.0"), fetcher=fetch)
    with pytest.raises(PackageError, match="catalog lists"):
        await reg.install_from_index(entry(digest, pid="other_notes"), fetcher=fetch)
    p = await reg.install_from_index(entry(digest), fetcher=fetch)
    assert not p.reviewed and reg.source_record("acme_notes")["source"]["sha256"] == digest
    assert reg.installed_connector("acme_notes") is not None


@pytest.mark.parametrize("make,why", [
    (lambda: tar_gz(files_of(), top="", commit=""), None),
    (lambda: tar_gz({"../evil.md": b"x", **files_of()}, top="", commit=""), "unsafe path"),
    (lambda: tar_gz({"/etc/evil.md": b"x", **files_of()}, top="", commit=""), "unsafe path"),
    (lambda: tar_gz(files_of(), top="", commit="", links=[("link.md", "/etc/passwd")]), "link"),
    (lambda: tar_gz(files_of(extra={"run.md": b"text"}), top="", commit="", modes={"run.md": 0o755}), "executable"),
    (lambda: tar_gz(files_of(extra={"tool.sh": b"echo"}), top="", commit=""), "program"),
    (lambda: tar_gz(files_of(extra={"notes.md": b"\x7fELF...."}), top="", commit=""), "program"),
    (lambda: tar_gz(files_of(extra={"notes.md": b"#!/bin/sh\n"}), top="", commit=""), "program"),
    (lambda: zip_bytes({"../../evil.md": b"x", **files_of()}), "unsafe path"),
    (lambda: zip_bytes({"C:/evil.md": b"x", **files_of()}), "unsafe path"),
    (lambda: zip_bytes(files_of(extra={"x.md": b"y"}), modes={"x.md": 0o755}), "executable"),
    (lambda: zip_bytes({**files_of(), **{f"f{i}.md": b"x" for i in range(70)}}), "more than"),
    (lambda: zip_bytes({**files_of(), "big.md": b"x" * (3 * 1024 * 1024)}), "larger than"),
    (lambda: b"not an archive at all", "not a zip or tar"),
])
def test_unpacking_refuses_slips_links_programs_and_bombs(tmp_path, make, why):
    dest = tmp_path / "out"
    dest.mkdir()
    if why is None:
        root, _ = unpack(make(), dest, strip_top=None)
        assert (root / MANIFEST).is_file()
        return
    with pytest.raises(PackageError, match=why):
        unpack(make(), dest, strip_top=None)
    assert not (tmp_path / "evil.md").exists() and not (tmp_path.parent / "evil.md").exists()


def test_a_symlink_in_a_zip_is_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link.md")
        info.external_attr = (0o120777) << 16
        zf.writestr(info, "/etc/passwd")
        zf.writestr(MANIFEST, json.dumps(manifest()))
    (tmp_path / "o").mkdir()
    with pytest.raises(PackageError, match="link"):
        unpack(buf.getvalue(), tmp_path / "o", strip_top=None)
