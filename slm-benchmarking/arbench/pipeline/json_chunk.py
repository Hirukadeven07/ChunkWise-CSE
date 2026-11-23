"""Stage 2 (variant `toc_json`) - ToC-structured JSON chunks.

Instead of packing paragraphs into flat text windows, the report is split along its table of
contents (toc.py). Each section's text is converted to typed JSON blocks and packed into
chunks that never cross a section boundary. Every chunk is a self-describing JSON object, and
that JSON (not flat text) is what the SLMs read in generation, verification, evaluation and
judging:

    {
     "document": "John Keells Holdings PLC Annual Report 2025/26",
     "section": {"title": "OUTLOOK AND RISKS", "path": ["MANAGEMENT DISCUSSION AND ANALYSIS",
                 "OUTLOOK AND RISKS"], "level": 2},
     "subsection": "Global Context",                 # nearest heading above the chunk text
     "pages": {"start": 139, "end": 140},
     "part": {"index": 1, "of": 6},                  # only when a section spans several chunks
     "content": [
      {"type": "heading", "text": "MACROECONOMIC OUTLOOK"},
      {"type": "paragraph", "text": "The global economy was envisaged to ..."},
      {"type": "list", "items": ["...", "..."]},
      {"type": "table", "columns": ["", "2026", "2025"], "rows": [["Revenue", "1,234", "1,100"]]},
      {"type": "figure_text", "text": "text extracted from a chart or infographic"}
     ]
    }

Tiny sibling sections (e.g. one-paragraph highlights) are merged under their parent so that
chunks stay useful; oversized tables/lists/paragraphs are split by rows/items/sentences.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from itertools import groupby

from arbench.pipeline.chunk import _HEADING, _SENT_SPLIT, Chunk, _blocks_from_page, quality_score, render_json_chunk
from arbench.pipeline.ingest import Document
from arbench.pipeline.toc import Section, extract_toc, norm, segment
from arbench.utils import count_tokens, extract_numbers

log = logging.getLogger("arbench.json_chunk")

# ----------------------------------------------------------------------------- typed blocks

_PICTURE = re.compile(r"<!--\s*Start of picture text\s*-->(.*?)(?:<!--\s*End of picture text\s*-->|\Z)", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LIST_ITEM = re.compile(r"^\s*(?:[-*•▪◦]|\d{1,2}[.)])\s+(.*)$")
_BOLD_LINE = re.compile(r"^\*\*[^*]{2,100}\*\*$")
_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$|^$")


def _inline(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = _COMMENT.sub(" ", s)
    s = re.sub(r"\*\*|__|`", "", s)
    s = re.sub(r"(?<!\w)_(\S(?:.*?\S)?)_(?!\w)", r"\1", s)  # _italic_ -> italic
    s = re.sub(r"^\s*#{1,6}\s+", "", s)
    s = re.sub(r"^\s*[»›▪◦•]\s*", "", s)  # decorative bullets
    return re.sub(r"\s+", " ", s).strip()


def _cell(s: str) -> str:
    return re.sub(r"^[,;\s]+(?=\S)", "", _inline(s))  # stray separators left by PDF table extraction


def parse_table(lines: list[str]) -> dict | None:
    """Markdown table -> {"type": "table", "columns": [...], "rows": [[...], ...]} (empty columns dropped)."""
    rows = []
    for ln in lines:
        row = ln.strip()
        row = row[1:] if row.startswith("|") else row  # strip ONE outer pipe: "|||x|" keeps its empty cells
        row = row[:-1] if row.endswith("|") else row
        cells = [_cell(c) for c in row.split("|")]
        if all(_SEPARATOR_CELL.match(c) for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any(r[j] for r in rows)]
    rows = [[r[j] for j in keep] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows or not keep:
        return None
    columns, body = _promote_header_rows(_join_split_labels(rows[0]), rows[1:])
    return {"type": "table", "columns": columns, "rows": body}


_YEAR = re.compile(r"^(?:FY\s?)?(?:19|20)\d{2}(?:\s*/\s*\d{2,4})?$", re.IGNORECASE)
_NUMERIC = re.compile(r"^[(\-–]?[\d.,]+%?\)?$")
_CURRENCIES = {"USD", "LKR", "EUR", "GBP", "INR", "JPY", "CNY", "AUD", "SGD", "RS", "MN", "BN"}


def _join_split_labels(header: list[str]) -> list[str]:
    """Re-join a spanning label the PDF extractor split over columns: "GRO"|"UP" -> "GROUP"|"GROUP"."""
    out = list(header)
    j = 0
    while j < len(out) - 1:
        a, b = out[j], out[j + 1]
        if (
            re.fullmatch(r"[A-Z]{1,6}", a or "-")
            and re.fullmatch(r"[A-Z]{1,6}", b or "-")
            and min(len(a), len(b)) <= 3
            and len(a + b) >= 5
            and not ({a, b} & _CURRENCIES)
        ):
            out[j] = out[j + 1] = a + b
            j += 2
        else:
            j += 1
    return out


def _promote_header_rows(columns: list[str], rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Multi-row headers (e.g. "GROUP" over "2026 | 2025") -> one column label per column ("GROUP 2026").

    A leading row is part of the header when it has no figures other than years and either names a
    year or labels several columns ("In Rs.'000s | Note"); a lone row label ("Current assets") ends it.
    """
    n = 0
    for r in rows[:3]:
        filled = [c for c in r if c]
        has_year = any(_YEAR.match(c) for c in filled)
        has_figure = any(_NUMERIC.match(c) and not _YEAR.match(c) for c in filled)
        if not filled or has_figure or not (has_year or len(filled) >= 2):
            break
        n += 1
    if n == 0:
        return columns, rows
    merged = [" ".join(c for c in [columns[j], *(r[j] for r in rows[:n])] if c) for j in range(len(columns))]
    return merged, rows[n:]


