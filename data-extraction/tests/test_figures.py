import csv
import json
from pathlib import Path

import pytest
from docling_json import CASH_FLOWS, FINANCIAL_POSITION, INCOME_STATEMENT, make_doc

from cse_extract.cli import main
from cse_extract.figures import (
    analyse_table,
    clean_label,
    entity_of,
    extract_figures,
    header_year,
    is_year_cell,
    load_tables,
    parse_number,
    table_figures,
    unit_of,
)
from cse_extract.reports import find_reports
from cse_extract.runner import outputs_for

# --- reading numbers, years and units ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, value",
    [
        ("1,234,567", 1234567), ("(734,567)", -734567), ("4.50", 4.5), ("(0.86)", -0.86),
        ("-", 0), ("–", 0), ("−45", -45), ("1 234 567", 1234567), ("Rs. 4.50", 4.5), ("0.00", 0),
    ],
)
def test_numbers_are_read_as_reports_print_them(text, value):
    assert parse_number(text).value == value


@pytest.mark.parametrize("text", ["", "Revenue", "Note 5", "n/a", "1,23,4"])
def test_text_is_not_a_number(text):
    assert parse_number(text) is None


def test_percentages_are_marked():
    number = parse_number("(12.5%)")
    assert number.value == -12.5 and number.percent
    assert not parse_number("12.5").percent


@pytest.mark.parametrize(
    "text, year",
    [("2025", 2025), ("2024/25", 2025), ("2023/2024", 2024), ("31.03.2025", 2025),
     ("As at 31 March 2025", 2025), ("Group 2025 Rs.'000", 2025), ("Note", None)],
)
def test_header_years(text, year):
    assert header_year(text) == year


def test_year_cells_are_told_apart_from_amounts():
    assert is_year_cell("2025") and is_year_cell("2024/25") and is_year_cell("As at 31.03.2025")
    assert not is_year_cell("2,025") and not is_year_cell("12,000") and not is_year_cell("Note")


@pytest.mark.parametrize(
    "text, unit",
    [("Rs.'000", (1e3, "Rs. '000")), ("LKR '000", (1e3, "Rs. '000")), ("Rs. Mn", (1e6, "Rs. Mn")),
     ("Rs. million", (1e6, "Rs. Mn")), ("Rs. Bn", (1e9, "Rs. Bn")), ("Rs.", (1.0, "Rs.")), ("2025", None)],
)
def test_units(text, unit):
    assert unit_of(text) == unit


def test_entities():
    assert entity_of("Group 2025") == "group"
    assert entity_of("Consolidated") == "group"
    assert entity_of("Company 2024") == "company" and entity_of("Bank") == "company"
    assert entity_of("2025") == ""


@pytest.mark.parametrize(
    "label, cleaned",
    [("Profit/(loss) for the year", "profit for the year"), ("(Loss)/profit before tax", "profit before tax"),
     ("Revenue 5", "revenue"), ("Income tax expense 12.1", "income tax expense"),
     ("Net cash (used in)/generated from operating activities", "net cash generated from operating activities")],
)
def test_labels_are_cleaned_for_matching(label, cleaned):
    assert clean_label(label) == cleaned


# --- reading tables --------------------------------------------------------------------------------


def _figures(*blocks):
    doc = make_doc(*blocks)
    return [f for table in load_tables(doc) for f in table_figures(analyse_table(table))]


def _value(figures, label, year, entity=""):
    matches = [f for f in figures if f.label == label and f.year == year and f.entity == entity]
    assert len(matches) == 1, matches
    return matches[0]


def test_an_income_statement_with_group_and_company_columns():
    figures = _figures(("heading", "Statement of Profit or Loss"), ("text", "In Rs. '000"), INCOME_STATEMENT)

    revenue = _value(figures, "Revenue", 2025, "group")
    assert (revenue.value, revenue.unit, revenue.value_lkr) == (1234567, "Rs. '000", 1234567000)
    assert (revenue.statement, revenue.page, revenue.column_header) == ("profit_or_loss", 112, "Group 2025")
    assert _value(figures, "Revenue", 2024, "company").value == 750000
    assert _value(figures, "Cost of sales", 2025, "group").value == -734567
    assert _value(figures, "Profit/(loss) before tax", 2024, "group").value == -20000
    assert _value(figures, "Income tax expense", 2024, "group").value == 0  # printed as a dash
    eps = _value(figures, "Basic earnings per share (Rs.)", 2025, "group")
    assert (eps.value, eps.unit, eps.value_lkr) == (4.5, "Rs.", 4.5)  # per share, not thousands
    assert not any(f.raw in ("5", "6", "7.1", "8") for f in figures)  # the Note column is skipped
    assert len(figures) == 7 * 4


