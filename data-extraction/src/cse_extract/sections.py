"""Find the key sections of each annual report and tag every block with its section (FR03, FR05).

Two ways of finding where a section starts, tried in this order:

1. The PDF's bookmarks (its clickable outline), when it has them. The company wrote these
   itself, so they are the most reliable, but many reports have none.
2. The headings Docling found (``title`` and ``section_header`` items), matched against the
   different ways reports word each section ("Chairman's Message", "Chairperson's Review",
   "Message from the Chairman", ...). This works on every report.

A section ends where the next section found in the report starts, and the last one runs to
the end of the report. A section that cannot be found is recorded as missing, not an error.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from cse_extract.config import Config
from cse_extract.reports import Report
from cse_extract.runner import outputs_for

logger = logging.getLogger(__name__)

SUMMARY_NAME = "sections_summary.csv"


@dataclass(frozen=True)
class SectionType:
    """One kind of section we look for, and the headings that mark its start."""

    key: str
    name: str
    patterns: tuple[str, ...]

    def matches(self, heading: str) -> bool:
        text = normalise(heading)
        return any(re.search(pattern, text) for pattern in self.patterns)


# Patterns are matched against normalise(heading): lower case, no apostrophes, single spaces.
# The order matters: a heading belongs to the first type that matches it, so the more specific
# ones (notes, chairman) come before the ones whose words they contain.
_CHAIR = r"chair(?:man|person|woman)?"
_CEO = r"(?:ceo|chief executive(?: officer)?|(?:group )?managing director|md)"
_ROLE_TEXT = r"(?:message|review|statement|letter|address|report)"
SECTION_TYPES: tuple[SectionType, ...] = (
    SectionType(
        "notes",
        "Notes to the financial statements",
        (r"\bnotes to (?:the )?(?:consolidated )?financial statements\b",),
    ),
    SectionType(
        "chairman_message",
        "Chairman's message",
        (
            rf"\b{_CHAIR}s? {_ROLE_TEXT}\b",
            rf"\b{_ROLE_TEXT} (?:from|of|by) (?:the |our )?{_CHAIR}\b",
        ),
    ),
    SectionType(
        "ceo_review",
        "CEO / Managing Director's review",
        (
            rf"\b{_CEO}s? {_ROLE_TEXT}\b",
            rf"\b{_ROLE_TEXT} (?:from|of|by) (?:the |our )?{_CEO}\b",
        ),
    ),
    SectionType(
        "management_discussion",
        "Management discussion and analysis",
        (r"\bmanagements? discussion (?:and|&) analysis\b", r"^md ?& ?a$"),
    ),
    SectionType(
        "risk_management",
        "Risk management",
        (r"\brisk management\b", r"\bmanaging risks?\b"),
    ),
    SectionType(
        "corporate_governance",
        "Corporate governance",
        (r"\bcorporate governance\b", r"^governance(?: report)?$"),
    ),
    SectionType(
        "auditor_report",
        "Independent auditor's report",
        (
            r"\bindependent auditors? report\b",
            r"\breport of the (?:independent )?auditors?\b",
            r"^auditors? report\b",
        ),
    ),
    SectionType(
        "profit_or_loss",
        "Statement of profit or loss",
        (
            r"\bstatements? of (?:profit or loss|comprehensive income|income)\b",
            r"\bincome statements?\b",
        ),
    ),
    SectionType(
        "financial_position",
        "Statement of financial position",
        (r"\bstatements? of financial position\b", r"\bbalance sheets?\b"),
    ),
    SectionType(
        "cash_flows",
        "Statement of cash flows",
        (r"\bstatements? of cash flows?\b", r"\bcash flow statements?\b"),
    ),
    SectionType(
        "changes_in_equity",
        "Statement of changes in equity",
        (r"\bstatements? of changes in (?:shareholders )?equity\b",),
    ),
)

HEADING_LABELS = ("title", "section_header")
# A page where headings of this many different sections appear is a contents page, not the
# start of all those sections, so its headings are ignored.
CONTENTS_PAGE_MIN_SECTIONS = 3


def normalise(text: str) -> str:
    """Lower case, without apostrophes, numbering or extra spaces, so that small differences in
    how a heading is typed do not stop it matching."""
    text = text.lower().replace("’", "").replace("'", "").replace("`", "")
    text = re.sub(r"[^a-z0-9&]+", " ", text)
    text = re.sub(r"^\d+ ", "", text.strip())  # "3 Chairman's Message" -> "chairmans message"
    return text.strip()


def section_type_for(heading: str) -> SectionType | None:
    """The first section type whose patterns match this heading, if any."""
    for section_type in SECTION_TYPES:
        if section_type.matches(heading):
            return section_type
    return None


# ---------------------------------------------------------------------------------------------
# Reading the Docling JSON


@dataclass(frozen=True)
class Block:
    """One text item or table of the Docling document, in reading order."""

    ref: str  # for example "#/texts/12"
    label: str
    text: str
    page: int | None


def reading_order(doc: dict) -> list[Block]:
    """The document's text items and tables in reading order.

    body.children holds references such as {"$ref": "#/texts/12"}; groups (lists, sections)
    and other items can hold more references in their own children, so we follow them all.
    """
    blocks: list[Block] = []
    seen: set[str] = set()

    def visit(ref: str) -> None:
        if ref in seen:
            return
        seen.add(ref)
        item = _resolve(doc, ref)
        if item is None:
            return
        kind = ref.split("/")[1] if ref.count("/") >= 2 else ""
        if kind in ("texts", "tables"):
            blocks.append(
                Block(
                    ref=ref,
                    label=item.get("label", "table" if kind == "tables" else ""),
                    text=item.get("text", ""),
                    page=_first_page(item),
                )
            )
        for child in item.get("children", []):
            if isinstance(child, dict) and "$ref" in child:
                visit(child["$ref"])

    for child in doc.get("body", {}).get("children", []):
        if isinstance(child, dict) and "$ref" in child:
            visit(child["$ref"])
    return blocks


def page_count(doc: dict, blocks: list[Block]) -> int:
    """Number of pages, from the document's page list, or the last page any block is on."""
    pages = [int(number) for number in doc.get("pages", {})]
    pages += [block.page for block in blocks if block.page]
    return max(pages, default=0)


