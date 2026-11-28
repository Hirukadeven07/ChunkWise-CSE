import csv
import json
from pathlib import Path

import pytest
from section_docs import docling_doc, pdf_with_bookmarks

from cse_extract.cli import main
from cse_extract.sections import (
    SECTION_TYPES_IN_REPORT_ORDER,
    Bookmark,
    bookmarks,
    detect_sections,
    normalise,
    section_type_for,
)


def by_key(result: dict) -> dict:
    return {section["key"]: section for section in result["sections"]}


# A small report: a contents page, then the sections in their usual order. Page 1 is the cover.
REPORT = [
    [("title", "Annual Report 2024")],
    [
        ("section_header", "Contents"),
        ("text", "Chairman's Message 3"),
        ("text", "Managing Director's Review 4"),
        ("text", "Risk Management 5"),
        ("text", "Independent Auditor's Report 6"),
    ],
    [("section_header", "Message from the Chairman"), ("text", "It was a strong year.")],
    [("section_header", "MD's Review"), ("text", "Profits rose by ten per cent.")],
    [("section_header", "Enterprise Risk Management"), ("text", "Credit risk stayed low.")],
    [("section_header", "INDEPENDENT AUDITORS' REPORT"), ("text", "In our opinion ...")],
    [
        ("section_header", "Statement of Profit or Loss"),
        ("text", "Revenue 4,200"),
        ("section_header", "Statement of Financial Position"),
        ("text", "Total assets 9,800"),
    ],
    [("section_header", "Notes to the Financial Statements"), ("text", "1. Corporate information")],
    [("text", "2. Basis of preparation")],
]


@pytest.mark.parametrize(
    "heading, key",
    [
        ("Chairman's Message", "chairman_message"),
        ("Chairperson’s Review", "chairman_message"),
        ("Message from the Chairman", "chairman_message"),
        ("Letter from our Chairwoman", "chairman_message"),
        ("CEO's Review", "ceo_review"),
        ("Managing Director's Statement", "ceo_review"),
        ("Message from the Chief Executive Officer", "ceo_review"),
        ("Management Discussion and Analysis", "management_discussion"),
        ("Management's Discussion & Analysis", "management_discussion"),
        ("Risk Management", "risk_management"),
        ("Corporate Governance Report", "corporate_governance"),
        ("Independent Auditor's Report", "auditor_report"),
        ("Report of the Auditors", "auditor_report"),
        ("Statement of Profit or Loss and Other Comprehensive Income", "profit_or_loss"),
        ("Income Statement", "profit_or_loss"),
        ("Statement of Financial Position", "financial_position"),
        ("Balance Sheet", "financial_position"),
        ("Statement of Cash Flows", "cash_flows"),
        ("Statement of Changes in Equity", "changes_in_equity"),
        ("Notes to the Consolidated Financial Statements", "notes"),
        ("3. Chairman's Message", "chairman_message"),
    ],
)
def test_headings_worded_differently_match_the_same_section(heading, key):
    assert section_type_for(heading).key == key


@pytest.mark.parametrize("heading", ["Board of Directors", "Financial Highlights", "Chairman", "Sustainability"])
def test_other_headings_match_no_section(heading):
    assert section_type_for(heading) is None


def test_normalise_ignores_case_apostrophes_and_numbering():
    assert normalise("  4. CHAIRMAN’S  Message ") == "chairmans message"


def test_sections_are_found_from_headings_with_start_and_end_pages():
    result = detect_sections(docling_doc(REPORT))
    sections = by_key(result)

    assert result["pages"] == 9
    assert result["has_bookmarks"] is False
    chairman = sections["chairman_message"]
    assert (chairman["heading"], chairman["start_page"], chairman["end_page"]) == (
        "Message from the Chairman",
        3,
        3,
    )
    assert chairman["method"] == "heading"
    assert (sections["ceo_review"]["start_page"], sections["ceo_review"]["end_page"]) == (4, 4)
    assert sections["auditor_report"]["start_page"] == 6


def test_contents_page_is_not_mistaken_for_section_starts():
    # The contents page (page 2) names four sections; none of them should start there.
    sections = by_key(detect_sections(docling_doc(REPORT)))
    assert all(s["start_page"] != 2 for s in sections.values())


def test_contents_page_with_headings_is_ignored_too():
    contents = [("section_header", "Chairman's Message"), ("section_header", "Risk Management"),
                ("section_header", "Corporate Governance")]
    doc = docling_doc([contents, [("section_header", "Chairman's Message"), ("text", "Hello")]])
    assert by_key(detect_sections(doc))["chairman_message"]["start_page"] == 2


