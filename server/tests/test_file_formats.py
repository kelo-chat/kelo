"""A user who asks for a file in some format gets that file in their files (Results), not a Markdown note.

The cause (2026-11-17, "Kelo seems to support saving files primarily in Markdown"): the one file tool the agent has, `write_file`, can only
write the agent's own notes: home.write refuses anything outside memory/ and notes/ (so files/ was refused) and refuses suffixes outside
.md .txt .yaml .json. The agent then wrote a note (.md). The documents skill is loaded on demand and its index line said "write new documents",
so nothing led it to `workroom_run` either. The fix: write_file keeps a text file (Deliverables.TEXT_SUFFIXES) in files/ as a result for the
user; Excel, Word, PowerPoint and PDF are made in the work room (checked in the real image: KELO_DOCKER_TESTS=1 below).
"""

import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from assistant.agent import run_agent
from assistant.deliverables import Deliverables, kind_of
from assistant.home import Home, Skills
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.tools.home_tools import home_tools

from test_server import AUTH, TOKEN, Running
from test_workroom import B64, AgentOnSocket

CONTENT = Path(__file__).resolve().parents[1] / "content"
# the six formats a user asks for, and the kind the app shows each as
SIX = {"budget.xlsx": "spreadsheet", "letter.docx": "document", "deck.pptx": "document", "report.pdf": "document", "people.csv": "spreadsheet",
       "notes.txt": "document"}


def tools_for(tmp_path, library=True):
    home = Home(tmp_path / "home")
    lib = Deliverables(home) if library else None
    return {t.name: t for t in home_tools(home, Skills(CONTENT / "skills"), library=lib)}, home, lib


# ------------------------------------------------------------------------------------------------ write_file keeps a text file for the user
async def test_write_file_keeps_csv_txt_and_json_in_the_users_files_and_never_over_an_earlier_one(tmp_path):
    tools, home, lib = tools_for(tmp_path)
    write = tools["write_file"].handler
    assert await write({"path": "files/people.csv", "content": "name,age\nAnn,30\n"}) == "saved files/people.csv (in the user's files)"
    assert await write({"path": "files/people.csv", "content": "name,age\nBob,31\n"}) == "saved files/people-1.csv (in the user's files)"
    assert await write({"path": "files/sub/a.json", "content": '{"a": 1}'}) == "saved files/a.json (in the user's files)"
    assert await write({"path": "files/list.txt", "content": "milk\n"}) == "saved files/list.txt (in the user's files)"
    assert sorted(r["name"] for r in lib.list()) == ["a.json", "list.txt", "people-1.csv", "people.csv"]
    assert lib.read_text("people.csv") == "name,age\nAnn,30\n"                    # the first one is untouched
    assert {r["name"]: r["kind"] for r in lib.list()}["people.csv"] == "spreadsheet"
    assert not (home.root / "notes").exists()                                      # nothing went to the agent's notes


@pytest.mark.parametrize("name", ["budget.xlsx", "letter.docx", "deck.pptx", "report.pdf", "photo.png", "noext", "files/.hidden"])
async def test_write_file_refuses_a_format_it_cannot_make_and_points_to_the_work_room(tmp_path, name):
    tools, home, lib = tools_for(tmp_path)
    out = await tools["write_file"].handler({"path": name if name.startswith("files/") else f"files/{name}", "content": "not really a file"})
    assert out.startswith("error:") and "workroom_run" in out and "documents skill" in out
    assert lib.list() == []


async def test_write_file_into_files_says_so_when_this_server_has_no_library(tmp_path):
    tools, home, _ = tools_for(tmp_path, library=False)
    assert (await tools["write_file"].handler({"path": "files/a.csv", "content": "a,b\n"})).startswith("error:")
    assert not (home.root / "files").exists()


async def test_write_file_still_keeps_the_agents_own_notes_and_refuses_other_places(tmp_path):
    tools, home, lib = tools_for(tmp_path)
    assert await tools["write_file"].handler({"path": "notes/plan.md", "content": "x"}) == "saved notes/plan.md"
    assert (await tools["write_file"].handler({"path": "other/plan.csv", "content": "x"})).startswith("error:")
    assert lib.list() == []


async def test_a_run_that_is_asked_for_a_csv_ends_with_the_file_in_the_library_not_a_note(tmp_path):
    tools, home, lib = tools_for(tmp_path)
    turns = iter([[ToolCall("c1", "write_file", '{"path":"files/expenses.csv","content":"item,amount\\ncoffee,4\\n"}'), Done("tool_calls")],
                  [TextDelta("Saved expenses.csv in your files."), Done("stop")]])

    async def stream(provider, request, **kw):
        for ev in next(turns):
            yield ev

    events = [e async for e in run_agent("p", "m", [{"role": "user", "content": "save my expenses as a csv"}], list(tools.values()), stream_fn=stream)]
    assert [r["name"] for r in lib.list()] == ["expenses.csv"] and lib.read_text("expenses.csv").startswith("item,amount")
    assert any(type(e).__name__ == "ToolResult" and "files/expenses.csv" in e.output for e in events)


