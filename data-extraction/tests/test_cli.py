import csv
from pathlib import Path

from pdfs import make_pdf, text_pdf

from cse_extract.cli import main


def test_list_counts_reports_by_year(config_file: Path, capsys):
    assert main(["list", "--config", str(config_file)]) == 0
    out = capsys.readouterr().out
    assert "3 reports" in out
    assert "(0 already extracted)" in out
    assert "2024: 2" in out


def test_run_extracts_and_reports_progress(config_file: Path, fake_converter, capsys):
    code = main(["run", "--config", str(config_file)], converter=fake_converter)
    assert code == 0
    assert "done: 3 success (30 pages" in capsys.readouterr().out
    main(["list", "--config", str(config_file)])
    assert "(3 already extracted)" in capsys.readouterr().out


def test_only_and_limit_narrow_the_run(config_file: Path, fake_converter):
    main(["run", "--config", str(config_file), "--only", "sampath"], converter=fake_converter)
    assert [p.name for p in fake_converter.calls] == ["sampathbankplc_2024.pdf"]
    fake_converter.calls.clear()
    main(["run", "--config", str(config_file), "--limit", "1", "--force"], converter=fake_converter)
    assert len(fake_converter.calls) == 1


def test_failed_reports_give_exit_code_1(config_file: Path, report_dir: Path, fake_converter):
    (report_dir / "S/SAMPATH BANK PLC/broken_2023.pdf").write_bytes(text_pdf(marker="broken"))
    assert main(["run", "--config", str(config_file)], converter=fake_converter) == 1


def test_bad_config_gives_exit_code_2(tmp_path: Path, capsys):
    assert main(["list", "--config", str(tmp_path / "missing.yaml")]) == 2
    assert "config file not found" in capsys.readouterr().err


def test_run_rejects_scanned_reports_without_failing(config_file: Path, report_dir: Path, fake_converter, capsys):
    (report_dir / "S/SAMPATH BANK PLC/scanned_2022.pdf").write_bytes(make_pdf([None, None, None]))
    assert main(["run", "--config", str(config_file)], converter=fake_converter) == 0
    assert "1 rejected, 3 success" in capsys.readouterr().out
    assert "scanned_2022.pdf" not in [p.name for p in fake_converter.calls]


def test_check_writes_a_validation_report(config_file: Path, report_dir: Path, capsys):
    folder = report_dir / "S/SAMPATH BANK PLC"
    (folder / "scanned_2022.pdf").write_bytes(make_pdf([None, None]))
    (folder / "zz_copy_2024.pdf").write_bytes((folder / "sampathbankplc_2024.pdf").read_bytes())

    assert main(["check", "--config", str(config_file)]) == 0

    assert "1 duplicate, 1 image_only, 3 text" in capsys.readouterr().out
    with open(config_file.parent / "output" / "validation.csv", newline="") as file:
        rows = {row["report_id"].rsplit("/", 1)[1]: row for row in csv.DictReader(file)}
    assert rows["zz_copy_2024"]["duplicate_of"] == "S/SAMPATH BANK PLC/sampathbankplc_2024"
    assert rows["scanned_2022"]["image_only_pages"] == "1-2"
    assert rows["sampathbankplc_2024"]["pages"] == "3"


def test_check_needs_validation_turned_on(config_file: Path, capsys):
    config_file.write_text(config_file.read_text() + "validation: {enabled: false}\n")
    assert main(["check", "--config", str(config_file)]) == 2
    assert "validation is turned off" in capsys.readouterr().err
