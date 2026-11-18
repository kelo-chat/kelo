"""The owner's file manager for the agent's files: Home.trash / trashed / restore / reset / mkdir, the purge after 30 days, what can never be
reached (outside the home, hidden names, the trash itself, symlinks), the agent's file tools staying out of the trash, and the routes
(api/files.py docstring), including their order next to the library's routes."""

import os

import httpx
import pytest

from assistant.agent import Ours
from assistant.home import SIDE_TEMPLATES, TEMPLATES, TRASH_DAYS, CoreFile, Home, HomeError, NotDeletable, Skills
from assistant.server import Settings, create_app
from assistant.tools.home_tools import home_tools

from test_server import AUTH, TOKEN, Running


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def home(tmp_path):
    h = Home(tmp_path / "home")
    h.clock = Clock()
    return h


def seed(h):
    h.write("memory/people/kim.md", "---\nname: Kim\n---\n\nlikes tea\n", by="user")
    h.write("memory/people/lee.md", "# Lee\n", by="user")
    h.write("notes/a.md", "note a\n", by="user")


# ------------------------------------------------------------------------------------------------ Home
def test_core_files_come_from_the_templates_not_a_hand_list(home):
    assert home.core_files() == ("assistant.md", "user.md", "MEMORY.md", "AGENTS.md", "CHECKLIST.md", "NOTIFY_PREFS.md")
    assert set(home.core_files()) == set(TEMPLATES) | set(SIDE_TEMPLATES)


def test_trash_moves_a_file_hides_it_everywhere_and_restore_puts_it_back(home):
    seed(home)
    e = home.trash("notes/a.md")
    assert e["path"] == "notes/a.md" and e["bytes"] == 7 and e["type"] == "file" and e["purge_at"] == e["deleted_at"] + TRASH_DAYS * 86400
    assert not (home.root / "notes" / "a.md").exists() and (home.root / ".trash" / e["id"] / "notes" / "a.md").read_text() == "note a\n"
    assert "notes/a.md" not in [r["path"] for r in home.tree()] and not any(".trash" in r["path"] for r in home.tree(True))
    assert not any(".trash" in r["path"] for r in home.walk("", 6)) and all(".trash" not in r["path"] for r in home.list())
    assert [r["id"] for r in home.trashed()] == [e["id"]] and home.trashed()[0]["path"] == "notes/a.md"
    assert home.restore(e["id"]) == "notes/a.md" and home.read("notes/a.md") == "note a\n"
    assert home.trashed() == [] and not (home.root / ".trash" / e["id"]).exists()


def test_a_folder_goes_with_its_files_and_comes_back_whole(home):
    seed(home)
    e = home.trash("memory/people", by="user")
    assert e["type"] == "dir" and e["bytes"] > 10 and not (home.root / "memory" / "people").exists()
    assert home.restore(e["id"]) == "memory/people"
    assert home.read("memory/people/kim.md").endswith("likes tea\n") and home.read("memory/people/lee.md") == "# Lee\n"


def test_restore_never_overwrites_what_is_there_now(home):
    seed(home)
    first = home.trash("notes/a.md")
    home.write("notes/a.md", "newer\n", by="user")
    assert home.restore(first["id"]) == "notes/a (restored).md"
    second = home.trash("notes/a.md")
    home.write("notes/a.md", "newest\n", by="user")
    assert home.restore(second["id"]) == "notes/a (restored 2).md"
    assert home.read("notes/a.md") == "newest\n" and home.read("notes/a (restored).md") == "note a\n" and home.read("notes/a (restored 2).md") == "newer\n"
    folder = home.trash("memory/people", by="user")
    home.mkdir("memory/people")
    assert home.restore(folder["id"]) == "memory/people (restored)" and home.read("memory/people (restored)/lee.md") == "# Lee\n"


