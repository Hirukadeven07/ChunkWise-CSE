from pathlib import Path

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
    (report_dir / "S/SAMPATH BANK PLC/broken_2023.pdf").write_bytes(b"")
    assert main(["run", "--config", str(config_file)], converter=fake_converter) == 1


def test_bad_config_gives_exit_code_2(tmp_path: Path, capsys):
    assert main(["list", "--config", str(tmp_path / "missing.yaml")]) == 2
    assert "config file not found" in capsys.readouterr().err
