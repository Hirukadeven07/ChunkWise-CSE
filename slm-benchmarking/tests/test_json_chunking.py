"""ToC recovery, section segmentation and ToC-structured JSON chunks (chunking.strategy = toc_json)."""

from __future__ import annotations

import json

from arbench import orchestrator, prompts
from arbench.config import load_config
from arbench.evals.tasks import build_messages
from arbench.pipeline.ingest import Document, Page
from arbench.pipeline.json_chunk import chunk_document_json, parse_table, split_block, type_block
from arbench.pipeline.toc import extract_toc, segment
from arbench.utils import read_jsonl
from tests.conftest import FakeClient, make_sample_pdf

PROSE = (
    "Group revenue increased by 8.4% to 2,415.6 million, compared with 2,228.4 million in the prior year. "
    "Operating profit rose to 312.9 million as automation lowered unit costs across the network. "
)


def _doc(pages: list[str], outline=None) -> Document:
    meta = {"pages": len(pages)}
    if outline is not None:
        meta["outline"] = outline
    return Document("d1", "r.pdf", "Acme Annual Report 2024", [Page(i + 1, t) for i, t in enumerate(pages)], meta)


def test_outline_toc_pins_sections_to_heading_lines():
    pages = [
        "Acme Annual Report 2024",
        f"#### **CHAIR'S STATEMENT**\n\n{PROSE}\n\n#### **FINANCIAL REVIEW**\n\n{PROSE}",
        f"{PROSE}\n\n###### Segment results\n\n{PROSE}",
    ]
    # bookmarks lost a space ("FINANCIALREVIEW") and both sections start on page 2
    outline = [[1, "STRATEGIC REPORT", 2], [2, "CHAIR'S STATEMENT", 2], [2, "FINANCIALREVIEW", 2]]
    entries, source = extract_toc(_doc(pages, outline))
    assert source == "outline"
    fin = entries[-1]
    assert fin.title == "FINANCIAL REVIEW" and fin.located and fin.line > 0
    assert fin.path == ["STRATEGIC REPORT", "FINANCIAL REVIEW"]

    sections = segment(_doc(pages, outline), entries)
    titles = [s.title for s in sections]
    assert titles == ["Front matter", "CHAIR'S STATEMENT", "FINANCIAL REVIEW"]
    assert sections[2].pages == [2, 3]  # the section runs on until the next ToC entry

    # a broken heading in the text must not overwrite a correct bookmark title
    entries, _ = extract_toc(_doc(["x", f"## **TRA NSPORTATION**\n\n{PROSE}", "y"],
                                  [[1, "A", 1], [1, "TRANSPORTATION", 2], [1, "B", 3]]))
    assert entries[1].title == "TRANSPORTATION" and entries[1].located


def test_contents_page_offset_is_learned():
    contents = "## CONTENTS\n\n" + "\n".join(
        f"- {n} {t}" for n, t in [(1, "Highlights"), (2, "Chair's Statement"), (3, "Financial Review"),
                                   (4, "Principal Risks"), (5, "Governance")]
    )
    body = [f"# {t}\n\n{PROSE}" for t in ["Highlights", "Chair's Statement", "Financial Review", "Principal Risks", "Governance"]]
    doc = _doc(["Cover page", contents] + body)  # printed page 1 == PDF page 3
    entries, source = extract_toc(doc)
    assert source == "contents_page"
    assert {e.title: e.page for e in entries}["Financial Review"] == 5


def test_headings_fallback_for_text_reports():
    doc = _doc([f"# Chair's Statement\n\n{PROSE}", f"# Financial Review\n\n{PROSE}\n\n## Segments\n\n{PROSE}"])
    entries, source = extract_toc(doc)
    assert source == "headings"
    assert [e.path for e in entries][-1] == ["Financial Review", "Segments"]


def test_parse_table_keeps_alignment_and_drops_empty_columns():
    t = parse_table([
        "|||**GROUP**||||",
        "|---|---|---|---|---|",
        "|Revenue|1|1,200<br>||,,<br>900|",
        "|Profit||300||250|",
    ])
    # leading empty header cells survive (alignment); the all-empty 4th column is dropped
    assert t["columns"] == ["", "", "GROUP", ""]
    assert t["rows"] == [["Revenue", "1", "1,200", "900"], ["Profit", "", "300", "250"]]


