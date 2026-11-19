from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class AnalysisRequest(BaseModel):
    url: str = Field(..., examples=["https://example.com"])


class Brand(BaseModel):
    name: str
    category: str
    summary: str


class Competitor(BaseModel):
    name: str
    website: Optional[str] = None
    main_service: str = ""
    target_market: str = ""
    strengths: List[str] = []
    weaknesses: List[str] = []
    source: str = "ai_suggested"  # always labeled, never presented as researched fact
    live_check: Optional[dict] = None  # filled by competitor_service from a real fetch


class ComparisonRow(BaseModel):
    competitor_name: str = ""
    comparison: str
    your_brand: str
    competitor: str


def _cap(n):
    """Keep at most n items (models sometimes over-deliver); min length is enforced by Field."""
    def _v(cls, v):
        return v[:n] if isinstance(v, list) else v
    return _v


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    competitors: List[Competitor] = Field(min_length=3)
    _cap_c = field_validator("competitors", mode="before")(_cap(5))
    comparison_table: List[ComparisonRow] = []
    strengths: List[str] = []
    opportunities: List[str] = []


class SearchQuery(BaseModel):
    query: str
    platforms: List[str]
    intent: str
    business_relevance: str = ""
    target_audience: str = ""
    search_volume: Optional[int] = None  # stays None: no data source is wired in


class SupportingContent(BaseModel):
    landing_pages: List[str] = []
    industry_pages: List[str] = []
    faqs: List[str] = []
    blog_topics: List[str] = []
    location_pages: List[str] = []


class SEO(BaseModel):
    primary_keyword: str
    secondary_keywords: List[str] = []
    long_tail_keywords: List[str] = []
    business_keywords: List[str] = []
    recommended_title: str
    meta_description: str
    supporting_content: SupportingContent


class Service(BaseModel):
    name: str
    description: str
    target_industry: str = ""
    target_customer: str = ""
    main_benefit: str = ""
    source: str = "website"  # "website" (found on site) or "inferred"


class AudienceSegment(BaseModel):
    audience: str
    business_type: str = ""
    requirement: str = ""


class TargetAudience(BaseModel):
    primary_audience: List[str]
    secondary_audience: List[str] = []
    industries: List[str] = []
    business_types: List[str] = []
    customer_profiles: List[str] = []
    geographic_focus: List[str] = []
    customer_needs: List[str] = []
    interests: List[str] = []
    recommended_segments: List[AudienceSegment] = []


class Analysis(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: List[SearchQuery] = Field(min_length=5)
    seo_improvements: SEO
    top_services: List[Service] = Field(min_length=5)
    target_audience: TargetAudience
    _cap_q = field_validator("target_search_queries", mode="before")(_cap(5))
    _cap_s = field_validator("top_services", mode="before")(_cap(5))


class LLMOutput(BaseModel):
    """Exactly what the LLM must return; validated before anything is sent back."""

    brand: Brand
    analysis: Analysis


class Meta(BaseModel):
    provider: str
    model: str
    llm_seconds: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached: bool = False
    pages_read: int = 1
    request_id: Optional[str] = None
    notes: List[str] = []


class AnalysisResponse(BaseModel):
    success: bool = True
    website: str
    brand: Brand
    analysis: Analysis
    meta: Meta


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: str
