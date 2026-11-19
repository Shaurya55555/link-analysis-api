"""Runs real analyses against a few sites and records measured latency, tokens and validity.

Usage: python scripts/benchmark.py https://site1 https://site2 ...
Writes samples/<host>.json and prints a markdown table.
"""
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.analysis_service import analyze  # noqa: E402

out_dir = Path(__file__).resolve().parent.parent / "samples"
out_dir.mkdir(exist_ok=True)
rows = []
for url in sys.argv[1:]:
    t0 = time.perf_counter()
    try:
        r = analyze(url)
        total = time.perf_counter() - t0
        (out_dir / (urlparse(r.website).hostname + ".json")).write_text(r.model_dump_json(indent=2), encoding="utf-8")
        m = r.meta
        rows.append((url, m.model, "valid", round(total, 1), m.llm_seconds, m.input_tokens, m.output_tokens, m.pages_read))
    except Exception as exc:  # noqa: BLE001
        rows.append((url, "-", f"FAILED: {exc.__class__.__name__}: {str(exc)[:80]}", round(time.perf_counter() - t0, 1), "-", "-", "-", "-"))
print("| URL | Model | Result | Total s | LLM s | In tok | Out tok | Pages |")
print("|---|---|---|---|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x) for x in r) + " |")
