# Data Extraction

Collects the annual reports of CSE-listed companies and extracts their text, tables and figures with Docling, as input for the rest of the ChunkWise CSE pipeline. Covers companies A-M (Maneesh) and N-Z (Madhushaney) for the last five years (2021-2026, or 2020-2025 where 2026 is not available).

Work happens on feature branches named `data-extraction/feature/...`, merged into `data-extraction/dev` by pull request. Stable releases go to `data-extraction/production`.

## What it does

`cse-extract` finds every PDF under the input folder and converts each one with Docling into:

- `<report>.json`: the full Docling document (headings, text blocks, tables, pictures, page numbers and positions), the input for chunking
- `<report>.md`: a Markdown version for reading

The outputs mirror the input folders, for example `Annual Reports/N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024.pdf` becomes `<output_dir>/N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024.json`.

Every attempted report gets a row in `manifest.csv` in the output folder with its status, page count and speed. Reports that already have both outputs are skipped, so an interrupted run can be started again, and a report that fails is logged without stopping the run. Folders named in `exclude_dirs` (such as `Interim`) are ignored.

## Running it

On a Colab GPU (about 1.5-2 pages per second on an L4): open `notebooks/extract_on_colab.ipynb` in Colab and follow the steps at the top. Until this code is merged into `data-extraction/dev`, set `BRANCH` in the notebook to the feature branch.

On your own computer (much slower without a GPU), with the PDFs in `data-extraction/data/Annual Reports` or another folder set in the config:

```bash
cd data-extraction
pip install -e ".[docling,dev]"
cse-extract list --config configs/default.yaml   # count the reports
cse-extract run --config configs/default.yaml    # extract them
```

`--only NATIONS SAMPATH` limits a run to reports whose path contains any of the given words, `--limit 5` stops after five reports, and `--force` redoes reports that are already extracted.

## Finding the key sections

After extraction, `cse-extract sections --config configs/default.yaml` finds where the key sections of each report start and end, so chunking and summarising can use just the parts they need (FR03, FR05). `--only` and `--limit` work as above. It reads each report's `<report>.json`, plus the original PDF if it is still in `input_dir`, and needs no GPU.

Sections looked for: chairman's message, CEO / managing director's review, management discussion and analysis, risk management, corporate governance, independent auditor's report, the statements of profit or loss, financial position, cash flows and changes in equity, and the notes to the financial statements.

How a section's start is found, in this order:

1. **PDF bookmarks**, when the PDF has them and is in `input_dir`. They are written by the company, so they are the most reliable. Only a minority of CSE reports have useful ones.
2. **Docling headings** (`title` and `section_header` items), matched against the different wordings reports use ("Chairman's Message", "Chairperson's Review", "Message from the Chairman", ...). Pages that mention three or more sections are treated as contents pages and ignored. A heading at the top of its page is preferred over an earlier one part-way down a page (such as an "Auditors' Report" paragraph inside the Directors' Report), and headings inside the notes to the financial statements (such as "27.2 Financial Risk Management") are not used for other sections.

A section ends on the page before the next section found starts (or on the same page if the next one starts part-way down it); the last one runs to the end of the report. A section found by bookmark ends earlier if the next bookmark of any kind (for example "Board of Directors") starts sooner. A section that is not found is recorded as missing, not as an error. Limits: a section without a heading or bookmark is not found, and a section found by its heading that is followed by pages that are not one of the sections above (for example "Board of Directors") is given those pages too.

It writes, next to each report's JSON:

`<report>.sections.json`

```json
{
  "report_id": "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024",
  "pages": 220,
  "has_bookmarks": false,
  "sections": [
    {"key": "chairman_message", "name": "Chairman's message", "found": true,
     "heading": "Message from the Chairman", "start_page": 8, "end_page": 11, "method": "heading"},
    {"key": "management_discussion", "name": "Management discussion and analysis", "found": false,
     "heading": null, "start_page": null, "end_page": null, "method": null}
  ],
  "blocks": [
    {"ref": "#/texts/0", "page": 1, "section": null},
    {"ref": "#/texts/57", "page": 8, "section": "chairman_message"},
    {"ref": "#/tables/3", "page": 150, "section": "financial_position"}
  ]
}
```

- `sections` always lists all eleven sections, in the order above. Pages are 1-based, like Docling's `prov.page_no`. `method` is `bookmark` or `heading`.
- `blocks` lists every text item and table of the Docling JSON in reading order. `ref` is the item's place in the Docling JSON (`#/texts/57` is `texts[57]`) and `section` is the key of the section it belongs to, or `null` if it comes before the first section or after a section's last page.

And in the output folder, `sections_summary.csv`: one row per report with `report_id`, `company`, `year`, `status` (`done`, `not_extracted` or `error`), `found` (for example `9/11`), and one column per section key saying how it was found (`bookmark` or `heading`), empty if it is missing. The file is rewritten on every run.

## Settings

`configs/default.yaml`; relative paths are resolved from the config file's folder.

| Setting | Meaning |
|---|---|
| `input_dir` | Folder of report PDFs, in company sub-folders |
| `output_dir` | Where the JSON, Markdown, `manifest.csv`, `.sections.json` files and `sections_summary.csv` go |
| `exclude_dirs` | Folder names to skip, case-insensitive |
| `docling.device` | `auto`, `cpu`, `cuda`, `mps` or `xpu` |
| `docling.ocr` | OCR for scanned pages; off, since scanned reports are out of scope |
| `docling.table_mode` | `accurate` or `fast` table structure recognition |
| `docling.layout_model` | A Docling layout preset such as `layout_egret_large`, or `null` for the default |

## Tests

```bash
pytest
```

The tests replace Docling with a stand-in converter, so they run in a second without Docling installed.
