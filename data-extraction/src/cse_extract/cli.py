"""Command-line entry point: ``cse-extract list``, ``check``, ``run`` and ``figures``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from cse_extract.config import ConfigError, load_config
from cse_extract.converter import Converter, build_docling_converter
from cse_extract.figures import FIGURES_NAME, KEY_FIGURES_NAME, write_figures
from cse_extract.reports import find_reports, select
from cse_extract.runner import (
    FAILED_STATUSES,
    VALIDATION_NAME,
    Validator,
    check_reports,
    default_validator,
    is_done,
    outputs_for,
    run_extraction,
)


def main(
    argv: list[str] | None = None,
    converter: Converter | None = None,
    validator: Validator | None = None,
) -> int:
    """Run the CLI. Returns the process exit code.

    converter and validator replace Docling and the PDF checks, which the tests use to run
    without real PDFs.
    """
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        config = load_config(args.config)
        reports = select(find_reports(config.input_dir, config.exclude_dirs), args.only)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        reports = reports[: args.limit]

    if args.command == "list":
        done = sum(is_done(report, config.output_dir) for report in reports)
        per_year = Counter(report.year or "unknown" for report in reports)
        print(f"{len(reports)} reports in {config.input_dir} ({done} already extracted)")
        for year, count in sorted(per_year.items(), key=lambda item: str(item[0])):
            print(f"  {year}: {count}")
        return 0

    if args.command == "figures":
        counts = write_figures(
            reports,
            lambda report: outputs_for(report, config.output_dir)[0],
            config.output_dir,
            log=logging.getLogger("cse_extract").info,
        )
        print(
            f"done: {counts['figures']} figures and {counts['key_figures']} key figures from "
            f"{counts['reports']} reports ({counts['not_extracted']} not extracted yet, "
            f"{counts['unreadable']} unreadable)"
        )
        print(f"details: {config.output_dir / FIGURES_NAME} and {config.output_dir / KEY_FIGURES_NAME}")
        return 0

    validator = validator or default_validator(config)
    if args.command == "check":
        if validator is None:
            print("error: validation is turned off in the config (validation.enabled)", file=sys.stderr)
            return 2
        print(f"checking {len(reports)} reports")
        counts = check_reports(config, reports, validator)
        print(f"done: {', '.join(f'{n} {k}' for k, n in sorted(counts.items())) or 'no reports'}")
        print(f"details: {config.output_dir / VALIDATION_NAME}")
        return 0

    if converter is None:
        try:
            converter = build_docling_converter(config.docling)
        except ImportError:
            print('error: Docling is not installed; run pip install -e ".[docling]"', file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    print(f"{len(reports)} reports, writing to {config.output_dir}")
    summary = run_extraction(config, reports, converter, force=args.force, validator=validator)
    print(f"done: {summary.line()}")
    failed = sum(summary.counts[status] for status in FAILED_STATUSES)
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cse-extract", description="Extract CSE annual reports from PDF with Docling."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("list", "count the reports a run would cover"),
        ("check", "check every PDF for a text layer and duplicates, without converting"),
        ("run", "check and extract the reports"),
        ("figures", "read the financial figures from the extracted tables"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", required=True, help="path to the YAML config")
        command.add_argument(
            "--only",
            nargs="+",
            metavar="TEXT",
            help="only reports whose path contains any of these, e.g. --only NATIONS SAMPATH",
        )
        command.add_argument("--limit", type=int, help="stop after this many reports")
        if name == "run":
            command.add_argument(
                "--force", action="store_true", help="redo reports that are already extracted"
            )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
