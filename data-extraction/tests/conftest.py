"""Shared test helpers: a small report folder and a stand-in for Docling."""

from __future__ import annotations

from pathlib import Path

import pytest

from cse_extract.converter import Conversion


class FakeDocument:
    """Writes small files instead of a real Docling document."""

    def __init__(self, name: str):
        self.name = name

    def save_as_json(self, filename: Path) -> None:
        Path(filename).write_text(f'{{"name": "{self.name}"}}', encoding="utf-8")

    def save_as_markdown(self, filename: Path) -> None:
        Path(filename).write_text(f"# {self.name}\n", encoding="utf-8")


class FakeConverter:
    """Pretends to convert PDFs. PDFs whose name contains 'broken' fail, and those whose
    name contains 'crash' raise an exception."""

    def __init__(self):
        self.calls: list[Path] = []

    def __call__(self, pdf: Path) -> Conversion:
        self.calls.append(pdf)
        if "crash" in pdf.name:
            raise RuntimeError("corrupt PDF")
        if "broken" in pdf.name:
            return Conversion(status="failure", pages=0, document=None, errors=["no pages"])
        return Conversion(status="success", pages=10, document=FakeDocument(pdf.stem))


@pytest.fixture
def fake_converter() -> FakeConverter:
    return FakeConverter()


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """An input folder laid out like the real Annual Reports folder."""
    root = tmp_path / "Annual Reports"
    for rel in (
        "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2023.pdf",
        "N/NATIONS TRUST BANK PLC/nationstrustbankplc_2024.pdf",
        "S/SAMPATH BANK PLC/sampathbankplc_2024.pdf",
        "S/SAMPATH BANK PLC/Interim/sampathbankplc_q1_2024.pdf",
        "S/SAMPATH BANK PLC/notes.txt",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7 test")
    return root


@pytest.fixture
def config_file(tmp_path: Path, report_dir: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"input_dir: {report_dir.as_posix()}\n"
        "output_dir: output\n"
        "exclude_dirs: [interim]\n",
        encoding="utf-8",
    )
    return path