def _resolve(doc: dict, ref: str) -> dict | None:
    # "#/texts/12" -> doc["texts"][12]
    parts = ref.lstrip("#/").split("/")
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    items = doc.get(parts[0])
    index = int(parts[1])
    if not isinstance(items, list) or index >= len(items):
        return None
    return items[index]


def _first_page(item: dict) -> int | None:
    for prov in item.get("prov", []):
        if prov.get("page_no"):
            return int(prov["page_no"])
    return None


# ---------------------------------------------------------------------------------------------
# Finding where each section starts


@dataclass(frozen=True)
class Start:
    """Where one section starts, and how we found it."""

    key: str
    heading: str
    page: int
    method: str  # "bookmark" or "heading"
    position: int  # index in the reading order of the first block of the section


def bookmarks(pdf_path: Path) -> list[tuple[str, int]] | None:
    """The PDF's bookmarks as (title, 1-based page) pairs, or None if the PDF cannot be read.

    Bookmarks that do not point to a page are left out.
    """
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except ImportError:  # the package is a dependency, but the tests may run without it
        logger.warning("pypdfium2 is not installed, so PDF bookmarks are not used")
        return None
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:  # a PDF pdfium cannot open still has its Docling headings
        logger.warning("could not read bookmarks of %s: %s", pdf_path.name, exc)
        return None
    try:
        found = []
        for bookmark in pdf.get_toc():
            index = _bookmark_page_index(pdf, bookmark, pdfium_c)
            if index is not None:
                found.append((bookmark.get_title(), index + 1))
        return found
    finally:
        pdf.close()


