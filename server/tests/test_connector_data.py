"""What came from a connector, counted when the owner disconnects it and deleted only on the owner's word (connector_data.py).

Only recorded origins count: library files saved by connector_download (origin in .origins.json) and proactive_log rows keyed
"mail:<connection name>:...". Files go to the library's trash and come back with a restore. The routes need the token, and the
disconnect response (DELETE /api/credentials/{name}) carries the summary without deleting anything."""

import httpx

from assistant.deliverables import Deliverables
from assistant.home import Home
from assistant.connector_data import ConnectorData
from assistant.proactive import SCHEMA
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def seed_items(store, *keys):
    store._db.executescript(SCHEMA)
    for key in keys:
        store._run("INSERT INTO proactive_log(key, source, title, urgency, status, reason, message, decided_at, expires_at, day, task_id, kind) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (key, "mail", "a mail", "지금", "sent", "why", "told the user", 1.0, 2e9, "2026-11-16", None, ""))


def world(tmp_path):
    home = Home(tmp_path / "home")
    home.clock = Clock()
    store = Store(tmp_path / "s.db")
    lib = Deliverables(home, Clock())
    lib.save("ticket.pdf", b"%PDF-1 gmail", origin="mail-gmail")
    lib.save("boarding.pdf", b"%PDF-1 gmail 2", origin="mail-gmail")
    lib.save("menu.pdf", b"%PDF-1 other", origin="mail-other")
    lib.save("own.txt", b"made by the agent")                      # no origin recorded: never counted, never touched
    seed_items(store, "mail:mail-gmail:1", "mail:mail-gmail:digest:7", "mail:mail-other:3", "mail:mail-gmailx:9", "mail:mail_gmail:4")
    return home, store, lib


def test_summary_counts_only_recorded_origins_per_kind(tmp_path):
    home, store, _ = world(tmp_path)
    cd = ConnectorData(store, home)
    assert cd.summary("mail-gmail") == {"files": 2, "notes": 0, "items": 2}     # the key prefix is exact: "mail-gmailx" and "mail_gmail" are other connections
    assert cd.summary("mail-other") == {"files": 1, "notes": 0, "items": 1}


def test_forget_removes_only_that_connections_data_and_leaves_the_rest(tmp_path):
    home, store, lib = world(tmp_path)
    cd = ConnectorData(store, home)
    assert cd.forget("mail-gmail") == {"files": 2, "notes": 0, "items": 2}
    assert cd.summary("mail-gmail") == {"files": 0, "notes": 0, "items": 0}
    assert cd.forget("mail-gmail") == {"files": 0, "notes": 0, "items": 0}      # nothing left: asking again deletes nothing more
    assert cd.summary("mail-other") == {"files": 1, "notes": 0, "items": 1}
    assert (lib.dir / "menu.pdf").is_file() and (lib.dir / "own.txt").is_file()
    keys = sorted(r["key"] for r in store._all("SELECT key FROM proactive_log"))
    assert keys == ["mail:mail-gmailx:9", "mail:mail-other:3", "mail:mail_gmail:4"]


def test_forgotten_files_go_to_the_library_trash_and_restore_brings_them_back_as_the_connectors(tmp_path):
    home, store, lib = world(tmp_path)
    ConnectorData(store, home).forget("mail-gmail")
    assert not (lib.dir / "ticket.pdf").exists() and not (lib.dir / "boarding.pdf").exists()
    assert sorted(t["name"] for t in lib.trashed()) == ["boarding.pdf", "ticket.pdf"]     # still there, restorable
    assert "ticket.pdf" not in lib.origins() and "boarding.pdf" not in lib.origins()
    name = lib.restore("ticket.pdf")
    assert lib.origins()[name] == "mail-gmail"
    assert ConnectorData(store, home).summary("mail-gmail")["files"] == 1


def test_an_unknown_or_empty_connector_has_zeros_and_forgets_nothing(tmp_path):
    home, store, lib = world(tmp_path)
    cd = ConnectorData(store, home)
    assert cd.summary("nobody") == {"files": 0, "notes": 0, "items": 0}
    assert cd.forget("nobody") == {"files": 0, "notes": 0, "items": 0}
    assert cd.summary("") == {"files": 0, "notes": 0, "items": 0} and cd.forget("") == {"files": 0, "notes": 0, "items": 0}
    assert len(lib.list()) == 4 and cd.summary("mail-other")["items"] == 1


def test_a_library_file_deleted_by_hand_is_not_counted(tmp_path):
    home, store, lib = world(tmp_path)
    (lib.dir / "menu.pdf").unlink()
    assert ConnectorData(store, home).summary("mail-other") == {"files": 0, "notes": 0, "items": 1}


def test_a_store_without_an_alert_log_has_no_items(tmp_path):
    store = Store(tmp_path / "bare.db")
    assert ConnectorData(store, None).summary("mail-gmail") == {"files": 0, "notes": 0, "items": 0}
    assert ConnectorData(store, None).forget("mail-gmail") == {"files": 0, "notes": 0, "items": 0}


def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home")), tools=[])


async def test_the_data_routes_need_the_token(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/connectors/mail-gmail/data")).status_code == 401
        assert (await c.post("/api/connectors/mail-gmail/forget")).status_code == 401


async def test_the_routes_return_counts_and_forget_deletes_only_that_connection(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        home, store, lib = world(tmp_path)   # seeded while the app runs: it reads the same files and database
        assert (await c.get("/api/connectors/mail-gmail/data")).json() == {"files": 2, "notes": 0, "items": 2}
        assert (await c.get("/api/connectors/nobody/data")).json() == {"files": 0, "notes": 0, "items": 0}
        assert (await c.post("/api/connectors/mail-gmail/forget")).json() == {"files": 2, "notes": 0, "items": 2}
        assert (await c.get("/api/connectors/mail-gmail/data")).json() == {"files": 0, "notes": 0, "items": 0}
        assert (await c.get("/api/connectors/mail-other/data")).json() == {"files": 1, "notes": 0, "items": 1}


async def test_disconnect_answers_with_the_data_it_left_and_deletes_nothing(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/credentials", json={"name": "acme", "hosts": ["api.acme.com"], "placement": {"type": "bearer_header"}})
        home, store, lib = world(tmp_path)
        lib.save("acme.csv", b"a,b\n1,2", origin="acme")
        got = (await c.delete("/api/credentials/acme")).json()
        assert got == {"ok": True, "data": {"files": 1, "notes": 0, "items": 0}}
        assert (lib.dir / "acme.csv").is_file()                              # the owner has not chosen yet: nothing moved
        assert (await c.get("/api/connectors/acme/data")).json()["files"] == 1
        assert (await c.post("/api/connectors/acme/forget")).json() == {"files": 1, "notes": 0, "items": 0}
        assert not (lib.dir / "acme.csv").exists()


async def test_a_file_saved_from_a_connector_records_that_connector(tmp_path):
    from assistant.netguard import Reply
    from assistant.tools.connect_tools import connect_tools

    class Net:
        async def __call__(self, method, url, **kw):
            return Reply(200, {"content-type": "application/pdf"}, b"%PDF-1.4 ticket", False)

    home = Home(tmp_path / "home")
    store = Store(tmp_path / "s.db")
    store.save_credential("asana-api", "Asana", ["api.example.test"], {"type": "bearer_header"}, "")
    store.set_credential_secret("asana-api", "sekret-value-12345")
    tools = {x.name: x for x in connect_tools(store, home=home, fetcher=Net())}
    out = str(await tools["connector_download"].handler({"name": "asana-api", "url": "https://api.example.test/files/ticket.pdf"}))
    assert "Saved to the user's library as files/ticket.pdf" in out
    assert Deliverables(home).origins() == {"ticket.pdf": "asana-api"}
    assert ConnectorData(store, home).summary("asana-api") == {"files": 1, "notes": 0, "items": 0}
