---
name: work-room
description: Recipes for workroom_run: spreadsheets, Word, PowerPoint, charts, file and media conversion.
---

# Work Room Recipes

Your work room is a private Linux computer with no internet. Run code there with `workroom_run(language, code, files_in)`:

- `files_in`: the user's files to copy in (`uploads/<name>`, or `files/<name>` for an earlier result). The code finds them in `$IN_DIR`.
- Write every result to `$OUT_DIR`. Those files land in the user's library (`files/`, shown as Results in the app); tell the user the names.
- Print what you need to know (row counts, totals, errors). Output is cut when long, so print summaries, not whole tables.
- Each run starts empty: nothing from an earlier run is left except installed packages. Do the whole job in one run when you can.
- Time limit 60 s by default, `timeout_s` up to 300 for big conversions.
- Ready to use: Python 3.12 with numpy, pandas, scipy, matplotlib, seaborn, plotly, openpyxl, xlsxwriter, python-docx, python-pptx, pypdf, pillow; Node 22; LibreOffice (`soffice --headless`), ffmpeg, poppler (`pdftotext`, `pdftoppm`), ImageMagick (`convert`), jq, ripgrep, zip. Fonts cover Korean, Japanese, Chinese, emoji and most other scripts (Noto).
- Missing a Python or Node package? `workroom_install(kind="pip"|"npm", packages=[...], reason="...")`. The user approves it on a card. Never try to download anything from code: there is no network.

## Spreadsheet (xlsx)

```python
import os, pandas as pd
df = pd.read_csv(os.path.join(os.environ["IN_DIR"], "sales.csv"))
summary = df.groupby("month", as_index=False)["amount"].sum()
with pd.ExcelWriter(os.path.join(os.environ["OUT_DIR"], "sales-summary.xlsx"), engine="xlsxwriter") as xw:
    df.to_excel(xw, sheet_name="data", index=False)
    summary.to_excel(xw, sheet_name="summary", index=False)
    sheet = xw.sheets["summary"]
    chart = xw.book.add_chart({"type": "column"})
    chart.add_series({"categories": ["summary", 1, 0, len(summary), 0], "values": ["summary", 1, 1, len(summary), 1], "name": "amount"})
    sheet.insert_chart("D2", chart)
print(summary.to_string(index=False))
```

Reading an uploaded Excel file: `pd.read_excel(path, sheet_name=None)` gives every sheet. To keep formulas and formatting when editing, use openpyxl (`load_workbook`, change cells, `save` to `$OUT_DIR`).

## Chart (png)

```python
import os, matplotlib.pyplot as plt
plt.rcParams["font.family"] = "Noto Sans CJK KR"      # also covers Korean, Japanese and Chinese labels
fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
ax.plot(["Jan", "Feb", "Mar"], [120, 135, 160], marker="o")
ax.set_title("Monthly sales")
fig.tight_layout()
fig.savefig(os.path.join(os.environ["OUT_DIR"], "monthly.png"))
```

Interactive chart: plotly's `fig.write_html(os.path.join(os.environ["OUT_DIR"], "chart.html"), include_plotlyjs=True)` (one self-contained file).

## Word (docx)

```python
import os
from docx import Document
from docx.shared import Pt
doc = Document()
doc.styles["Normal"].font.name = "Noto Sans CJK KR"
doc.add_heading("Meeting notes", level=1)
doc.add_paragraph("Attendees: Alex, Sam")
table = doc.add_table(rows=1, cols=2, style="Light Grid Accent 1")
table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Item", "Decision"
doc.save(os.path.join(os.environ["OUT_DIR"], "minutes.docx"))
```

## PowerPoint (pptx)

```python
import os
from pptx import Presentation
from pptx.util import Inches
prs = Presentation()
s = prs.slides.add_slide(prs.slide_layouts[1])
s.shapes.title.text = "Q3 summary"
s.placeholders[1].text = "Revenue up 18%\n240 new customers"
pic = prs.slides.add_slide(prs.slide_layouts[6])
# pic.shapes.add_picture(path_to_png, Inches(0.5), Inches(0.5), width=Inches(9))
prs.save(os.path.join(os.environ["OUT_DIR"], "q3.pptx"))
```

## PDF

Make a PDF from a Word, Excel or PowerPoint file with LibreOffice (bash):

```bash
cd "$OUT_DIR"
soffice --headless --convert-to pdf --outdir "$OUT_DIR" "$IN_DIR/report.docx"
```

Also: `--convert-to docx` (from .doc/.odt/.rtf), `--convert-to xlsx` (from .xls/.ods/.csv). Text of a PDF: `pdftotext -layout "$IN_DIR/a.pdf" -`; pages as images: `pdftoppm -png -r 120 "$IN_DIR/a.pdf" "$OUT_DIR/page"`. Merge or split with pypdf (`PdfReader`, `PdfWriter`).

## Images

```bash
convert "$IN_DIR/photo.jpg" -resize 1600x1600\> -quality 85 "$OUT_DIR/photo-small.jpg"
convert "$IN_DIR/a.png" "$IN_DIR/b.png" +append "$OUT_DIR/side-by-side.png"
```

Pillow works too (`from PIL import Image`).

## Audio and video (ffmpeg)

```bash
ffmpeg -hide_banner -loglevel error -i "$IN_DIR/talk.m4a" -ar 44100 -b:a 128k "$OUT_DIR/talk.mp3"                 # audio format
ffmpeg -hide_banner -loglevel error -i "$IN_DIR/clip.mov" -vf "scale=-2:720" -c:v libx264 -crf 23 -c:a aac "$OUT_DIR/clip.mp4"   # smaller video
ffmpeg -hide_banner -loglevel error -ss 00:00:05 -t 10 -i "$IN_DIR/clip.mp4" -c copy "$OUT_DIR/cut.mp4"            # a 10 s cut
ffmpeg -hide_banner -loglevel error -i "$IN_DIR/clip.mp4" -vf "fps=10,scale=480:-1" "$OUT_DIR/clip.gif"              # GIF
```

Long videos: raise `timeout_s` (up to 300). Results over 20 MB are not saved; lower the size first.
