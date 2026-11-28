"""Extract financial figures from the tables in Docling's output, by code (SRS FR08).

Small language models misread numbers, so figures are taken straight from the table structure
that Docling's TableFormer model recovered, never from generated text. For every table this:

1. rebuilds the grid of cells, including cells that span several rows or columns;
2. splits the header rows from the data rows, and reads each column's year (2025, 2024/25,
   31.03.2025...), entity (Group or Company) and unit (Rs., Rs. '000, Rs. Mn...);
3. parses every number the way annual reports print them: 1,234 and (1,234) for negatives, a
   dash for nil, percentages;
4. guesses which statement the table belongs to (profit or loss, financial position, cash
   flows, changes in equity) from the heading above it and the row labels;
5. picks out key figures such as revenue, profit before tax and total assets.

Everything keeps its page number, so a figure in a summary can be traced back to the report
(FR13) and checked against its source (FR12).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- numbers ---------------------------------------------------------------------------------

_DASHES = {"-", "–", "—", "−", "--"}
_NUMBER = re.compile(
    r"""^\(?\s*
        (?:rs\.?|lkr|usd|\$)?\s*          # optional currency
        ([-–−])?\s*                       # optional minus sign
        (\d{1,3}(?:[,\s]\d{3})+|\d+)      # digits, with thousand separators
        (\.\d+)?                          # decimals
        \s*\)?\s*(%)?\s*\)?$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Number:
    value: float
    percent: bool = False


def parse_number(text: str) -> Number | None:
    """Read a number as annual reports print it, or None if the text is not a number.

    >>> parse_number("(1,234.5)").value
    -1234.5
    """
    text = text.strip().replace(" ", " ")
    if not text:
        return None
    if text in _DASHES:
        return Number(0.0)
    match = _NUMBER.match(text)
    if not match:
        return None
    sign, digits, decimals, percent = match.groups()
    value = float(re.sub(r"[,\s]", "", digits) + (decimals or ""))
    negative = bool(sign) or (text.startswith("(") and text.rstrip("% ").endswith(")"))
    return Number(-value if negative else value, percent=bool(percent))


# --- years, entities and units -----------------------------------------------------------------

_FISCAL = re.compile(r"(?<!\d)(20\d{2})\s*[/\-]\s*(\d{2}|20\d{2})(?!\d)")
_DATE = re.compile(r"(?<!\d)\d{1,2}[./\-]\d{1,2}[./\-](20\d{2})(?!\d)")
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def header_year(text: str) -> int | None:
    """The year a column header refers to. A financial year such as 2024/25 counts as the year
    it ends in, matching how reports are named."""
    if match := _FISCAL.search(text):
        start, end = match.groups()
        return int(end) if len(end) == 4 else int(start[:2] + end)
    if match := _DATE.search(text):
        return int(match.group(1))
    years = _YEAR.findall(text)
    return int(years[-1]) if years else None


def is_year_cell(text: str) -> bool:
    """True for header cells such as '2025', '2024/25', '31.03.2025' or '2025 Rs. '000', which
    look like numbers but name a year."""
    compact = text.strip()
    if re.fullmatch(r"20\d{2}(\s*[/\-]\s*(\d{2}|20\d{2}))?", compact):
        return True
    return header_year(compact) is not None and parse_number(compact) is None


def entity_of(text: str) -> str:
    """'group', 'company' or '' for a column header."""
    lower = text.lower()
    if re.search(r"\b(group|consolidated)\b", lower):
        return "group"
    if re.search(r"\b(company|bank)\b", lower):
        return "company"
    return ""


_UNITS = (
    (re.compile(r"(?i)\bbn\b|\bbillion"), 1e9, "Rs. Bn"),
    (re.compile(r"(?i)\bmn\b|\bmillion"), 1e6, "Rs. Mn"),
    (re.compile(r"(?i)['’`‘]\s*000|\bthousands?\b|rs\s*\.?\s*000\b"), 1e3, "Rs. '000"),
    (re.compile(r"(?i)\b(rs|lkr|rupees)\b"), 1.0, "Rs."),
)