def test_the_trash_is_purged_after_thirty_days_and_leaves_the_librarys_files_alone(home):
    seed(home)
    old, new = home.trash("notes/a.md"), None
    (home.root / ".trash" / "index.json").write_text("[]")                      # the library's own index and files share the folder
    (home.root / ".trash" / "abc123-report.xlsx").write_bytes(b"x")
    home.clock.t += (TRASH_DAYS - 1) * 86400
    new = home.trash("memory/people/lee.md", by="user")
    home.clock.t += 2 * 86400                                                   # old: 31 days, new: 2 days
    assert home.purge_trash() == 1
    assert [r["id"] for r in home.trashed()] == [new["id"]] and not (home.root / ".trash" / old["id"]).exists()
    assert (home.root / ".trash" / "index.json").exists() and (home.root / ".trash" / "abc123-report.xlsx").exists()
    home.forget_trashed(new["id"])
    assert home.trashed() == []
    with pytest.raises(HomeError):
        home.forget_trashed(new["id"])


def test_core_files_cannot_be_deleted_only_reset_and_the_old_text_is_kept(home):
    for name in home.core_files():
        with pytest.raises(CoreFile):
            home.trash(name)                                                    # even when the side file was never made
    home.write("MEMORY.md", "# MEMORY.md\n\n- Kim's door code is 4821\n", by="user")
    got = home.reset("MEMORY.md")
    assert got["path"] == "MEMORY.md" and home.read("MEMORY.md") == TEMPLATES["MEMORY.md"]
    assert "door code" in (home.root / ".trash" / got["trashed"]["id"] / "MEMORY.md").read_text()
    assert home.restore(got["trashed"]["id"]) == "MEMORY (restored).md"      # the template is there now: the old text comes back beside it
    assert home.reset("CHECKLIST.md")["trashed"] is None and home.read("CHECKLIST.md") == SIDE_TEMPLATES["CHECKLIST.md"]
    with pytest.raises(NotDeletable):
        home.reset("notes/a.md")


def test_only_the_editors_own_places_can_be_deleted(home):
    seed(home)
    home.save_upload("a.pdf", b"%PDF-1.4 x")
    (home.root / "files").mkdir(exist_ok=True)
    (home.root / "files" / "r.txt").write_text("r")
    (home.root / "loose.md").write_text("x")
    for bad in ("uploads/a.pdf", "files/r.txt", "loose.md", "memory", "notes", "uploads"):
        with pytest.raises(NotDeletable):
            home.trash(bad)
    assert (home.root / "uploads" / "a.pdf").exists() and (home.root / "files" / "r.txt").exists()


def test_mkdir_makes_folders_only_inside_memory_and_notes(home):
    assert home.mkdir("notes/trips/2027") == "notes/trips/2027" and (home.root / "notes" / "trips" / "2027").is_dir()
    assert home.mkdir("notes/trips") == "notes/trips"                          # an existing folder is fine
    assert "notes/trips/2027" in [r["path"] for r in home.tree(True) if r["type"] == "dir"] and "notes/trips" not in [r["path"] for r in home.tree()]
    home.write("notes/f.md", "x", by="user")
    for bad in ("uploads/x", "newdir", "notes/f.md/sub"):
        with pytest.raises(HomeError):
            home.mkdir(bad)


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "~/x", "notes/../../x", "", ".env", ".trash", "notes/.env", "x\x00y"])
def test_paths_outside_the_home_and_hidden_names_are_never_reachable(home, bad):
    seed(home)
    e = home.trash("notes/a.md")
    for call in (lambda: home.trash(bad), lambda: home.read(bad), lambda: home.write(bad, "x"), lambda: home.mkdir(bad), lambda: home.reset(bad)):
        with pytest.raises(HomeError):
            call()
    for inside in (f".trash/{e['id']}", f".trash/{e['id']}/notes/a.md", f".trash/{e['id']}/.entry.json"):
        for call in (lambda: home.trash(inside), lambda: home.read(inside), lambda: home.write(inside, "x"), lambda: home.mkdir(inside)):
            with pytest.raises(HomeError):
                call()
    assert (home.root / ".trash" / e["id"] / "notes" / "a.md").read_text() == "note a\n"


