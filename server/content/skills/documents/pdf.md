# PDF: reading, making, merging, splitting and filling

Read this before you make, change or fill a PDF. The work room has pypdf (Python), LibreOffice and poppler (`pdftotext`, `pdftoppm`, `pdfimages`). It has no OCR engine and no table extractor. Intermediate files go to `$TMPDIR`, results to `$OUT_DIR`.

## Reading

- `extract_document(path)` gives the text. For a long file, read its saved text in parts with `read_file` and `offset`.
- Keep the layout for columns and tables: `pdftotext -layout "$IN_DIR/report.pdf" -`.
- One page or a page range: `pdftotext -f 3 -l 5 -layout "$IN_DIR/report.pdf" -`.
- Metadata: `PdfReader(path).metadata` gives title, author and subject.
- Tables: poppler has no table extractor. Read the `-layout` text and rebuild the columns in Python from the spacing. Check two or three rows against the page image. If exact tables matter and the room cannot do it, ask the user to approve `workroom_install(kind="pip", packages=["pdfplumber"], reason="...")`.
- No text came out: the file is a scan. The room has no OCR. Render a few pages (below), look at them, and say which parts you could read.

## Merge and split

```python
import os
from pypdf import PdfReader, PdfWriter

OUT = os.environ["OUT_DIR"]
writer = PdfWriter()
for name in ["a.pdf", "b.pdf"]:
    for page in PdfReader(os.path.join(os.environ["IN_DIR"], name)).pages:
        writer.add_page(page)
with open(os.path.join(OUT, "merged.pdf"), "wb") as f:
    writer.write(f)
```

- Split: one `PdfWriter` per page or per range. Name each output after its pages (`report-p1-3.pdf`).
- Results go to `$OUT_DIR`. Do not leave split parts there that the user did not ask for.

## Rotate, watermark, protect

- Rotate: `page.rotate(90)` turns a page clockwise, then `writer.add_page(page)`.
- Watermark: read the watermark as a one-page PDF, then for each page call `page.merge_page(watermark_page)` before adding it to the writer. Make the watermark page yourself with a LibreOffice conversion of a small .docx (see below).
- Password: `writer.encrypt("user-password", "owner-password")` before `writer.write`. Give the user the password once, in your reply. Do not save it in a file.

## Making a new PDF

The room has no PDF drawing library. Make the document as .docx (python-docx, see the `word` guide) or .pptx or .xlsx, save it in `$TMPDIR`, and convert it into `$OUT_DIR`:

```bash
soffice --headless --convert-to pdf --outdir "$OUT_DIR" "$TMPDIR/letter.docx"
```

- Use a font that has the glyphs you need. For Korean, use "Noto Sans CJK KR" in the document.
- Do not type Unicode subscript or superscript characters (₂, ²). Use the document's own super- and subscript formatting, or write the number plainly.
- Keep the source file in `$TMPDIR`, so only the PDF shows up as a result.
- The same command converts .pptx, .xlsx, .odt and .rtf files.

## Fillable forms

Check first whether the form has fields:

```bash
python /opt/kelo-tools/office/check_fillable_fields.py "$IN_DIR/form.pdf"
```

If it prints that the PDF has fillable fields:

1. List the fields:

```bash
python /opt/kelo-tools/office/extract_form_field_info.py "$IN_DIR/form.pdf" "$TMPDIR/fields.json"
```

   Each entry has `field_id`, `page` (1-based), `type` (`text`, `checkbox`, `radio_group` or `choice`) and `rect`. Checkboxes have `checked_value` and `unchecked_value`. Radio groups list `radio_options`; choice fields list `choice_options` with `value` and `text`.

2. Write `$TMPDIR/values.json` with one entry for each field you fill. Use the `field_id` and `page` from step 1:

```json
[
  {"field_id": "last_name", "page": 1, "value": "Kim"}
]
```

   For a checkbox, `value` is its `checked_value` from step 1. For a radio group, `value` is one of its option values.

3. Fill and save to `$OUT_DIR`:

```bash
python /opt/kelo-tools/office/fill_fillable_fields.py "$IN_DIR/form.pdf" "$TMPDIR/values.json" "$OUT_DIR/form-filled.pdf"
```

4. Check the result: read the values back with `PdfReader(path).get_fields()` and render the page (below). Look at the picture.

Do not guess a value for a field the user did not give you. Leave it empty and list it in your reply.

## Forms without fields

If `check_fillable_fields.py` says there are no fields, extract structure and overlay annotations:
- Extract labels, lines, and checkboxes from a non-fillable PDF: `python /opt/kelo-tools/office/extract_form_structure.py "$IN_DIR/form.pdf" "$TMPDIR/structure.json"`
- Check bounding boxes for overlaps and text height: `python /opt/kelo-tools/office/check_bounding_boxes.py "$TMPDIR/fields.json"`
- Render bounding boxes on a page image to verify coordinates: `python /opt/kelo-tools/office/create_validation_image.py 1 "$TMPDIR/fields.json" "$VIEW_DIR/page_1.png" "$VIEW_DIR/val_1.png"`
- Fill a non-fillable PDF with text annotations: `python /opt/kelo-tools/office/fill_pdf_form_with_annotations.py "$IN_DIR/form.pdf" "$TMPDIR/fields.json" "$OUT_DIR/form-filled.pdf"`

## Pictures and pages

- Pictures from a PDF: `pdfimages -j "$IN_DIR/a.pdf" "$TMPDIR/img"` (JPEG where possible). Copy only the images the user wants into `$OUT_DIR`.
- Pages as images, to look at them: `pdftoppm -png -r 80 -f 1 -l 2 "$IN_DIR/a.pdf" "$VIEW_DIR/page"`. The pictures in `$VIEW_DIR` come back to you: at most 8 per run, so choose the pages (`-f`, `-l`).
- Convert PDF pages to PNG images: `python /opt/kelo-tools/office/convert_pdf_to_images.py "$IN_DIR/form.pdf" "$VIEW_DIR"`

## Check (required)

- After any change, run `pdftotext -layout` on the output and read the part you changed.
- Render the changed pages into `$VIEW_DIR` and look at them.
- Confirm the page count: `len(PdfReader(path).pages)`.

## Hand-over

- Name each output file and its page count.
- For a filled form, list the fields you left empty and why.
