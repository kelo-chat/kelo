"""A linked package is untrusted input (packages.py load_package strict, scan_secrets; nest_drafts.py and nest.py link installs).
Strict checks for links and updates: an unknown top-level manifest field and any key-like text refuse the package. Local folders keep
today's behaviour (warnings). Nothing here reaches the network (test_nest.World serves GitHub and the archives)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from assistant import packages as pk
from assistant.nest import NestError
from assistant.packages import MANIFEST, PackageError, Registry, load_package, scan_secrets

from test_nest import EXAMPLES, World, ext, simple_ui, write_manifest

# built at run time so that no literal in this file looks like a key to a secret scanner
PEM_HEAD = "-----BEGIN " + "PRIVATE KEY-----"
PEM_TAIL = "-----END " + "PRIVATE KEY-----"
ANTHROPIC_FAKE = "sk-ant-" + "x" * 30
JWT_FAKE = "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12


def skill(folder: Path, files: dict[str, str] | None = None, **extra) -> Path:
    m = {"format": 1, "id": folder.name, "kind": "skill", "version": "1.0.0", "name": "Demo", "description": "A demo skill", **extra}
    write_manifest(folder, m, {"SKILL.md": "---\nname: Demo\ndescription: A demo skill\n---\n\nDo the demo.\n", **(files or {})})
    return folder


# ---------------------------------------------------------------------------------------------------------------- A: strict fields
def test_strict_refuses_an_unknown_top_level_field_and_local_mode_keeps_it(tmp_path):
    folder = skill(tmp_path / "demo-skill", zzz_future=True)
    with pytest.raises(PackageError) as e:
        load_package(folder, strict=True)
    assert "zzz_future" in str(e.value)
    warnings: list[str] = []
    assert load_package(folder, warnings=warnings).id == "demo-skill"
    assert any("zzz_future" in w for w in warnings)


@pytest.mark.parametrize("where", ["strings", "ui", "store", "connect", "connector"])
def test_strict_refuses_unknown_fields_inside_each_section(tmp_path, where):
    """Unknown fields inside these sections were already refused in every mode; strict must refuse them too."""
    if where == "connector":
        m = {"format": 1, "id": "my_conn", "kind": "connector", "version": "1.0.0", "name": "My", "description": "d",
             "connector": {"connect": {"type": "mcp", "url": "https://mcp.example.com/mcp", "blurb": "b"}, "bogus": 1}}
    elif where == "connect":
        m = {"format": 1, "id": "my_conn", "kind": "connector", "version": "1.0.0", "name": "My", "description": "d",
             "connector": {"connect": {"type": "mcp", "url": "https://mcp.example.com/mcp", "blurb": "b", "bogus": 1}}}
    else:
        m = ext(simple_ui())
        if where == "strings":
            m["strings"] = {"en": {"name": "Mine", "bogus": "x"}}
        elif where == "ui":
            m["ui"]["bogus"] = 1
        else:
            m["store"]["bogus"] = 1
    folder = write_manifest(tmp_path / m["id"], m)
    with pytest.raises(PackageError):
        load_package(folder, strict=True)


def test_a_clean_package_still_loads_in_strict_mode():
    got = {p.name: load_package(p, strict=True) for p in EXAMPLES.iterdir() if p.is_dir()}
    assert len(got) >= 9 and got["example_notes"].connect["type"] == "mcp"


# ---------------------------------------------------------------------------------------------------------------- A: key scan
def test_scan_finds_key_shapes_by_file_and_line_and_never_the_value(tmp_path):
    folder = skill(tmp_path / "demo-skill", {"notes.md": f"# Notes\n\nFirst line\nuse {ANTHROPIC_FAKE} here\n",
                                             "SKILL.md": f"---\nname: D\ndescription: D\n---\n{PEM_HEAD}\nMIIB\n{PEM_TAIL}\n"})
    hits = scan_secrets(folder)
    assert {"file": "notes.md", "line": 4, "kind": "api_key"} in hits
    assert {"file": "SKILL.md", "line": 5, "kind": "private_key"} in hits
    assert all(set(h) == {"file", "line", "kind"} for h in hits)
    assert "x" * 30 not in json.dumps(hits)


def test_strict_refuses_a_key_and_the_error_does_not_show_it(tmp_path, caplog):
    folder = skill(tmp_path / "demo-skill", {"notes.md": f"use {ANTHROPIC_FAKE}\n"})
    caplog.set_level(logging.DEBUG)
    with pytest.raises(PackageError) as e:
        load_package(folder, strict=True)
    msg = str(e.value)
    assert "notes.md" in msg and "line 1" in msg and "API key" in msg
    assert "kelo" in msg.lower() and ANTHROPIC_FAKE not in msg and ANTHROPIC_FAKE not in caplog.text


def test_local_mode_loads_a_key_with_a_warning(tmp_path):
    folder = skill(tmp_path / "demo-skill", {"notes.md": f"use {ANTHROPIC_FAKE}\n"})
    warnings: list[str] = []
    assert load_package(folder, warnings=warnings).id == "demo-skill"
    assert any("notes.md line 1" in w for w in warnings)
    assert not any(ANTHROPIC_FAKE in w for w in warnings)


@pytest.mark.parametrize("text, kind", [
    (f"Authorization header: Bearer {'a' * 24}", "token"),
    (f"token {JWT_FAKE}", "token"),
    (f"{PEM_HEAD}\nAAAA\n{PEM_TAIL}", "private_key"),
])
def test_other_key_shapes_are_refused(tmp_path, text, kind):
    folder = skill(tmp_path / "demo-skill", {"notes.md": text + "\n"})
    assert [h["kind"] for h in scan_secrets(folder)] == [kind]
    with pytest.raises(PackageError):
        load_package(folder, strict=True)


def test_ordinary_text_about_passwords_and_codes_is_not_a_key(tmp_path):
    folder = skill(tmp_path / "demo-skill", {"notes.md": "Ask the user for their password. The order code is 123456.\n"
                                                         "Store the password in the vault, never in a file.\n"})
    assert scan_secrets(folder) == []
    assert load_package(folder, strict=True).id == "demo-skill"


def test_binary_files_are_skipped_by_the_scan(tmp_path):
    folder = skill(tmp_path / "demo-skill")
    (folder / "pic.bin.md").write_bytes(b"\xff\xfe\x00 binary" + bytes(range(128, 255)))
    assert scan_secrets(folder) == []


def test_a_clean_package_installs_from_a_link(tmp_path):
    world = World(tmp_path)
    folder = skill(tmp_path / "clean-skill")
    world.push(folder)
    import asyncio
    card = asyncio.run(world.nest.install_from_link(World.link("clean-skill")))
    assert card["id"] == "clean-skill" and card["permissions"]["hash"]


def test_a_link_with_a_key_is_refused_before_anything_is_installed(tmp_path):
    import asyncio
    world = World(tmp_path)
    folder = skill(tmp_path / "keyed-skill", {"notes.md": f"use {ANTHROPIC_FAKE}\n"})
    world.push(folder)
    with pytest.raises(NestError) as e:
        asyncio.run(world.nest.install_from_link(World.link("keyed-skill")))
    assert e.value.status == 422 and "notes.md" in str(e.value) and ANTHROPIC_FAKE not in str(e.value)
    assert world.registry.get("keyed-skill") is None


def test_a_link_with_an_unknown_top_field_is_refused(tmp_path):
    import asyncio
    world = World(tmp_path)
    folder = skill(tmp_path / "future-skill", future_field=1)
    world.push(folder)
    with pytest.raises(NestError) as e:
        asyncio.run(world.nest.install_from_link(World.link("future-skill")))
    assert e.value.status == 422 and "future_field" in str(e.value)


def test_the_refusal_texts_are_translated():
    from assistant import i18n
    from assistant.packages import SECRET_FOUND, UNKNOWN_FIELDS
    ko = i18n.t(SECRET_FOUND, lang="ko", kind="API 키", file="notes.md", line=4)
    assert ko.startswith("이 패키지에는") and "notes.md 4번째 줄" in ko and "{" not in ko
    assert "Kelo" in i18n.t(UNKNOWN_FIELDS, lang="en", fields="zzz") and "zzz" in i18n.t(UNKNOWN_FIELDS, lang="ko", fields="zzz")


# ---------------------------------------------------------------------------------------------------------------- B: all or nothing
def _installed_names(world: World) -> set[str]:
    d = world.registry.installed_dir
    return {p.name for p in d.iterdir()} if d and d.exists() else set()


def _fail_first_write(monkeypatch, n_fail: int = 1) -> dict[str, int]:
    """Make the first `n_fail` writes of the records file raise (the rollback's own writes then succeed)."""
    calls = {"n": 0}
    orig = Registry._write_records

    def flaky(self, records, root=None):
        calls["n"] += 1
        if calls["n"] <= n_fail:
            raise OSError("disk full (test)")
        return orig(self, records, root)

    monkeypatch.setattr(Registry, "_write_records", flaky)
    return calls


def test_a_failed_fresh_install_leaves_no_folder_and_no_record(tmp_path, monkeypatch):
    world = World(tmp_path)
    folder = skill(tmp_path / "fresh-skill")
    world.push(folder)
    card = asyncio.run(world.nest.install_from_link(World.link("fresh-skill")))
    _fail_first_write(monkeypatch)
    with pytest.raises(OSError):
        asyncio.run(world.nest.install_link(card["token"], card["permissions"]["hash"]))
    assert "fresh-skill" not in _installed_names(world)
    assert world.registry.get("fresh-skill") is None
    assert "fresh-skill" not in world.registry._read_records()


def test_a_fresh_install_whose_state_write_fails_is_removed_again(tmp_path, monkeypatch):
    world = World(tmp_path)
    folder = skill(tmp_path / "state-skill")
    world.push(folder)
    card = asyncio.run(world.nest.install_from_link(World.link("state-skill")))

    def boom(self, fn):
        raise OSError("state file locked (test)")

    monkeypatch.setattr(world.nest, "_change", boom.__get__(world.nest))
    with pytest.raises(OSError):
        asyncio.run(world.nest.install_link(card["token"], card["permissions"]["hash"]))
    assert world.registry.get("state-skill") is None
    assert "state-skill" not in world.registry._read_records()


def test_a_failed_update_keeps_the_old_version_and_its_record(tmp_path, monkeypatch):
    world = World(tmp_path)
    folder = skill(tmp_path / "ver-skill")
    old = asyncio.run(installed_pkg(world, folder))
    old_hashes = dict(world.registry._read_records()["ver-skill"]["files"])
    (folder / "SKILL.md").write_text("---\nname: Demo\ndescription: A demo skill\n---\n\nDo the demo, better.\n")
    world.push(folder)
    _fail_first_write(monkeypatch)
    with pytest.raises(OSError):
        asyncio.run(world.nest.update("ver-skill"))
    got = world.registry.get("ver-skill")
    assert got is not None and got.version == old.version and got.text("description") == "A demo skill"
    assert dict(world.registry._read_records()["ver-skill"]["files"]) == old_hashes
    assert not [n for n in _installed_names(world) if "-old-" in n or n.endswith(".tmp")]


def test_a_failed_update_state_write_puts_the_previous_version_back(tmp_path, monkeypatch):
    world = World(tmp_path)
    folder = skill(tmp_path / "back-skill")
    asyncio.run(installed_pkg(world, folder))
    (folder / "SKILL.md").write_text("---\nname: Demo\ndescription: A demo skill\n---\n\nNew steps.\n")
    world.push(folder)

    def boom(self, fn):
        raise OSError("state file locked (test)")

    monkeypatch.setattr(world.nest, "_change", boom.__get__(world.nest))
    with pytest.raises(OSError):
        asyncio.run(world.nest.update("back-skill"))
    assert "Do the demo." in world.registry.skill_text("back-skill")
    assert "New steps." not in world.registry.skill_text("back-skill")


def test_a_leftover_from_a_stopped_install_comes_back_when_its_folder_is_missing(tmp_path):
    installed = tmp_path / "packages"
    (installed / ".lost-skill-old-0a1b2c3d").mkdir(parents=True)
    (installed / ".lost-skill-old-0a1b2c3d" / "SKILL.md").write_text("---\nname: Lost\ndescription: Back\n---\n")
    (installed / ".kept-skill-old-0a1b2c3d").mkdir()
    (installed / "kept-skill").mkdir()
    (installed / "kept-skill" / "SKILL.md").write_text("---\nname: Kept\ndescription: New\n---\n")
    Registry(installed=installed)
    assert (installed / "lost-skill" / "SKILL.md").exists()
    assert not (installed / ".lost-skill-old-0a1b2c3d").exists()
    assert not (installed / ".kept-skill-old-0a1b2c3d").exists()
    assert (installed / "kept-skill" / "SKILL.md").exists()


def test_a_new_commit_with_the_same_permissions_still_updates(tmp_path):
    world = World(tmp_path)
    folder = skill(tmp_path / "plain-skill")
    asyncio.run(installed_pkg(world, folder))
    (folder / "SKILL.md").write_text("---\nname: Demo\ndescription: A demo skill\n---\n\nVersion two.\n")
    world.push(folder)
    row = asyncio.run(world.nest.update("plain-skill"))
    assert "Version two." in world.registry.skill_text("plain-skill")
    assert row["id"] == "plain-skill"
    assert not [n for n in _installed_names(world) if "-old-" in n or n.endswith(".tmp")]


async def installed_pkg(world: World, folder: Path):
    """Install a skill from its link (the owner's preview, then the install)."""
    world.push(folder)
    card = await world.nest.install_from_link(World.link(folder.name))
    await world.nest.install_link(card["token"], card["permissions"]["hash"])
    return world.registry.get(folder.name)


# ---------------------------------------------------------------------------------------------------------------- C: exactly what it installs
def test_the_preview_shows_every_file_its_text_and_where_it_can_reach(tmp_path):
    world = World(tmp_path)
    folder = skill(tmp_path / "shown-skill", {"notes.md": "# Notes\nSee https://docs.example.com.\n"})
    world.push(folder)
    card = asyncio.run(world.nest.install_from_link(World.link("shown-skill")))
    c = card["contents"]
    names = [f["path"] for f in c["files"]]
    assert names == sorted(names) and {"SKILL.md", "notes.md", MANIFEST} <= set(names)
    by = {f["path"]: f for f in c["files"]}
    assert by["notes.md"]["bytes"] == len("# Notes\nSee https://docs.example.com.\n".encode())
    assert len(by["notes.md"]["sha256"]) == 64 and c["total_bytes"] == sum(f["bytes"] for f in c["files"])
    assert c["texts"]["notes.md"].startswith("# Notes") and "Do the demo." in c["texts"]["SKILL.md"]
    assert MANIFEST in c["texts"] and c["cut"] == [] and c["warnings"] == []
    assert c["endpoints"] == []


def test_a_long_text_is_cut_and_marked(tmp_path):
    folder = skill(tmp_path / "long-skill", {"long.md": "a" * (pk.CONTENTS_TEXT + 500)})
    c = pk.contents(folder, load_package(folder))
    assert c["cut"] == ["long.md"] and c["texts"]["long.md"].endswith("…")
    assert len(c["texts"]["long.md"].encode()) <= pk.CONTENTS_TEXT + 4
    assert "SKILL.md" not in c["cut"]


def test_endpoints_list_connector_addresses_permission_hosts_and_links(tmp_path):
    notes = load_package(EXAMPLES / "example_notes")
    assert any("notes.example.com" in e for e in pk.endpoints(notes))
    assert pk.endpoints(notes) == sorted(pk.endpoints(notes))
    ui = simple_ui()
    ui["actions"]["help"] = {"type": "open_url", "label": "Help", "url": "https://www.example.com/help"}
    m = ext(ui, permissions={"hosts": ["api.example.com", "www.example.com"], "ui": ["home_card"], "actions": ["open_url", "store"], "store": True})
    folder = write_manifest(tmp_path / "my-ext", m)
    eps = pk.endpoints(load_package(folder))
    assert "https://www.example.com/help" in eps and "api.example.com" in eps and "www.example.com" in eps


def test_an_update_lists_what_changed_against_the_installed_files(tmp_path):
    world = World(tmp_path)
    folder = skill(tmp_path / "chg-skill", {"a.md": "one\n", "b.md": "two\n"})
    asyncio.run(installed_pkg(world, folder))
    (folder / "a.md").unlink()
    (folder / "b.md").write_text("two, changed\n")
    (folder / "c.md").write_text("three\n")
    world.push(folder)
    view = asyncio.run(world.nest.check_update("chg-skill", force=True))
    assert view["changed"] == {"added": ["c.md"], "removed": ["a.md"], "modified": ["b.md"]}
    assert view["contents"]["texts"]["c.md"] == "three\n" and view["contents"]["files"]
    row = world.nest.installed()[0]
    assert row["update"]["changed"]["modified"] == ["b.md"] and "contents" not in row["update"]


def test_placeholder_bearer_in_docs_is_not_a_key(tmp_path):
    from assistant.packages import scan_secrets
    (tmp_path / "SKILL.md").write_text("Send `Authorization: Bearer YOUR_ACCESS_TOKEN_HERE` with each call.\n", encoding="utf-8")
    assert scan_secrets(tmp_path) == []
    (tmp_path / "SKILL.md").write_text("Authorization: Bearer abcDEF1234567890ghijKLMN\n", encoding="utf-8")
    assert [h["kind"] for h in scan_secrets(tmp_path)] == ["token"]


def test_key_scan_finds_keys_in_non_utf8_files_and_strict_refuses(tmp_path):
    from assistant.packages import PackageError, load_package, scan_secrets
    bad_bytes = b"\xff\xfe\x80\nAuthorization: Bearer abcDEF1234567890ghijKLMN\n"
    (tmp_path / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")
    (tmp_path / "extra.bin").write_bytes(bad_bytes)
    hits = scan_secrets(tmp_path)
    assert len(hits) == 1 and hits[0]["file"] == "extra.bin" and hits[0]["kind"] == "token"

    with pytest.raises(PackageError, match="token in extra.bin"):
        load_package(tmp_path, strict=True)
