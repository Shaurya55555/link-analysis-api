"""Runs real analyses and records MEASURED latency, tokens, schema validity and groundedness.

Usage: GEMINI_MODEL=<model> python scripts/benchmark.py https://site1 https://site2 ...
Writes samples/<model>/<host>.json and samples/benchmark_<model>.json, prints a markdown table.

Groundedness checks (automatic, no ground-truth labels needed):
  brand_in_site     brand name tokens appear in the fetched page text/title
  services_grounded share of "website"-sourced services whose name keywords appear in the site text
  competitors_live  share of suggested competitors whose homepage was actually reachable
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.analysis_service import analyze  # noqa: E402
from app.services.website_extractor import extract_site  # noqa: E402

STOP = {"and", "the", "for", "with", "services", "service", "solutions", "platform", "tools", "online", "products"}
model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL") or "default"
root = Path(__file__).resolve().parent.parent / "samples"
(out_dir := root / model).mkdir(parents=True, exist_ok=True)


def words(s):
    return {w for w in re.findall(r"[a-z0-9]{4,}", s.lower()) if w not in STOP}


rows, results = [], []
for url in sys.argv[1:]:
    t0 = time.perf_counter()
    try:
        r = analyze(url)
        total = time.perf_counter() - t0
        site = extract_site(url)
        corpus = " ".join(p.title + " " + p.meta_description + " " + " ".join(p.headings) + " " + p.text for p in site.pages).lower()
        brand_ok = any(w in corpus for w in words(r.brand.name)) or r.brand.name.lower() in corpus
        ws = [s for s in r.analysis.top_services if s.source == "website"]
        grounded = [any(w in corpus for w in words(s.name)) for s in ws]
        comps = r.analysis.competitor_analysis.competitors
        live = [bool(c.live_check and c.live_check.get("reachable")) for c in comps]
        m = r.meta
        res = dict(url=url, ok=True, total_s=round(total, 1), llm_s=m.llm_seconds, in_tok=m.input_tokens,
                   out_tok=m.output_tokens, pages=m.pages_read, brand_in_site=brand_ok,
                   services_website=len(ws), services_grounded=round(sum(grounded) / len(ws), 2) if ws else None,
                   competitors=len(comps), competitors_live=round(sum(live) / len(live), 2))
        (out_dir / (urlparse(r.website).hostname + ".json")).write_text(r.model_dump_json(indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        res = dict(url=url, ok=False, total_s=round(time.perf_counter() - t0, 1), error=f"{exc.__class__.__name__}: {str(exc)[:100]}")
    results.append(res)

(root / f"benchmark_{model}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"Model: {model}")
print("| Site | Valid | Total s | LLM s | In tok | Out tok | Brand found | Services grounded | Competitors live |")
print("|---|---|---|---|---|---|---|---|---|")
for r in results:
    if r["ok"]:
        print(f"| {urlparse(r['url']).hostname} | yes | {r['total_s']} | {r['llm_s']} | {r['in_tok']} | {r['out_tok']} | {r['brand_in_site']} | {r['services_grounded']} | {r['competitors_live']} |")
    else:
        print(f"| {urlparse(r['url']).hostname} | NO ({r['error']}) | {r['total_s']} | - | - | - | - | - | - |")
