"""Tools that let the agent read and write its own files and read skills."""

from __future__ import annotations

import asyncio
from pathlib import Path
from collections.abc import Callable
from typing import Any

from ..agent import Ours, Tool, ToolOutput
from ..agent.fence import data, marked
from .. import data_trail
from ..data_trail import note_item
from ..deliverables import DIR as LIBRARY_DIR
from ..deliverables import TEXT_SUFFIXES, Deliverables
from ..home import DOCUMENT_SUFFIXES, Home, HomeError, Skills
from ..photos import PhotoError, is_image, view_copy

NO_VISION = ("[The model you are running on cannot look at pictures, so the photo was NOT seen. Do not guess what it shows. Tell the user "
             "plainly, and that they can pick a model that sees images (for example Claude) in settings, or describe the photo in words.]")


READ_CAP = 12_000      # characters returned by one read
# where text from outside lives in the agent's area: the user's uploads, results saved from sites and programs, text read from uploads
EXTERNAL_DIRS = ("uploads/", "files/", "notes/extracted/")


def external_page(text: str) -> str:
    """A page of a file from outside (page() above): its text marked as external data, the pointer to the rest ours."""
    body, _, more = text.rpartition("\n[showing lines ")
    if body and more.endswith("for the rest]"):
        return data(body) + "\n[showing lines " + more
    return data(text)
LIST_LINES = 80        # rows returned by one listing
LIST_CHARS = 4_500


def page(text: str, offset: Any = None, cap: int = READ_CAP) -> str:
    """Return `cap` characters starting at line `offset` (1-based), with a pointer to the rest.

    Long tool results are spilled to a private file by some CLIs, which the model then cannot open here; short ones stay in
    the conversation. `offset` is a line number so it matches what CLIs send as StartLine.
    """
    lines = text.split("\n")
    try:
        start = max(1, int(offset)) if offset not in (None, "") else 1
    except (TypeError, ValueError):
        start = 1
    out, used, i = [], 0, start - 1
    while i < len(lines) and (used + len(lines[i]) + 1 <= cap or not out):
        out.append(lines[i][:cap])
        used += len(lines[i]) + 1
        i += 1
    if i < len(lines):
        out.append(f"[showing lines {start}-{i} of {len(lines)}; call again with offset={i + 1} for the rest]")
    return "\n".join(out)


def clip_listing(lines: list[str]) -> str:
    shown, used = [], 0
    for ln in lines[:LIST_LINES]:
        if used + len(ln) + 1 > LIST_CHARS:
            break
        shown.append(ln)
        used += len(ln) + 1
    more = len(lines) - len(shown)
    return "\n".join(shown) + (f"\n[{more} more entries not shown; narrow it with path, name or type]" if more > 0 else "")


HOW_TO = ("Call list_files with no path to see what you can read. Documents the user uploaded are under uploads/ and are read "
          "with read_file uploads/<name>.")
CLI_FILES = {"GEMINI.md", "AGENTS.md.orig", "CLAUDE.md"}   # rules files some CLIs load; their content is already in your context


def _is_upload(raw: str, rel: str) -> bool:
    return rel.startswith("uploads/") or "/uploads/" in raw.replace("\\", "/")