def unit_of(text: str) -> tuple[float, str] | None:
    """(multiplier, label) for the first unit mentioned in text, or None."""
    for pattern, multiplier, label in _UNITS:
        if pattern.search(text):
            return multiplier, label
    return None


# --- statements and key figures --------------------------------------------------------------

STATEMENT_HEADINGS = {
    "profit_or_loss": re.compile(r"(?i)profit or loss|income statement|comprehensive income|statement of income"),
    "financial_position": re.compile(r"(?i)financial position|balance sheet"),
    "cash_flows": re.compile(r"(?i)cash flows?"),
    "changes_in_equity": re.compile(r"(?i)changes in equity"),
}
STATEMENT_LABELS = {
    "profit_or_loss": ("revenue", "turnover", "gross profit", "cost of sales", "profit before tax",
                       "income tax", "profit for the year", "earnings per share", "finance cost",
                       "administrative expenses", "net interest income"),
    "financial_position": ("total assets", "total liabilities", "total equity", "non-current assets",
                           "current assets", "property, plant", "stated capital", "retained earnings",
                           "current liabilities", "trade and other payables"),
    "cash_flows": ("operating activities", "investing activities", "financing activities",
                   "cash and cash equivalents at", "net increase", "net decrease"),
    "changes_in_equity": ("balance as at", "total comprehensive income for the year",
                          "dividends paid", "transfer to"),
}

# metric -> (statement it normally comes from, pattern on the cleaned row label)
KEY_FIGURES: dict[str, tuple[str, re.Pattern]] = {
    name: (statement, re.compile(pattern))
    for name, statement, pattern in (
        ("revenue", "profit_or_loss", r"^(total )?(revenue|turnover|gross income)( from contracts with customers)?$"),
        ("net_interest_income", "profit_or_loss", r"^net interest income$"),
        ("gross_profit", "profit_or_loss", r"^gross profit$"),
        ("operating_profit", "profit_or_loss", r"^(operating profit|profit from operations|results from operating activities)$"),
        ("profit_before_tax", "profit_or_loss", r"^(profit|loss) before (income )?tax(ation)?$"),
        ("income_tax", "profit_or_loss", r"^(income tax( expense)?|tax expense|taxation)$"),
        ("profit_for_the_year", "profit_or_loss", r"^(net )?(profit|loss) for the (year|period)$"),
        ("basic_eps", "profit_or_loss", r"^(basic )?earnings per (ordinary )?share( basic)?$"),
        ("total_assets", "financial_position", r"^total assets$"),
        ("total_liabilities", "financial_position", r"^total liabilities$"),
        ("total_equity", "financial_position", r"^total (shareholders )?equity$"),
        ("cash_from_operations", "cash_flows", r"^net cash (generated )?(from|used in|from used in|used in from) operating activities$"),
        ("cash_at_end_of_year", "cash_flows", r"^cash and cash equivalents at (the )?end of (the )?(year|period)$"),
    )
}