def test_a_title_across_the_table_is_not_a_column_header():
    figures = _figures(("heading", "Statement of Financial Position"), FINANCIAL_POSITION)
    assets = _value(figures, "Total assets", 2025)
    assert (assets.entity, assets.column_header) == ("", "As at 31.03.2025")
    assert (assets.unit, assets.value_lkr) == ("Rs. Mn", pytest.approx(52345.7e6))
    assert assets.statement == "financial_position"


def test_statements_are_recognised_from_labels_when_there_is_no_heading():
    figures = _figures(CASH_FLOWS)
    flow = _value(figures, "Net cash (used in)/generated from operating activities", 2024)
    assert (flow.statement, flow.value, flow.unit) == ("cash_flows", -3500, "")


def test_a_table_of_text_or_an_empty_table_gives_no_figures():
    words = ("table", [["Director", "Role"], ["A. Perera", "Chairman"]], 3, 1)
    empty = ("table", [[]], 4, 0)
    assert _figures(words, empty) == []


def test_heading_and_unit_are_found_through_groups_in_reading_order():
    doc = make_doc(("heading", "Statement of Cash Flows"), ("text", "Rs. Mn"), CASH_FLOWS)
    doc["groups"] = [{"self_ref": "#/groups/0", "children": doc["body"]["children"]}]
    doc["body"]["children"] = [{"$ref": "#/groups/0"}]
    table = analyse_table(load_tables(doc)[0])
    assert "Statement of Cash Flows" in table.context
    assert table.columns[1].unit == (1e6, "Rs. Mn")


# --- key figures ----------------------------------------------------------------------------------


def _keys(tmp_path: Path, *blocks):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(make_doc(*blocks)))
    return extract_figures(path)[1]


def test_key_figures_are_picked_from_the_statements(tmp_path: Path):
    keys = _keys(
        tmp_path,
        ("heading", "Statement of Profit or Loss"), ("text", "Rs. '000"), INCOME_STATEMENT,
        ("heading", "Statement of Financial Position"), FINANCIAL_POSITION,
        ("heading", "Statement of Cash Flows"), ("text", "Rs. '000"), CASH_FLOWS,
    )
    found = {(k.metric, k.year, k.entity): k for k in keys}
    assert found[("revenue", 2025, "group")].value_lkr == 1234567000
    assert found[("profit_before_tax", 2024, "group")].value == -20000
    assert found[("profit_for_the_year", 2025, "company")].value == 63000
    assert found[("basic_eps", 2025, "group")].value == 4.5
    assert found[("total_assets", 2025, "")].page == 114
    assert found[("total_equity", 2024, "")].value == 6500.2
    assert found[("cash_from_operations", 2025, "")].value_lkr == 12_000_000
    assert found[("cash_at_end_of_year", 2024, "")].value == 1800
    assert ("gross_profit", 2025, "group") in found


def test_the_primary_statement_wins_over_a_later_note(tmp_path: Path):
    note = ("table", [["", "2025"], ["Revenue", "999"]], 150, 1)
    keys = _keys(tmp_path, ("heading", "Statement of Profit or Loss"), INCOME_STATEMENT,
                 ("heading", "5. Revenue"), note)
    revenue = [k for k in keys if k.metric == "revenue" and k.year == 2025 and k.entity == "group"]
    assert revenue[0].value == 1234567 and revenue[0].page == 112
    # the note's figure has no entity, so it is kept separately rather than replacing the statement's
    assert [k.value for k in keys if k.metric == "revenue" and k.entity == ""] == [999]


# --- the figures command ----------------------------------------------------------------------------


def test_figures_command_writes_both_csv_files(config_file: Path, report_dir: Path, capsys):
    from cse_extract.config import load_config

    config = load_config(config_file)
    reports = find_reports(config.input_dir, config.exclude_dirs)
    json_path = outputs_for(reports[0], config.output_dir)[0]
    json_path.parent.mkdir(parents=True)
    json_path.write_text(json.dumps(make_doc(("heading", "Statement of Profit or Loss"), INCOME_STATEMENT)))
    broken = outputs_for(reports[1], config.output_dir)[0]
    broken.write_text("{not json")

    assert main(["figures", "--config", str(config_file)]) == 0

    out = capsys.readouterr().out
    assert "28 figures and" in out and "from 1 reports (1 not extracted yet, 1 unreadable)" in out
    with open(config.output_dir / "key_figures.csv", newline="") as file:
        rows = list(csv.DictReader(file))
    revenue = next(r for r in rows if r["metric"] == "revenue" and r["year"] == "2025" and r["entity"] == "group")
    assert revenue["report_id"] == "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2023"
    assert revenue["company"] == "NATIONS TRUST BANK PLC" and revenue["report_year"] == "2023"
    assert revenue["value"] == "1234567.0" and revenue["page"] == "112"
    with open(config.output_dir / "figures.csv", newline="") as file:
        assert len(list(csv.DictReader(file))) == 28
