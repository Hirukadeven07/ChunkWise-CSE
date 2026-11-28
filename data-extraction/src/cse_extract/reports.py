"""Find the annual report PDFs to extract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


@dataclass(frozen=True)
class Report:
    """One annual report PDF.

    Attributes:
        report_id: Path of the PDF relative to the input folder, without the extension,
            for example ``N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024``. Unique by
            construction, and used to name the outputs.
        company: Name of the folder that holds the PDF.
        year: Last 20xx year in the file name, or None if there is none.
        path: Full path to the PDF.
    """

    report_id: str
    company: str
    year: int | None
    path: Path


def find_reports(input_dir: Path, exclude_dirs: frozenset[str] = frozenset()) -> list[Report]:
    """List every PDF under input_dir, sorted by report_id.

    Folders whose names are in exclude_dirs (compared in lower case) are skipped at any depth.
    """
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input folder not found: {input_dir}")
    reports = []
    for path in input_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        rel = path.relative_to(input_dir)
        if any(part.lower() in exclude_dirs for part in rel.parts[:-1]):
            continue
        years = YEAR.findall(path.stem)
        reports.append(
            Report(
                report_id=str(PurePosixPath(*rel.with_suffix("").parts)),
                company=rel.parent.name if len(rel.parts) > 1 else "",
                year=int(years[-1]) if years else None,
                path=path,
            )
        )
    return sorted(reports, key=lambda report: report.report_id)


def select(reports: list[Report], only: list[str] | None) -> list[Report]:
    """Keep the reports whose report_id contains any of the given strings (case-insensitive)."""
    if not only:
        return reports
    needles = [text.lower() for text in only]
    return [report for report in reports if any(n in report.report_id.lower() for n in needles)]
