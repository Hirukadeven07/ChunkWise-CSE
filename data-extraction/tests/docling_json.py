"""Build small documents in Docling's JSON format (DoclingDocument) for the tests."""

from __future__ import annotations

Cell = str | tuple[str, int]  # text, or (text, number of columns it spans)


def make_doc(*blocks: tuple) -> dict:
    """A Docling document from blocks in reading order.

    A block is ("heading", text), ("text", text) or ("table", rows, page, header_rows), where
    rows is a list of rows of cells and the first header_rows rows are flagged as column headers.
    A cell is its text, or (text, span) for a cell spanning several columns.
    """
    texts, tables, children = [], [], []
    for block in blocks:
        kind = block[0]
        if kind in ("heading", "text"):
            texts.append({
                "self_ref": f"#/texts/{len(texts)}",
                "label": "section_header" if kind == "heading" else "text",
                "text": block[1],
                "prov": [{"page_no": 1, "bbox": {}, "charspan": [0, len(block[1])]}],
            })
            children.append({"$ref": texts[-1]["self_ref"]})
        else:
            _, rows, page, header_rows = block
            tables.append(_table(len(tables), rows, page, header_rows))
            children.append({"$ref": tables[-1]["self_ref"]})
    return {
        "schema_name": "DoclingDocument",
        "texts": texts,
        "tables": tables,
        "groups": [],
        "body": {"self_ref": "#/body", "children": children},
    }


def _table(index: int, rows: list[list[Cell]], page: int, header_rows: int) -> dict:
    cells = []
    num_cols = max(sum(c[1] if isinstance(c, tuple) else 1 for c in row) for row in rows)
    for r, row in enumerate(rows):
        col = 0
        for cell in row:
            text, span = cell if isinstance(cell, tuple) else (cell, 1)
            cells.append({
                "text": text,
                "start_row_offset_idx": r,
                "end_row_offset_idx": r + 1,
                "start_col_offset_idx": col,
                "end_col_offset_idx": col + span,
                "row_span": 1,
                "col_span": span,
                "column_header": r < header_rows,
                "row_header": False,
                "row_section": False,
            })
            col += span
    return {
        "self_ref": f"#/tables/{index}",
        "label": "table",
        "prov": [{"page_no": page, "bbox": {}, "charspan": [0, 0]}],
        "captions": [],
        "data": {"table_cells": cells, "num_rows": len(rows), "num_cols": num_cols},
    }


INCOME_STATEMENT = (
    "table",
    [
        ["", "", ("Group", 2), ("Company", 2)],
        ["", "Note", "2025", "2024", "2025", "2024"],
        ["Revenue", "5", "1,234,567", "1,100,000", "800,000", "750,000"],
        ["Cost of sales", "", "(734,567)", "(700,000)", "(500,000)", "(480,000)"],
        ["Gross profit", "", "500,000", "400,000", "300,000", "270,000"],
        ["Profit/(loss) before tax", "6", "150,000", "(20,000)", "90,000", "60,000"],
        ["Income tax expense", "7.1", "(45,000)", "-", "(27,000)", "(18,000)"],
        ["Profit/(loss) for the year", "", "105,000", "(20,000)", "63,000", "42,000"],
        ["Basic earnings per share (Rs.)", "8", "4.50", "(0.86)", "2.70", "1.80"],
    ],
    112,
    2,
)

FINANCIAL_POSITION = (
    "table",
    [
        [("SENKADAGALA FINANCE COMPANY PLC", 3)],
        ["Rs. Mn", "As at 31.03.2025", "As at 31.03.2024"],
        ["Property, plant and equipment", "2,500.5", "2,400.0"],
        ["Total assets", "52,345.7", "48,000.2"],
        ["Total liabilities", "45,000.0", "41,500.0"],
        ["Total equity", "7,345.7", "6,500.2"],
    ],
    114,
    2,
)

CASH_FLOWS = (
    "table",
    [
        ["", "2024/25", "2023/24"],
        ["Net cash (used in)/generated from operating activities", "12,000", "(3,500)"],
        ["Net cash used in investing activities", "(4,000)", "(2,000)"],
        ["Cash and cash equivalents at the end of the year", "9,800", "1,800"],
    ],
    116,
    1,
)