def test_two_sections_starting_on_the_same_page():
    sections = by_key(detect_sections(docling_doc(REPORT)))
    assert (sections["profit_or_loss"]["start_page"], sections["profit_or_loss"]["end_page"]) == (7, 7)
    assert sections["financial_position"]["start_page"] == 7


def test_last_section_runs_to_the_end_of_the_report():
    notes = by_key(detect_sections(docling_doc(REPORT)))["notes"]
    assert (notes["start_page"], notes["end_page"]) == (8, 9)


def test_missing_sections_are_recorded_not_errors():
    result = detect_sections(docling_doc(REPORT))
    sections = by_key(result)
    # Every section is always listed, in the usual report order.
    assert [s["key"] for s in result["sections"]] == [t.key for t in SECTION_TYPES_IN_REPORT_ORDER]
    governance = sections["corporate_governance"]
    assert governance == {
        "key": "corporate_governance",
        "name": "Corporate governance",
        "found": False,
        "heading": None,
        "start_page": None,
        "end_page": None,
        "method": None,
    }


def test_section_with_no_heading_is_recorded_as_missing():
    # The CEO's review on page 2 has no heading, so it is recorded as missing. Its page falls
    # inside the chairman's message, which runs until the next section found (page 3). This
    # is a known limit of finding sections by their headings.
    doc = docling_doc(
        [
            [("section_header", "Chairman's Message"), ("text", "Chairman text")],
            [("text", "CEO text without a heading")],
            [("section_header", "Risk Management"), ("text", "Risk text")],
        ]
    )
    result = detect_sections(doc)
    assert by_key(result)["ceo_review"]["found"] is False
    assert by_key(result)["chairman_message"]["end_page"] == 2
    tags = {block["ref"]: block["section"] for block in result["blocks"]}
    assert tags["#/texts/2"] == "chairman_message"
    assert tags["#/texts/4"] == "risk_management"


def test_every_block_is_tagged_with_its_section():
    result = detect_sections(docling_doc(REPORT))
    tags = [(block["page"], block["section"]) for block in result["blocks"]]
    assert tags[0] == (1, None)  # the cover comes before any section
    assert (3, "chairman_message") in tags
    assert (7, "profit_or_loss") in tags and (7, "financial_position") in tags
    assert tags[-1] == (9, "notes")
    assert len(result["blocks"]) == sum(len(page) for page in REPORT)


def test_blocks_inside_groups_are_followed():
    result = detect_sections(docling_doc(REPORT, grouped=True))
    assert len(result["blocks"]) == sum(len(page) for page in REPORT)
    assert by_key(result)["notes"]["start_page"] == 8


def test_page_headers_do_not_start_sections():
    doc = docling_doc(
        [
            [("page_header", "Risk Management"), ("text", "Cover text")],
            [("section_header", "Risk Management"), ("text", "Risk text")],
        ]
    )
    assert by_key(detect_sections(doc))["risk_management"]["start_page"] == 2


@pytest.mark.parametrize("use_actions", [False, True])
def test_bookmarks_are_read_from_the_pdf(tmp_path: Path, use_actions):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(
        pdf_with_bookmarks(5, [("Cover", 1), ("_GoBack", 4), ("Chairman's Review", 3)], use_actions)
    )
    # The hidden "_GoBack" bookmark that word processors leave behind is dropped.
    assert bookmarks(pdf) == [Bookmark("Cover", 1, 0), Bookmark("Chairman's Review", 3, 0)]


def test_bookmarked_section_ends_where_the_next_bookmark_starts(tmp_path: Path):
    # "Board of Directors" is not a section we look for, but its bookmark shows that the
    # chairman's message ends on page 3, not just before risk management on page 5.
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(
        pdf_with_bookmarks(9, [("Chairman's Message", 3), ("Board of Directors", 4), ("Risk Management", 5)])
    )
    doc = docling_doc([[("text", f"Page {n}")] for n in range(1, 10)])
    sections = by_key(detect_sections(doc, pdf))
    assert (sections["chairman_message"]["start_page"], sections["chairman_message"]["end_page"]) == (3, 3)
    assert (sections["risk_management"]["start_page"], sections["risk_management"]["end_page"]) == (5, 9)


