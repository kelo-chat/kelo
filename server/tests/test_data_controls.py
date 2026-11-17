"""Your data (data_controls.py, data_import.py, api/data.py): the export ZIP and what must never be in it, each import format found by
its structure (not its file names) with the model call faked, the start over (what goes, what stays, the backup first, the typed word),
and the auth of every route."""

from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant.data_controls import BACKUP_PREFIX, CONFIRM_WORD, EXPORT_PREFIX, DataError
from assistant.data_import import (Conv, Importer, chunks, format_of, paste_chunks, read_archive)
from assistant.providers import Done, TextDelta
from assistant.server import create_app
from assistant.store import Store

from test_agent_profile import app_settings
from test_server import AUTH, Running

SECRETS = ("sk-test-ZZ-api-key-1234567890", "hunter2-vault-secret-9876", "device-token-hash-abcdef", "Super Vault Label QQ")


def make_app(tmp_path: Path, facts=None, calls=None):
    """The app with a home; the model answers every request with `facts` as the JSON the import asks for."""
    async def fn(provider, request, **kw):
        if calls is not None:
            calls.append(request)
        yield TextDelta(json.dumps({"facts": facts or []}))
        yield Done("stop")
    return create_app(app_settings(tmp_path, scheduler=False, home_dir=str(tmp_path / "home")), tools=[], stream_fn=fn)


def store_of(app):
    return app.state.workers.store


def seed(app, tmp_path: Path):
    """Something in everything the export reads, and four secrets that must not appear anywhere in it."""
    store = store_of(app)
    now = time.time()
    store.set_profile(user_name="Mina", timezone="Asia/Seoul")
    store.set_key("anthropic", SECRETS[0])
    store.save_credential("shop", SECRETS[3], ["shop.example.com"], {"header": "Authorization"})
    store.set_credential_secret("shop", SECRETS[1])
    store._run("INSERT INTO devices(name, token_hash, created_at) VALUES('Pixel', ?, ?)", (SECRETS[2], now))
    store.add_message("user", "I live in Busan")
    store.add_message("assistant", "Nice city")
    home = tmp_path / "home"
    (home / "memory" / "log").mkdir(parents=True, exist_ok=True)
    (home / "memory" / "log" / "2026-11-01.md").write_text("- Mina likes tea\n")
    (home / "memory" / "people").mkdir(exist_ok=True)
    (home / "memory" / "people" / "jun.md").write_text("Jun is Mina's brother\n")
    (home / "MEMORY.md").write_text("# MEMORY.md\n\n- Mina lives in Busan\n")
    (home / "user.md").write_text("# user.md\n\nMina\n")
    store._run("INSERT INTO memory_claims(claim_id, run_id, kind, salience, text, quote, speaker, handle, status, source_path, source_line, "
               "created_at, updated_at) VALUES('c1', 'r1', 'fact', 'high', 'Lives in Busan', 'I live in Busan', 'user', 'm1', 'active', "
               "'memory/log/2026-11-01.md', 1, ?, ?)", (now, now))
    store._run("INSERT INTO memory_runs(run_id, started_at, trigger, outcome) VALUES('r1', ?, 'idle', 'ok')", (now,))
    store._run("INSERT INTO schedules(name, prompt, cron, created_at, updated_at) VALUES('Morning', 'news', '0 9 * * *', ?, ?)", (now, now))
    store._run("INSERT INTO schedule_runs(schedule_id, started_at, status) VALUES(1, ?, 'done')", (now,))
    store._run("INSERT INTO follow_items(key, kind, title, starts_at, created_at, updated_at) VALUES('f1', 'flight', 'KE123', ?, ?, ?)",
               (now + 3600, now, now))
    store._run("INSERT INTO forget_tombstones(id, label, terms, created_at) VALUES('t1', 'my ex', '[]', ?)", (now,))


