"""Extract every report in the input folder and record each result."""

from __future__ import annotations

import csv
import logging
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cse_extract.config import Config
from cse_extract.converter import Converter
from cse_extract.reports import Report

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.csv"
MANIFEST_COLUMNS = (
    "report_id",
    "company",
    "year",
    "status",
    "pages",
    "seconds",
    "pages_per_second",
    "error",
    "finished_at",
)


@dataclass
class RunSummary:
    """What happened in one run, counted by status (success, partial_success, failure,
    error or skipped)."""

    counts: Counter = field(default_factory=Counter)
    pages: int = 0
    seconds: float = 0.0

    def line(self) -> str:
        parts = [f"{count} {status}" for status, count in sorted(self.counts.items())]
        rate = f", {self.pages / self.seconds:.2f} pages/s" if self.seconds else ""
        return f"{', '.join(parts) or 'nothing to do'} ({self.pages} pages{rate})"


def outputs_for(report: Report, output_dir: Path) -> tuple[Path, Path]:
    """Where a report's JSON and Markdown go, mirroring its place in the input folder."""
    base = output_dir / Path(*report.report_id.split("/"))
    return base.with_name(base.name + ".json"), base.with_name(base.name + ".md")


def is_done(report: Report, output_dir: Path) -> bool:
    json_path, md_path = outputs_for(report, output_dir)
    return json_path.is_file() and md_path.is_file()


def run_extraction(
    config: Config,
    reports: list[Report],
    converter: Converter,
    force: bool = False,
) -> RunSummary:
    """Convert each report unless its outputs already exist (or force is set).

    Every attempted report gets one row in output_dir/manifest.csv, written as soon as it
    finishes, so an interrupted run loses nothing and can simply be started again. A report
    that fails is recorded and the run moves on to the next one.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    summary = RunSummary()
    total = len(reports)

    with _open_manifest(config.output_dir / MANIFEST_NAME) as (manifest, writer):
        for number, report in enumerate(reports, 1):
            label = f"[{number}/{total}] {report.report_id}"
            if not force and is_done(report, config.output_dir):
                summary.counts["skipped"] += 1
                continue

            row = _extract_one(report, config.output_dir, converter)
            writer.writerow(row)
            manifest.flush()

            summary.counts[row["status"]] += 1
            summary.pages += row["pages"]
            summary.seconds += row["seconds"]
            message = f"{label}: {row['status']}, {row['pages']} pages, {row['pages_per_second']} pages/s"
            if row["error"]:
                logger.warning("%s (%s)", message, row["error"])
            else:
                logger.info(message)

    return summary


def _extract_one(report: Report, output_dir: Path, converter: Converter) -> dict:
    json_path, md_path = outputs_for(report, output_dir)
    start = time.perf_counter()
    status, pages, error = "error", 0, ""
    try:
        conversion = converter(report.path)
        status, pages = conversion.status, conversion.pages
        if conversion.ok:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            # Write to temporary names first so a crash never leaves a half-written report
            # that a later run would mistake for a finished one.
            tmp_json, tmp_md = json_path.with_suffix(".json.part"), md_path.with_suffix(".md.part")
            conversion.document.save_as_json(tmp_json)
            conversion.document.save_as_markdown(tmp_md)
            tmp_json.replace(json_path)
            tmp_md.replace(md_path)
        error = "; ".join(conversion.errors)
    except Exception as exc:  # one bad PDF must not stop the whole run
        status, error = "error", f"{type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - start
    return {
        "report_id": report.report_id,
        "company": report.company,
        "year": report.year or "",
        "status": status,
        "pages": pages,
        "seconds": round(seconds, 1),
        "pages_per_second": round(pages / seconds, 2) if seconds and pages else 0,
        "error": error,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@contextmanager
def _open_manifest(path: Path):
    """Open the manifest for appending, writing the header if the file is new."""
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=MANIFEST_COLUMNS)
        if new:
            writer.writeheader()
        yield file, writer
