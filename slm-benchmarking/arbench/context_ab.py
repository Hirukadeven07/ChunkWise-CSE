"""Controlled A/B for the context format: JSON chunks vs. the same chunks as flat text.

A `toc_json` run and a `packed` run differ in more than the format (different chunk boundaries,
different generated questions). To isolate the effect of *feeding JSON to the SLM*, this builds a
copy of a `toc_json` benchmark in which every model-facing context is re-rendered as flat text from
the very same chunks. Questions, gold answers, retrieval results and the judge's evidence (kept as
JSON) are identical, so the only difference between the two evaluations is the context format.

    from arbench.context_ab import make_text_context_benchmark
    make_text_context_benchmark("outputs/colab_json_run", "outputs/colab_json_textctx_bench")
    # then: python run_benchmark.py --reuse-benchmark outputs/colab_json_textctx_bench --run-name ...
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from arbench.evals.tasks import format_excerpts
from arbench.pipeline.chunk import Chunk
from arbench.utils import read_jsonl, write_json, write_jsonl

TEXT_CONTEXT_LABEL = "toc_json_text_context"


def make_text_context_benchmark(src_run: str | Path, dst_run: str | Path) -> Path:
    src_run, dst_run = Path(src_run), Path(dst_run)
    chunks = {r["chunk_id"]: Chunk(**{**r, "payload": None}) for r in read_jsonl(src_run / "02_chunks.jsonl")}
    items = read_jsonl(src_run / "benchmark" / "annual_report_benchmark.jsonl")
    if not any(it.get("context_format") == "json" for it in items):
        raise ValueError(f"{src_run} is not a toc_json run (its contexts are not JSON)")

    if dst_run.exists():
        shutil.rmtree(dst_run)
    shutil.copytree(src_run / "benchmark", dst_run / "benchmark")
    for it in items:
        if it.get("context_format") != "json":
            continue
        ctx = [chunks[c] for c in it["context_chunk_ids"] if c in chunks]
        it["context"] = format_excerpts(ctx) if ctx else ""
        it["context_format"] = "text"  # evidence for the judge stays JSON -> both arms graded identically
    write_jsonl(dst_run / "benchmark" / "annual_report_benchmark.jsonl", items)

    meta = json.loads((src_run / "benchmark_meta.json").read_text(encoding="utf-8"))
    meta["chunking"] = TEXT_CONTEXT_LABEL
    meta["ab_source_run"] = src_run.name
    write_json(dst_run / "benchmark_meta.json", meta)
    shutil.copy2(src_run / "02_chunks.jsonl", dst_run / "02_chunks.jsonl")
    return dst_run
