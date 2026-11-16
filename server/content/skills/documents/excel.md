# Excel (.xlsx): making a workbook

Read this before you make or change an .xlsx. Use `workroom_run` (Python). openpyxl writes formulas and formatting; pandas moves bulk data.

## Rules for every workbook

- Use formulas, not numbers worked out in Python. Write `ws["B10"] = "=SUM(B2:B9)"`, not the total. The sheet must update when its inputs change.
- Follow the user's spec literally: exact sheet names, exact column headers, the formulas they spelled out.
- Put every assumption in its own labeled cell. Point formulas at it: `=B5*(1+$B$6)`, never `=B5*1.05`.
- Give every formula cell a number format: `#,##0` for counts, `$#,##0` (unit in the header, e.g. `Revenue ($k)`) for money, `0.0%` for rates, `yyyy-mm-dd` for dates. Store a percentage as 0.15, not 15.
- Keep years as text ("2026"), so they are not shown as 2,026.
- Use a plain font (Arial) unless the user's file already has its own.
- Set column widths to fit the longest text, about 2 characters more. Freeze the header row: `ws.freeze_panes = "A2"`.
- Write a short note where the reader sees it: a legend row, or a cell comment, saying where a number came from. If the user gave it, say so.

## Formulas that calculate everywhere

- Use Excel 2007-era functions: SUMIFS, INDEX, MATCH, IFERROR, SUMPRODUCT, ROUND, COUNTIFS.
- Six newer functions work only with a prefix in openpyxl: `_xlfn.TEXTJOIN`, `_xlfn.CONCAT`, `_xlfn.IFS`, `_xlfn.SWITCH`, `_xlfn.MAXIFS`, `_xlfn.MINIFS`. Written without it, they show #NAME?.
- Do not use XLOOKUP, XMATCH, FILTER, SORT, UNIQUE or SEQUENCE. Use INDEX/MATCH for lookups. Sort and remove duplicates in Python before you write the cells.
- Quote sheet names with spaces in references: `='Assumptions Inputs'!$B$5`.
- Check each formula's range before you build the rest. Write two or three formulas, recalculate, and confirm they return the values you expect.

## Recalculate and check (required when the file has formulas)

openpyxl saves formulas without results. Other programs show those cells as empty until they are calculated. Run this in the same job, after the file is saved to `$OUT_DIR`:

```bash
python /opt/kelo-tools/office/recalc.py "$OUT_DIR/report.xlsx" 60
```

The script calculates a copy with LibreOffice and writes only the results into your file. It prints JSON:

- `status`: `success` or `errors_found`.
- `total_formulas`, `total_errors`.
- `error_summary`: the cells for each error type (#REF!, #DIV/0!, #VALUE!, #NAME?, #N/A, #NUM!, #NULL!).
- `not_computed`: formulas LibreOffice cannot evaluate (a function it lacks, or a link to another workbook). Those cells keep their old value. Tell the user.

Rules:

- Fix every cell the script names, then run it again. Do not hand over a file with `errors_found`.
- If the JSON has an `error` key, nothing was calculated. Read the message and fix it first.
- A clean check proves the formulas evaluate, not that they are right. Compare two or three results with a Python calculation.

To read the calculated values back, load the file again with `load_workbook(path, data_only=True)` and print a short summary (totals, a few rows), not the whole sheet.

## Editing an existing workbook

- Copy the uploaded file from `files_in` into `$OUT_DIR` and edit the copy. Never edit the input.
- Match its conventions: colors, fonts, layout. They override the rules above.
- Write only into the input cells. Leave the existing formulas as they are.
- Load with `load_workbook(path)` to keep formulas. Load with `data_only=True` only to read values. Never save a workbook loaded that way: its formulas become plain numbers.
- openpyxl drops text boxes, shapes and slicers when it saves. Check whether the file has any (look in `xl/drawings/` and `xl/slicers/` of the unzipped file). If it does, say which ones may be lost.
- A merged cell takes its value in the top-left cell only.

## Charts

- Use the chart code in the `work-room` skill (xlsxwriter, through pandas) for new charts.
- If you use openpyxl charts, set `chart.roundedCorners = False`. Set `overlay = False` on the legend and on each title. On a bar or line chart, set `chart.x_axis.delete = False` and `chart.y_axis.delete = False`. Without these, Excel can draw titles over the plot or hide the axes.

## Financial models

Only when the user asks for one, or the file already works that way.

- Blue text for typed-in inputs, black for formulas, green for links to another sheet.
- Negatives in parentheses: `$#,##0;($#,##0);-`. Zero shows as a dash.
- Guard any division that can be zero. Use the same formula in every period of a row.

## Reading a workbook

- `extract_document(path)` gives a text preview of each sheet. Use `sheets="Sheet1"` for one sheet.
- For formulas and values together, load the file twice: once normally (formulas), once with `data_only=True` (values after recalculation).

## Hand-over

- Name the file and each sheet, and give the key totals from the recalculated values.
- Say what you assumed, and any cell the check listed as `not_computed`.
