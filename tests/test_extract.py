import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

from assistant.home import Home, HomeError
from assistant.server import Settings, create_app
from assistant.tools.extract import extract_tool, run_extraction

from test_server import AUTH, TOKEN, Running, scripted  # noqa: F401

CONTENT = Path(__file__).resolve().parents[1] / "content"


def make_docx(path):
    import docx
    d = docx.Document()
    d.add_heading("분기 보고서", 1)
    d.add_paragraph("매출은 지난 분기보다 12% 늘었습니다.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "항목", "금액", "광고비", "3,000,000"
    d.save(path)


def make_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["month", "revenue"]); ws.append(["Jan", 100]); ws.append(["Feb", 150])
    wb.create_sheet("Notes").append(["memo", "hidden-sheet-text"])
    wb.save(path)


def make_pdf(text: str) -> bytes:
    """A one-page PDF with a real cross-reference table (strict readers such as pypdf need one)."""
    stream = f"BT /F1 14 Tf 10 50 Td ({text}) Tj ET".encode() if text else b""
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream", b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + o + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer<</Root 1 0 R/Size %d>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


MIN_PDF = make_pdf("Hello PDF world")


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path / "home")


def upload(home, tmp_path, name, maker):
    src = tmp_path / name
    maker(src)
    return home.save_upload(name, src.read_bytes())


async def test_docx_and_xlsx_are_extracted_to_one_plain_text_file(home, tmp_path):
    d = upload(home, tmp_path, "report.docx", make_docx)
    r = await run_extraction(home, d)
    assert "notes/extracted/report.md" in r and "12% 늘었습니다" in r
    text = home.read("notes/extracted/report.md")
    assert text.startswith("# report.docx") and "## 분기 보고서" in text and "광고비 | 3,000,000" in text
    assert [p.name for p in (home.root / "notes" / "extracted").iterdir()] == ["report.md"]      # no manifest, pages or chunks
    x = upload(home, tmp_path, "sales.xlsx", make_xlsx)
    await run_extraction(home, x)
    text = home.read("notes/extracted/sales.md")
    assert "## Sales" in text and "Jan | 100" in text and "Feb | 150" in text and "hidden-sheet-text" in text
    await run_extraction(home, "uploads/sales.xlsx", sheets="Sales")
    assert "hidden-sheet-text" not in home.read("notes/extracted/sales.md") and "Jan" in home.read("notes/extracted/sales.md")


async def test_pdf_is_extracted_with_or_without_poppler(home, tmp_path, monkeypatch):
    p = home.save_upload("hello.pdf", MIN_PDF)
    await run_extraction(home, p)
    assert "Hello PDF world" in home.read("notes/extracted/hello.md") and "## Page 1" in home.read("notes/extracted/hello.md")
    from assistant import documents
    monkeypatch.setattr(documents.shutil, "which", lambda _n: None)              # pypdf alone (in-process call)
    assert "Hello PDF world" in documents.extract(home.upload_path("hello.pdf"))


async def test_only_uploaded_documents_can_be_extracted_and_nothing_leaves_the_area(home, tmp_path):
    secret = tmp_path / "secret.docx"
    make_docx(secret)
    for bad in ("../../secret.docx", str(secret), "notes/x.docx", "missing.docx", "", "uploads/../secret.docx"):
        with pytest.raises(HomeError):
            await run_extraction(home, bad)
    # an absolute path from another tool is reduced to its file name, then looked up in uploads/ only
    home.save_upload("report.docx", secret.read_bytes())
    assert "notes/extracted/report.md" in await run_extraction(home, "/home/user/workspace/report.docx")
    # a broken file is a clear error, never a traceback or a hang
    home.save_upload("broken.docx", b"this is not a zip")
    tool = extract_tool(home)
    assert (await tool.handler({"path": "broken.docx"})).startswith("error: could not read the document")


def test_uploads_accept_only_documents_never_overwrite_and_sanitize_names(home, monkeypatch):
    import assistant.home as home_mod
    monkeypatch.setattr(home_mod, "MAX_UPLOAD", 1000)
    assert home.save_upload("../../evil/내 문서 (1).pdf", b"%PDF") == "uploads/내 문서 (1).pdf"      # path part dropped, Korean kept
    assert home.save_upload("내 문서 (1).pdf", b"%PDF") == "uploads/내 문서 (1)-1.pdf"              # earlier upload kept
    assert home.save_upload("we!rd:na*me?.docx", b"PK") == "uploads/we_rd_na_me_.docx"
    for name, data in (("run.sh", b"x"), ("a.exe", b"x"), ("noext", b"x"), ("a.pdf", b""), ("a.pdf", b"x" * 1001)):
        with pytest.raises(HomeError):
            home.save_upload(name, data)
    assert sorted(u["path"] for u in home.uploads()) == sorted(["uploads/내 문서 (1)-1.pdf", "uploads/내 문서 (1).pdf", "uploads/we_rd_na_me_.docx"])
    assert not any(p.name == "evil" for p in home.root.parent.iterdir())              # the traversal did not create anything outside