async def export_zip(c: httpx.AsyncClient) -> tuple[dict, zipfile.ZipFile]:
    assert (await c.post("/api/data/export")).status_code == 200
    for _ in range(200):
        st = (await c.get("/api/data/export")).json()
        if st["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert st["state"] == "done", st
    got = await c.get(st["download"])
    assert got.status_code == 200
    return st, zipfile.ZipFile(io.BytesIO(got.content))


# ----------------------------------------------------------------------------------------------------------------- export
async def test_the_export_holds_the_data_and_none_of_the_secrets(tmp_path):
    app = make_app(tmp_path)
    seed(app, tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/data/export")).json() == {"state": "idle"}
        st, z = await export_zip(c)
        names = set(z.namelist())
        assert st["file"].startswith(EXPORT_PREFIX) and st["download"] == f"/api/files/library/{st['file']}/file"
        assert {"README.txt", "messages.jsonl", "schedules.json", "follows.json", "connectors.json", "profile.json",
                "memory-files/MEMORY.md", "memory-files/user.md", "memory-files/memory/log/2026-11-01.md",
                "memory-files/memory/people/jun.md", "memory-files/memory/claims.jsonl"} <= names
        msgs = [json.loads(line) for line in z.read("messages.jsonl").decode().splitlines()]
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "I live in Busan"), ("assistant", "Nice city")]
        assert json.loads(z.read("memory-files/memory/claims.jsonl").splitlines()[0])["text"] == "Lives in Busan"
        assert [s["name"] for s in json.loads(z.read("schedules.json"))["schedules"]] == ["Morning"]
        assert [f["title"] for f in json.loads(z.read("follows.json"))] == ["KE123"]
        assert json.loads(z.read("profile.json"))["profile"]["user_name"] == "Mina"
        everything = b"\n".join(z.read(n) for n in names)
        for secret in SECRETS:
            assert secret.encode() not in everything, secret
        assert b"shop.example.com" not in everything and b"Pixel" not in everything          # the vault's hosts, the devices' names
        # the status survives a new process: the newest ZIP in the library stands for it
        again = (await c.get("/api/data/export")).json()
        assert again["state"] == "done" and again["file"] == st["file"]


async def test_connectors_are_listed_by_name_and_state_only():
    from assistant.data_controls import DataControls
    store = Store()
    rows = [{"id": "gmail", "name": "Gmail", "status": "connected", "account": "me@example.com", "token": "SECRET-TOKEN"},
            {"id": "slack", "name": "Slack", "status": "available", "token": "SECRET-TOKEN-2"}]
    data = DataControls(store, None, None, connectors=lambda: rows)
    zbytes, counts = data.build_zip()
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    assert json.loads(z.read("connectors.json")) == [{"id": "gmail", "name": "Gmail", "status": "connected"}]
    assert b"SECRET-TOKEN" not in b"".join(z.read(n) for n in z.namelist()) and counts["connectors"] == 1


# ----------------------------------------------------------------------------------------------------------------- import: formats
def chatgpt_doc():
    def node(i, parent, role, text, kids=()):
        return {"id": i, "parent": parent, "children": list(kids),
                "message": None if role is None else {"author": {"role": role}, "create_time": float(i.strip("n")), "content": {"content_type": "text", "parts": [text]}}}
    return [{"title": "Trip", "create_time": 1_760_000_000.0, "current_node": "n3",
             "mapping": {"n0": node("n0", None, None, "", ["n1"]), "n1": node("n1", "n0", "user", "I am vegetarian and live in Busan", ["n2"]),
                         "n2": node("n2", "n1", "assistant", "Noted ASSISTANT-WORDS", ["n3"]), "n3": node("n3", "n2", "user", "My sister Jun visits in May", [])}}]


def claude_doc():
    return [{"uuid": "u1", "name": "Plans", "created_at": "2026-03-01T10:00:00Z",
             "chat_messages": [{"sender": "human", "text": "I work night shifts at a hospital"},
                               {"sender": "assistant", "text": "Got it ASSISTANT-WORDS"},
                               {"sender": "human", "text": "", "content": [{"type": "text", "text": "I prefer short answers"}]}]}]


def gemini_doc():
    return [{"header": "Gemini Apps", "title": "Prompted I run every morning at six", "time": "2026-02-02T06:00:00.000Z", "products": ["Gemini Apps"]},
            {"header": "YouTube", "title": "Watched a video", "time": "2026-02-02T06:00:00.000Z", "products": ["YouTube"]}]