def test_financial_statement_header_rows_become_column_labels():
    t = parse_table([
        "|||**GRO**|**UP**|**COMPA**|**NY**|",
        "|---|---|---|---|---|---|",
        "|For the year ended 31 March||**2026**|**2025**|**2026**|**2025**|",
        "|**In Rs.'000s**|**Note**|||||",
        "|Continuing operations||||||",
        "|Total revenue|14|528,850,693|317,377,726|3,847,701|3,180,195|",
    ])
    assert t["columns"] == ["For the year ended 31 March In Rs.'000s", "Note",
                            "GROUP 2026", "GROUP 2025", "COMPANY 2026", "COMPANY 2025"]
    assert t["rows"][0][0] == "Continuing operations"
    # an ordinary table is left alone, and currency codes are never glued together
    plain = parse_table(["| Segment | USD | LKR |", "|---|---|---|", "| Retail | 1.2 | 360 |"])
    assert plain["columns"] == ["Segment", "USD", "LKR"] and plain["rows"] == [["Retail", "1.2", "360"]]


def test_type_block_kinds():
    assert type_block("#### **RISKS**\nKey risks are listed below.") == [
        {"type": "heading", "text": "RISKS"},
        {"type": "paragraph", "text": "Key risks are listed below."},
    ]
    assert type_block("- » first item\n  continued\n- second")[0] == {"type": "list", "items": ["first item continued", "second"]}
    assert type_block("_Source: IMF_")[0]["text"] == "Source: IMF"
    assert type_block("**9**") == []


def test_split_block_repeats_table_columns():
    big = {"type": "table", "columns": ["Item", "2024"], "rows": [[f"Line {i}", f"{i},000"] for i in range(400)]}
    parts = split_block(big, 300)
    assert len(parts) > 1 and all(p["columns"] == ["Item", "2024"] for p in parts)
    assert sum(len(p["rows"]) for p in parts) == 400


def test_json_chunks_are_valid_json_and_respect_sections():
    pages = [f"# Chair's Statement\n\n{PROSE * 3}", f"# Financial Review\n\n{PROSE * 3}\n\n| Segment | 2024 |\n|---|---|\n| Retail | 349.3 |"]
    chunks, toc = chunk_document_json(_doc(pages), target_tokens=150, max_tokens=300, overlap_tokens=20, min_tokens=30)
    assert toc["toc_source"] == "headings"
    assert chunks
    for c in chunks:
        obj = json.loads(c.prompt_text)
        assert obj["document"] == "Acme Annual Report 2024"
        assert len({tuple(obj["section"]["path"])}) == 1
        assert c.section == " > ".join(obj["section"]["path"])
    fin = [c for c in chunks if c.payload["section"]["title"] == "Financial Review"]
    assert any(b["type"] == "table" for c in fin for b in c.payload["content"])
    assert all(c.pages == [2] for c in fin)  # never mixed with the chair's statement


def test_eval_prompt_explains_json_context():
    item = {"task": "grounded_qa", "question": "q?", "context": "[{}]", "context_format": "json"}
    assert prompts.JSON_EXCERPTS_NOTE.strip() in build_messages(item)[0]["content"]
    item["context_format"] = "text"
    assert "JSON objects" not in build_messages(item)[0]["content"]


# ----------------------------------------------------------------------------- end to end


def _cfg(tmp_path, input_dir):
    return load_config(profile="smoke", overrides={
        "input_dir": str(input_dir),
        "output_dir": str(tmp_path / "outputs"),
        "cache_path": None,
        "models": [{"name": "alpha:1b"}, {"name": "beta:2b"}, {"name": "gamma:3b"}],
        "chunking": {"strategy": "toc_json", "target_tokens": 120, "max_tokens": 200, "overlap_tokens": 20,
                     "min_tokens": 40, "min_quality": 0.2},
        "generation": {"single_hop": {"num_chunks": 4, "questions_per_chunk": 2}, "multi_hop": {"num_groups": 1},
                       "multi_choice": {"num_chunks": 2, "questions_per_chunk": 1}},
        "evaluation": {"unanswerable_fraction": 0.5},
        "scoring": {"bootstrap_samples": 200},
    })