def _bookmark_page_index(pdf, bookmark, pdfium_c) -> int | None:
    # A bookmark points to its page either directly (a "Dest") or through a "GoTo" action,
    # which is what many design tools write, so both are checked.
    dest = bookmark.get_dest()
    if dest is not None:
        return dest.get_index()
    action = pdfium_c.FPDFBookmark_GetAction(bookmark)
    if action:
        raw_dest = pdfium_c.FPDFAction_GetDest(pdf, action)
        if raw_dest:
            index = pdfium_c.FPDFDest_GetDestPageIndex(pdf, raw_dest)
            return index if index >= 0 else None
    return None


def starts_from_bookmarks(marks: list[tuple[str, int]], blocks: list[Block]) -> list[Start]:
    """The first bookmark that matches each section type."""
    starts: dict[str, Start] = {}
    for title, page in marks:
        section_type = section_type_for(title)
        if section_type is None or section_type.key in starts:
            continue
        starts[section_type.key] = Start(
            key=section_type.key,
            heading=title.strip(),
            page=page,
            method="bookmark",
            position=_first_position_on_or_after(blocks, page),
        )
    return list(starts.values())


def contents_pages(blocks: list[Block]) -> set[int]:
    """Pages that look like a contents page: they mention many different sections."""
    keys_per_page: dict[int, set[str]] = {}
    for block in blocks:
        if block.page is None or block.label == "table":
            continue
        section_type = section_type_for(block.text)
        if section_type is not None:
            keys_per_page.setdefault(block.page, set()).add(section_type.key)
    return {page for page, keys in keys_per_page.items() if len(keys) >= CONTENTS_PAGE_MIN_SECTIONS}


def starts_from_headings(blocks: list[Block], skip: set[str] = frozenset()) -> list[Start]:
    """The first Docling heading that matches each section type, ignoring contents pages and
    the section types in skip (already found another way)."""
    ignored_pages = contents_pages(blocks)
    starts: dict[str, Start] = {}
    for position, block in enumerate(blocks):
        if block.label not in HEADING_LABELS or block.page is None or block.page in ignored_pages:
            continue
        section_type = section_type_for(block.text)
        if section_type is None or section_type.key in skip or section_type.key in starts:
            continue
        starts[section_type.key] = Start(
            key=section_type.key,
            heading=block.text.strip(),
            page=block.page,
            method="heading",
            position=position,
        )
    return list(starts.values())


def _first_position_on_or_after(blocks: list[Block], page: int) -> int:
    for position, block in enumerate(blocks):
        if block.page is not None and block.page >= page:
            return position
    return len(blocks)


# ---------------------------------------------------------------------------------------------
# Putting it together


def detect_sections(doc: dict, pdf_path: Path | None = None) -> dict:
    """Find the sections of one Docling document and tag each block with its section.

    pdf_path is the original PDF, for its bookmarks; without it only headings are used.
    Returns the content of <report>.sections.json (see the README for the format).
    """
    blocks = reading_order(doc)
    pages = page_count(doc, blocks)

    marks = bookmarks(pdf_path) if pdf_path is not None and pdf_path.is_file() else None
    starts = starts_from_bookmarks(marks or [], blocks)
    found_by_bookmark = {start.key for start in starts}
    starts += starts_from_headings(blocks, skip=found_by_bookmark)
    starts.sort(key=lambda start: (start.page, start.position))

    # Each section ends on the page before the next section starts, or on that same page when
    # the next one starts part-way down it. The last one runs to the end of the report.
    ends: dict[str, int] = {}
    for current, following in zip(starts, starts[1:] + [None]):
        if following is None:
            ends[current.key] = pages
        else:
            ends[current.key] = max(current.page, following.page - 1)

    by_key = {start.key: start for start in starts}
    sections = []
    for section_type in SECTION_TYPES_IN_REPORT_ORDER:
        start = by_key.get(section_type.key)
        sections.append(
            {
                "key": section_type.key,
                "name": section_type.name,
                "found": start is not None,
                "heading": start.heading if start else None,
                "start_page": start.page if start else None,
                "end_page": ends[start.key] if start else None,
                "method": start.method if start else None,
            }
        )

    return {
        "pages": pages,
        "has_bookmarks": bool(marks),
        "sections": sections,
        "blocks": _tag_blocks(blocks, starts, ends),
    }


