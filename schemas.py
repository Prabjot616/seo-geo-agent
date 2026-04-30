from typing import Optional
from pydantic import BaseModel


# ============================================================
# SCRAPING / PRODUCT DATA
# ============================================================

class MetaAnalysis(BaseModel):
    present: bool
    content: Optional[str] = None
    length: Optional[int] = None
    issues: list[str]


class HeadingAnalysis(BaseModel):
    h1_count: int
    h1_texts: list[str]
    h2_texts: list[str]
    h3_texts: list[str]
    issues: list[str]


class SocialSignals(BaseModel):
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    twitter_card: Optional[str] = None
    twitter_site: Optional[str] = None
    profiles_found: dict        # {"facebook": "url", "linkedin": "url", ...}
    issues: list[str]           # ["Missing og:image", ...]


class ProductData(BaseModel):
    url: str
    title: str
    site_name: Optional[str] = None
    meta: MetaAnalysis
    headings: HeadingAnalysis
    images_total: int
    images_missing_alt: list[str]
    schema_types: list[str]
    canonical_url: Optional[str] = None
    word_count: int
    paragraphs: list[str]
    links: list[str]
    is_shopify: bool
    social: Optional[SocialSignals] = None
    # Shopify-specific fields
    tags: Optional[list[str]] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    price: Optional[str] = None


# ============================================================
# SEO
# ============================================================

class KeywordSet(BaseModel):
    primary: list[str]
    long_tail: list[str]
    informational: list[str]
    commercial: list[str]


class SEOData(BaseModel):
    keywords: KeywordSet
    target_audience: str
    search_intent: str
    title_issues: list[str]
    meta_issues: list[str]
    heading_issues: list[str]
    image_alt_issues: list[str]
    social_issues: list[str]    # copied from product_data.social.issues
    content_score: int          # 0-100
    recommendations: list[str]


# ============================================================
# COMPETITORS
# ============================================================

class CompetitorProfile(BaseModel):
    url: str
    title: str
    meta_description: Optional[str] = None
    h1_tags: list[str]
    h2_tags: list[str]
    schema_types: list[str]
    has_schema: bool
    keywords: Optional[str] = None
    strengths: list[str]


class CompetitorData(BaseModel):
    target_url: str
    competitors: list[CompetitorProfile]
    keyword_gaps: list[str]
    content_gaps: list[str]
    schema_gaps: list[str]
    what_competitors_do_better: list[str]
    opportunities: list[str]


# ============================================================
# TECHNICAL SEO
# ============================================================

class TechnicalIssue(BaseModel):
    id: str
    title: str
    description: str
    score: float
    impact: str                         # "high" | "medium" | "low"
    savings_ms: Optional[float] = None


class TechnicalSEOData(BaseModel):
    url: str
    mobile_scores: dict                 # performance, accessibility, seo, best_practices (0-100)
    desktop_scores: dict                # same keys — desktop run
    score_delta: dict                   # mobile vs desktop diff per category
    lcp_ms: Optional[float] = None
    cls_score: Optional[float] = None
    fid_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    render_blocking_resources: list[str]
    opportunities: list[TechnicalIssue]
    diagnostics: list[TechnicalIssue]
    technical_seo_score: int            # 0-10
    cwv_display: Optional[dict] = None


# ============================================================
# GEO
# ============================================================

class GEOData(BaseModel):
    ai_visibility_score: int            # 0-10
    entity_optimisation: str
    faq_opportunities: list[str]
    content_gaps: list[str]
    geo_recommendations: list[str]
    schema_suggestions: list[str]


class LocalGEOData(BaseModel):
    global_market_opportunities: list[dict]   # [{region, keywords, volume, recommendations}]
    language_gaps: list[str]
    regional_keywords: list[dict]             # [{keyword, region, intent}]
    local_schema_needed: bool
    local_schema_fields: list[str]
    ai_visibility_by_region: list[dict]       # [{region, visibility, content_needed}]
    local_geo_score: int                      # 0-10


# ============================================================
# FINAL REPORT
# ============================================================

class DeficiencyItem(BaseModel):
    issue: str
    impact: str     # "High" | "Medium" | "Low"
    fix: str


class ActionPoint(BaseModel):
    rank: int
    action: str
    expected_outcome: str
    effort: str     # "Hours" | "Days" | "Weeks"


class OptimisedContent(BaseModel):
    seo_title: str
    meta_description: str
    meta_description_variants: list[str]    # 3 variants at ~120, ~140, ~155 chars
    primary_keywords: list[str]
    long_tail_keywords: list[str]


class SchemaMarkupItem(BaseModel):
    type: str           # e.g. "Organization"
    description: str    # why it's needed
    json_ld: str        # ready-to-paste JSON-LD code as a string


class GeneratedReport(BaseModel):
    scores: dict                            # {seo, geo, technical_seo, competitor_position, local_geo, overall}
    seo_deficiencies: list[DeficiencyItem]
    geo_deficiencies: list[DeficiencyItem]
    technical_deficiencies: list[DeficiencyItem]
    social_deficiencies: list[DeficiencyItem]   # new — social signal gaps
    competitor_gaps: list[DeficiencyItem]
    priority_actions: list[ActionPoint]
    optimised_content: OptimisedContent
    schema_markup_needed: list[SchemaMarkupItem]  # now includes actual JSON-LD code