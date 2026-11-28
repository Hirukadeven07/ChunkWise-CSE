"""Turn one PDF into a Docling document.

Docling is imported only inside build_docling_converter, so the rest of the package (and the
tests) work without it installed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cse_extract.config import DoclingSettings

OK_STATUSES = ("success", "partial_success")


class SavableDocument(Protocol):
    """The parts of a DoclingDocument the pipeline uses."""

    def save_as_json(self, filename: Path) -> None: ...

    def save_as_markdown(self, filename: Path) -> None: ...


@dataclass
class Conversion:
    """Result of converting one PDF.

    Attributes:
        status: success, partial_success or failure, as Docling reports it.
        pages: Number of pages Docling processed.
        document: The converted document, or None when the conversion failed.
        errors: Messages Docling reported, if any.
    """

    status: str
    pages: int
    document: SavableDocument | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in OK_STATUSES and self.document is not None


Converter = Callable[[Path], Conversion]


def build_docling_converter(settings: DoclingSettings) -> Converter:
    """Set up Docling once and return a function that converts one PDF."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    except ImportError:  # Docling releases before accelerator_options became its own module
        from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions

    # The threaded pipeline runs the layout, table and other models in parallel batches,
    # which keeps a GPU busy. Older Docling releases only have the standard pipeline.
    try:
        from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
        from docling.pipeline.threaded_standard_pdf_pipeline import ThreadedStandardPdfPipeline

        options_cls, pipeline = ThreadedPdfPipelineOptions, {"pipeline_cls": ThreadedStandardPdfPipeline}
    except ImportError:
        options_cls, pipeline = PdfPipelineOptions, {}

    options = options_cls(
        do_ocr=settings.ocr,
        do_table_structure=True,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.ACCURATE if settings.table_mode == "accurate" else TableFormerMode.FAST,
            do_cell_matching=True,
        ),
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice(settings.device)),
    )
    if settings.layout_model:
        options.layout_options = _layout_options(settings.layout_model)

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options, **pipeline)}
    )
    converter.initialize_pipeline(InputFormat.PDF)

    def convert(pdf: Path) -> Conversion:
        result = converter.convert(pdf, raises_on_error=False)
        status = result.status.value if hasattr(result.status, "value") else str(result.status)
        document = result.document if status in OK_STATUSES else None
        return Conversion(
            status=status,
            pages=len(result.pages),
            document=document,
            errors=[_message(error) for error in (result.errors or [])],
        )

    return convert


def _layout_options(preset: str) -> Any:
    """Layout model options for a named Docling preset such as layout_egret_large."""
    try:
        from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions
    except ImportError as exc:
        raise ValueError(
            f"this Docling version does not support layout_model presets ('{preset}'); "
            "set docling.layout_model to null or upgrade Docling"
        ) from exc
    return LayoutObjectDetectionOptions.from_preset(preset)


def _message(error: Any) -> str:
    return str(getattr(error, "error_message", error))