# ------------------------------------------------------------------------------------------------ the steering text (the regression for the cause)
def test_the_skills_index_leads_every_requested_format_to_the_documents_skill():
    index = {e["id"]: e["description"] for e in Skills(CONTENT / "skills").index}
    for word in ("Excel", "Word", "PowerPoint", "PDF", "CSV"):
        assert word in index["documents"], (word, index["documents"])
    line = next(ln for ln in Skills(CONTENT / "skills").index_text().splitlines() if ln.startswith("- documents:"))
    assert "Excel" in line and "PowerPoint" in line


async def test_the_write_file_tool_text_tells_the_model_where_a_requested_file_goes(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    d = tools["write_file"].description
    assert "files/<name>" in d and "workroom_run" in d and "Excel, Word, PowerPoint and PDF" in d


def test_the_documents_skill_says_a_requested_file_never_goes_in_a_note():
    text = (CONTENT / "skills" / "documents" / "SKILL.md").read_text(encoding="utf-8")
    assert "not into a note" in text and "files/<name>" in text and "workroom_run" in text


# ------------------------------------------------------------------------------------------------ the six formats, through the normal path
async def test_each_of_the_six_formats_ends_as_a_file_in_the_users_files_through_a_chat_turn(tmp_path):
    made = {"n": 0}

    def fake_run(body):
        made["n"] += 1
        made["code"] = body["code"]
        files = [{"name": n, "bytes": 8, "b64": B64(b"data-" + n.encode()[:3])} for n in ("budget.xlsx", "letter.docx", "deck.pptx", "report.pdf")]
        return {"exit_code": 0, "stdout": "made 4 files\n", "stderr": "", "timed_out": False, "killed_for": "", "duration_s": 0.2,
                "files": files, "skipped": []}

    fake = AgentOnSocket({"/run": fake_run, "/list": lambda b: {"pip": [], "npm": []}})
    turns = iter([
        [ToolCall("c1", "read_skill", '{"name":"documents","file":"excel.md"}'),
         ToolCall("c2", "workroom_run", '{"language":"python","code":"# makes the xlsx, docx, pptx and pdf"}'), Done("tool_calls")],
        [ToolCall("c3", "write_file", '{"path":"files/people.csv","content":"name,age\\nAnn,30\\n"}'),
         ToolCall("c4", "write_file", '{"path":"files/notes.txt","content":"hello"}'), Done("tool_calls")],
        [TextDelta("All six are in your files."), Done("stop")],
    ])

    async def fn(provider, request, **kw):
        for ev in next(turns):
            yield ev

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False, workroom_dir=str(fake.dir), public_url="https://kelo.example.com")
    try:
        app = create_app(s, stream_fn=fn)
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            tid = (await c.post("/api/turns", json={"text": "make me a budget in Excel, a letter in Word, slides, a PDF, a CSV and a text file"})).json()["id"]
            after, done, events = 0, False, []
            for _ in range(100):
                r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 2})).json()
                events += r["events"]
                after, done = r["next"], r["done"]
                if done:
                    break
            assert done and made["n"] == 1
            lib = (await c.get("/api/files/library")).json()
            assert {f["name"]: f["kind"] for f in lib} == SIX
            assert not (tmp_path / "home" / "notes").exists() or not list((tmp_path / "home" / "notes").glob("*.md"))
    finally:
        fake.close()


def test_every_requested_format_is_a_known_kind_of_result():
    assert {n: kind_of(n) for n in SIX} == SIX
    assert {kind_of(n) for n in ("a.tsv", "b.xls", "c.rtf", "d.odt", "e.html")} == {"spreadsheet", "document"}


# ------------------------------------------------------------------------------------------------ the real image (opt-in)
@pytest.mark.skipif(os.environ.get("KELO_DOCKER_TESTS") != "1" or not shutil.which("docker"),
                    reason="needs Docker and the assistant-workroom:local image; set KELO_DOCKER_TESTS=1")
def test_real_work_room_makes_all_six_formats_with_the_shipped_helpers():
    code = r'''
import csv, os
out = os.environ["OUT_DIR"]
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active; ws.append(["a", "b"]); ws.append([1, 2]); ws["C2"] = "=A2+B2"; wb.save(out + "/budget.xlsx")
from docx import Document
d = Document(); d.add_heading("Hi", 1); d.add_paragraph("body"); d.save(out + "/letter.docx")
from pptx import Presentation
p = Presentation(); s = p.slides.add_slide(p.slide_layouts[0]); s.shapes.title.text = "T"; p.save(out + "/deck.pptx")
csv.writer(open(out + "/people.csv", "w", newline="")).writerows([["a", "b"], [1, 2]])
open(out + "/notes.txt", "w").write("hello")
os.system("cd " + out + " && soffice --headless --convert-to pdf letter.docx >/dev/null 2>&1 && mv letter.pdf report.pdf")
print(os.popen("python /opt/kelo-tools/office/recalc.py " + out + "/budget.xlsx 60").read())
'''
    out = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL", "--user", "1003:1050",
                          "--tmpfs", "/tmp", "--tmpfs", "/work:uid=1003,mode=700", "-e", "OUT_DIR=/work/out", "-e", "HOME=/tmp",
                          "--entrypoint", "sh", "assistant-workroom:local", "-c",
                          "mkdir -p /work/out && python3 -c 'import sys; exec(sys.stdin.read())' && echo ---LS--- && ls /work/out"],
                         input=code, capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr
    assert '"status": "success"' in out.stdout
    listed = set(out.stdout.split("---LS---", 1)[1].split())
    assert set(SIX) <= listed, listed