def clean_label(text: str) -> str:
    """A row label in a form the key-figure patterns can match: lower case, without
    '(loss)', note numbers, punctuation or extra spaces."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"\((loss|losses|profit)\)\s*/?|/\s*\((loss|losses|profit)\)", " ", text)
    text = re.sub(r"\b(loss|profit)\s*/\s*(?=(loss|profit)\b)", "", text)
    text = re.sub(r"\(.*?\)", " ", text)  # anything else in brackets, e.g. (Note 12)
    text = re.sub(r"\bnotes?\s*\d+(\.\d+)*\b", " ", text)
    text = re.sub(r"\s+\d{1,2}(\.\d{1,2})*[a-z]?\s*$", "", text.strip())  # trailing note reference, e.g. 12.1
    text = re.sub(r"[^a-z0-9 ,]", " ", text).replace(",", " ")
    return re.sub(r"\s+", " ", text).strip()


# --- reading Docling tables --------------------------------------------------------------------


@dataclass
class Column:
    index: int
    header: str = ""
    year: int | None = None
    entity: str = ""
    unit: tuple[float, str] | None = None
    is_note: bool = False


@dataclass
class Table:
    """One table from a Docling document, as a grid of cell texts."""

    index: int
    page: int | None
    grid: list[list[str]]
    header_flags: list[list[bool]]
    context: str = ""  # heading, caption and nearby text above the table
    statement: str = "other"
    columns: list[Column] = field(default_factory=list)
    header_rows: int = 0


def load_tables(doc: dict[str, Any]) -> list[Table]:
    """Every table in a Docling document (the parsed JSON), in reading order."""
    texts = doc.get("texts") or []
    preceding = _text_before_tables(doc)
    tables = []
    for index, item in enumerate(doc.get("tables") or []):
        data = item.get("data") or {}
        rows, cols = int(data.get("num_rows") or 0), int(data.get("num_cols") or 0)
        if not rows or not cols:
            continue
        grid = [[""] * cols for _ in range(rows)]
        flags = [[False] * cols for _ in range(rows)]
        for cell in data.get("table_cells") or []:
            text = " ".join(str(cell.get("text", "")).split())
            for r in range(cell["start_row_offset_idx"], min(cell["end_row_offset_idx"], rows)):
                for c in range(cell["start_col_offset_idx"], min(cell["end_col_offset_idx"], cols)):
                    grid[r][c] = text
                    flags[r][c] = bool(cell.get("column_header"))
        captions = " ".join(
            _ref_text(texts, ref) for ref in item.get("captions") or []
        )
        prov = item.get("prov") or []
        tables.append(
            Table(
                index=index,
                page=prov[0].get("page_no") if prov else None,
                grid=grid,
                header_flags=flags,
                context=" ".join(filter(None, [preceding.get(item.get("self_ref", ""), ""), captions])),
            )
        )
    return tables


def _ref_text(texts: list, ref: dict) -> str:
    target = ref.get("$ref") or ref.get("cref") or ""
    if target.startswith("#/texts/"):
        number = int(target.rsplit("/", 1)[1])
        if number < len(texts):
            return texts[number].get("text", "")
    return ""


def _text_before_tables(doc: dict[str, Any], keep: int = 4) -> dict[str, str]:
    """For each table, the last few texts before it in reading order, which usually hold the
    statement's heading and its unit, e.g. 'Statement of Profit or Loss ... Rs. '000'."""
    texts = doc.get("texts") or []
    groups = doc.get("groups") or []
    recent: list[str] = []
    before: dict[str, str] = {}

    def walk(node: dict) -> None:
        for child in node.get("children") or []:
            ref = child.get("$ref") or child.get("cref") or ""
            kind, _, number = ref.removeprefix("#/").partition("/")
            if not number.isdigit():
                continue
            if kind == "texts" and int(number) < len(texts):
                text = texts[int(number)]
                if text.get("label") not in ("page_header", "page_footer") and text.get("text"):
                    recent.append(text["text"])
                    del recent[:-keep]
            elif kind == "tables":
                before[ref] = " ".join(recent)
            elif kind == "groups" and int(number) < len(groups):
                walk(groups[int(number)])

    walk(doc.get("body") or {})
    return before


def analyse_table(table: Table) -> Table:
    """Find the header rows, the columns' years, entities and units, and the statement."""
    rows, cols = len(table.grid), len(table.grid[0])
    label_col = _label_column(table.grid)

    # Header rows: leading rows flagged as column headers, or holding no number other than years.
    header_rows = 0
    for r in range(rows):
        values = [table.grid[r][c] for c in range(cols) if c != label_col and table.grid[r][c]]
        flagged = any(table.header_flags[r][c] for c in range(cols) if c != label_col)
        numeric = [v for v in values if parse_number(v) is not None and not is_year_cell(v)]
        if flagged or not numeric:
            header_rows = r + 1
        else:
            break
    if header_rows == rows:  # no data rows at all
        header_rows = 0
    table.header_rows = header_rows

    # A header cell stretched across every value column (a title, or "Rs. '000" for the whole
    # table) describes the table, not one column.
    value_cols = [c for c in range(cols) if c != label_col]
    banners = []
    for r in range(header_rows):
        texts = {table.grid[r][c] for c in value_cols}
        if len(value_cols) > 1 and len(texts) == 1 and next(iter(texts)):
            banners.append(next(iter(texts)))
    table.context = " ".join(filter(None, [table.context, *banners]))

    table_unit = unit_of(" ".join(banners)) or unit_of(table.context) or unit_of(
        " ".join(" ".join(row) for row in table.grid[:header_rows])
    )
    columns = []
    for c in range(cols):
        parts: list[str] = []
        for r in range(header_rows):
            text = table.grid[r][c]
            if text and text not in parts and (c == label_col or text not in banners):
                parts.append(text)
        header = " ".join(parts)
        columns.append(
            Column(
                index=c,
                header=header,
                year=header_year(header),
                entity=entity_of(header),
                unit=unit_of(header) or table_unit,
                is_note=c != label_col and bool(re.fullmatch(r"(?i)notes?|note no\.?", header.strip())),
            )
        )
    table.columns = columns
    table.statement = _statement(table, label_col)
    return table