class RecordingClient(FakeClient):
    prompts_seen: list[str] = []

    def chat(self, model, req):
        RecordingClient.prompts_seen.append("\n".join(m["content"] for m in req.messages))
        return super().chat(model, req)


def test_full_pipeline_with_json_chunks(tmp_path, monkeypatch, sample_report_dir):
    RecordingClient.prompts_seen = []
    monkeypatch.setattr(orchestrator, "OllamaClient", RecordingClient)
    run_dir = orchestrator.run(_cfg(tmp_path, sample_report_dir), run_name="json")

    toc = json.loads((run_dir / "02_toc.json").read_text(encoding="utf-8"))
    assert toc[0]["toc_source"] == "headings" and toc[0]["n_entries"] >= 4
    chunks = json.loads((run_dir / "02_chunks.json").read_text(encoding="utf-8"))
    assert chunks and all("section" in c and "content" in c for c in chunks)

    items = read_jsonl(run_dir / "benchmark" / "annual_report_benchmark.jsonl")
    assert items and all(i["context_format"] == "json" for i in items)
    for i in items:
        if i["context"]:
            assert isinstance(json.loads(i["context"]), list)  # the excerpts the SLMs read are a JSON array

    assert any("## Input format" in p for p in RecordingClient.prompts_seen)  # generators were told about JSON
    assert any(prompts.JSON_EXCERPTS_NOTE.strip() in p for p in RecordingClient.prompts_seen)  # eval + judge too
    res = json.loads((run_dir / "results" / "results.json").read_text(encoding="utf-8"))
    assert res["chunking"] == "toc_json"
    assert "chunking `toc_json`" in (run_dir / "reports" / "comparison_report.md").read_text(encoding="utf-8")


def test_text_context_ab_reuses_the_same_questions(tmp_path, monkeypatch, sample_report_dir):
    from arbench.context_ab import TEXT_CONTEXT_LABEL, make_text_context_benchmark

    monkeypatch.setattr(orchestrator, "OllamaClient", FakeClient)
    cfg = _cfg(tmp_path, sample_report_dir)
    json_run = orchestrator.run(cfg, run_name="json_gen", generate_only=True)
    ab = make_text_context_benchmark(json_run, tmp_path / "ab_bench")

    a = read_jsonl(json_run / "benchmark" / "annual_report_benchmark.jsonl")
    b = read_jsonl(ab / "benchmark" / "annual_report_benchmark.jsonl")
    assert [i["item_id"] for i in a] == [i["item_id"] for i in b]
    for x, y in zip(a, b):
        assert x["question"] == y["question"] and x["evidence"] == y["evidence"]  # judge input unchanged
        if y["context"]:
            assert y["context_format"] == "text" and y["context"].startswith("[Excerpt 1")

    run = orchestrator.run(cfg, run_name="ab_eval", reuse_benchmark=str(ab))
    res = json.loads((run / "results" / "results.json").read_text(encoding="utf-8"))
    assert res["chunking"] == TEXT_CONTEXT_LABEL


def test_json_chunks_from_pdf_outline(tmp_path):
    import pymupdf as fitz

    from arbench.pipeline.ingest import ingest_file

    pdf = make_sample_pdf(tmp_path / "report.pdf")
    with fitz.open(pdf) as d:
        d.set_toc([[1, "Chair's Statement", 2], [1, "Financial Review", 3], [1, "Principal Risks", 4],
                   [1, "Sustainability", 5], [1, "Governance", 6]])
        d.saveIncr()
    cfg = load_config()
    doc = ingest_file(pdf, tmp_path, cfg["ingestion"])
    assert doc.meta["outline"][1][1] == "Financial Review"
    chunks, toc = chunk_document_json(doc, 700, 1000, 80, 20)
    assert toc["toc_source"] == "outline"
    assert {c.payload["section"]["title"] for c in chunks} >= {"Financial Review", "Governance"}
