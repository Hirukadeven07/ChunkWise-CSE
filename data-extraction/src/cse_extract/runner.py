"""Extract every report in the input folder and record each result."""

from __future__ import annotations

import csv
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cse_extract.config import Config
from cse_extract.converter import OK_STATUSES, Converter
from cse_extract.reports import Report
from cse_extract.validate import Validation, page_ranges, validate_pdf

logger = logging.getLogger(__name__)

Validator = Callable[[Path], Validation]

MANIFEST_NAME = "manifest.csv"
MANIFEST_COLUMNS = (
    "report_id",
    "company",
    "year",
    "status",
    "pages",
    "seconds",
    "pages_per_second",
    "text_ratio",
    "image_only_pages",
    "sha256",
    "error",
    "finished_at",
)
VALIDATION_NAME = "validation.csv"
VALIDATION_COLUMNS = (
    "report_id",
    "company",
    "year",
    "outcome",
    "pages",
    "text_ratio",
    "image_only_pages",
    "duplicate_of",
    "sha256",
    "reason",
)

# Statuses a report can end up with in the manifest:
#   success, partial_success, failure  Docling's own result
#   error                              something crashed while converting
#   rejected                           failed validation (unreadable or mostly image-only)
#   duplicate                          the same file as a report that was already converted
FAILED_STATUSES = ("failure", "error")


@dataclass
class RunSummary:
    """What happened in one run, counted by status, plus skipped reports."""

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


def default_validator(config: Config) -> Validator | None:
    """The validator the config asks for, or None when validation is turned off."""
    if not config.validation.enabled:
        return None
    return lambda pdf: validate_pdf(pdf, config.validation)


def run_extraction(
    config: Config,
    reports: list[Report],
    converter: Converter,
    force: bool = False,
    validator: Validator | None = None,
) -> RunSummary:
    """Validate and convert each report unless its outputs already exist (or force is set).

    With a validator, each PDF is checked first: unreadable and mostly image-only reports are
    recorded as rejected, and a PDF identical to one already converted is recorded as a
    duplicate. Neither is sent to Docling.

    Every report that is not skipped gets one row in output_dir/manifest.csv, written as soon as
    it finishes, so an interrupted run loses nothing and can simply be started again. A report
    that fails is recorded and the run moves on to the next one.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = config.output_dir / MANIFEST_NAME
    converted = _converted_fingerprints(manifest_path)
    summary = RunSummary()
    total = len(reports)

    with _open_csv(manifest_path, MANIFEST_COLUMNS) as (manifest, writer):
        for number, report in enumerate(reports, 1):
            label = f"[{number}/{total}] {report.report_id}"
            if not force and is_done(report, config.output_dir):
                summary.counts["skipped"] += 1
                continue

            row = _process(report, config.output_dir, converter, validator, converted)
            writer.writerow(row)
            manifest.flush()

            summary.counts[row["status"]] += 1
            summary.pages += row["pages"] if row["status"] in OK_STATUSES else 0
            summary.seconds += row["seconds"] if row["status"] in OK_STATUSES else 0
            message = f"{label}: {row['status']}, {row['pages']} pages"
            if row["status"] in OK_STATUSES:
                message += f", {row['pages_per_second']} pages/s"
            if row["error"]:
                logger.warning("%s (%s)", message, row["error"])
            else:
                logger.info(message)

    return summary


def check_reports(config: Config, reports: list[Report], validator: Validator) -> Counter:
    """Validate every report without converting it, writing output_dir/validation.csv.

    Useful before a long GPU run, and as data for the exploratory analysis.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    counts: Counter = Counter()
    path = config.output_dir / VALIDATION_NAME
    path.unlink(missing_ok=True)
    with _open_csv(path, VALIDATION_COLUMNS) as (file, writer):
        for number, report in enumerate(reports, 1):
            result = _safe_validate(validator, report)
            duplicate_of = seen.get(result.sha256, "") if result.sha256 else ""
            if result.sha256 and not duplicate_of:
                seen[result.sha256] = report.report_id
            outcome = "duplicate" if duplicate_of and result.accepted else result.outcome
            counts[outcome] += 1
            writer.writerow(
                {
                    "report_id": report.report_id,
                    "company": report.company,
                    "year": report.year or "",
                    "outcome": outcome,
                    "pages": result.pages,
                    "text_ratio": result.text_ratio,
                    "image_only_pages": page_ranges(result.image_only_pages),
                    "duplicate_of": duplicate_of,
                    "sha256": result.sha256,
                    "reason": result.reason,
                }
            )
            file.flush()
            logger.info("[%d/%d] %s: %s", number, len(reports), report.report_id, outcome)
    return counts


def _process(
    report: Report,
    output_dir: Path,
    converter: Converter,
    validator: Validator | None,
    converted: dict[str, str],
) -> dict:
    row = {
        "report_id": report.report_id,
        "company": report.company,
        "year": report.year or "",
        "status": "error",
        "pages": 0,
        "seconds": 0,
        "pages_per_second": 0,
        "text_ratio": "",
        "image_only_pages": "",
        "sha256": "",
        "error": "",
        "finished_at": "",
    }
    if validator is not None:
        result = _safe_validate(validator, report)
        row.update(
            pages=result.pages,
            text_ratio=result.text_ratio,
            image_only_pages=page_ranges(result.image_only_pages),
            sha256=result.sha256,
        )
        original = converted.get(result.sha256) if result.sha256 else None
        if not result.accepted:
            row.update(status="rejected", error=result.reason)
        elif original and original != report.report_id:
            row.update(status="duplicate", error=f"same file as {original}")
        if row["status"] in ("rejected", "duplicate"):
            row["finished_at"] = _now()
            return row

    row.update(_convert(report, output_dir, converter))
    if row["status"] in OK_STATUSES and row["sha256"]:
        converted.setdefault(row["sha256"], report.report_id)
    row["finished_at"] = _now()
    return row


def _convert(report: Report, output_dir: Path, converter: Converter) -> dict:
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
        "status": status,
        "pages": pages,
        "seconds": round(seconds, 1),
        "pages_per_second": round(pages / seconds, 2) if seconds and pages else 0,
        "error": error,
    }


def _safe_validate(validator: Validator, report: Report) -> Validation:
    try:
        return validator(report.path)
    except Exception as exc:  # a broken file is recorded as unreadable, not a crash
        return Validation(sha256="", outcome="unreadable", reason=f"{type(exc).__name__}: {exc}")


def _converted_fingerprints(manifest_path: Path) -> dict[str, str]:
    """Fingerprints of reports earlier runs converted successfully, mapped to their report_id."""
    fingerprints: dict[str, str] = {}
    for row in _read_rows(manifest_path):
        if row.get("status") in OK_STATUSES and row.get("sha256"):
            fingerprints.setdefault(row["sha256"], row["report_id"])
    return fingerprints


def _read_rows(path: Path) -> Iterable[dict]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _open_csv(path: Path, columns: tuple[str, ...]):
    """Open a CSV log for appending, writing the header if the file is new.

    A log written by an older version with different columns is kept under a new name
    (for example manifest.old-1.csv) rather than mixed with the new rows.
    """
    if path.is_file() and path.stat().st_size:
        with open(path, newline="", encoding="utf-8") as file:
            header = next(csv.reader(file), [])
        if tuple(header) != columns:
            number = 1
            while (old := path.with_name(f"{path.stem}.old-{number}{path.suffix}")).exists():
                number += 1
            path.rename(old)
            logger.warning("%s had different columns; kept it as %s", path.name, old.name)
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        if new:
            writer.writeheader()
        yield file, writer
