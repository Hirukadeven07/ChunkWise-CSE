"""Table-of-contents (ToC) recovery + section segmentation for structure-aware chunking.

Where does every section of the report start? Sources, best first:

1. ``outline``        - the PDF's bookmarks: exact pages and hierarchy
2. ``contents_page``  - the printed "Contents" page; printed page numbers are mapped to PDF
                        pages through an offset learned from the text itself
3. ``headings``       - Markdown headings in the extracted text (pymupdf4llm, DOCX styles, .md)

Each entry is then pinned to its heading *line* on the start page (falling back to the top of
the page), so two sections starting on the same page are still separated. The text between
consecutive entries becomes one `Section`, carrying its full ToC path
(e.g. ["MANAGEMENT DISCUSSION AND ANALYSIS", "OUTLOOK AND RISKS"]).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from arbench.pipeline.ingest import Document, Page

log = logging.getLogger("arbench.toc")

FRONT_MATTER = "Front matter"


@dataclass
class TocEntry:
    level: int
    title: str
    page: int  # 1-based PDF page on which the section starts
    source: str  # outline | contents_page | headings
    path: list[str] = field(default_factory=list)
    line: int = 0  # line index on `page` where the section starts
    located: bool = False  # the heading line was found in the extracted text

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    title: str
    level: int
    path: list[str]
    lines: list[tuple[int, str]] = field(default_factory=list)  # (page, line) in reading order

    @property
    def pages(self) -> list[int]:
        return sorted({p for p, _ in self.lines})


# ----------------------------------------------------------------------------- helpers


def clean_title(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\*\*|__|`", "", s)
    s = s.strip().lstrip("#").strip(" *_-–•>")
    return re.sub(r"\s+", " ", s).strip()


def norm(s: str) -> str:
    """Case/space/punctuation-insensitive key ('DISCUSSIONAND' == 'Discussion and')."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _find_heading_line(lines: list[str], key: str) -> int | None:
    """Index of the line that carries the section title (prefer Markdown headings / bold lines)."""
    if len(key) < 3:
        return None
    fallback = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(s) > 200 or s.startswith("|"):
            continue
        n = norm(clean_title(s))
        if not n:
            continue
        hit = (
            n == key
            or (len(key) >= 6 and n.startswith(key) and len(n) <= len(key) + 25)
            or (len(n) >= 6 and key.startswith(n) and len(n) >= 0.6 * len(key))
        )
        if hit:
            if s.startswith(("#", "**")):
                return i
            if fallback is None:
                fallback = i
    return fallback


def _assign_paths(entries: list[TocEntry]) -> None:
    stack: list[TocEntry] = []
    for e in entries:
        while stack and stack[-1].level >= e.level:
            stack.pop()
        e.path = [s.title for s in stack] + [e.title]
        stack.append(e)


def _locate(entries: list[TocEntry], pages: list[Page]) -> None:
    lines_by_page = {p.number: p.text.splitlines() for p in pages}
    for e in entries:
        if e.located:
            continue
        key = norm(e.title)
        for pg in (e.page, e.page + 1):  # bookmarks sometimes point at a divider page just before
            lines = lines_by_page.get(pg)
            idx = _find_heading_line(lines, key) if lines else None
            if idx is not None:
                e.page, e.line, e.located = pg, idx, True
                better = clean_title(lines[idx])
                if _restores_spaces(e.title, better):
                    e.title = better  # e.g. bookmark "DISCUSSIONAND ANALYSIS" -> "DISCUSSION AND ANALYSIS"
                break


_SHORT_WORDS = {"a", "an", "and", "of", "the", "in", "on", "to", "for", "at", "by", "or", "its", "our", "we", "s"}


def _fragments(s: str) -> int:
    return sum(1 for w in re.findall(r"[A-Za-z]+", s) if len(w) <= 3 and w.lower() not in _SHORT_WORDS)


def _restores_spaces(old: str, new: str) -> bool:
    """True if `new` is `old` with lost word breaks restored - not with new breaks ("TRA NSPORTATION")."""
    return norm(old) == norm(new) and new.count(" ") > old.count(" ") and _fragments(new) <= _fragments(old)


# ----------------------------------------------------------------------------- ToC sources


