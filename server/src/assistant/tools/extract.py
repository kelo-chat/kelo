"""extract_document: the text of a PDF / Word / Excel file the user uploaded, saved as notes/extracted/<name>.md.

The work is done by assistant.documents in a child process (no shell, a time limit, a minimal environment), only on a file in
uploads/, writing only that one Markdown file.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from typing import Any

from ..agent import Ours, Tool
from ..agent.fence import data, marked
from ..data_trail import note_item
from ..home import Home, HomeError
from ..media import kind_of

DOCUMENTS = {".pdf", ".docx", ".xlsx", ".xlsm"}
TIMEOUT = 120.0
MAX_TEXT_BYTES = 20_000_000
PREVIEW = 2500


def extracted_path(stem: str) -> str:
    return f"notes/extracted/{stem}.md"


async def run_extraction(home: Home, name: str, *, layout: bool = False, sheets: str = "") -> str:
    src = home.upload_path(name)
    if src.suffix.lower() not in DOCUMENTS:
        if kind_of(src.name) != "image":
            raise HomeError(f"{src.name} is a {kind_of(src.name)} file the user keeps to watch or play; it has no text to read")
        raise HomeError(f"{src.name} is a photo, not a document: look at it with view_image(name={src.name!r})")
    cmd = [sys.executable, "-I", "-m", "assistant.documents", str(src)]
    if layout and src.suffix.lower() == ".pdf":
        cmd.append("--layout")
    if sheets.strip() and all(c.isalnum() or c in " ,_-" for c in sheets):
        cmd += ["--sheets", sheets.strip()]
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
    with tempfile.TemporaryDirectory(prefix="extract-") as tmp:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=tmp, env={**env, "HOME": tmp}, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), TIMEOUT)
        except TimeoutError:
            proc.kill()
            raise HomeError("reading the document took too long and was stopped") from None
    if proc.returncode != 0:
        raise HomeError("could not read the document: " + stderr.decode("utf-8", "replace").strip()[:300])
    if len(stdout) > MAX_TEXT_BYTES:
        raise HomeError("the document's text is too large")
    text = stdout.decode("utf-8", "replace").strip()
    rel = extracted_path(src.stem)
    home.write(rel, f"# {src.name}\n\n{text}\n", by="system")
    if not text:
        return (f"{src.name} has no text layer (a scan or pictures only). Saved an empty {rel}. To read a scan, convert its pages to "
                "images in the work room and look at them.")
    # the document's words are marked as external data (the agent loop fences them); the lines around them are ours
    return (f"Read {src.name} -> {rel} ({len(text):,} characters). The whole text: read_file {rel}.\nPreview:\n" + data(text[:PREVIEW])
            + ("\n[preview cut]" if len(text) > PREVIEW else ""))


def extractor_for(home: Home):
    """A coroutine `path -> text` that raises HomeError; used by extract_document, by read_file on uploads/ and by the app."""
    async def run(path: str, layout: bool = False, sheets: str = "") -> str:
        return await run_extraction(home, path, layout=layout, sheets=sheets)

    return run


def extract_tool(home: Home) -> Tool:
    run = extractor_for(home)

    async def handler(args: dict[str, Any]) -> str:
        try:
            got = await run(str(args.get("path", "")), bool(args.get("layout")), str(args.get("sheets") or ""))
        except HomeError as e:
            return Ours(f"error: {e}")
        note_item(str(args.get("path", "")), title=str(args.get("path", "")).rsplit("/", 1)[-1])
        return got if marked(got) else Ours(got)

    return Tool("extract_document",
                "Get the text of an uploaded PDF, Word or Excel file: saved to notes/extracted/<name>.md, a preview returned.",
                {"type": "object", "properties": {"path": {"type": "string", "description": "file name or uploads/<name>"},
                                                  "layout": {"type": "boolean", "description": "keep PDF columns side by side"},
                                                  "sheets": {"type": "string", "description": "comma-separated sheet names"}},
                 "required": ["path"]}, handler, read_only=True, untrusted=True, source="file", trail="file")
