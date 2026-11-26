from pathlib import Path

import pytest
from pdfs import TEXT, make_pdf, text_pdf

from cse_extract.config import ValidationSettings
from cse_extract.validate import (
    IMAGE_ONLY,
    PARTIAL,
    TEXT_OK,
    UNREADABLE,
    file_sha256,
    page_ranges,
    read_text_layer,
    validate_pdf,
)

SETTINGS = ValidationSettings()


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_text_pdf_passes(tmp_path: Path):
    result = validate_pdf(_write(tmp_path, "a.pdf", text_pdf(pages=4)), SETTINGS)
    assert (result.outcome, result.pages, result.text_ratio, result.image_only_pages) == (TEXT_OK, 4, 1.0, [])
    assert result.accepted


def test_a_few_image_only_pages_still_pass_as_partial(tmp_path: Path):
    pdf = make_pdf([TEXT, TEXT, None, TEXT, None])  # e.g. scanned signature pages
    result = validate_pdf(_write(tmp_path, "a.pdf", pdf), SETTINGS)
    assert (result.outcome, result.text_ratio, result.image_only_pages) == (PARTIAL, 0.6, [3, 5])
    assert result.accepted


def test_a_mostly_scanned_pdf_is_rejected(tmp_path: Path):
    result = validate_pdf(_write(tmp_path, "a.pdf", make_pdf([None, None, TEXT, None])), SETTINGS)
    assert (result.outcome, result.text_ratio) == (IMAGE_ONLY, 0.25)
    assert not result.accepted
    assert "25% of 4 pages" in result.reason and "OCR" in result.reason


def test_a_page_with_only_a_few_characters_counts_as_image_only(tmp_path: Path):
    layer = read_text_layer(_write(tmp_path, "a.pdf", make_pdf([TEXT, "Page 2"])), min_chars_per_page=50)
    assert layer.image_only_pages == [2]


def test_thresholds_come_from_the_settings(tmp_path: Path):
    path = _write(tmp_path, "a.pdf", make_pdf([None, TEXT]))
    assert validate_pdf(path, ValidationSettings(min_text_ratio=0.5)).outcome == PARTIAL
    assert validate_pdf(path, ValidationSettings(min_text_ratio=0.9)).outcome == IMAGE_ONLY
    assert validate_pdf(path, ValidationSettings(min_chars_per_page=1000)).outcome == IMAGE_ONLY


@pytest.mark.parametrize("data", [b"", b"not a pdf at all", b"%PDF-1.7 truncated"])
def test_a_file_that_is_not_a_readable_pdf_is_rejected(tmp_path: Path, data: bytes):
    result = validate_pdf(_write(tmp_path, "a.pdf", data), SETTINGS)
    assert result.outcome == UNREADABLE
    assert not result.accepted
    assert result.reason


def test_identical_files_share_a_fingerprint(tmp_path: Path):
    a = _write(tmp_path, "a.pdf", text_pdf(marker="x"))
    b = _write(tmp_path, "b.pdf", text_pdf(marker="x"))
    c = _write(tmp_path, "c.pdf", text_pdf(marker="y"))
    assert file_sha256(a) == file_sha256(b) != file_sha256(c)
    assert len(file_sha256(a)) == 64


@pytest.mark.parametrize(
    "pages, text",
    [([], ""), ([4], "4"), ([1, 2, 3, 7], "1-3, 7"), ([9, 8, 2, 10], "2, 8-10")],
)
def test_page_ranges(pages, text):
    assert page_ranges(pages) == text
