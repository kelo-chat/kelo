---
name: Documents
description: Read, summarize, compare or convert PDF, Word and Excel files, and write new documents.
---

# Documents

## Reading what the user uploaded
- Uploaded files are in `uploads/` (listed under "Your Files"). If the user mentions a document that is not there, ask them to attach it; do not guess its content.
- `extract_document(path)` returns a preview and saves the full text to `notes/extracted/<name>.md`. For a long file, read that text in parts with `read_file` and `offset`.
- PDF with columns or tables: `layout=true`. Excel: `sheets="Sheet1,Sheet2"` to read only some sheets.
- No text came out: it is a scan. Render a few of its pages into `$VIEW_DIR` in the work room (`pdftoppm`) and look at them if your model sees images; otherwise say it is a scan.

## Summaries and answers
- Start with what the user needs (the decision, the amount, the deadline), then the supporting points.
- Quote numbers, dates and names exactly as the document has them, with the page or sheet when it helps.
- Say what the document does not cover rather than filling gaps.

## Making or converting files
- Before you make a file, read its guide: `read_skill(name="documents", file="excel.md")`, or `word.md`, `powerpoint.md`, `pdf.md`.
- Make it in the work room with `workroom_run` (see the `work-room` skill). Pass the user's files in with `files_in` (`uploads/<name>` or `files/<name>`). Edit a copy, never the input.
- Write results to `$OUT_DIR`; they appear in the user's files. Write checks and intermediate files to `$TMPDIR`, so they do not appear as results. Render pictures you want to look at into `$VIEW_DIR`: they come back to you, not to the user's files.
- Helper scripts for these formats are in the work room; each guide shows how to call them. Do not install a package at run time to make a script work. If you need one, ask with `workroom_install`.

## Rules for every format
- Check before you hand over: recalculate (Excel) or render the pages into `$VIEW_DIR` and look at them (Word, PowerPoint, PDF). Fix what you find, then check again.
- Write the document in the user's language. Use their names, numbers and dates exactly as given, and mark any figure you assumed.
- For a substantial piece (a report, plan or proposal), draft it, then check it: is every claim supported or marked unknown, does it answer what they must decide or do, is it specific, is it easy to follow? Give only the final version, not the draft or the checks.
- Tell the user the file name and what is in it. Offer a share link (`share_file`) only when they want to send it to someone.
