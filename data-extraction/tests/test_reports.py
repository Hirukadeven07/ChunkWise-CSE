from pathlib import Path

import pytest

from cse_extract.reports import find_reports, select


def test_finds_pdfs_and_reads_company_and_year(report_dir: Path):
    reports = find_reports(report_dir, frozenset({"interim"}))
    assert [r.report_id for r in reports] == [
        "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2023",
        "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024",
        "S/SAMPATH BANK PLC/sampathbankplc_2024",
    ]
    assert reports[0].company == "NATIONS TRUST BANK PLC"
    assert reports[0].year == 2023
    assert reports[0].path == report_dir / "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2023.pdf"


def test_excluded_folders_are_case_insensitive(report_dir: Path):
    ids = [r.report_id for r in find_reports(report_dir)]
    assert "S/SAMPATH BANK PLC/Interim/sampathbankplc_q1_2024" in ids
    ids = [r.report_id for r in find_reports(report_dir, frozenset({"interim"}))]
    assert not any("Interim" in i for i in ids)


@pytest.mark.parametrize(
    "name, year",
    [
        ("odelplc_2025.pdf", 2025),
        ("odelplc_2024_2.pdf", 2024),
        ("Annual Report 2023-2024.PDF", 2024),
        ("no year here.pdf", None),
        ("scan_120245.pdf", None),
    ],
)
def test_year_comes_from_the_last_year_in_the_name(tmp_path: Path, name: str, year):
    (tmp_path / "ODEL PLC").mkdir()
    (tmp_path / "ODEL PLC" / name).write_bytes(b"")
    assert find_reports(tmp_path)[0].year == year


def test_missing_input_folder_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_reports(tmp_path / "missing")


def test_select_matches_any_text_case_insensitively(report_dir: Path):
    reports = find_reports(report_dir, frozenset({"interim"}))
    assert len(select(reports, None)) == 3
    assert [r.company for r in select(reports, ["sampath"])] == ["SAMPATH BANK PLC"]
    assert len(select(reports, ["2023", "sampath"])) == 2
