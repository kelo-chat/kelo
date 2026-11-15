import os

import pytest

from assistant.agent import Ours
from assistant.home import CORE, Home, HomeError, Skills
from assistant.tools.home_tools import home_tools


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path / "home")


def test_core_files_are_seeded_once_and_never_overwritten(tmp_path):
    h = Home(tmp_path / "h")
    assert all((h.root / n).is_file() for n in CORE)
    (h.root / "MEMORY.md").write_text("# MEMORY.md\n- real memory\n")
    Home(tmp_path / "h")  # restart
    assert "real memory" in (h.root / "MEMORY.md").read_text()


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "~/x.md", "a/../../b.md", "", "x\x00.md", "a\\b.md", ".history/x"])
def test_paths_outside_the_area_are_refused(home, bad):
    with pytest.raises(HomeError):
        home.read(bad)
    with pytest.raises(HomeError):
        home.write(bad, "x")


def test_symlinks_cannot_be_used_to_escape(home, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("TOP SECRET")
    os.symlink(secret, home.root / "notes_link.md")
    os.symlink(tmp_path, home.root / "memory")
    for p in ("notes_link.md", "memory/secret.md"):
        with pytest.raises(HomeError, match="symbolic"):
            home.read(p)
    assert "notes_link.md" not in [r["path"] for r in home.list()]


def test_write_is_limited_to_core_files_and_the_three_folders(home):
    assert home.write("MEMORY.md", "# MEMORY.md\n- likes tea\n") == "MEMORY.md"
    assert home.write("memory/people/kim.md", "# Kim") == "memory/people/kim.md"
    assert home.write("notes/todo.md", "x") == "notes/todo.md"
    for bad in ("evil.md", "memory.md", "memory", "skills/x.md", "MEMORY.exe", "notes/run.sh"):
        with pytest.raises(HomeError):
            home.write(bad, "x")
    with pytest.raises(HomeError, match="too large"):
        home.write("notes/big.md", "x" * 100_001)


def test_overwrites_keep_history_and_it_is_capped(home):
    for i in range(25):
        home.write("MEMORY.md", f"# v{i}")
    hist = list((home.root / ".history" / "MEMORY.md").iterdir())
    assert len(hist) == 20
    assert home.read("MEMORY.md") == "# v24"
    assert not any(".history" in r["path"] for r in home.tree())  # internal: not shown or readable
    with pytest.raises(HomeError):
        home.read(".history/MEMORY.md/x.bak")


def test_context_block_injects_core_files_the_people_roster_and_truncates(home):
    home.write("MEMORY.md", "# MEMORY.md\n- " + "a" * 20000)
    home.write("memory/people/kim.md", "---\nname: Kim\nrelation: friend\n---\n# Kim")
    home.write("memory/people/lee.md", "# Lee, no front matter")
    block = home.context_block(per_file=500)
    assert "### MEMORY.md" in block and "[truncated; read_file MEMORY.md" in block
    assert "- Kim (friend): memory/people/kim.md" in block and "- lee: memory/people/lee.md" in block
    for name in ("assistant.md", "user.md", "AGENTS.md"):     # still the bare templates: named once, not injected
        assert f"### {name}" not in block
    assert "Still empty (only the template's headings): assistant.md, user.md, AGENTS.md." in block
    home.write("user.md", "# user.md\n\n## Name and how to address them\nMina\n")
    assert "### user.md" in home.context_block() and "Mina" in home.context_block()
    home.write("MEMORY.md", "# MEMORY.md\n- " + "b" * 20000)
    assert len(home.context_block()) < 6_000 + 3_000     # the per-file cap bounds what one file adds


def test_skills_index_comes_from_front_matter_and_reads_by_name_only(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SKILL.md").write_text("---\nname: Documents\ndescription: Write docs.\nrequires: [browser]\n---\n# Documents\nbody")
    (tmp_path / "Bad Name").mkdir()
    (tmp_path / "Bad Name" / "SKILL.md").write_text("---\nname: x\n---\n")
    s = Skills(tmp_path)
    assert [e["id"] for e in s.index] == ["docs"] and s.index[0]["requires"] == ["browser"]
    assert "- docs: Write docs." in s.index_text()
    assert s.read("docs") == "# Documents\nbody"
    for bad in ("../index", "docs/../docs", "nope", "", "Bad Name"):
        with pytest.raises(HomeError):
            s.read(bad)
    assert Skills(tmp_path / "missing").index_text() == ""


def test_an_older_layout_is_moved_into_the_new_one_and_nothing_is_lost(tmp_path):
    root = tmp_path / "h"
    root.mkdir()
    (root / "SOUL.md").write_text("# SOUL.md\n\nWarm, short sentences.\n")
    (root / "IDENTITY.md").write_text("# IDENTITY.md\n\nName: Sage. Calm and curious.\n")
    (root / "USER.md").write_text("# USER.md\n\n민지, lives in Seoul.\n")
    (root / "memory" / "people").mkdir(parents=True)
    (root / "memory" / "people" / "INDEX.md").write_text("- kim.md: Kim Minsu, friend\n")
    (root / "memory" / "people" / "kim.md").write_text("# Kim Minsu\nLikes climbing.\n")
    (root / "memory" / "groups").mkdir()
    (root / "memory" / "groups" / "INDEX.md").write_text("- club.md: climbing club\n")
    (root / "memory" / "groups" / "club.md").write_text("# Climbing club\nTuesdays.\n")
    h = Home(root)
    names = {p.name for p in root.iterdir()}
    assert {"assistant.md", "user.md", "MEMORY.md", "AGENTS.md"} <= names and not {"SOUL.md", "IDENTITY.md", "USER.md"} & names
    a = h.read("assistant.md")
    assert "Name: Sage. Calm and curious." in a and "Warm, short sentences." in a and "## Boundaries" in a
    assert h.read("user.md").startswith("# user.md") and "민지, lives in Seoul." in h.read("user.md")
    kim = h.read("memory/people/kim.md")
    assert kim.startswith("---\nname: Kim Minsu\n") and "relation: friend" in kim and "Likes climbing." in kim
    assert not (root / "memory" / "people" / "INDEX.md").exists() and not (root / "memory" / "groups").exists()
    assert "Tuesdays." in h.read("notes/groups/club.md")
    backup = next((root / ".backup").iterdir())
    for old in ("SOUL.md", "IDENTITY.md", ".user-md-old", "memory/people/INDEX.md", "memory/groups/INDEX.md", "memory/groups/club.md"):
        assert (backup / old).is_file(), old
    assert "Warm, short sentences." in (backup / "SOUL.md").read_text() and "민지" in (backup / ".user-md-old").read_text()
    assert "USER.md -> user.md" in (backup / "MIGRATION.txt").read_text()
    assert Home(root).migrated == []                                     # a second start changes nothing
    assert not any(t["path"].startswith(".backup") for t in h.tree())    # backups are internal


def test_unfilled_old_templates_become_fresh_new_files(tmp_path):
    root = tmp_path / "h"
    root.mkdir()
    (root / "SOUL.md").write_text("# SOUL.md\n\nYour persona and tone. Nothing captured yet.\n")
    (root / "USER.md").write_text("# USER.md\n\nWho you are helping: name, what to call them, what they care about. Nothing captured yet.\n")
    h = Home(root)
    from assistant.home import TEMPLATES
    assert h.read("assistant.md") == TEMPLATES["assistant.md"] and h.read("user.md") == TEMPLATES["user.md"]


async def test_tools_report_errors_as_text_not_exceptions(home, tmp_path):
    (tmp_path / "s").mkdir()
    tools = {t.name: t for t in home_tools(home, Skills(tmp_path / "s"))}
    assert "read_skill" not in tools                     # no skills -> no tool
    assert (await tools["write_file"].handler({"path": "MEMORY.md", "content": "# M\n- x"})) == "saved MEMORY.md"
    assert "- x" in await tools["read_file"].handler({"path": "MEMORY.md"})
    assert (await tools["read_file"].handler({"path": "../x"})).startswith("error:")
    assert (await tools["write_file"].handler({"path": "evil.md", "content": "x"})).startswith("error:")
    assert "MEMORY.md" in await tools["list_files"].handler({})
    assert not any(t.requires_approval for t in tools.values())
    assert {n for n, t in tools.items() if t.untrusted} == {"read_file"}   # a file from outside is fenced; the agent's own are not (Ours)
    assert isinstance(await tools["read_file"].handler({"path": "MEMORY.md"}), Ours)


def test_an_absolute_path_from_another_tool_is_re_rooted_but_never_escapes(home, tmp_path):
    home.write("memory/people/kim.md", "# Kim")
    assert home.relativize("/tmp/hermes_agy_abc/memory/people/kim.md") == "memory/people/kim.md"
    assert home.relativize("/tmp/hermes_agy_abc/USER.md") == "USER.md"
    assert home.relativize("/etc/passwd") == "/etc/passwd"             # nothing of ours matches: left alone, then refused
    r = home.relativize("/tmp/x/../../MEMORY.md")
    assert r == "MEMORY.md" and ".." not in r and not r.startswith("/")   # only ever a plain path inside the area
    with pytest.raises(HomeError):
        home.read(home.relativize("/etc/passwd"))
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    assert home.relativize(str(outside)) == str(outside)               # a real file elsewhere is not "ours"
    with pytest.raises(HomeError):
        home.read(home.relativize(str(outside)))


async def test_tools_accept_agys_absolute_paths(home, tmp_path):
    tools = {t.name: t for t in home_tools(home, None)}
    home.write("MEMORY.md", "# MEMORY.md\n- likes tea")
    assert "likes tea" in await tools["read_file"].handler({"path": "/tmp/hermes_agy_zz/MEMORY.md"})
    assert (await tools["read_file"].handler({"path": "/etc/passwd"})).startswith("error:")


async def test_list_files_with_a_foreign_absolute_path_shows_our_directory_not_theirs(home):
    tools = {t.name: t for t in home_tools(home, None)}
    out = await tools["list_files"].handler({"path": "/tmp/hermes_agy_x"})
    assert "MEMORY.md" in out and "hermes_agy" not in out
    assert "MEMORY.md" in await tools["list_files"].handler({"path": "/etc"})       # shows ours, never /etc


async def test_parent_directory_listing_is_refused_by_the_files_area(home):
    tools = {t.name: t for t in home_tools(home, None)}
    assert (await tools["list_files"].handler({"path": "../.."})).startswith("error:")
    assert (await tools["read_file"].handler({"path": "../../etc/passwd"})).startswith("error:")


def test_extra_skill_files_are_readable_only_by_their_names(tmp_path):
    (tmp_path / "ads").mkdir()
    (tmp_path / "ads" / "SKILL.md").write_text("---\nname: Ads\ndescription: Plan ads.\n---\n# Ads\nmain")
    (tmp_path / "ads" / "analysis.md").write_text("# Analysis\ndetail")
    (tmp_path / "secret.md").write_text("NOT A SKILL FILE")
    s = Skills(tmp_path)
    main = s.read("ads")
    assert main.endswith("analysis.md]") and "read_skill(name, file)" in main and "main" in main
    assert s.read("ads", "analysis.md") == "# Analysis\ndetail"
    for bad in ("../secret.md", "../../secret.md", "SKILL.md.bak", "/etc/passwd", "other.md"):
        with pytest.raises(HomeError):
            s.read("ads", bad)


async def test_read_skill_tool_takes_an_optional_file(tmp_path):
    (tmp_path / "ads").mkdir()
    (tmp_path / "ads" / "SKILL.md").write_text("---\nname: Ads\n---\n# Ads")
    (tmp_path / "ads" / "more.md").write_text("# More")
    tool = {t.name: t for t in home_tools(Home(tmp_path / "h"), Skills(tmp_path))}["read_skill"]
    assert "required" in tool.parameters and tool.parameters["required"] == ["name"] and "file" in tool.parameters["properties"]
    assert (await tool.handler({"name": "ads", "file": "more.md"})).endswith("# More")
    assert (await tool.handler({"name": "ads", "file": "../x"})).startswith("error:")


def test_walk_is_bounded_and_never_leaks_dotfiles_symlinks_or_history(home, tmp_path):
    home.write("memory/people/kim.md", "# Kim")
    home.write("memory/club.md", "# Club")
    home.write("MEMORY.md", "# v1")
    home.write("MEMORY.md", "# v2")                              # creates .history
    os.symlink(tmp_path, home.root / "notes")                     # a link out of the area
    paths = [r["path"] for r in home.walk()]
    assert "memory/people/kim.md" in paths and "memory/club.md" in paths
    assert not any(p.startswith(".") or ".history" in p or p.startswith("notes") for p in paths)
    assert [r["path"] for r in home.walk(max_depth=1) if "/" in r["path"]] == []
    assert [r["path"] for r in home.walk("memory", name="*.md", kind="f")] == ["memory/club.md", "memory/people/kim.md"]
    assert len(home.walk(limit=3)) == 3
    with pytest.raises(HomeError):
        home.walk("../..")


async def test_list_files_tool_recurses_when_asked(home):
    home.write("memory/people/kim.md", "# Kim")
    tool = {t.name: t for t in home_tools(home, None)}["list_files"]
    flat, deep = await tool.handler({}), await tool.handler({"recursive": True, "max_depth": 3})
    assert "kim.md" not in flat and "memory/people/kim.md" in deep
    assert "kim.md" in await tool.handler({"path": "/tmp/hermes_agy_z", "recursive": True, "name": "*.md"})   # foreign absolute path -> our root


def test_walk_accepts_several_name_patterns(home):
    home.write("memory/people/kim.md", "# Kim")
    home.write("notes/todo.txt", "x")
    assert sorted(r["path"] for r in home.walk(name=["kim*", "todo*"], kind="f")) == ["memory/people/kim.md", "notes/todo.txt"]


def test_uploaded_documents_are_listed_in_the_prompt_context_so_nothing_needs_searching(home):
    assert "Documents the user uploaded" not in home.context_block()
    home.save_upload("보고서.docx", b"PK" + b"x" * 40)
    block = home.context_block()
    assert "### Documents the user uploaded" in block and "uploads/보고서.docx (42B)" in block and "extract_document" in block


def test_page_returns_a_part_with_a_pointer_to_the_rest():
    from assistant.tools.home_tools import page
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(1, 400))
    first = page(text, cap=1000)
    assert first.startswith("line 1 ") and "[showing lines 1-" in first and "call again with offset=" in first and len(first) < 1250
    nxt = int(first.rsplit("offset=", 1)[1].split()[0])
    second = page(text, nxt, cap=1000)
    assert second.startswith(f"line {nxt} ") and second.splitlines()[0] != first.splitlines()[0]
    assert "[showing" not in page("short\ntext") and page("a\nb\nc", 2) == "b\nc"
    assert page("x" * 5000, cap=100).startswith("x" * 100)                  # one huge line cannot blow the cap


async def test_long_listings_and_files_are_clipped_so_they_stay_in_the_conversation(home):
    for i in range(120):
        home.write(f"notes/n{i:03d}.md", "# n")
    home.write("notes/big.md", "\n".join(f"row {i} " + "y" * 80 for i in range(1, 600)))
    tools = {t.name: t for t in home_tools(home, None)}
    listing = await tools["list_files"].handler({"path": "notes"})
    assert len(listing) < 5200 and "more entries not shown; narrow it" in listing
    part = await tools["read_file"].handler({"path": "notes/big.md"})
    assert len(part) < 12_300 and "call again with offset=" in part
    nxt = int(part.rsplit("offset=", 1)[1].split()[0])
    assert (await tools["read_file"].handler({"path": "notes/big.md", "offset": nxt})).startswith(f"row {nxt} ")
    assert (await tools["read_file"].handler({"path": "notes/big.md", "offset": 1})).startswith("row 1 ")   # a CLI's StartLine


async def test_the_top_level_listing_shows_uploaded_documents_and_cli_rules_files_are_explained(home):
    home.save_upload("보고서.docx", b"PK" + b"x" * 40)
    tools = {t.name: t for t in home_tools(home, None)}
    top = await tools["list_files"].handler({"path": "/tmp/hermes_agy_x"})
    assert "d uploads" in top and "- uploads/보고서.docx (42 bytes)  <- document from the user: read_file uploads/보고서.docx" in top
    assert top.index("d uploads") < top.index("- uploads/보고서.docx")
    for p in ("/tmp/hermes_agy_x/GEMINI.md", "GEMINI.md"):
        msg = await tools["read_file"].handler({"path": p})
        assert "already in your context" in msg and "uploads/" in msg and not msg.startswith("error")
    err = await tools["read_file"].handler({"path": "/tmp/hermes_agy_x/home/.gemini/brain/x/log.txt"})
    assert err.startswith("error:") and "list_files with no path" in err


async def test_a_model_that_goes_looking_for_its_tool_list_in_the_rules_file_is_told_the_tools(tmp_path):
    from assistant.agent import Tool
    from assistant.home import Home
    from assistant.server import tool_catalog
    from assistant.tools.home_tools import home_tools
    ping = Tool("ping", "Say pong. More detail.", {"type": "object", "properties": {}}, None)
    tools = home_tools(Home(tmp_path / "h"), None, catalog=lambda: tool_catalog([ping]))
    read = next(t for t in tools if t.name == "read_file")
    out = await read.handler({"path": "/tmp/hermes_agy_x/GEMINI.md"})
    assert "already in your context" in out and "- ping: Say pong" in out and "More detail" not in out


async def test_listing_the_clis_own_folder_also_shows_the_tools_but_a_normal_listing_does_not(tmp_path):
    from assistant.agent import Tool
    from assistant.home import Home
    from assistant.server import tool_catalog
    from assistant.tools.home_tools import home_tools
    ping = Tool("ping", "Say pong.", {"type": "object", "properties": {}}, None)
    lst = next(t for t in home_tools(Home(tmp_path / "h"), None, catalog=lambda: tool_catalog([ping])) if t.name == "list_files")
    assert "- ping: Say pong" in await lst.handler({"path": "/tmp/hermes_agy_x"})
    assert "ping" not in await lst.handler({"path": ""})


async def test_search_skills_tool_returns_matches_or_a_hint():
    from pathlib import Path
    from assistant.home import Home, Skills
    from assistant.tools.home_tools import home_tools
    import tempfile
    content = Path(__file__).resolve().parents[1] / "content" / "skills"
    t = {x.name: x for x in home_tools(Home(tempfile.mkdtemp()), Skills(content))}
    assert t["search_skills"].read_only
    assert "shopping-and-bookings" in await t["search_skills"].handler({"query": "book a hotel"})
    assert "No skill matches" in await t["search_skills"].handler({"query": "zzzzqqq"})