def _label_column(grid: list[list[str]]) -> int:
    """The leftmost column that mostly holds text rather than numbers."""
    for c in range(len(grid[0])):
        cells = [row[c] for row in grid if row[c]]
        if cells and sum(parse_number(v) is None for v in cells) >= len(cells) / 2:
            return c
    return 0


def _statement(table: Table, label_col: int) -> str:
    labels = " ".join(row[label_col] for row in table.grid).lower()
    scores = {}
    for name, heading in STATEMENT_HEADINGS.items():
        score = 3 if heading.search(table.context) else 0
        score += sum(keyword in labels for keyword in STATEMENT_LABELS[name])
        scores[name] = score
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "other"


# --- figures -------------------------------------------------------------------------------------


@dataclass
class Figure:
    """One number from one table cell."""

    table: int
    page: int | None
    statement: str
    row: int
    label: str
    column_header: str
    year: int | None
    entity: str
    raw: str
    value: float
    unit: str
    multiplier: float
    value_lkr: float | None  # value in rupees, when the unit is a currency amount


def table_figures(table: Table) -> list[Figure]:
    """Every number in a table's data rows, with its row label and column details."""
    label_col = _label_column(table.grid)
    figures = []
    for r in range(table.header_rows, len(table.grid)):
        label = table.grid[r][label_col]
        if not label or parse_number(label) is not None:
            continue
        per_share = bool(re.search(r"(?i)per (ordinary )?share", label))
        ratio = bool(re.search(r"(?i)\bratio\b|\bcover\b|\btimes\b|%", label))
        for column in table.columns:
            if column.index == label_col or column.is_note:
                continue
            raw = table.grid[r][column.index]
            number = parse_number(raw) if raw else None
            if number is None:
                continue
            if column.year is None and column.header == "" and _looks_like_note(raw):
                continue
            if number.percent or "%" in column.header or "%" in label:
                multiplier, unit = 1.0, "%"
            elif ratio:
                multiplier, unit = 1.0, ""
            elif per_share:
                multiplier, unit = 1.0, "Rs."  # per-share amounts are never in thousands
            else:
                multiplier, unit = column.unit or (1.0, "")
            figures.append(
                Figure(
                    table=table.index,
                    page=table.page,
                    statement=table.statement,
                    row=r,
                    label=label,
                    column_header=column.header,
                    year=column.year,
                    entity=column.entity,
                    raw=raw,
                    value=number.value,
                    unit=unit,
                    multiplier=multiplier,
                    value_lkr=number.value * multiplier if unit.startswith("Rs") else None,
                )
            )
    return figures


def _looks_like_note(raw: str) -> bool:
    """Note references such as '12' or '5.3' in an unlabelled column next to the labels."""
    return bool(re.fullmatch(r"\d{1,2}(\.\d{1,2})?", raw.strip()))


@dataclass
class KeyFigure:
    metric: str
    year: int | None
    entity: str
    value: float
    unit: str
    value_lkr: float | None
    page: int | None
    table: int
    label: str
    statement: str


