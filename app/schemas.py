from typing import List, Optional

from pydantic import BaseModel, Field


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
    comparison: str
    your_brand: str
    competitor: str


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    competitors: List[Competitor]
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
    target_search_queries: List[SearchQuery]
    seo_improvements: SEO
    top_services: List[Service]
    target_audience: TargetAudience


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
