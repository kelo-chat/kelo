# Word (.docx): making or editing a document

Read this before you make or change a .docx. Use `workroom_run` (Python with python-docx). Check the result by rendering it to pages.

## Making a new document

- Start from `Document()`. Set the page size on purpose: `section.page_width = Inches(8.5)`, `section.page_height = Inches(11)` for US Letter, and margins of 1 inch.
- Use real heading styles: `doc.add_heading("Scope", level=1)`, level 2 for sub-sections. Do not fake headings with bold text. Headings are what make a navigation pane, a contents list and a summary work.
- Set the body font on the Normal style. Set the Korean font too, or Korean text falls back to a default font:

```python
from docx.oxml.ns import qn
style = doc.styles["Normal"]
style.font.name = "Arial"
style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Noto Sans CJK KR")
```

- Bullets and numbers: use the styles "List Bullet" and "List Number". Do not type "•" or "1." yourself.
- Use short paragraphs. One idea per paragraph. Leave a blank line between sections through the style's spacing, not empty paragraphs.
- Page numbers in the footer, with a PAGE field:

```python
from docx.oxml import parse_xml
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
footer_par = doc.sections[0].footer.paragraphs[0]
footer_par._p.append(parse_xml(f'<w:fldSimple {W} w:instr="PAGE"/>'))
```

## Tables

- Use `doc.add_table(rows=..., cols=...)` with a built-in style such as "Table Grid" or "Light Grid Accent 1". Set the header row in bold.
- Set the widths: `table.autofit = False`, then `width` on every cell in each column (`cell.width = Inches(2.0)`). Column widths must add up to the table width.
- Repeat the header row on each page: add `<w:tblHeader/>` to the first row's `trPr`.
- Right-align numbers. Keep text left-aligned.
- Shade the header with a light fill, using `w:shd` with `w:val="clear"`.

## Contents list

- A real table of contents is a field that Word fills in. It needs a refresh, which a headless render does not do.
- Either add a TOC field (`TOC \o "1-3" \h \z \u`) and tell the user to update it (right-click, Update Field), or write a plain list of the headings with no page numbers.
- Say in the reply which one you made.

## Editing an existing document

- Copy the uploaded file from `files_in` to `$OUT_DIR`, open the copy with `Document(path)`, and save it under a new name. Never change the input.
- python-docx keeps everything it does not touch: styles, headers, images, tables. Do not rebuild the whole document to change a sentence.
- One phrase often sits in several runs (the text is split where the formatting or spelling marks change). Look for the phrase in `paragraph.text`, not in each run. To replace it, put the new text in the first run that holds part of the phrase and clear the rest. Keep the first run's formatting.
- Merge split text runs before editing XML: `python /opt/kelo-tools/office/merge_runs.py "$IN_DIR/doc.docx" -o "$OUT_DIR/merged.docx"`
- Match the document's own styles. Use the names it already has (`doc.styles`), not new ones.
- Text in a table cell: go through `table.rows[i].cells[j].paragraphs`.
- Images: `doc.inline_shapes` lists the ones in the body. Replace an image by deleting its paragraph and adding the new picture in its place.

## Tracked changes and comments

- To accept all tracked changes into a clean copy, run:

```bash
python /opt/kelo-tools/office/accept_changes.py "$IN_DIR/contract.docx" "$OUT_DIR/contract-clean.docx"
```

  LibreOffice does this. A paragraph whose text was all deleted can leave an empty bullet behind. Check the result.
- Add a comment to a document: `python /opt/kelo-tools/office/comment.py "$OUT_DIR/contract.docx" "Comment text" -o "$OUT_DIR/contract.docx"`
- python-docx cannot write tracked changes. If the user asks for them, offer a clean edit or a list of the changes in a note.

## Check the result (required)

Render the file to PDF and look at the pages. Keep the render out of `$OUT_DIR`, so it does not show up as a result:

```bash
soffice --headless --convert-to pdf --outdir "$TMPDIR" "$OUT_DIR/minutes.docx"
pdftoppm -png -r 80 -f 1 -l 3 "$TMPDIR/minutes.pdf" "$VIEW_DIR/page"
ls "$VIEW_DIR"
```

The page pictures in `$VIEW_DIR` come back to you: at most 8 per run, so render only the pages you need (`-f` and `-l`: the first, the last, any with a table). Look for: headings that did not take their style, tables running past the margin, text cut in a table cell, a page with only one line, a missing Korean glyph (boxes). Fix the file and render again.

To check the text and styles, read the file back with python-docx (in the same job, or with `extract_document` after the job):

```python
from docx import Document
for p in Document(path).paragraphs[:40]:
    print(p.style.name, "|", p.text)
```

- Validate XML structure and tracked changes: `python /opt/kelo-tools/office/validate.py "$OUT_DIR/minutes.docx" --original "$IN_DIR/minutes.docx" --author "Author"`

## Hand-over

- Name the file and say what it holds: the sections, the number of pages if you rendered it.
- If you put in a contents list or a TOC field, say so.
