"""Check each PDF before conversion (SRS FR02).

Two checks run before Docling sees a report:

- Text layer: every page is read with pypdfium2 and counted as a text page if it has at least
  min_chars_per_page characters. Scanned or image-only reports are out of scope (no OCR), so a
  report whose share of text pages is below min_text_ratio is rejected. Reports with only a few
  image-only pages, such as a scanned auditor's signature page, still pass.
- Duplicates: a SHA-256 fingerprint of the file's bytes catches the same PDF saved twice under
  different names, so it is converted only once.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cse_extract.config import ValidationSettings

# Validation outcomes, from best to worst.
TEXT_OK = "text"          # every page has a text layer
PARTIAL = "partial"       # some image-only pages, but enough text to extract
IMAGE_ONLY = "image_only"  # too few text pages: rejected
UNREADABLE = "unreadable"  # the file could not be opened as a PDF: rejected


@dataclass
class TextLayer:
    """How much of a PDF has a text layer.

    Attributes:
        pages: Number of pages in the PDF.
        image_only_pages: 1-based numbers of the pages with too little text.
        error: Why the PDF could not be read, or "" if it could.
    """

    pages: int
    image_only_pages: list[int] = field(default_factory=list)
    error: str = ""

    @property
    def text_ratio(self) -> float:
        return (self.pages - len(self.image_only_pages)) / self.pages if self.pages else 0.0


@dataclass
class Validation:
    """Result of checking one PDF before conversion."""

    sha256: str
    outcome: str
    pages: int = 0
    text_ratio: float = 0.0
    image_only_pages: list[int] = field(default_factory=list)
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.outcome in (TEXT_OK, PARTIAL)


TextLayerReader = Callable[[Path, int], TextLayer]


def file_sha256(path: Path) -> str:
    """SHA-256 of the file's bytes, read in chunks so large PDFs do not fill memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_layer(pdf: Path, min_chars_per_page: int) -> TextLayer:
    """Count the pages of a PDF whose text layer has fewer than min_chars_per_page characters
    (whitespace not counted)."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(str(pdf))
    except (pypdfium2.PdfiumError, OSError) as exc:
        return TextLayer(pages=0, error=f"cannot open PDF: {exc}")
    try:
        image_only = []
        for index in range(len(document)):
            page = document[index]
            text_page = page.get_textpage()
            try:
                chars = sum(not c.isspace() for c in text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
            if chars < min_chars_per_page:
                image_only.append(index + 1)
        return TextLayer(pages=len(document), image_only_pages=image_only)
    finally:
        document.close()


def validate_pdf(
    pdf: Path,
    settings: ValidationSettings,
    reader: TextLayerReader = read_text_layer,
) -> Validation:
    """Fingerprint a PDF and check its text layer."""
    sha256 = file_sha256(pdf)
    layer = reader(pdf, settings.min_chars_per_page)
    if layer.error or layer.pages == 0:
        return Validation(sha256, UNREADABLE, reason=layer.error or "PDF has no pages")
    ratio = round(layer.text_ratio, 3)
    if ratio < settings.min_text_ratio:
        return Validation(
            sha256,
            IMAGE_ONLY,
            layer.pages,
            ratio,
            layer.image_only_pages,
            reason=(
                f"only {ratio:.0%} of {layer.pages} pages have a text layer "
                f"(minimum {settings.min_text_ratio:.0%}); scanned reports need OCR, which is out of scope"
            ),
        )
    outcome = PARTIAL if layer.image_only_pages else TEXT_OK
    return Validation(sha256, outcome, layer.pages, ratio, layer.image_only_pages)


def page_ranges(pages: list[int]) -> str:
    """Write page numbers compactly, e.g. [1, 2, 3, 7] -> "1-3, 7"."""
    ranges: list[str] = []
    start = previous = None
    for page in sorted(pages):
        if start is None:
            start = previous = page
        elif page == previous + 1:
            previous = page
        else:
            ranges.append(f"{start}-{previous}" if start != previous else str(start))
            start = previous = page
    if start is not None:
        ranges.append(f"{start}-{previous}" if start != previous else str(start))
    return ", ".join(ranges)