def _is_heading(line: str) -> bool:
    s = line.strip()
    return bool(s.startswith("#") or _BOLD_LINE.match(s) or _HEADING.match(s)) and len(_inline(s)) <= 120


def type_block(block: str) -> list[dict]:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if not lines:
        return []
    if all(ln.strip().startswith("|") for ln in lines):
        t = parse_table(lines)
        return [t] if t else []
    if _is_heading(lines[0]) and (len(lines) == 1 or lines[0].strip().startswith("#")):
        text = _inline(lines[0])
        head = [{"type": "heading", "text": text}] if re.search(r"[A-Za-z]{2}", text) else []
        return head + type_block("\n".join(lines[1:])) if len(lines) > 1 else head
    if _LIST_ITEM.match(lines[0]):
        items: list[str] = []
        for ln in lines:
            m = _LIST_ITEM.match(ln)
            if m:
                items.append(_inline(m[1]))
            elif items:
                items[-1] = (items[-1] + " " + _inline(ln)).strip()
        items = [i for i in items if i]
        return [{"type": "list", "items": items}] if items else []
    text = _inline(" ".join(lines))
    if len(text) < 4 or not re.search(r"[A-Za-z]{2}", text):
        return []  # stray page numbers, lone symbols
    return [{"type": "paragraph", "text": text}]


def section_blocks(section: Section) -> list[tuple[int, dict]]:
    """Typed JSON blocks of a section, each with the page it came from."""
    out: list[tuple[int, dict]] = []
    for page, grp in groupby(section.lines, key=lambda x: x[0]):
        text = "\n".join(ln for _, ln in grp)
        pos = 0
        segments: list[tuple[str, str]] = []
        for m in _PICTURE.finditer(text):
            segments.append(("text", text[pos : m.start()]))
            segments.append(("figure", m.group(1)))
            pos = m.end()
        segments.append(("text", text[pos:]))
        for kind, seg in segments:
            if kind == "figure":
                t = _inline(seg)
                if len(t) >= 20:
                    out.append((page, {"type": "figure_text", "text": t}))
                continue
            for blk in _blocks_from_page(_COMMENT.sub("", seg)):
                out.extend((page, b) for b in type_block(blk))
    # consecutive list blocks are one list (pymupdf4llm separates items with blank lines)
    merged: list[tuple[int, dict]] = []
    for page, b in out:
        if b["type"] == "list" and merged and merged[-1][1]["type"] == "list":
            merged[-1][1]["items"].extend(b["items"])
        else:
            merged.append((page, b))
    # the section title is already in the chunk metadata
    if merged and merged[0][1]["type"] == "heading" and norm(merged[0][1]["text"]) == norm(section.title):
        merged = merged[1:]
    return merged


# ----------------------------------------------------------------------------- splitting oversized blocks


def block_tokens(b: dict) -> int:
    return count_tokens(json.dumps(b, ensure_ascii=False))