def test_a_symlink_to_the_outside_is_never_followed_deleted_or_restored_through(home, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "s.md").write_text("TOP SECRET")
    home.mkdir("notes")
    home.mkdir("memory")
    os.symlink(outside, home.root / "notes" / "link")
    os.symlink(outside / "s.md", home.root / "memory" / "s.md")
    for bad in ("notes/link", "notes/link/s.md", "memory/s.md"):
        with pytest.raises(HomeError, match="symbolic"):
            home.trash(bad)
    assert (outside / "s.md").read_text() == "TOP SECRET" and "link" not in " ".join(r["path"] for r in home.tree(True))
    home.write("notes/dir/x.md", "x", by="user")
    os.symlink(outside / "s.md", home.root / "notes" / "dir" / "inner.md")      # a link inside a folder: moved as a link, size 0, never read
    e = home.trash("notes/dir")
    assert e["bytes"] == 1 and (outside / "s.md").read_text() == "TOP SECRET"


async def test_the_agents_file_tools_cannot_reach_the_trash(home, tmp_path):
    seed(home)
    e = home.trash("notes/a.md")
    (tmp_path / "s").mkdir()
    tools = {t.name: t for t in home_tools(home, Skills(tmp_path / "s"))}
    inside = f".trash/{e['id']}/notes/a.md"
    for raw in (inside, f"{home.root}/{inside}", f".trash/{e['id']}", ".trash"):
        assert (await tools["read_file"].handler({"path": raw})).startswith("error:")
        assert (await tools["write_file"].handler({"path": raw, "content": "x"})).startswith("error:")
        assert (await tools["list_files"].handler({"path": raw})).startswith("error:")
    listing = await tools["list_files"].handler({"recursive": True, "max_depth": 6})
    assert ".trash" not in listing and "notes/a.md" not in listing
    assert ".trash" not in await tools["list_files"].handler({}) and "notes/a.md" not in home.context_block()
    assert (home.root / ".trash" / e["id"] / "notes" / "a.md").read_text() == "note a\n"


def test_the_agent_sees_a_user_delete_and_reset_in_its_next_prompt_and_nothing_else_is_sent(home):
    home.write("MEMORY.md", "# MEMORY.md\n\n## About the user\n- likes tea\n", by="user")
    seed(home)
    assert "likes tea" in home.context_block() and "notes/a.md" in home.context_block()
    home.trash("notes/a.md")
    home.reset("MEMORY.md")
    block = home.context_block()
    assert "likes tea" not in block and "notes/a.md" not in block and "MEMORY.md" in block.split("Still empty")[1]


# ------------------------------------------------------------------------------------------------ the routes
def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "s.db"),
                               home_dir=str(tmp_path / "state" / "home")), tools=[])


