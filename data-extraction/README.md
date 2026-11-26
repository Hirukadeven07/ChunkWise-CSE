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

## Settings

`configs/default.yaml`; relative paths are resolved from the config file's folder.

| Setting | Meaning |
|---|---|
| `input_dir` | Folder of report PDFs, in company sub-folders |
| `output_dir` | Where the JSON, Markdown and `manifest.csv` go |
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
