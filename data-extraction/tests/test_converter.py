"""Check how build_docling_converter drives Docling, using stand-in Docling modules."""

import enum
import sys
import types
from pathlib import Path

import pytest

from cse_extract.config import DoclingSettings
from cse_extract.converter import build_docling_converter


class _Options:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
def fake_docling(monkeypatch):
    """Install minimal docling modules and return a record of how they were used."""
    used = {}

    class AcceleratorDevice(str, enum.Enum):
        AUTO, CPU, CUDA, MPS, XPU = "auto", "cpu", "cuda", "mps", "xpu"

    class TableFormerMode(str, enum.Enum):
        ACCURATE, FAST = "accurate", "fast"

    class Status(str, enum.Enum):
        SUCCESS, FAILURE = "success", "failure"

    class ThreadedPdfPipelineOptions(_Options):
        pass

    class LayoutObjectDetectionOptions:
        @staticmethod
        def from_preset(name):
            return f"layout:{name}"

    class DocumentConverter:
        def __init__(self, format_options):
            used["format_option"] = format_options["pdf"]

        def initialize_pipeline(self, fmt):
            used["initialized"] = fmt

        def convert(self, source, raises_on_error):
            used["raises_on_error"] = raises_on_error
            failed = "bad" in Path(source).name
            return types.SimpleNamespace(
                status=Status.FAILURE if failed else Status.SUCCESS,
                pages=[object()] * 3,
                document=object(),
                errors=[types.SimpleNamespace(error_message="unreadable")] if failed else [],
            )

    modules = {
        "docling": {},
        "docling.datamodel": {},
        "docling.datamodel.base_models": {"InputFormat": types.SimpleNamespace(PDF="pdf")},
        "docling.datamodel.accelerator_options": {
            "AcceleratorDevice": AcceleratorDevice,
            "AcceleratorOptions": _Options,
        },
        "docling.datamodel.pipeline_options": {
            "PdfPipelineOptions": _Options,
            "ThreadedPdfPipelineOptions": ThreadedPdfPipelineOptions,
            "TableFormerMode": TableFormerMode,
            "TableStructureOptions": _Options,
            "LayoutObjectDetectionOptions": LayoutObjectDetectionOptions,
        },
        "docling.document_converter": {
            "DocumentConverter": DocumentConverter,
            "PdfFormatOption": _Options,
        },
        "docling.pipeline": {},
        "docling.pipeline.threaded_standard_pdf_pipeline": {"ThreadedStandardPdfPipeline": "threaded"},
    }
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    return used


def test_settings_reach_docling(fake_docling):
    build_docling_converter(
        DoclingSettings(device="cuda", table_mode="fast", layout_model="layout_egret_large")
    )
    option = fake_docling["format_option"]
    assert option.pipeline_cls == "threaded"
    pipeline = option.pipeline_options
    assert pipeline.do_ocr is False
    assert pipeline.do_table_structure is True
    assert pipeline.accelerator_options.device == "cuda"
    assert pipeline.table_structure_options.mode == "fast"
    assert pipeline.table_structure_options.do_cell_matching is True
    assert pipeline.layout_options == "layout:layout_egret_large"
    assert fake_docling["initialized"] == "pdf"


def test_results_are_read_into_a_conversion(fake_docling, tmp_path):
    convert = build_docling_converter(DoclingSettings())
    good = convert(tmp_path / "odelplc_2024.pdf")
    assert (good.status, good.pages, good.ok, good.errors) == ("success", 3, True, [])
    assert fake_docling["raises_on_error"] is False

    bad = convert(tmp_path / "bad_2024.pdf")
    assert (bad.status, bad.ok, bad.document, bad.errors) == ("failure", False, None, ["unreadable"])


def test_falls_back_to_the_standard_pipeline(fake_docling, monkeypatch):
    monkeypatch.setitem(sys.modules, "docling.pipeline.threaded_standard_pdf_pipeline", None)
    build_docling_converter(DoclingSettings())
    option = fake_docling["format_option"]
    assert not hasattr(option, "pipeline_cls")
    assert type(option.pipeline_options) is _Options