def key_figures(figures: list[Figure]) -> list[KeyFigure]:
    """The headline figures (revenue, profit before tax, total assets...), one per metric, year
    and entity. A match in the expected statement wins over one elsewhere, then the earliest
    table, which in annual reports is normally the primary statement rather than a note."""
    best: dict[tuple, tuple[int, Figure]] = {}
    for figure in figures:
        cleaned = clean_label(figure.label)
        for metric, (statement, pattern) in KEY_FIGURES.items():
            if not pattern.match(cleaned):
                continue
            rank = 0 if figure.statement == statement else 1
            key = (metric, figure.year, figure.entity)
            if key not in best or (rank, figure.table, figure.row) < (best[key][0], best[key][1].table, best[key][1].row):
                best[key] = (rank, figure)
    return [
        KeyFigure(metric, f.year, f.entity, f.value, f.unit, f.value_lkr, f.page, f.table, f.label, f.statement)
        for (metric, _, _), (_, f) in sorted(
            best.items(), key=lambda item: (list(KEY_FIGURES).index(item[0][0]), item[0][2], -(item[0][1] or 0))
        )
    ]


def extract_figures(json_path: Path) -> tuple[list[Figure], list[KeyFigure]]:
    """All figures and the key figures from one Docling JSON file."""
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    figures = [f for table in load_tables(doc) for f in table_figures(analyse_table(table))]
    return figures, key_figures(figures)


# --- writing the results ---------------------------------------------------------------------

FIGURES_NAME = "figures.csv"
KEY_FIGURES_NAME = "key_figures.csv"
FIGURE_COLUMNS = (
    "report_id", "company", "report_year", "table", "page", "statement", "row", "label",
    "column_header", "year", "entity", "raw", "value", "unit", "multiplier", "value_lkr",
)
KEY_FIGURE_COLUMNS = (
    "report_id", "company", "report_year", "metric", "year", "entity", "value", "unit",
    "value_lkr", "page", "table", "statement", "label",
)


def write_figures(reports: list, json_for, output_dir: Path, log=None) -> dict[str, int]:
    """Extract figures from each report's Docling JSON and write figures.csv and
    key_figures.csv to output_dir, replacing earlier versions.

    Args:
        reports: Reports to include (cse_extract.reports.Report).
        json_for: Function giving a report's Docling JSON path.
        output_dir: Where the two CSV files go.
        log: Optional function called with a progress message per report.

    Returns:
        Counts: reports read, reports without JSON yet, unreadable JSON files, figures and
        key figures written.
    """
    import csv

    counts = {"reports": 0, "not_extracted": 0, "unreadable": 0, "figures": 0, "key_figures": 0}
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / FIGURES_NAME, "w", newline="", encoding="utf-8") as all_file, open(
        output_dir / KEY_FIGURES_NAME, "w", newline="", encoding="utf-8"
    ) as key_file:
        all_writer = csv.DictWriter(all_file, fieldnames=FIGURE_COLUMNS)
        key_writer = csv.DictWriter(key_file, fieldnames=KEY_FIGURE_COLUMNS)
        all_writer.writeheader()
        key_writer.writeheader()
        for number, report in enumerate(reports, 1):
            path = json_for(report)
            if not path.is_file():
                counts["not_extracted"] += 1
                continue
            try:
                figures, keys = extract_figures(path)
            except (ValueError, KeyError, TypeError) as exc:  # damaged or unexpected JSON
                counts["unreadable"] += 1
                if log:
                    log(f"[{number}/{len(reports)}] {report.report_id}: could not read ({exc})")
                continue
            base = {"report_id": report.report_id, "company": report.company, "report_year": report.year or ""}
            for f in figures:
                all_writer.writerow({**base, **_row(f, FIGURE_COLUMNS)})
            for k in keys:
                key_writer.writerow({**base, **_row(k, KEY_FIGURE_COLUMNS)})
            counts["reports"] += 1
            counts["figures"] += len(figures)
            counts["key_figures"] += len(keys)
            if log:
                log(f"[{number}/{len(reports)}] {report.report_id}: {len(figures)} figures, {len(keys)} key figures")
    return counts


def _row(item: Any, columns: tuple[str, ...]) -> dict:
    values = {}
    for name in columns:
        if hasattr(item, name):
            value = getattr(item, name)
            values[name] = "" if value is None else (round(value, 6) if isinstance(value, float) else value)
    return values