def _tag_blocks(blocks: list[Block], starts: list[Start], ends: dict[str, int]) -> list[dict]:
    """Give each block the section it falls in: the last section that started at or before it
    in reading order, as long as the block is not past that section's end page."""
    by_position = sorted(starts, key=lambda start: start.position)
    tagged = []
    current: Start | None = None
    upcoming = iter(by_position)
    next_start = next(upcoming, None)
    for position, block in enumerate(blocks):
        while next_start is not None and next_start.position <= position:
            current, next_start = next_start, next(upcoming, None)
        section = None
        if current is not None and (block.page is None or block.page <= ends[current.key]):
            section = current.key
        tagged.append({"ref": block.ref, "page": block.page, "section": section})
    return tagged


# The order sections are listed in, in the output: the usual order of an annual report.
_REPORT_ORDER = (
    "chairman_message",
    "ceo_review",
    "management_discussion",
    "risk_management",
    "corporate_governance",
    "auditor_report",
    "profit_or_loss",
    "financial_position",
    "cash_flows",
    "changes_in_equity",
    "notes",
)
SECTION_TYPES_IN_REPORT_ORDER = tuple(
    next(t for t in SECTION_TYPES if t.key == key) for key in _REPORT_ORDER
)


# ---------------------------------------------------------------------------------------------
# Running over every report


def sections_path(report: Report, output_dir: Path) -> Path:
    """<output_dir>/<LETTER>/<COMPANY>/<file>.sections.json, next to the report's JSON."""
    json_path, _ = outputs_for(report, output_dir)
    return json_path.with_name(json_path.stem + ".sections.json")


def run_sections(config: Config, reports: list[Report]) -> dict[str, int]:
    """Write <report>.sections.json for every extracted report, and a summary CSV of which
    sections were found in each report. Returns counts by status (done, not_extracted, error).
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["report_id", "company", "year", "status", "found"] + list(_REPORT_ORDER)
    counts = {"done": 0, "not_extracted": 0, "error": 0}

    with open(config.output_dir / SUMMARY_NAME, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for report in reports:
            row = {"report_id": report.report_id, "company": report.company, "year": report.year or ""}
            json_path, _ = outputs_for(report, config.output_dir)
            if not json_path.is_file():
                row["status"] = "not_extracted"
            else:
                try:
                    result = detect_sections(
                        json.loads(json_path.read_text(encoding="utf-8")), report.path
                    )
                    result = {"report_id": report.report_id, **result}
                    _write_json(sections_path(report, config.output_dir), result)
                    row["status"] = "done"
                    found = [s for s in result["sections"] if s["found"]]
                    row["found"] = f"{len(found)}/{len(result['sections'])}"
                    # The cell says how each section was found; empty means it is missing.
                    row.update({s["key"]: s["method"] or "" for s in result["sections"]})
                    logger.info("%s: %s sections found", report.report_id, row["found"])
                except Exception as exc:  # one bad file must not stop the run
                    row["status"] = "error"
                    logger.warning("%s: %s: %s", report.report_id, type(exc).__name__, exc)
            counts[row["status"]] += 1
            writer.writerow(row)
    return counts


def _write_json(path: Path, data: dict) -> None:
    # Write to a temporary name first so a crash never leaves a half-written file.
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