def test_heading_at_the_top_of_a_page_beats_an_earlier_mid_page_one():
    # Page 1 is a Directors' Report with a short "Auditors' Report" paragraph part-way down;
    # the real auditor's report starts at the top of page 3.
    doc = docling_doc(
        [
            [("section_header", "Annual Report of the Board of Directors"), ("text", "Principal activities ..."),
             ("section_header", "Auditors' Report"), ("text", "Messrs KPMG were reappointed.")],
            [("text", "More of the Directors' Report")],
            [("page_header", "Annual Report 2021"), ("section_header", "Independent Auditor's Report"),
             ("text", "Opinion")],
        ]
    )
    auditor = by_key(detect_sections(doc))["auditor_report"]
    assert (auditor["start_page"], auditor["heading"]) == (3, "Independent Auditor's Report")


def test_mid_page_heading_is_used_when_there_is_no_better_one():
    doc = docling_doc([[("text", "Intro"), ("section_header", "Risk Management"), ("text", "Risks")]])
    assert by_key(detect_sections(doc))["risk_management"]["start_page"] == 1


def test_note_titles_inside_the_notes_do_not_start_other_sections():
    doc = docling_doc(
        [
            [("section_header", "Notes to the Financial Statements"), ("text", "1. Reporting entity")],
            [("section_header", "27.2 Financial Risk Management"), ("text", "Credit risk ...")],
        ]
    )
    sections = by_key(detect_sections(doc))
    assert sections["risk_management"]["found"] is False
    assert (sections["notes"]["start_page"], sections["notes"]["end_page"]) == (1, 2)


def test_bookmarks_are_used_first_and_headings_fill_the_gaps(tmp_path: Path):
    # The bookmark says the chairman's message starts on page 4, although a heading for it
    # appears on page 3: the bookmark wins. Risk management has no bookmark, so its heading
    # is used.
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_with_bookmarks(9, [("Chairperson's Message", 4)]))
    result = detect_sections(docling_doc(REPORT), pdf)
    sections = by_key(result)

    assert result["has_bookmarks"] is True
    assert sections["chairman_message"]["method"] == "bookmark"
    assert sections["chairman_message"]["start_page"] == 4
    assert sections["chairman_message"]["heading"] == "Chairperson's Message"
    assert sections["risk_management"]["method"] == "heading"


def test_report_with_no_bookmarks_uses_headings(tmp_path: Path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(pdf_with_bookmarks(9, []))
    result = detect_sections(docling_doc(REPORT), pdf)
    assert result["has_bookmarks"] is False
    assert by_key(result)["chairman_message"]["method"] == "heading"


def test_unreadable_pdf_falls_back_to_headings(tmp_path: Path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"not a pdf")
    assert by_key(detect_sections(docling_doc(REPORT), pdf))["chairman_message"]["method"] == "heading"


def test_sections_command_writes_files_and_summary(config_file: Path, tmp_path: Path, capsys):
    # Two of the three reports have been extracted; the third has not.
    output = tmp_path / "output"
    for report_id in ("N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024", "S/SAMPATH BANK PLC/sampathbankplc_2024"):
        path = output / f"{report_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(docling_doc(REPORT)), encoding="utf-8")

    assert main(["sections", "--config", str(config_file)]) == 0
    assert "2 done, 1 not extracted yet, 0 error" in capsys.readouterr().out

    written = json.loads(
        (output / "S/SAMPATH BANK PLC/sampathbankplc_2024.sections.json").read_text(encoding="utf-8")
    )
    assert written["report_id"] == "S/SAMPATH BANK PLC/sampathbankplc_2024"
    assert by_key(written)["notes"]["end_page"] == 9

    with open(output / "sections_summary.csv", encoding="utf-8") as file:
        rows = {row["report_id"]: row for row in csv.DictReader(file)}
    sampath = rows["S/SAMPATH BANK PLC/sampathbankplc_2024"]
    assert sampath["status"] == "done"
    assert sampath["found"] == "7/11"
    assert sampath["chairman_message"] == "heading"
    assert sampath["corporate_governance"] == ""
    assert rows["N/NATIONS TRUST BANK PLC/nationstrustbankplc_2023"]["status"] == "not_extracted"


def test_sections_command_records_a_broken_file_and_carries_on(config_file: Path, tmp_path: Path):
    output = tmp_path / "output"
    broken = output / "S/SAMPATH BANK PLC/sampathbankplc_2024.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{ not json", encoding="utf-8")
    assert main(["sections", "--config", str(config_file)]) == 1
    with open(output / "sections_summary.csv", encoding="utf-8") as file:
        statuses = {row["report_id"]: row["status"] for row in csv.DictReader(file)}
    assert statuses["S/SAMPATH BANK PLC/sampathbankplc_2024"] == "error"
