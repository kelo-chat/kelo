import httpx

from assistant.server import Settings, create_app

from test_extract import CONTENT, make_docx, make_xlsx
from test_server import AUTH, TOKEN, Running, scripted


def app_for(tmp_path, content=CONTENT):
    return create_app(Settings(token=TOKEN, cdp_host=None, home_dir=str(tmp_path / "h"), content_dir=str(content)), tools=None, stream_fn=scripted())


async def test_the_documents_page_sees_only_what_the_user_uploaded(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/uploads")).json() == []                  # a fresh assistant has the built-in skills, notes and reference files, but none of them is a document of the user's
        d = tmp_path / "r.docx"
        make_docx(d)
        await c.put("/api/uploads/r.docx", content=d.read_bytes())
        rows = (await c.get("/api/uploads")).json()
        assert [r["path"] for r in rows] == ["uploads/r.docx"] and rows[0]["bytes"] == d.stat().st_size and rows[0]["modified"] > 1_700_000_000


async def test_an_uploaded_document_can_be_read_in_the_app_and_the_text_is_kept_for_next_time(tmp_path):
    d = tmp_path / "r.docx"
    make_docx(d)
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/uploads/r.docx/text")).status_code == 401
        c.headers.update(AUTH)
        await c.put("/api/uploads/r.docx", content=d.read_bytes())
        assert not (tmp_path / "h" / "notes" / "extracted" / "r.md").exists()
        first = (await c.get("/api/uploads/r.docx/text")).json()
        assert first["name"] == "r.docx" and "분기 보고서" in first["text"] and "12%" in first["text"] and first["truncated"] is False
        text_file = tmp_path / "h" / "notes" / "extracted" / "r.md"
        assert text_file.is_file()
        text_file.write_text("# kept\n\nfrom the saved copy\n", encoding="utf-8")                      # the second call reads the saved text, it does not extract again
        assert "from the saved copy" in (await c.get("/api/uploads/r.docx/text")).json()["text"]
        x = tmp_path / "t.xlsx"
        make_xlsx(x)
        await c.put("/api/uploads/t.xlsx", content=x.read_bytes())
        assert "hidden-sheet-text" in (await c.get("/api/uploads/t.xlsx/text")).json()["text"]


async def test_only_uploads_can_be_asked_for_by_name_nothing_else_in_the_area_is_reachable(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for name in ("MEMORY.md", "..%2FMEMORY.md", "notes", "nothing.pdf", "../../etc/passwd", "%2e%2e%2fMEMORY.md"):
            assert (await c.get(f"/api/uploads/{name}/text")).status_code == 404, name
            assert (await c.get(f"/api/uploads/{name}/file")).status_code == 404, name


async def test_the_original_can_be_downloaded_but_never_shown_inline(tmp_path):
    d = tmp_path / "보고서 1.docx"
    make_docx(d)
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/uploads/r.docx/file")).status_code == 401
        c.headers.update(AUTH)
        saved = (await c.put("/api/uploads/" + d.name, content=d.read_bytes())).json()["path"].split("/")[-1]
        r = await c.get(f"/api/uploads/{saved}/file")
        assert r.status_code == 200 and r.content == d.read_bytes()
        assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert r.headers["content-disposition"].startswith("attachment") and r.headers["x-content-type-options"] == "nosniff"


async def test_a_document_without_a_text_layer_reads_as_empty_instead_of_failing(tmp_path):
    from test_extract import make_pdf
    blank = make_pdf("")
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/uploads/scan.pdf", content=blank)
        r = await c.get("/api/uploads/scan.pdf/text")
        assert r.status_code == 200 and r.json()["text"].strip() == "# scan.pdf"
        assert (await c.get("/api/uploads/scan.pdf/file")).status_code == 200                       # the download still works


async def test_the_version_is_shown_to_signed_in_devices_only(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/about")).status_code == 401
        v = (await c.get("/api/about", headers=AUTH)).json()["version"]
        assert v and v[0].isdigit() or v == "dev"