def test_the_agent_cannot_write_into_uploads(home):
    with pytest.raises(HomeError):
        home.write("uploads/x.pdf", "x")


async def test_api_upload_then_extract_end_to_end(tmp_path, monkeypatch):
    import assistant.home as home_mod
    monkeypatch.setattr(home_mod, "MAX_UPLOAD", 1_000_000)
    src = tmp_path / "r.docx"
    make_docx(src)
    s = Settings(token=TOKEN, cdp_host=None, home_dir=str(tmp_path / "h"), content_dir=str(CONTENT))
    async with Running(create_app(s, tools=None, stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.put("/api/uploads/r.docx", content=src.read_bytes())).status_code == 401
        c.headers.update(AUTH)
        r = await c.put("/api/uploads/r.docx", content=src.read_bytes())
        assert r.json() == {"path": "uploads/r.docx", "ok": True}
        assert (await c.put("/api/uploads/evil.exe", content=b"MZ")).status_code == 422
        assert (await c.put("/api/uploads/big.pdf", content=b"x" * 1_000_100, headers={"cf-ray": "x"})).status_code == 413   # the limit applies through Cloudflare only
        assert [u["path"] for u in (await c.get("/api/uploads")).json()] == ["uploads/r.docx"]
        assert (await c.get("/api/files/uploads/r.docx")).status_code == 404                        # binaries are not served as text


async def test_extract_tool_is_offered_whenever_the_agent_has_files(tmp_path):
    seen = []

    async def fn(provider, request, **k):
        seen.append([t["function"]["name"] for t in request.tools])
        from assistant.providers import Done, TextDelta
        yield TextDelta("ok"); yield Done("stop")

    from test_server import read_events
    every = []
    for home in (str(tmp_path / "h"), None):
        s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=home, content_dir=str(CONTENT))
        app = create_app(s, stream_fn=fn)
        every.append({t.name for t in app.state.tools})
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
                await read_events(r)
    assert "extract_document" in every[0] and "extract_document" not in every[1]   # loadable with find_tools (tools on demand)
    assert "find_tools" in seen[0] and "extract_document" not in seen[0]


async def test_deleting_an_upload_removes_the_extracted_text_too(home, tmp_path):
    d = upload(home, tmp_path, "report.docx", make_docx)
    await run_extraction(home, d)
    assert (home.root / "notes" / "extracted" / "report.md").is_file()
    (home.root / "notes" / "extracted" / "report").mkdir()                 # what an older server left: a folder per document
    assert home.delete_upload("report.docx") is True
    assert not (home.root / "notes" / "extracted" / "report.md").exists() and not (home.root / "notes" / "extracted" / "report").exists()
    assert home.uploads() == []
    with pytest.raises(HomeError):
        home.delete_upload("report.docx")
    with pytest.raises(HomeError):
        home.delete_upload("../MEMORY.md")                         # only uploaded documents, only by file name


async def test_api_can_delete_an_upload(tmp_path):
    src = tmp_path / "r.docx"
    make_docx(src)
    s = Settings(token=TOKEN, cdp_host=None, home_dir=str(tmp_path / "h"), content_dir=str(CONTENT))
    async with Running(create_app(s, tools=None, stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.delete("/api/uploads/r.docx")).status_code == 401
        c.headers.update(AUTH)
        await c.put("/api/uploads/r.docx", content=src.read_bytes())
        assert (await c.delete("/api/uploads/r.docx")).json() == {"ok": True}
        assert (await c.delete("/api/uploads/r.docx")).status_code == 404 and (await c.get("/api/uploads")).json() == []


async def test_reading_an_uploaded_document_with_read_file_extracts_its_text(home, tmp_path):
    from assistant.tools.extract import extractor_for
    from assistant.tools.home_tools import home_tools
    d = upload(home, tmp_path, "report.docx", make_docx)
    tools = {t.name: t for t in home_tools(home, None, extractor_for(home))}
    for path in (d, "uploads/report.docx", "/tmp/hermes_agy_x/uploads/report.docx"):
        out = await tools["read_file"].handler({"path": path})
        assert "12% 늘었습니다" in out and "notes/extracted/report.md" in out, path
    assert (await tools["read_file"].handler({"path": "uploads/missing.docx"})).startswith("error: no uploaded document")
    # without the extractor the binary is simply refused, as before
    plain = {t.name: t for t in home_tools(home, None)}
    assert (await plain["read_file"].handler({"path": d})).startswith("error:")


def test_read_only_tools_are_marked(tmp_path):
    from assistant.tools.extract import extract_tool
    from assistant.tools.home_tools import home_tools
    h = Home(tmp_path / "h")
    marks = {t.name: t.read_only for t in home_tools(h, None)}
    assert marks == {"read_file": True, "write_file": False, "list_files": True, "view_image": True}
    assert extract_tool(h).read_only is True
