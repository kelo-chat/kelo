# PowerPoint (.pptx): making or editing a deck

Read this before you make or change a .pptx. Use `workroom_run` (Python with python-pptx). Check every slide by rendering it.

## Setting up

- `Presentation()` is 4:3. For a widescreen deck set the size first: `prs.slide_width = Inches(13.333)`, `prs.slide_height = Inches(7.5)`.
- Default layouts: 0 Title, 1 Title and Content, 5 Title Only, 6 Blank. Use a layout's placeholders for titles and bullets, so the text takes the theme's styles.
- Keep one look for the whole deck: one font pair, one title position, one accent color. Pick colors that fit the topic. Do not default to blue, and do not use a cream background unless the user asks for one.
- Arial and Times New Roman render at the same width in the room, so the render is reliable for them. Calibri, Cambria and Georgia have no matching font here: the render is only approximate, so leave about 10% extra room in their boxes. Korean text needs a Korean font in the run: `run.font.name = "Noto Sans CJK KR"`, or the user's own font.

## One idea per slide

- The title states the point: "Q3 revenue rose 18%", not "Revenue".
- Put at most about six lines on a slide. If a slide needs more, split it.
- A deck of more than a few slides has a short opening (what and why), the content, and a closing with next steps.
- Every content slide has a visual element: a chart, a table, an icon or a picture. A title and bullets alone are weak. Use the same visual system on every slide.
- Put the source of a number in a small line at the bottom of its slide.

## Size and fit

Sizes for a deck made from scratch:

| Element | Size |
|---|---|
| Title | 36 pt, bold |
| Body | 16–18 pt (never below 14 pt) |
| Caption, source line | 10–12 pt |

- Leave at least 0.5 inch from each edge. Leave at least 0.3 inch between blocks.
- python-pptx does not measure text. Size each box for its text: count the lines (about 60–70 characters per line at 16 pt on a 6-inch box) and make the box tall enough.
- Shrink-to-fit is applied only when PowerPoint next edits the text. Do not rely on it. Size the box, then check the render.
- Set `text_frame.word_wrap = True` on every text box.
- Set the margins to 0 only when the text must line up with a shape's edge.

## Adding content

```python
import os
from pptx import Presentation
from pptx.util import Inches
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

s = prs.slides.add_slide(prs.slide_layouts[5])          # Title Only
s.shapes.title.text = "Q3 revenue rose 18%"
data = CategoryChartData()
data.categories = ["Jul", "Aug", "Sep"]
data.add_series("Revenue ($k)", (120, 135, 160))
s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.8), Inches(11.5), Inches(4.8), data)
s.notes_slide.notes_text_frame.text = "Speaker note: the September jump came from two large renewals."
prs.save(os.path.join(os.environ["OUT_DIR"], "q3.pptx"))
```

- Prefer native charts (`add_chart`) to pictures of charts: the user can edit them.
- Tables: `shapes.add_table(rows, cols, left, top, width, height).table`, then set each cell's text and each `table.columns[i].width`.
- Pictures: `shapes.add_picture(path, left, top, width=...)`. Give the width only, so the height keeps the ratio.
- Speaker notes: `slide.notes_slide.notes_text_frame.text`, not a text box on the slide.
- Bullets come from the layout. Do not type "•" into the text.
- Keep the placeholders you use, and delete the ones you do not use. An empty placeholder shows "Click to add text" in the render.

## Editing an existing deck

- Copy the uploaded deck from `files_in` into `$OUT_DIR` and open the copy with `Presentation(path)`. Save under a new name. Never change the input.
- Use the deck's own layouts (`prs.slide_layouts`) and its theme. Do not add a new look.
- To change text in a shape, set `run.text` on the existing runs. Setting `text_frame.text` throws away the formatting of the paragraph.
- To delete a slide:

```python
slides = prs.slides._sldIdLst
item = list(slides)[2]                 # the third slide (0-based)
prs.part.drop_rel(item.rId)
slides.remove(item)
```

- To move a slide, move its `sldId` element inside `prs.slides._sldIdLst`.
- To duplicate a slide or add one from a layout, run `python /opt/kelo-tools/office/add_slide.py "$OUT_DIR/q3.pptx" slide2.xml -o "$OUT_DIR/q3.pptx"`.
- Clean unreferenced media and orphaned slides from an unpacked deck: `python /opt/kelo-tools/office/clean.py "$TMPDIR/unpacked"`.
- Template slots and source items must match: if the template has four cards and the content has three, remove the fourth card and its text, then check for an empty frame.

## Check the text and the render (required)

Read every slide's text back and look for leftover placeholder text (`TODO`, `Click to add`, `lorem`, `xxx`):

```python
for n, slide in enumerate(Presentation(path).slides, 1):
    texts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text]
    print(n, "|", " / ".join(t.replace("\n", " ") for t in texts))
```

Or run `python /opt/kelo-tools/office/slide_text.py "$OUT_DIR/q3.pptx"`. It prints each slide's text, tables, chart data and notes.
- Render a thumbnail grid of slides to check layouts: `python /opt/kelo-tools/office/thumbnail.py "$OUT_DIR/q3.pptx" "$VIEW_DIR/thumbnails"`

Then render the slides and look at each one. Keep the render out of `$OUT_DIR`:

```bash
soffice --headless --convert-to pdf --outdir "$TMPDIR" "$OUT_DIR/q3.pptx"
pdftoppm -png -r 80 -f 1 -l 4 "$TMPDIR/q3.pdf" "$VIEW_DIR/slide"
ls "$VIEW_DIR"
```

The slide pictures in `$VIEW_DIR` come back to you: at most 8 per run, so render only the slides you need (`-f` and `-l`: the first, the last, any with a chart). Look for: text cut off at a box edge or the slide edge, text running into a chart or a picture, a title that wraps onto a second line and hits the content, a word split across two lines, uneven gaps, and different title positions from slide to slide. Fix the code, then rebuild and render again.

## Hand-over

- Name the file and the number of slides, and list the slide titles in order.
- Say which numbers came from the user and which you took from the data.
