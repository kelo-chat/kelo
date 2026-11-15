"""Plain text from a PDF, Word (.docx) or Excel (.xlsx/.xlsm) file the user uploaded.

`python -I -m assistant.documents <file> [--layout] [--sheets A,B]` prints the text as Markdown on stdout. tools/extract.py runs it
that way, in a child process with a time limit, so a broken or hostile file can never hang or crash the server.

- PDF: `pdftotext` (poppler, in the Docker image) when it is installed, else pypdf. One "## Page N" heading per page.
- Word: paragraphs in order (headings as Markdown headings), then each table as rows of cells separated by " | ".
- Excel: one "## <sheet>" heading per sheet and its rows (computed values, not formulas), cells separated by " | ".
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

MAX_ROWS = 5_000          # rows per sheet
MAX_PAGES = 500
MAX_CHARS = 2_000_000     # the whole text


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).replace("\n", " ").strip()


def pdf_text(path: Path, layout: bool = False) -> str:
    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(["pdftotext", "-enc", "UTF-8", *(["-layout"] if layout else []), "-l", str(MAX_PAGES), str(path), "-"],
                                 capture_output=True, timeout=90, check=True).stdout.decode("utf-8", "replace")
            pages = out.split("\f")
            return "\n\n".join(f"## Page {i}\n\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
        except (subprocess.SubprocessError, OSError):
            pass   # fall back to pypdf below
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages[:MAX_PAGES], 1):
        text = (page.extract_text(extraction_mode="layout") if layout else page.extract_text()) or ""
        if text.strip():
            parts.append(f"## Page {i}\n\n{text.strip()}")
    return "\n\n".join(parts)


def docx_text(path: Path) -> str:
    import docx
    d = docx.Document(str(path))
    lines: list[str] = []
    for p in d.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name if p.style is not None else "") or ""
        if style.startswith("Heading") and style[-1:].isdigit():
            lines.append("#" * min(int(style[-1]) + 1, 6) + " " + text)
        elif style == "Title":
            lines.append("# " + text)
        else:
            lines.append(text)
    for n, table in enumerate(d.tables, 1):
        lines.append(f"\n### Table {n}")
        for row in table.rows:
            lines.append(" | ".join(_cell(c.text) for c in row.cells))
    return "\n\n".join(lines) if not d.tables else "\n".join(lines)


def xlsx_text(path: Path, sheets: list[str] | None = None) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            if sheets and ws.title not in sheets:
                continue
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS:
                    rows.append(f"[{MAX_ROWS} rows shown; the sheet has more]")
                    break
                cells = [_cell(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    rows.append(" | ".join(cells))
            parts.append(f"## {ws.title}\n\n" + ("\n".join(rows) if rows else "(empty)"))
    finally:
        wb.close()
    return "\n\n".join(parts)


def extract(path: Path, *, layout: bool = False, sheets: list[str] | None = None) -> str:
    kind = path.suffix.lower()
    if kind == ".pdf":
        text = pdf_text(path, layout)
    elif kind == ".docx":
        text = docx_text(path)
    elif kind in (".xlsx", ".xlsm"):
        text = xlsx_text(path, sheets)
    else:
        raise ValueError(f"not a document this server reads: {path.name}")
    return text[:MAX_CHARS] + (f"\n[cut at {MAX_CHARS:,} characters]" if len(text) > MAX_CHARS else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--layout", action="store_true")
    ap.add_argument("--sheets", default="")
    a = ap.parse_args(argv)
    try:
        text = extract(Path(a.file), layout=a.layout, sheets=[s.strip() for s in a.sheets.split(",") if s.strip()] or None)
    except Exception as e:  # noqa: BLE001 - any parser failure is reported as one short line
        print(f"{type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
