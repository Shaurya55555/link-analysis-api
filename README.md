# AI Website Link Analysis API

FastAPI service that takes a website URL, extracts the real content, and uses an LLM to return five structured sections: competitor comparison, top 5 search queries, SEO improvements, top 5 services and target audience.

## Live demo

- Base URL: https://link-analysis-api-self.vercel.app
- Endpoint: `POST https://link-analysis-api-self.vercel.app/link-analysis`
- API docs: https://link-analysis-api-self.vercel.app/docs

```bash
curl -X POST https://link-analysis-api-self.vercel.app/link-analysis   -H "Content-Type: application/json" -d '{"url": "https://zerodha.com"}'
```

Deployed on Vercel (Python, 60 s function limit). An analysis takes roughly 15 to 30 s. Real outputs from the benchmark runs are in [`samples/`](samples/).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then set GEMINI_API_KEY (or OPENAI_API_KEY + LLM_PROVIDER=openai)
uvicorn app.main:app --host 0.0.0.0 --port 4000
```

Docs: http://localhost:4000/docs. Tests (LLM and network mocked, no key needed): `pytest -q`.

## Request and response

```bash
curl -X POST http://localhost:4000/link-analysis \
  -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
```

Response shape (abridged):

```json
{
  "success": true,
  "website": "https://example.com/",
  "brand": {"name": "...", "category": "...", "summary": "..."},
  "analysis": {
    "competitor_analysis": {"brand_overview": "...", "competitors": [{"name": "...", "source": "ai_suggested", "live_check": {"reachable": true, "page_title": "..."}}], "comparison_table": [], "strengths": [], "opportunities": []},
    "target_search_queries": [{"query": "...", "platforms": ["Google", "LinkedIn"], "intent": "...", "search_volume": null}],
    "seo_improvements": {"primary_keyword": "...", "secondary_keywords": [], "long_tail_keywords": [], "recommended_title": "...", "meta_description": "...", "supporting_content": {"faqs": [], "blog_topics": []}},
    "top_services": [{"name": "...", "source": "website"}],
    "target_audience": {"primary_audience": [], "secondary_audience": [], "industries": [], "customer_needs": []}
  },
  "meta": {"provider": "gemini", "model": "...", "llm_seconds": 0.0, "input_tokens": 0, "output_tokens": 0, "cached": false, "pages_read": 3}
}
```

Errors always use `{"success": false, "error": "...", "code": "..."}`: `400 invalid_or_blocked_url`, `422 invalid_request`, `502 website_unreachable`, `502 llm_failure`.

## How it works

1. **Validate** (`utils/validators.py`): http/https only, no credentials in URL, limited ports, DNS is resolved and every address must be public. This blocks localhost, private ranges, link-local (cloud metadata 169.254.169.254) and reserved ranges.
2. **Fetch** (`services/website_extractor.py`): redirects are followed manually and each hop is re-validated, so a public URL cannot redirect into an internal one. Response size is capped, content type must be HTML, timeouts are enforced.
3. **Extract**: title, meta and OpenGraph tags, headings, and body text from `<main>`; scripts, nav, footer and forms are stripped and duplicate lines dropped to save tokens. Up to 2 extra same-site pages (about, services, products) are read from the nav links.
4. **Analyze** (`services/analysis_service.py`, `llm_service.py`): one LLM call in JSON mode with a strict schema prompt. Output is validated with Pydantic; on a validation error the error is fed back for one retry. A fallback provider is used if the primary one fails.
5. **Verify competitors** (`services/competitor_service.py`): the model's competitor suggestions are labeled `source: "ai_suggested"` and each suggested website is fetched live (SSRF-safe) so the response shows whether it is reachable and its real page title. They are never presented as researched fact.
6. **Cache**: in-memory, 1 hour per URL, so repeat requests cost no tokens.

Design decisions tied to the brief:
- `search_volume` is always `null`. No search-volume data source is integrated, so nothing is invented.
- Services carry `source: "website"` or `"inferred"`.
- API keys come only from environment variables. `.env` is git-ignored.

## LLM research: cost, speed and accuracy

Pricing is USD per 1M tokens, standard paid tier, non-batch, read from the vendors' official pricing pages on 2026-11-19. Model details come from each vendor's model page. Prices change, so recheck before production use.

| Provider | Model | Input / Output | Context (in / out) | Structured JSON | Notes |
|---|---|---|---|---|---|
| Google | `gemini-3.5-flash-lite` | $0.30 / $2.50 | 1,048,576 / 65,536 | Supported | Free tier exists; output price includes thinking tokens |
| OpenAI | `gpt-5.6-luna` (nano tier) | $0.20 / $1.20 | 1,050,000 / 128,000 | Supported | Cached input $0.02 |
| OpenAI | `gpt-6-luna` | $0.10 / $0.50 | not verified | not verified | Cheapest listed; not checked further |
| Anthropic | `claude-haiku-5-5` | $0.10 / $0.50 (prompts up to 100K) | 1M / 128K | Not confirmed on its model page | Prompts over 100K cost $0.50 / $2.50 |

Not verified from primary pages in this run: the `gpt-6-luna` context window and JSON support, and whether Haiku 5.5 supports schema-enforced output (this app does not depend on it, because output is validated by Pydantic). Speed and accuracy are not claimed in this table; see the measured results below.

### Estimated cost per analysis (ESTIMATE, not measured)

Assumption: about 4,500 input tokens (12,000 characters of cleaned content plus the instruction prompt) and about 2,500 output tokens.

| Model | Estimated cost per analysis |
|---|---|
| `gemini-3.5-flash-lite` | about $0.0076 |
| `gpt-5.6-luna` | about $0.0039 |
| `claude-haiku-5-5` | about $0.0017 |

At these sizes, output tokens dominate cost, so the long structured JSON is what to optimize first.

### Measured results (real runs, 2026-11-19)

`python scripts/benchmark.py <urls>` against `gemini-3.5-flash-lite` (default settings, 1 run each, so treat as indicative not statistical). Full outputs are in `samples/`.

| Site | Result | End-to-end s | LLM call s | Input tokens | Output tokens | Pages read |
|---|---|---|---|---|---|---|
| stripe.com | valid | 20.6 | 7.79 | 3,239 | 2,614 | 3 |
| zerodha.com | valid | 13.2 | 7.89 | 2,223 | 2,729 | 3 |
| fastapi.tiangolo.com | valid | 15.0 | 7.32 | 3,040 | 2,342 | 3 |

- LLM latency was about 7.3 to 7.9 s per analysis. End-to-end is 13 to 21 s because it also fetches up to 3 site pages and live-checks 3 to 5 suggested competitor sites.
- Real token use was 2.2k to 3.2k input and 2.3k to 2.7k output. At list price ($0.30 / $2.50) that is roughly $0.007 to $0.008 per analysis, in line with the estimate above (measured tokens, calculated cost).
- 3 of 3 runs produced schema-valid JSON after 1 run each. One earlier run on stripe.com returned malformed JSON once; a same-provider retry was added and the re-run passed.
- Not measured: OpenAI and Anthropic models (no keys used), and answer accuracy against ground truth. Accuracy was only spot-checked by reading the outputs: services and brand details matched each site's real content, and suggested competitors were reachable real sites. No accuracy score is claimed.

### Recommendation

- **Primary:** `gemini-3.5-flash-lite`. Native JSON mode, a free tier for development and demos, a very large context window, and a low price. It is the default because it is the cheapest option that can be run at no cost while testing.
- **Fallback:** `gpt-5.6-luna` through the OpenAI-compatible provider (`FALLBACK_PROVIDER=openai`), which is cheaper per token and has a different failure domain.
- **Balancing approach:** use the small model for everything by default; escalate to a larger model only when Pydantic validation fails twice. Final model choice should follow the measured benchmark above, not these estimates.

### Cost optimization applied

- HTML is stripped to meaningful text (no scripts, nav, footer, duplicate lines) and capped at `MAX_CONTENT_CHARS`.
- One LLM call produces all five sections, so the page content is sent once.
- Per-URL cache with TTL avoids repeated calls.
- The instruction prompt is compact and the schema is described once.
- Possible next steps: provider context caching for the fixed instruction prompt, and batch pricing for offline bulk analysis.

## Project layout

```
app/main.py                      FastAPI app and error mapping
app/config.py                    env-based settings
app/schemas.py                   Pydantic request/response/LLM-output models
app/services/website_extractor.py  safe fetch + content extraction
app/services/llm_service.py      Gemini + OpenAI-compatible, JSON mode, fallback
app/services/analysis_service.py prompt, validation/retry, cache
app/services/competitor_service.py live checks of suggested competitors
app/utils/validators.py          URL validation and SSRF protection
tests/test_link_analysis.py      21 tests (SSRF, extraction, LLM handling, API flow)
```

## Limitations

- Sites that render content only with JavaScript return little text and give a clear `website_unreachable` error rather than guessing.
- Competitor suggestions come from the model; the live check only confirms a site exists, not that the strengths and gaps are accurate.
- The in-memory cache is per process.