def _group_units(units: list, make, max_tokens: int) -> list[dict]:
    base = block_tokens(make([]))
    out, cur, cur_tok = [], [], base
    for u in units:
        t = count_tokens(json.dumps(u, ensure_ascii=False)) + 1
        if cur and cur_tok + t > max_tokens:
            out.append(make(cur))
            cur, cur_tok = [], base
        cur.append(u)
        cur_tok += t
    if cur:
        out.append(make(cur))
    return out


def split_block(b: dict, max_tokens: int) -> list[dict]:
    if block_tokens(b) <= max_tokens:
        return [b]
    kind = b["type"]
    if kind == "table":
        return _group_units(b["rows"], lambda rows: {"type": "table", "columns": b["columns"], "rows": rows}, max_tokens)
    if kind == "list":
        return _group_units(b["items"], lambda items: {"type": "list", "items": items}, max_tokens)
    units: list[str] = []
    for sent in _SENT_SPLIT.split(b["text"]):
        if count_tokens(sent) <= max_tokens * 0.8:
            units.append(sent)
            continue
        words = sent.split()
        step = max(30, int(len(words) * max_tokens * 0.6 / max(1, count_tokens(sent))))
        units.extend(" ".join(words[i : i + step]) for i in range(0, len(words), step))
    return _group_units(units, lambda parts: {"type": kind, "text": " ".join(parts)}, max_tokens)


# ----------------------------------------------------------------------------- units + packing


@dataclass
class _Unit:
    title: str
    level: int
    path: list[str]
    sibling_parent: tuple
    blocks: list[tuple[int, dict, int]] = field(default_factory=list)  # page, block, tokens
    merged: bool = False

    @property
    def tokens(self) -> int:
        return sum(t for _, _, t in self.blocks)


def _heading(text: str, page: int) -> tuple[int, dict, int]:
    b = {"type": "heading", "text": text}
    return page, b, block_tokens(b)


def build_units(sections: list[Section], target: int, max_tokens: int, min_tokens: int) -> list[_Unit]:
    units: list[_Unit] = []
    for s in sections:
        blocks = [
            (page, piece, block_tokens(piece))
            for page, b in section_blocks(s)
            for piece in split_block(b, max_tokens)
        ]
        if not blocks:
            continue
        u = _Unit(s.title, s.level, list(s.path), tuple(s.path[:-1]), blocks)
        prev = units[-1] if units else None
        if (
            prev is not None
            and prev.sibling_parent == u.sibling_parent
            and (prev.tokens < min_tokens or u.tokens < min_tokens)
            and prev.tokens + u.tokens <= target
        ):
            # tiny sibling sections -> one chunk under the parent, child titles kept as headings
            if not prev.merged:
                prev.blocks.insert(0, _heading(prev.title, prev.blocks[0][0]))
                if prev.sibling_parent:
                    prev.path = list(prev.sibling_parent)
                    prev.title, prev.level = prev.path[-1], max(1, prev.level - 1)
                prev.merged = True
            prev.blocks.append(_heading(u.title, blocks[0][0]))
            prev.blocks.extend(blocks)
            continue
        units.append(u)
    return units


HEADING_BREAK = 0.6  # inside a long section, start a new chunk at a sub-heading once this share of target is used


def pack_unit(blocks: list[tuple[int, dict, int]], target: int, overlap: int, min_tokens: int, max_tokens: int):
    """Greedy packing that prefers to break at sub-headings and never ends a chunk on a heading."""
    packs: list[list[tuple[int, dict, int]]] = []
    cur: list[tuple[int, dict, int]] = []
    cur_tok = 0

    def flush(carry_ok: bool = True) -> None:
        nonlocal cur, cur_tok
        tail: list[tuple[int, dict, int]] = []
        while cur and cur[-1][1]["type"] == "heading":  # never end a chunk on a heading
            tail.insert(0, cur.pop())
        if cur:
            packs.append(cur)
            carry, carry_tok = [], 0
            for x in reversed(cur):
                if x[1]["type"] == "heading" or carry_tok + x[2] > overlap:
                    break
                carry.insert(0, x)
                carry_tok += x[2]
            if len(carry) == len(cur) or not carry_ok:
                carry = []
        else:
            carry = []
        cur = carry + tail
        cur_tok = sum(x[2] for x in cur)

    for item in blocks:
        at_heading = item[1]["type"] == "heading" and not (cur and cur[-1][1]["type"] == "heading")
        if cur and cur_tok + item[2] > target:
            flush(carry_ok=not at_heading)
        elif cur and at_heading and cur_tok >= HEADING_BREAK * target:
            flush(carry_ok=False)  # a new sub-section starts: no overlap from the previous one
        cur.append(item)
        cur_tok += item[2]
    if cur and (not packs or cur_tok > overlap):
        packs.append(cur)
    if len(packs) >= 2 and sum(x[2] for x in packs[-1]) < min_tokens:
        if sum(x[2] for x in packs[-2]) + sum(x[2] for x in packs[-1]) <= max_tokens:
            last = packs.pop()
            packs[-1].extend(x for x in last if x not in packs[-1])
    return packs


