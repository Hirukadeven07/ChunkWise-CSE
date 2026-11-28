import csv
from pathlib import Path

from cse_extract.config import load_config
from cse_extract.reports import Report, find_reports
from cse_extract.runner import MANIFEST_NAME, outputs_for, run_extraction


def _manifest(output_dir: Path) -> list[dict]:
    with open(output_dir / MANIFEST_NAME, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _setup(config_file: Path):
    config = load_config(config_file)
    return config, find_reports(config.input_dir, config.exclude_dirs)


def test_outputs_mirror_the_input_folders(tmp_path: Path):
    report = Report("N/ODEL PLC/odelplc_2024", "ODEL PLC", 2024, tmp_path / "x.pdf")
    json_path, md_path = outputs_for(report, tmp_path / "out")
    assert json_path == tmp_path / "out/N/ODEL PLC/odelplc_2024.json"
    assert md_path == tmp_path / "out/N/ODEL PLC/odelplc_2024.md"


def test_extracts_every_report_and_logs_it(config_file: Path, fake_converter):
    config, reports = _setup(config_file)
    summary = run_extraction(config, reports, fake_converter)

    assert summary.counts["success"] == 3
    assert summary.pages == 30
    for report in reports:
        json_path, md_path = outputs_for(report, config.output_dir)
        assert json_path.read_text().startswith("{")
        assert md_path.read_text().startswith("# ")
    rows = _manifest(config.output_dir)
    assert [row["report_id"] for row in rows] == [r.report_id for r in reports]
    assert rows[0]["company"] == "NATIONS TRUST BANK PLC"
    assert rows[0]["year"] == "2023"
    assert rows[0]["status"] == "success"
    assert not list(config.output_dir.rglob("*.part"))


def test_second_run_skips_finished_reports(config_file: Path, fake_converter):
    config, reports = _setup(config_file)
    run_extraction(config, reports, fake_converter)
    fake_converter.calls.clear()

    summary = run_extraction(config, reports, fake_converter)

    assert fake_converter.calls == []
    assert summary.counts["skipped"] == 3
    assert len(_manifest(config.output_dir)) == 3


def test_force_redoes_finished_reports(config_file: Path, fake_converter):
    config, reports = _setup(config_file)
    run_extraction(config, reports, fake_converter)
    summary = run_extraction(config, reports, fake_converter, force=True)
    assert summary.counts["success"] == 3
    assert len(_manifest(config.output_dir)) == 6


def test_a_failed_or_crashing_report_does_not_stop_the_run(config_file: Path, fake_converter):
    config, reports = _setup(config_file)
    for name in ("broken_2022.pdf", "crash_2021.pdf"):
        (config.input_dir / "N/NATIONS TRUST BANK PLC" / name).write_bytes(b"")
    reports = find_reports(config.input_dir, config.exclude_dirs)

    summary = run_extraction(config, reports, fake_converter)

    assert summary.counts == {"success": 3, "failure": 1, "error": 1}
    rows = {row["report_id"].rsplit("/", 1)[1]: row for row in _manifest(config.output_dir)}
    assert rows["broken_2022"]["error"] == "no pages"
    assert rows["crash_2021"]["error"] == "RuntimeError: corrupt PDF"
    # failed reports leave no output, so the next run tries them again
    assert not list(config.output_dir.rglob("broken_2022.*"))
    fake_converter.calls.clear()
    run_extraction(config, reports, fake_converter)
    assert sorted(p.name for p in fake_converter.calls) == ["broken_2022.pdf", "crash_2021.pdf"]


def test_a_report_with_only_json_is_redone(config_file: Path, fake_converter):
    config, reports = _setup(config_file)
    run_extraction(config, reports, fake_converter)
    outputs_for(reports[0], config.output_dir)[1].unlink()
    fake_converter.calls.clear()

    run_extraction(config, reports, fake_converter)

    assert fake_converter.calls == [reports[0].path]


# --- validation before conversion -------------------------------------------------------

from pdfs import make_pdf, text_pdf  # noqa: E402

from cse_extract.runner import default_validator  # noqa: E402


def _run(config, converter, force=False):
    reports = find_reports(config.input_dir, config.exclude_dirs)
    return run_extraction(config, reports, converter, force=force, validator=default_validator(config))


def test_scanned_and_unreadable_reports_are_rejected_not_converted(config_file: Path, fake_converter):
    config, _ = _setup(config_file)
    folder = config.input_dir / "N/NATIONS TRUST BANK PLC"
    (folder / "scanned_2022.pdf").write_bytes(make_pdf([None, None, None]))
    (folder / "damaged_2021.pdf").write_bytes(b"%PDF-1.7 cut off")

    summary = _run(config, fake_converter)

    assert summary.counts == {"success": 3, "rejected": 2}
    assert sorted(p.name for p in fake_converter.calls) == [
        "nationstrustbankplc_2023.pdf", "nationstrustbankplc_2024.pdf", "sampathbankplc_2024.pdf"]
    rows = {row["report_id"].rsplit("/", 1)[1]: row for row in _manifest(config.output_dir)}
    assert rows["scanned_2022"]["status"] == "rejected"
    assert rows["scanned_2022"]["image_only_pages"] == "1-3"
    assert "OCR" in rows["scanned_2022"]["error"]
    assert "cannot open PDF" in rows["damaged_2021"]["error"]
    assert rows["nationstrustbankplc_2023"]["text_ratio"] == "1.0"
    assert len(rows["nationstrustbankplc_2023"]["sha256"]) == 64


def test_the_same_file_under_two_names_is_converted_once(config_file: Path, fake_converter):
    config, _ = _setup(config_file)
    folder = config.input_dir / "S/SAMPATH BANK PLC"
    (folder / "zz_copy_2024.pdf").write_bytes((folder / "sampathbankplc_2024.pdf").read_bytes())

    summary = _run(config, fake_converter)

    assert summary.counts == {"success": 3, "duplicate": 1}
    row = next(r for r in _manifest(config.output_dir) if r["report_id"].endswith("zz_copy_2024"))
    assert row["error"] == "same file as S/SAMPATH BANK PLC/sampathbankplc_2024"


def test_duplicates_are_caught_across_runs(config_file: Path, fake_converter):
    config, _ = _setup(config_file)
    _run(config, fake_converter)
    folder = config.input_dir / "S/SAMPATH BANK PLC"
    (folder / "zz_copy_2024.pdf").write_bytes((folder / "sampathbankplc_2024.pdf").read_bytes())
    fake_converter.calls.clear()

    summary = _run(config, fake_converter)

    assert summary.counts == {"skipped": 3, "duplicate": 1}
    assert fake_converter.calls == []


def test_force_does_not_mark_a_report_as_a_duplicate_of_itself(config_file: Path, fake_converter):
    config, _ = _setup(config_file)
    _run(config, fake_converter)
    assert _run(config, fake_converter, force=True).counts == {"success": 3}


def test_validation_can_be_turned_off(config_file: Path, fake_converter):
    config_file.write_text(config_file.read_text() + "validation: {enabled: false}\n")
    config, _ = _setup(config_file)
    (config.input_dir / "N/NATIONS TRUST BANK PLC/scanned_2022.pdf").write_bytes(make_pdf([None]))
    assert _run(config, fake_converter).counts == {"success": 4}


def test_an_old_manifest_with_other_columns_is_kept_aside(config_file: Path, fake_converter):
    config, _ = _setup(config_file)
    config.output_dir.mkdir(parents=True)
    (config.output_dir / "manifest.csv").write_text("report_id,status\nold,success\n")

    _run(config, fake_converter)

    assert (config.output_dir / "manifest.old-1.csv").read_text().startswith("report_id,status\n")
    assert [row["status"] for row in _manifest(config.output_dir)] == ["success"] * 3
