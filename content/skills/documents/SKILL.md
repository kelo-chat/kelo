---
name: Documents
description: Read, summarize, compare or convert PDF, Word and Excel files, and write new documents.
---

# Documents

## Reading what the user uploaded
- Uploaded files are in `uploads/` (listed under "Your Files"). If the user mentions a document that is not there, ask them to attach it; do not guess its content.
- `extract_document(path)` returns a preview and saves the full text to `notes/extracted/<name>.md`. For a long file, read that text in parts with `read_file` and `offset`.
- PDF with columns or tables: `layout=true`. Excel: `sheets="Sheet1,Sheet2"` to read only some sheets.
- No text came out: it is a scan. Turn its pages into images in the work room (`pdftoppm`) and look at them with `view_image` if your model sees images; otherwise say it is a scan.

## Summaries and answers
- Start with what the user needs (the decision, the amount, the deadline), then the supporting points.
- Quote numbers, dates and names exactly as the document has them, with the page or sheet when it helps.
- Say what the document does not cover rather than filling gaps.

## Making or converting files
- Use the work room (`workroom_run`, see the `work-room` skill): Word with python-docx, Excel with openpyxl or pandas, PowerPoint with python-pptx, PDF with LibreOffice.
- Pass the user's files in with `files_in` (`uploads/<name>` or `files/<name>`). Write results to `$OUT_DIR`; they appear in the user's files.
- For a substantial piece (a report, plan or proposal), draft it, then check it: is every claim supported or marked unknown, does it answer what they must decide or do, does it say something specific rather than generic, is it easy to follow? Fix what fails and give only the final version, not the draft or the checks.
- Tell the user the file name and what is in it. Offer a share link (`share_file`) only when they want to send it to someone.
