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