def blocks_to_text(blocks: list[dict]) -> str:
    """Flat Markdown-ish text of JSON blocks (for BM25, quality scoring and grounding checks)."""
    parts = []
    for b in blocks:
        if b["type"] == "heading":
            parts.append(f"## {b['text']}")
        elif b["type"] == "list":
            parts.append("\n".join(f"- {i}" for i in b["items"]))
        elif b["type"] == "table":
            parts.append("\n".join("| " + " | ".join(r) + " |" for r in [b["columns"], *b["rows"]]))
        else:
            parts.append(b["text"])
    return "\n\n".join(parts)


def chunk_document_json(doc: Document, target_tokens: int, max_tokens: int, overlap_tokens: int, min_tokens: int):
    entries, source = extract_toc(doc)
    sections = segment(doc, entries)
    units = build_units(sections, target_tokens, max_tokens, min_tokens)
    chunks: list[Chunk] = []
    for u in units:
        packs = pack_unit(u.blocks, target_tokens, overlap_tokens, min_tokens, max_tokens)
        last_heading = ""
        for k, pack in enumerate(packs):
            pages = sorted({p for p, _, _ in pack})
            content = [b for _, b, _ in pack]
            payload: dict = {
                "document": doc.title,
                "section": {"title": u.title, "path": u.path, "level": u.level},
            }
            if last_heading and content[0]["type"] != "heading":
                payload["subsection"] = last_heading
            payload["pages"] = {"start": pages[0], "end": pages[-1]}
            if len(packs) > 1:
                payload["part"] = {"index": k + 1, "of": len(packs)}
            payload["content"] = content
            for b in content:
                if b["type"] == "heading":
                    last_heading = b["text"]
            body = blocks_to_text(content)
            text = f"# {' > '.join(u.path)}\n\n{body}"
            idx = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}_j{idx:04d}",
                    doc_id=doc.doc_id,
                    index=idx,
                    text=text,
                    pages=pages,
                    section=" > ".join(u.path)[:120],
                    n_tokens=count_tokens(render_json_chunk(payload)),
                    quality=quality_score(body),
                    n_numbers=len(extract_numbers(body, include_years=False)),
                    payload=payload,
                )
            )
    toc_info = {
        "document": doc.filename,
        "title": doc.title,
        "toc_source": source,
        "n_entries": len(entries),
        "n_sections": len(sections),
        "n_units": len(units),
        "entries": [e.to_dict() for e in entries],
    }
    return chunks, toc_info


def run_json_chunking(docs: list[Document], cfg_chunk) -> tuple[list[Chunk], list[dict]]:
    all_chunks: list[Chunk] = []
    tocs: list[dict] = []
    for doc in docs:
        chunks, toc = chunk_document_json(
            doc, cfg_chunk["target_tokens"], cfg_chunk["max_tokens"], cfg_chunk["overlap_tokens"], cfg_chunk["min_tokens"]
        )
        kept = [c for c in chunks if c.quality >= cfg_chunk["min_quality"] and c.n_tokens >= cfg_chunk["min_tokens"]]
        located = sum(1 for e in toc["entries"] if e["located"])
        log.info(
            "  %s: ToC from %s (%d entries, %d pinned to a heading line) -> %d sections -> %d JSON chunks "
            "(%d kept after quality filter, avg %d tokens)",
            doc.filename,
            toc["toc_source"],
            toc["n_entries"],
            located,
            toc["n_sections"],
            len(chunks),
            len(kept),
            sum(c.n_tokens for c in kept) / max(1, len(kept)),
        )
        toc["n_chunks"], toc["n_kept"] = len(chunks), len(kept)
        tocs.append(toc)
        all_chunks.extend(kept)
    if not all_chunks:
        raise ValueError("JSON chunking produced no usable chunks - is the document text extractable?")
    return all_chunks, tocs