def toc_from_outline(outline, n_pages: int) -> list[TocEntry]:
    out = []
    for row in outline or []:
        try:
            level, title, page = int(row[0]), clean_title(str(row[1])), int(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        if title and 1 <= page <= n_pages:
            out.append(TocEntry(max(1, level), title, page, "outline"))
    return out


_NUM_FIRST = re.compile(r"^(?P<indent>\s*)(?:[-*•>]\s*)*(?P<page>\d{1,4})\s+(?P<title>[A-Za-z][^|]{2,90}?)\s*$")
_NUM_LAST = re.compile(
    r"^(?P<indent>\s*)(?:[-*•>]\s*)*(?P<title>[A-Za-z][^|]{2,90}?)\s*(?:\.{2,}|…+|\s{2,}|\t)\s*(?P<page>\d{1,4})\s*$"
)


def toc_from_contents_page(pages: list[Page], max_scan: int = 15) -> list[TocEntry]:
    n_pages = max(p.number for p in pages)
    cands: list[tuple[int, str, int]] = []  # indent, title, printed page
    for p in pages[:max_scan]:
        found = []
        for raw in p.text.splitlines():
            line = re.sub(r"\*\*|__", "", raw).rstrip()
            m = _NUM_FIRST.match(line) or _NUM_LAST.match(line)
            if not m:
                continue
            title, printed = clean_title(m["title"]), int(m["page"])
            if len(norm(title)) >= 3 and 1 <= printed <= n_pages:
                found.append((len(m["indent"].expandtabs(4)), title, printed))
        if len(found) >= 5 or (len(found) >= 3 and re.search(r"\bcontents\b", p.text, re.IGNORECASE)):
            cands.extend(found)
    if len(cands) < 3:
        return []
    # printed page numbers rarely equal PDF page numbers (covers, unnumbered inserts): learn the offset
    page_keys = {p.number: norm(p.text[:4000]) for p in pages}
    best_off, best_hits = 0, -1
    for off in range(-10, 31):
        hits = sum(1 for _, t, pg in cands if norm(t) in page_keys.get(pg + off, ""))
        if hits > best_hits:
            best_off, best_hits = off, hits
    if best_hits < max(2, 0.25 * len(cands)):
        log.info("  contents page found but could not be anchored to the text (%d/%d hits)", best_hits, len(cands))
        return []
    indents = sorted({i for i, _, _ in cands})
    out, seen = [], set()
    for ind, title, printed in cands:
        key = (norm(title), printed)
        if key in seen:
            continue
        seen.add(key)
        page = printed + best_off
        if 1 <= page <= n_pages:
            out.append(TocEntry(min(indents.index(ind) + 1, 4), title, page, "contents_page"))
    return out


_MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def toc_from_headings(pages: list[Page], max_levels: int = 2) -> list[TocEntry]:
    raw = []
    for p in pages:
        for i, line in enumerate(p.text.splitlines()):
            m = _MD_HEAD.match(line.strip())
            if not m:
                continue
            title = clean_title(m[2])
            if 3 <= len(title) <= 120 and re.search(r"[A-Za-z]{2}", title):
                raw.append((len(m[1]), title, p.number, i))
    if not raw:
        return []
    levels = sorted({h for h, *_ in raw})[:max_levels]
    return [
        TocEntry(levels.index(h) + 1, t, pg, "headings", line=i, located=True)
        for h, t, pg, i in raw
        if h in levels
    ]


def extract_toc(doc: Document) -> tuple[list[TocEntry], str]:
    """Best available ToC for a document, located in the text and sorted in reading order."""
    n_pages = max(p.number for p in doc.pages)
    entries = toc_from_outline(doc.meta.get("outline"), n_pages)
    source = "outline"
    if len(entries) < 3:
        entries, source = toc_from_contents_page(doc.pages), "contents_page"
    if len(entries) < 3:
        entries, source = toc_from_headings(doc.pages), "headings"
    if not entries:
        return [], "none"

    _locate(entries, doc.pages)
    if source == "outline":
        _assign_paths(entries)  # bookmark order encodes the hierarchy, even when pages are out of order
        entries = sorted(enumerate(entries), key=lambda x: (x[1].page, x[1].line, x[0]))
        entries = [e for _, e in entries]
    else:
        entries.sort(key=lambda e: (e.page, e.line))
        _assign_paths(entries)
    return entries, source


def segment(doc: Document, entries: list[TocEntry]) -> list[Section]:
    """Cut the document text at every ToC entry; text before the first entry is front matter."""
    ordered = sorted(enumerate(entries), key=lambda x: (x[1].page, x[1].line, x[0]))
    sections: list[Section] = []
    cur = Section(FRONT_MATTER, 1, [FRONT_MATTER])
    j = 0
    for p in doc.pages:
        for i, line in enumerate(p.text.splitlines()):
            while j < len(ordered) and (ordered[j][1].page, ordered[j][1].line) <= (p.number, i):
                e = ordered[j][1]
                j += 1
                if cur.lines and any(ln.strip() for _, ln in cur.lines):
                    sections.append(cur)
                cur = Section(e.title, e.level, list(e.path))
            cur.lines.append((p.number, line))
    if any(ln.strip() for _, ln in cur.lines):
        sections.append(cur)
    return sections