def home_tools(home: Home, skills: Skills | None, extractor: Any = None, catalog: Any = None, library: Deliverables | None = None,
               owner_file: Callable[[str], bool] | None = None) -> list[Tool]:
    """`owner_file(rel)`: True when an upload is a file the owner attached to their own chat message (chat_attachments.owner_attached):
    its text is given to the run, not outside text (the result is still fenced for the model: a document is data, never instructions)."""
    def own_upload(args: dict[str, Any]) -> bool:
        raw = str(args.get("path", ""))
        rel = home.relativize(raw)
        return bool(owner_file) and _is_upload(raw, rel) and owner_file(rel)

    async def read_file(args: dict[str, Any]) -> str:
        try:
            raw = str(args.get("path", ""))
            rel = home.relativize(raw)
            is_upload = _is_upload(raw, rel)
            if is_upload and is_image(raw):
                return Ours(f"{raw.rsplit('/', 1)[-1]} is a photo, not a text file: look at it with view_image.")
            if extractor and is_upload:
                got = await extractor(raw)  # "reading" an uploaded PDF / Word / Excel file means extracting its text
                note_item(rel, title=rel.rsplit("/", 1)[-1])
                return got if marked(str(got)) else Ours(got)   # the extracted text comes marked; a refusal is ours
            in_home = not rel.startswith("/") and (home.root / rel).is_file()   # (an absolute path may exist in the CLI's own folder)
            if raw.replace("\\", "/").rsplit("/", 1)[-1] in CLI_FILES and not in_home:
                return Ours("That is the rules file your command-line tool loads; its content is already in your context (your instructions). "
                            "Nothing to read there. " + HOW_TO + (f"\n\n{catalog()}" if catalog else ""))
            text = page(home.read(rel), args.get("offset"))
            note_item(rel, title=rel.rsplit("/", 1)[-1], kind="memory" if rel in ("MEMORY.md", "user.md") or rel.startswith("memory/") else
                      "notes" if rel.startswith("notes/") else "file")
            carry = data_trail.carried(home.root / rel)   # labels the file took when it was written (user.kelo_trail)
            for c in carry:
                if not c.get("outside"):
                    note_item(str(c.get("source_id") or rel), party=str(c.get("party") or ""), title=str(c.get("title") or ""), kind=str(c.get("kind") or ""))
            # a file from outside (an upload, a saved download, text read from an upload, a file written from outside text) is fenced;
            # the agent's own files are not
            return external_page(text) if is_upload or rel.startswith(EXTERNAL_DIRS) or any(c.get("outside") for c in carry) else Ours(text)
        except HomeError as e:
            return Ours(f"error: {e}. {HOW_TO}")

    async def write_file(args: dict[str, Any]) -> str:
        try:
            rel = home.relativize(str(args.get("path", "")))
            if rel.replace("\\", "/").lstrip("./").split("/")[0] == LIBRARY_DIR:
                return keep_for_user(rel, args.get("content", ""))
            saved = home.write(rel, args.get("content", ""))
            data_trail.mark_written(home.root / rel, str(args.get("content", "")))
            return f"saved {saved}"
        except HomeError as e:
            return f"error: {e}"

    def keep_for_user(rel: str, content: Any) -> str:
        """A text file written to files/ is a result for the user (their library), kept like one the work room made: never over an
        earlier file, and it is named as it was kept. Other formats are made in the work room."""
        suffix = Path(rel).suffix.lower()
        if library is None or suffix not in TEXT_SUFFIXES:
            raise HomeError(f"write_file saves only text files ({', '.join(sorted(TEXT_SUFFIXES))}) in files/; make {Path(rel).name or 'a file in another format'} "
                            "(Excel, Word, PowerPoint, PDF, ...) with workroom_run after reading the documents skill")
        if not isinstance(content, str):
            raise HomeError("content must be text")
        saved = library.save(Path(rel).name, content.encode("utf-8"))
        data_trail.mark_written(library.dir / saved, content)
        return f"saved {LIBRARY_DIR}/{saved} (in the user's files)"

    async def list_files(args: dict[str, Any]) -> str:
        try:
            raw = str(args.get("path", "") or "")
            rel = home.relativize(raw)
            rel = "" if rel.startswith("/") or rel.strip() in (".", "./") else rel  # "." / absolute path of another tool: the workspace root
            if args.get("recursive"):
                kind = args.get("type") if args.get("type") in ("f", "d") else None
                name = args["name"] if isinstance(args.get("name"), (str, list)) and args.get("name") else None
                depth = int(args.get("max_depth") or 4)
                rows = home.walk(rel, depth, kind, name)
            else:
                rows = home.list(rel)
        except HomeError as e:
            return f"error: {e}"
        lines = [f"{'d' if r['type'] == 'dir' else '-'} {r['path']}" + (f" ({r['bytes']} bytes)" if r["bytes"] is not None else "")
                 for r in rows]
        if rel == "" and not args.get("name") and args.get("type") != "f":
            ups = home.uploads()
            if ups:  # show the user's documents right at the top: that is what people ask about
                at = next((i for i, ln in enumerate(lines) if ln.startswith("d uploads")), len(lines))
                lines[at + 1:at + 1] = [f"- {u['path']} ({u['bytes']} bytes)  <- " + (f"photo from the user: view_image {u['path']}" if u["kind"] == "image"
                                        else f"{u['kind']} from the user (to watch or play in the app; no text)" if u["kind"] in ("video", "audio")
                                        else f"document from the user: read_file {u['path']}") for u in ups[:20]]
        out = clip_listing(lines) or "(empty)"
        # a model that lists its CLI's own workspace folder is looking for what it can do: say so
        return out + (f"\n\n{catalog()}" if catalog and raw.strip().startswith("/") else "")

    async def read_skill(args: dict[str, Any]) -> str:
        try:
            if not skills:
                return Ours("error: no skills available")
            text = page(skills.read(str(args.get("name", "")), args.get("file") or None), args.get("offset"))
            return text if marked(text) else Ours(text)   # an installed package's text comes marked (fenced); a built-in skill is ours
        except HomeError as e:
            return Ours(f"error: {e}")

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}
    tools = [
        Tool("read_file", "Read one of your files.",
             obj({"path": {"type": "string"}, "offset": {"type": "integer", "description": "line to go on from, for a long file"}}, ["path"]),
             read_file, read_only=True, untrusted=True, source="file", trail="file", own_data=own_upload),
        Tool("write_file", "Replace one of your own files with new full content; read it first and keep what is still true. Writable: "
             "CHECKLIST.md, notes/ (your notes, not something the user asked for). MEMORY.md, user.md, AGENTS.md, NOTIFY_PREFS.md and memory/ "
             "are kept by the memory pass from the user's own words: use `remember` for those. A text file the user asked for (.csv .txt "
             ".json ...): path files/<name>, kept in their files. Excel, Word, PowerPoint and PDF files: workroom_run (documents skill).",
             obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), write_file),
        Tool("list_files", "List your files (empty path: the top level).",
             obj({"path": {"type": "string"}, "recursive": {"type": "boolean"},
                  "max_depth": {"type": "integer", "description": "up to 6"}, "type": {"type": "string", "description": "f or d"},
                  "name": {"type": "string", "description": "glob"}}, []), list_files, read_only=True),
    ]
    async def view_image(args: dict[str, Any]) -> Any:
        try:
            path = home.upload_path(str(args.get("name") or args.get("path") or ""))
        except HomeError as e:
            return f"error: {e}"
        note_item(f"uploads/{path.name}", title=path.name)
        if not is_image(path.name):
            if path.suffix.lower() not in DOCUMENT_SUFFIXES:
                return f"error: {path.name} is a video or sound file, not a photo: it cannot be looked at"
            return f"error: {path.name} is a document, not a photo: read it with read_file uploads/{path.name}"
        try:
            url, w, h, sw, sh = await asyncio.to_thread(view_copy, path)
        except PhotoError as e:
            return f"error: {e}"
        return ToolOutput(f"uploads/{path.name}: a photo the user uploaded, {w}x{h} pixels (shown to you at {sw}x{sh}). The picture follows.",
                          [url], no_vision=NO_VISION)

    tools.append(Tool("view_image", "Look at a photo the user uploaded (you get the picture when your model can see images).",
                      obj({"name": {"type": "string", "description": "its file name in uploads/"}}, ["name"]), view_image, read_only=True, trail="photos"))

    async def search_skills(args: dict[str, Any]) -> str:
        if not skills:
            return "error: no skills available"
        found = skills.search(str(args.get("query", "")))
        own = "\n".join(f"- {e['id']}: {e['description']}" for e in found if e.get("source") != "installed")
        theirs = "\n".join(f"- {e['id']}: {e['description']}" for e in found if e.get("source") == "installed")
        if not theirs:
            return Ours(own or "No skill matches. Try other words, or read the Skills list.")
        # installed packages describe themselves in their authors' words: fenced
        return (own + "\n" if own else "") + "Installed skills (third-party descriptions):\n" + data(theirs)

    if skills and (skills.dynamic or skills.index):   # dynamic: installed skill packages may appear later (packages.py)
        tools.append(Tool("search_skills", "Find skills by words from the user's request.",
                          obj({"query": {"type": "string"}}, ["query"]), search_skills, read_only=True, untrusted=True, source="package"))
        tools.append(Tool("read_skill", "Read a skill by its id.",
                          obj({"name": {"type": "string"}, "file": {"type": "string", "description": "one of the extra files it names"},
                               "offset": {"type": "integer"}}, ["name"]), read_skill, read_only=True, untrusted=True, source="package"))
    return tools