def make_zip(path: Path, files: dict[str, object]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, doc in files.items():
            z.writestr(name, doc if isinstance(doc, (bytes, str)) else json.dumps(doc))
    return path


def test_each_format_is_found_by_its_keys_and_not_by_the_file_name(tmp_path):
    cases = {"chatgpt": chatgpt_doc(), "claude": claude_doc(), "gemini": gemini_doc()}
    for want, doc in cases.items():
        assert format_of(doc) == want
        # a name that says nothing, a name that lies: the keys decide
        for name in ("part-0001.bin", "conversations.json" if want != "claude" else "MyActivity.json"):
            fmt, convs = read_archive(make_zip(tmp_path / f"{want}.zip", {name: doc, "photo.png": b"\x89PNG", "notes.txt": "plain words"}))
            assert fmt == want
            said = [m for c in convs for m in c.user]
            assert said and not any("ASSISTANT-WORDS" in m for m in said) and not any("Watched" in m for m in said)
    assert [m for c in read_archive(make_zip(tmp_path / "a.zip", {"x": chatgpt_doc()}))[1] for m in c.user] == [
        "I am vegetarian and live in Busan", "My sister Jun visits in May"]                                    # the branch, in order
    assert [m for c in read_archive(make_zip(tmp_path / "b.zip", {"x": claude_doc()}))[1] for m in c.user] == [
        "I work night shifts at a hospital", "I prefer short answers"]                                         # text, or content blocks


def test_a_zip_without_chats_and_a_file_that_is_no_zip_are_refused(tmp_path):
    with pytest.raises(DataError) as e:
        read_archive(make_zip(tmp_path / "n.zip", {"a.json": [{"foo": 1}], "b.json": {"mapping": {}}, "c.txt": "hello"}))
    assert e.value.status == 422
    (tmp_path / "x.zip").write_bytes(b"not a zip at all")
    with pytest.raises(DataError):
        read_archive(tmp_path / "x.zip")
    assert format_of({"mapping": {}}) is None and format_of([]) is None and format_of("text") is None


def test_chunks_group_by_conversation_read_each_message_once_and_cut_long_ones():
    zone = ZoneInfo("UTC")
    convs = [Conv("A", 1_760_000_000.0, ["one", "two", "one"]), Conv("B", None, ["x" * 5000, "three"])]
    out = chunks(convs, zone)
    assert out[0].startswith("## A (2025-10-09)") and out[0].count("- one") == 1
    assert "## B" in out[0] and "x" * 3000 in out[0] and "x" * 3001 not in out[0]
    big = chunks([Conv("C", None, [f"message {i} " + "y" * 900 for i in range(40)])], zone)
    assert len(big) > 1 and all(len(p) <= 12_500 for p in big) and all(p.startswith("## C") for p in big)
    assert paste_chunks("- a\n\n- b\n") == ["- a\n- b"] and paste_chunks("   ") == []


# ----------------------------------------------------------------------------------------------------------------- import: the job
def fact(text, quote, **kw):
    return {"text": text, "kind": "fact", "salience": "high", "quote": quote, "valid_until": "", "secret": False, **kw}


async def wait_import(c: httpx.AsyncClient) -> dict:
    for _ in range(400):
        st = (await c.get("/api/data/import")).json()
        if st["state"] != "running":
            return st
        await asyncio.sleep(0.02)
    raise AssertionError("the import did not finish")


async def test_a_pasted_reply_is_added_as_claims_sourced_to_the_assistant(tmp_path):
    reply = "- Lives in Busan\n- Is vegetarian\n- Password: hunter2x9Q!"
    calls = []
    facts = [fact("Lives in Busan", "Lives in Busan"), fact("Is vegetarian", "Is vegetarian"),
             fact("Invented fact", "this quote is not in the text"),                    # no verbatim quote: dropped
             fact("Password: hunter2x9Q!", "Password: hunter2x9Q!"),               # a secret: dropped
             fact("Likes tea", "Is vegetarian", secret=True)]                          # the model says secret: dropped
    app = make_app(tmp_path, facts, calls)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        prompt = (await c.get("/api/data/import/prompt")).json()["prompt"]
        assert "short facts" in prompt and "one per line" in prompt
        assert (await c.get("/api/data/import")).json() == {"state": "idle"}
        assert (await c.post("/api/data/import/text", json={"text": "  ", "assistant": "ChatGPT"})).status_code == 422
        r = await c.post("/api/data/import/text", json={"text": reply, "assistant": "ChatGPT"})
        assert r.status_code == 200 and r.json()["state"] in ("running", "done")
        st = await wait_import(c)
        assert st["state"] == "done" and st["assistant"] == "ChatGPT" and st["total"] == 1 and st["done"] == 1
        assert [a["text"] for a in st["added"]] == ["Lives in Busan", "Is vegetarian"] and st["skipped"] == 3
        rows = store_of(app)._all("SELECT text, handle, speaker, status, source_path FROM memory_claims ORDER BY text")
        assert [(r["text"], r["handle"], r["speaker"], r["status"]) for r in rows] == [
            ("Is vegetarian", "import:ChatGPT:1", "user", "active"), ("Lives in Busan", "import:ChatGPT:1", "user", "active")]
        log = (tmp_path / "home" / rows[0]["source_path"]).read_text()
        assert "Imported from ChatGPT" in log and "Lives in Busan" in log and "hunter2" not in log
        # a second import of the same list adds nothing (the facts are known)
        await c.post("/api/data/import/text", json={"text": reply, "assistant": "ChatGPT"})
        again = await wait_import(c)
        assert again["added"] == [] and again["skipped"] == 5 and again["state"] == "done"
        assert len(store_of(app)._all("SELECT 1 FROM memory_claims")) == 2
        # the pasted words reached the model as data, behind the instructions
        assert "Lives in Busan" in str(calls[0].messages[-1]["content"]) and "DATA" in str(calls[0].messages[0]["content"])
        # the result is kept for the page after a restart
        assert json.loads(store_of(app).meta("data_import_last"))["state"] == "done"


@pytest.mark.parametrize("name,doc,said,quote", [("chatgpt", chatgpt_doc(), "vegetarian", "I am vegetarian and live in Busan"),
                                                  ("claude", claude_doc(), "night shifts", "I work night shifts at a hospital"),
                                                  ("gemini", gemini_doc(), "every morning", "I run every morning at six")])
async def test_an_uploaded_export_is_read_by_structure_and_its_facts_are_added(tmp_path, name, doc, said, quote):
    calls = []
    app = make_app(tmp_path / "srv", [fact(f"Fact from {name}", quote)], calls)
    zpath = make_zip(tmp_path / "export.zip", {"zz9.dat": doc, "image.png": b"\x89PNG\r\n"})
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        r = await c.put("/api/data/import/file", content=zpath.read_bytes())
        assert r.status_code == 200, r.text
        st = await wait_import(c)
        assert st["state"] == "done" and st["assistant"] == {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini"}[name]
        assert [a["text"] for a in st["added"]] == [f"Fact from {name}"]
        sent = " ".join(str(m["content"]) for req in calls for m in req.messages)
        assert said in sent and "ASSISTANT-WORDS" not in sent                 # only the person's own words go to the model
        handle = store_of(app)._all("SELECT handle FROM memory_claims")[0]["handle"]
        assert handle.startswith(f"import:{st['assistant']}:")
        assert not list(Path(__import__("tempfile").gettempdir()).glob("kelo-import-*/export.zip"))   # the upload is deleted


async def test_an_upload_that_is_no_chat_export_fails_the_job_and_a_failing_model_adds_nothing(tmp_path):
    app = make_app(tmp_path / "srv")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await c.put("/api/data/import/file", content=make_zip(tmp_path / "x.zip", {"a.txt": "hello"}).read_bytes())
        st = await wait_import(c)
        assert st["state"] == "failed" and "ChatGPT, Claude or Gemini" in st["error"]
        await c.put("/api/data/import/file", content=b"plain bytes")
        assert (await wait_import(c))["state"] == "failed"


async def test_the_importer_counts_failed_chunks_and_stops_when_cancelled(tmp_path):
    from assistant.home import Home
    store, home = Store(), Home(tmp_path / "home")
    answers = iter([None, {"facts": [fact("Has a dog", "Has a dog")]}])
    gate = asyncio.Event()

    async def ask(system, user, max_tokens=None):
        await gate.wait()
        return next(answers, None)

    imp = Importer(store, home, ask)
    pieces = [f"line {i} " + "z" * 11_000 for i in range(3)]
    imp.start_text("\n".join(pieces) + "\nHas a dog", "Pi")
    assert imp.running() and imp.status()["total"] >= 3
    with pytest.raises(DataError):
        imp.start_text("again")                                                           # one import at a time
    imp.cancel()
    gate.set()
    for _ in range(100):
        if not imp.running():
            break
        await asyncio.sleep(0.01)
    st = imp.status()
    assert st["state"] == "cancelled" and st["done"] < st["total"]


# ----------------------------------------------------------------------------------------------------------------- start over
async def test_start_over_clears_what_it_should_keeps_the_rest_and_saves_a_backup_first(tmp_path):
    app = make_app(tmp_path)
    seed(app, tmp_path)
    store = store_of(app)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        goal = (await c.post("/api/goals", json={"title": "Run a 10k", "kickoff": False})).json()
        (tmp_path / "home" / "uploads").mkdir(exist_ok=True)
        (tmp_path / "home" / "uploads" / "mine.pdf").write_bytes(b"%PDF")
        (tmp_path / "home" / ".memory-passes" / "p1").mkdir(parents=True)
        (tmp_path / "home" / ".memory-passes" / "p1" / "pass.json").write_text("{}")
        word = (await c.get("/api/data")).json()["confirm_word"]
        assert word == CONFIRM_WORD

        # the wrong word: nothing is touched, nothing is saved
        for bad in ("", "delete", "resets"):
            r = await c.post("/api/data/reset", json={"confirm": bad})
            assert r.status_code == 400
        assert len(store._all("SELECT 1 FROM messages")) == 2 and not (tmp_path / "home" / "files").exists()

        r = await c.post("/api/data/reset", json={"confirm": f"  {word.upper()} "})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["ok"] and out["backup"]["name"].startswith(BACKUP_PREFIX) and out["backup"]["path"] == f"files/{out['backup']['name']}"
        assert "signed_out" not in out

        # the backup holds what was there before the clear, and no secret
        got = await c.get(f"/api/files/library/{out['backup']['name']}/file")
        z = zipfile.ZipFile(io.BytesIO(got.content))
        assert b"I live in Busan" in z.read("messages.jsonl") and b"Mina lives in Busan" in z.read("memory-files/MEMORY.md")
        assert not any(s.encode() in b"".join(z.read(n) for n in z.namelist()) for s in SECRETS)

        # cleared
        for table in ("messages", "memory_claims", "memory_runs", "schedules", "schedule_runs", "follow_items", "forget_tombstones"):
            assert len(store._all(f"SELECT 1 FROM {table}")) == 0, table
        home = tmp_path / "home"
        assert "Busan" not in (home / "MEMORY.md").read_text() and "Mina" not in (home / "user.md").read_text()
        assert not list((home / "memory").rglob("*.md")) and not (home / ".memory-passes").exists()
        assert not any("Busan" in p.read_text() for p in (home / ".history").rglob("*") if p.is_file())
        # kept
        assert store.credential("shop") is not None and store.profile()["user_name"] == "Mina"
        assert store._all("SELECT 1 FROM api_keys") and store._all("SELECT 1 FROM devices")
        assert (home / "uploads" / "mine.pdf").read_bytes() == b"%PDF"
        assert [g["title"] for g in (await c.get("/api/goals")).json()] == ["Run a 10k"] and goal["id"]
        assert (await c.get(f"/api/files/library/{out['backup']['name']}/file")).status_code == 200


async def test_start_over_can_also_sign_out_of_the_connectors(tmp_path):
    app = make_app(tmp_path)
    seed(app, tmp_path)
    store = store_of(app)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        out = (await c.post("/api/data/reset", json={"confirm": CONFIRM_WORD, "sign_out_connectors": True})).json()
        assert out["signed_out"] == 1 and store.credentials() == []
        assert store._all("SELECT 1 FROM api_keys") and store._all("SELECT 1 FROM devices")                 # models and pairing stay


async def test_nothing_is_cleared_when_the_backup_cannot_be_made(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seed(app, tmp_path)
    data = app.state.appctx.data_controls

    def broken():
        raise DataError("the library is full", 507)
    monkeypatch.setattr(data, "backup_before_reset", broken)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/data/reset", json={"confirm": CONFIRM_WORD})
        assert r.status_code == 507 and "library is full" in r.text
        assert len(store_of(app)._all("SELECT 1 FROM messages")) == 2 and len(store_of(app)._all("SELECT 1 FROM memory_claims")) == 1
        assert "Busan" in (tmp_path / "home" / "MEMORY.md").read_text()


# ----------------------------------------------------------------------------------------------------------------- auth
@pytest.mark.parametrize("method,path,kw", [("GET", "/api/data", {}), ("GET", "/api/data/export", {}), ("POST", "/api/data/export", {}),
                                            ("GET", "/api/data/import/prompt", {}), ("GET", "/api/data/import", {}),
                                            ("POST", "/api/data/import/text", {"json": {"text": "x"}}),
                                            ("PUT", "/api/data/import/file", {"content": b"x"}), ("DELETE", "/api/data/import", {}),
                                            ("POST", "/api/data/reset", {"json": {"confirm": CONFIRM_WORD}})])
async def test_every_route_needs_the_token(tmp_path, method, path, kw):
    app = make_app(tmp_path)
    seed(app, tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.request(method, path, **kw)).status_code == 401
        assert (await c.request(method, path, headers={"Authorization": "Bearer wrong"}, **kw)).status_code == 401
    assert len(store_of(app)._all("SELECT 1 FROM messages")) == 2                  # an unsigned reset changed nothing
