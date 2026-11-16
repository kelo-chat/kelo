"""Tests for Nest package drafts made by talking (packages.py, nest_drafts.py, home.py, nest.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from assistant.home import Home, Skills
from assistant.nest import Nest
from assistant.nest_drafts import (
    ALLOWED_EXACT,
    ALLOWED_PREFIXES,
    DraftError,
    DraftManager,
    MAX_DRAFT_FILE,
    MAX_DRAFT_TOTAL,
    check_draft_path,
    draft_tools,
)
from assistant.packages import Registry, load_package
from assistant.store import Store


def test_check_draft_path():
    # Allowed paths
    check_draft_path("kelo-package.json")
    check_draft_path("SKILL.md")
    check_draft_path("ui/main.json")
    check_draft_path("ui/screens/home.json")
    check_draft_path("assets/icon.png")

    # Forbidden paths: traversal, absolute, outside allowed set
    with pytest.raises(DraftError, match="traversal"):
        check_draft_path("../secret.txt")
    with pytest.raises(DraftError, match="traversal"):
        check_draft_path("ui/../../secret.txt")
    with pytest.raises(DraftError, match="Absolute path"):
        check_draft_path("/etc/passwd")
    with pytest.raises(DraftError, match="not allowed"):
        check_draft_path("malicious.sh")
    with pytest.raises(DraftError, match="not allowed"):
        check_draft_path("src/code.py")
    with pytest.raises(DraftError, match="not allowed"):
        check_draft_path("other.json")


def test_draft_create_write_check_delete(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    store = Store(str(tmp_path / "store.db"))
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    # 1. Create
    info = mgr.create("skill", "my-reader", "My Reading Skill", "Helps read books")
    assert info["id"] == "my-reader"
    assert info["version"] == "0.0.1"
    assert (drafts_dir / "my-reader" / "kelo-package.json").is_file()

    # Loaded in registry as draft
    reg.refresh()
    pkg = reg.draft("my-reader")
    assert pkg is not None
    assert pkg.source == "draft"
    assert pkg.version == "0.0.1"

    # 2. Write file and verify version bump
    w = mgr.write("my-reader", "SKILL.md", "---\nname: My Reading Skill\ndescription: Helps read books\n---\nRead books nicely.")
    assert w["version"] == "0.0.2"
    assert (drafts_dir / "my-reader" / "SKILL.md").read_text() == "---\nname: My Reading Skill\ndescription: Helps read books\n---\nRead books nicely."

    reg.refresh()
    pkg = reg.draft("my-reader")
    assert pkg.version == "0.0.2"

    # 3. Check
    chk = mgr.check("my-reader")
    assert chk["valid"] is True
    assert chk["version"] == "0.0.2"
    assert chk["id"] == "my-reader"

    # 4. Summary & Details
    summary = mgr.drafts_summary()
    assert len(summary) == 1
    assert summary[0]["id"] == "my-reader"
    assert summary[0]["version"] == "0.0.2"
    assert summary[0]["shared"] is None

    details = mgr.draft_details("my-reader")
    assert details["id"] == "my-reader"
    assert "SKILL.md" in details["files"]
    assert details["findings"] == []

    # 5. Delete
    deleted = mgr.delete("my-reader")
    assert deleted is True
    assert not (drafts_dir / "my-reader").exists()
    reg.refresh()
    assert reg.draft("my-reader") is None


def test_draft_size_limits(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    store = Store(str(tmp_path / "store.db"))
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    mgr.create("skill", "size-test", "Size Test", "Testing file limits")

    # Single file over 256 KB
    too_large_file = "A" * (MAX_DRAFT_FILE + 10)
    with pytest.raises(DraftError, match="exceeds 256 KB limit"):
        mgr.write("size-test", "SKILL.md", too_large_file)

    # Total draft over 2 MB across multiple files
    chunk = "B" * (250 * 1024)
    for i in range(8):
        mgr.write("size-test", f"ui/screen_{i}.json", chunk)

    with pytest.raises(DraftError, match="exceeds 2 MB total limit"):
        mgr.write("size-test", "ui/overflow.json", chunk)


def test_version_bump_and_live_event(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    store = Store(str(tmp_path / "store.db"))
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    mgr.create("skill", "event-test", "Event Test", "Testing events")

    events = []

    async def fake_emit(ev: dict):
        events.append(ev)
        return True

    tools = {t.name: t for t in draft_tools(mgr, emit_fn=fake_emit)}

    # write bumps version 0.0.1 -> 0.0.2 and emits nest_changed
    asyncio.run(tools["nest_draft_write"].handler({"id": "event-test", "path": "SKILL.md", "content": "# Hello"}))
    assert len(events) == 1
    assert events[0] == {"type": "nest_changed", "id": "event-test"}
    assert reg.draft("event-test").version == "0.0.2"

    # write again bumps 0.0.2 -> 0.0.3 and emits nest_changed
    asyncio.run(tools["nest_draft_write"].handler({"id": "event-test", "path": "SKILL.md", "content": "# Hello 2"}))
    assert len(events) == 2
    assert events[1] == {"type": "nest_changed", "id": "event-test"}
    assert reg.draft("event-test").version == "0.0.3"


def test_draft_skill_in_index(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    store = Store(str(tmp_path / "store.db"))
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    mgr.create("skill", "draft-skill", "Draft Skill", "Skill made in draft")
    mgr.write("draft-skill", "SKILL.md", "---\nname: Draft Skill\ndescription: Skill made in draft\n---\nRun instructions.")

    home = Home(home_dir)
    skills = Skills(registry=reg, home=home)

    idx = skills.index
    found = next((s for s in idx if s["id"] == "draft-skill"), None)
    assert found is not None
    assert found["source"] == "draft"

    text = skills.index_text()
    assert "Draft, made here:" in text
    assert "draft-skill" in text
    assert "Skill made in draft" in text


def test_custom_skill_to_draft(tmp_path: Path):
    custom_dir = tmp_path / "custom_skills"
    drafts_dir = tmp_path / "package-drafts"
    (custom_dir / "my-custom").mkdir(parents=True)
    (custom_dir / "my-custom" / "SKILL.md").write_text(
        "---\nname: Custom Reviewer\ndescription: Reviews articles\n---\nReview step 1.\nReview step 2.",
        encoding="utf-8",
    )

    store = Store(str(tmp_path / "store.db"))
    reg = Registry(custom=custom_dir, drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    pkg = mgr.copy_custom_skill("my-custom")
    assert pkg.id == "my-custom"
    assert pkg.source == "draft"
    assert pkg.version == "0.0.1"

    # Draft has copied SKILL.md and generated kelo-package.json
    draft_folder = drafts_dir / "my-custom"
    assert (draft_folder / "SKILL.md").is_file()
    assert (draft_folder / "kelo-package.json").is_file()
    manifest = json.loads((draft_folder / "kelo-package.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "my-custom"
    assert manifest["kind"] == "skill"

    # Custom skill itself still exists
    assert (custom_dir / "my-custom" / "SKILL.md").is_file()


def test_draft_extensions_in_nest(tmp_path: Path):
    drafts_dir = tmp_path / "package-drafts"
    store = Store(str(tmp_path / "store.db"))
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=store)

    ui_manifest = {
        "format": 1,
        "id": "reading-tab",
        "kind": "extension",
        "version": "0.0.1",
        "name": "Reading Tab",
        "description": "Tracks reading",
        "permissions": {
            "ui": ["tab"],
        },
        "ui": {
            "schema": 1,
            "surfaces": [
                {
                    "id": "main",
                    "place": "tab",
                    "title": "Reading",
                    "icon": "book",
                    "screen": "main",
                }
            ],
            "screens": {
                "main": {
                    "components": [
                        {"type": "text", "text": "Books to read"}
                    ]
                }
            },
        },
    }

    mgr.create("extension", "reading-tab", "Reading Tab", "Tracks reading")
    mgr.write("reading-tab", "kelo-package.json", json.dumps(ui_manifest))

    nest = Nest(reg, store, drafts_dir=drafts_dir)
    exts = nest.extensions()
    surface = next((s for s in exts["places"]["tab"] if s["package"] == "reading-tab"), None)
    assert surface is not None
    assert surface.get("draft") is True