async def test_the_file_manager_routes_and_their_order_next_to_the_librarys(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        assert (await c.put("/api/files/notes/trips/rome.md", json={"content": "pasta\n"})).json() == {"path": "notes/trips/rome.md", "ok": True}   # creates
        assert (await c.get("/api/files/notes/trips/rome.md")).json()["content"] == "pasta\n"
        made = await c.post("/api/files/notes/empty/folder")
        assert made.status_code == 200 and made.json() == {"ok": True, "path": "notes/empty"}
        assert (await c.post("/api/files/uploads/x/folder")).status_code == 422
        rows = (await c.get("/api/files?folders=1")).json()
        assert {"path": "notes/empty", "type": "dir", "bytes": None, "writable": True} in rows
        assert all("type" not in r for r in (await c.get("/api/files")).json()) and "notes/empty" not in [r["path"] for r in (await c.get("/api/files")).json()]
        # core files: 409 with the reset hint; reset puts the old text in the trash
        for name in ("assistant.md", "user.md", "MEMORY.md", "AGENTS.md", "CHECKLIST.md", "NOTIFY_PREFS.md"):
            r = await c.delete(f"/api/files/{name}")
            assert r.status_code == 409 and r.json() == {"detail": "core file", "reset": True}, name
        await c.put("/api/files/MEMORY.md", json={"content": "# MEMORY.md\n\n- secret plan\n"})
        r = await c.post("/api/files/MEMORY.md/reset")
        assert r.status_code == 200 and r.json()["path"] == "MEMORY.md" and r.json()["trashed"]["bytes"] > 10
        assert (await c.get("/api/files/MEMORY.md")).json()["content"] == TEMPLATES["MEMORY.md"]
        assert (await c.post("/api/files/notes/trips/rome.md/reset")).status_code == 422
        # delete -> trash list -> restore / forget
        d = await c.delete("/api/files/notes/trips")
        assert d.status_code == 200 and d.json()["ok"] is True and d.json()["trashed"]["type"] == "dir"
        assert (await c.get("/api/files/notes/trips/rome.md")).status_code == 404
        trash = (await c.get("/api/home-trash")).json()
        assert {r["path"] for r in trash} == {"MEMORY.md", "notes/trips"} and all(set(r) >= {"id", "path", "bytes", "deleted_at", "purge_at"} for r in trash)
        folder = next(r for r in trash if r["path"] == "notes/trips")
        back = await c.post(f"/api/home-trash/{folder['id']}/restore")
        assert back.json() == {"ok": True, "path": "notes/trips"} and (await c.get("/api/files/notes/trips/rome.md")).json()["content"] == "pasta\n"
        old = next(r for r in trash if r["path"] == "MEMORY.md")
        assert (await c.post(f"/api/home-trash/{old['id']}/restore")).json()["path"] == "MEMORY (restored).md"
        assert (await c.post(f"/api/home-trash/{old['id']}/restore")).status_code == 404
        await c.put("/api/files/notes/x.md", json={"content": "x"})
        t = (await c.delete("/api/files/notes/x.md")).json()["trashed"]
        assert (await c.delete(f"/api/home-trash/{t['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/home-trash/{t['id']}")).status_code == 404 and (await c.get("/api/home-trash")).json() == []
        for bad_id in ("..", "zzzz", "a" * 12):
            assert (await c.post(f"/api/home-trash/{bad_id}/restore")).status_code == 404
        # not reachable: hidden names, the trash itself, other places
        t2 = (await c.delete("/api/files/notes/trips/rome.md")).json()["trashed"]
        for path in (".env", ".trash", f".trash/{t2['id']}/notes/trips/rome.md", ".history/x", "%2e%2e/x", "..%2fx", "/etc/passwd"):
            assert (await c.delete(f"/api/files/{path}")).status_code == 404, path
            assert (await c.get(f"/api/files/{path}")).status_code == 404, path
            assert (await c.post(f"/api/files/{path}/folder")).status_code == 404, path
        assert (await c.put(f"/api/files/.trash/{t2['id']}/x.md", json={"content": "x"})).status_code == 422
        # uploads and the library keep their own deletes
        assert (await c.delete("/api/files/uploads/a.pdf")).status_code in (404, 422)
        assert (await c.put("/api/uploads/a.pdf", content=b"%PDF" + b"0" * 100)).status_code == 200
        assert (await c.delete("/api/files/uploads/a.pdf")).status_code == 422 and (await c.get("/api/uploads")).json()[0]["path"] == "uploads/a.pdf"
        # route order: the library's routes still answer their own paths, with their own shapes
        assert (await c.get("/api/files/library")).json() == [] and (await c.get("/api/files/trash")).json() == []
        miss = await c.delete("/api/files/library/nothing.pdf")
        assert miss.status_code == 404 and "nothing.pdf" in miss.json()["detail"]                     # the library's own message, not the home's
        assert (await c.post("/api/files/library/nothing.pdf/restore")).status_code == 404
        assert (await c.post("/api/files/library/nothing.pdf/share", json={"confirm": True})).status_code == 404
        # a result in the library: its own delete/restore/trash, never the home's
        lib = tmp_path / "state" / "home" / "files"
        lib.mkdir(exist_ok=True)
        (lib / "report.txt").write_text("quarterly")
        assert [r["name"] for r in (await c.get("/api/files/library")).json()] == ["report.txt"]
        gone = await c.delete("/api/files/library/report.txt")
        assert gone.status_code == 200 and set(gone.json()["trashed"]) >= {"id", "name", "bytes", "deleted_at", "purge_at"} and "path" not in gone.json()["trashed"]
        assert [r["name"] for r in (await c.get("/api/files/trash")).json()] == ["report.txt"] and [r["path"] for r in (await c.get("/api/home-trash")).json()] == ["notes/trips/rome.md"]
        assert (await c.post("/api/files/library/report.txt/restore")).json() == {"ok": True, "name": "report.txt"}
        assert (lib / "report.txt").read_text() == "quarterly"
        assert (await c.delete("/api/files/files/report.txt")).status_code == 422 and (lib / "report.txt").exists()   # files/ is the library's
