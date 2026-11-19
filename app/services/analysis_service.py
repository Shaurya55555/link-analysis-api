"""Orchestrates: extract -> prompt LLM -> validate -> verify competitors -> respond."""
import json
import time
from typing import Dict, Optional, Tuple

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import AnalysisResponse, LLMOutput, Meta
from app.services.competitor_service import verify_competitors
from app.services.llm_service import LLMError, generate_json
from app.services.website_extractor import extract_site

SYSTEM_PROMPT = """You are a senior digital-marketing and competitive-intelligence analyst.
You receive text extracted from a real website and return ONE JSON object, nothing else.

Rules:
- Base every statement about the brand on the supplied content. Never produce generic output that could fit any company.
- Competitors: suggest 3 to 5 real, well-known competitors in the same category and region. They are YOUR suggestions, not researched facts. Give each one's real homepage URL only if you are confident; otherwise null.
- Search queries: exactly 5. Cover the platforms Google, Facebook, Instagram, LinkedIn, X across the set; each query lists the platforms where it fits. Do NOT include search volumes or rankings.
- Services: exactly 5. Use services found in the content with source "website". If the site shows fewer than 5, add inferred ones with source "inferred".
- Keep lists concise (3 to 6 items) and every string specific to this business.
- No markdown, no commentary, valid JSON only.

Output schema (keys must match exactly):
{
 "brand": {"name": str, "category": str, "summary": str},
 "analysis": {
  "competitor_analysis": {
    "brand_overview": str,
    "competitors": [{"name": str, "website": str|null, "main_service": str, "target_market": str, "strengths": [str], "weaknesses": [str]}],
    "comparison_table": [{"competitor_name": str, "comparison": str, "your_brand": str, "competitor": str}],
    "strengths": [str], "opportunities": [str]},
  "target_search_queries": [{"query": str, "platforms": [str], "intent": str, "business_relevance": str, "target_audience": str}],
  "seo_improvements": {"primary_keyword": str, "secondary_keywords": [str], "long_tail_keywords": [str], "business_keywords": [str],
    "recommended_title": str (max 60 chars), "meta_description": str (max 160 chars),
    "supporting_content": {"landing_pages": [str], "industry_pages": [str], "faqs": [str], "blog_topics": [str], "location_pages": [str]}},
  "top_services": [{"name": str, "description": str, "target_industry": str, "target_customer": str, "main_benefit": str, "source": "website"|"inferred"}],
  "target_audience": {"primary_audience": [str], "secondary_audience": [str], "industries": [str], "business_types": [str],
    "customer_profiles": [str], "geographic_focus": [str], "customer_needs": [str], "interests": [str],
    "recommended_segments": [{"audience": str, "business_type": str, "requirement": str}]}
 }
}
The comparison_table must compare the brand against each of the first 3 competitors on these 5 rows: Brand Name, Main Service, Target Market, Key Strength, Opportunity (15 rows; set competitor_name on each)."""

_cache: Dict[str, Tuple[float, AnalysisResponse]] = {}


def analyze(url: str, client: Optional[httpx.Client] = None) -> AnalysisResponse:
    settings = get_settings()
    own = client is None
    client = client or httpx.Client(timeout=settings.fetch_timeout_s)
    try:
        site = extract_site(url, client)
        hit = _cache.get(site.url)
        if hit and time.time() - hit[0] < settings.cache_ttl_s:
            cached = hit[1].model_copy(deep=True)
            cached.meta.cached = True
            return cached

        content = site.to_prompt_text(settings.max_content_chars)
        user_msg = f"Website URL: {site.url}\n\nExtracted website content:\n{content}"

        parsed, result, last_err = None, None, ""
        for _ in range(2):  # one retry with the validation error fed back to the model
            prompt = user_msg
            if last_err:
                prompt += f"\n\nYour previous reply failed schema validation: {last_err}\nReturn corrected JSON only."
            result = generate_json(SYSTEM_PROMPT, prompt)
            try:
                parsed = LLMOutput.model_validate(result.data)
                break
            except ValidationError as exc:
                last_err = json.dumps(exc.errors(include_url=False, include_input=False), default=str)[:800]
        if parsed is None or result is None:
            raise LLMError("LLM output failed schema validation twice")

        notes = []
        primary = {"gemini": settings.gemini_model, "openai": settings.openai_model,
                   "anthropic": settings.anthropic_model}.get(settings.llm_provider)
        if result.model != primary:
            notes.append(f"Primary model {primary} was unavailable; answered by fallback model {result.model}.")
        verify_competitors(parsed.analysis.competitor_analysis.competitors, client)
        response = AnalysisResponse(
            website=site.url, brand=parsed.brand, analysis=parsed.analysis,
            meta=Meta(provider=result.provider, model=result.model, llm_seconds=round(result.seconds, 2),
                      input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                      pages_read=len(site.pages), notes=notes))
        _cache[site.url] = (time.time(), response)
        return response
    finally:
        if own:
            client.close()
