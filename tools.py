import os
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote

from .config import SCRAPE_HEADERS, SERPAPI_KEY, PAGESPEED_API_KEY, EXCLUDED_COMPETITOR_DOMAINS
from .schemas import (
    ProductData, MetaAnalysis, HeadingAnalysis, SocialSignals,
    CompetitorProfile, TechnicalSEOData, TechnicalIssue,
)


# ============================================================
# TOOL: Scrape Website
# All deterministic SEO checks done here in Python.
# LLM agents receive pre-computed facts — not raw HTML.
# ============================================================

def scrape_website(url: str) -> dict:
    """Scrapes a website and extracts structured SEO data with deterministic checks.

    Args:
        url: The website URL to scrape.

    Returns:
        A ProductData-compatible dictionary with all SEO signals pre-computed.
    """
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        # ── Title ─────────────────────────────────────────
        title = soup.title.text.strip() if soup.title else ""

        # ── Site name ─────────────────────────────────────
        site_name = ""
        og_site = soup.find("meta", {"property": "og:site_name"})
        if og_site:
            site_name = og_site.get("content", "")

        # ── Meta description: deterministic length + quality check ──
        meta_tag = soup.find("meta", {"name": "description"})
        if not meta_tag:
            meta_tag = soup.find("meta", {"property": "og:description"})

        meta_issues = []
        if not meta_tag:
            meta_issues.append("Missing meta description")
            meta_analysis = MetaAnalysis(present=False, issues=meta_issues)
        else:
            content = meta_tag.get("content", "").strip()
            length = len(content)
            if length == 0:
                meta_issues.append("Meta description is empty")
            elif length > 160:
                meta_issues.append(f"Too long ({length} chars, max 160)")
            elif length < 50:
                meta_issues.append(f"Too short ({length} chars, min 50 recommended)")
            meta_analysis = MetaAnalysis(
                present=True, content=content, length=length, issues=meta_issues
            )

        # ── Headings: full hierarchy analysis ─────────────
        h1_texts = [h.get_text(strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
        h2_texts = [h.get_text(strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)]
        h3_texts = [h.get_text(strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)]

        heading_issues = []
        if len(h1_texts) == 0:
            heading_issues.append("Missing H1 tag")
        if len(h1_texts) > 1:
            heading_issues.append(f"Multiple H1 tags found ({len(h1_texts)}) — only one allowed")
        if len(h2_texts) == 0:
            heading_issues.append("No H2 tags — missing content structure")

        heading_analysis = HeadingAnalysis(
            h1_count=len(h1_texts),
            h1_texts=h1_texts[:3],
            h2_texts=h2_texts[:10],
            h3_texts=h3_texts[:10],
            issues=heading_issues,
        )

        # ── Images: full alt text audit ───────────────────
        all_images = soup.find_all("img")
        images_missing_alt = []
        for img in all_images:
            src = img.get("src", "")
            alt = img.get("alt", "").strip()
            if src and not alt:
                if not src.startswith("http"):
                    src = "https:" + src
                images_missing_alt.append(src)

        # ── Schema types: parsed from JSON-LD ─────────────
        schema_types = []
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    t = data.get("@type")
                    if t:
                        schema_types.append(t if isinstance(t, str) else str(t))
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            t = item.get("@type")
                            if t:
                                schema_types.append(t if isinstance(t, str) else str(t))
            except Exception:
                pass

        # ── Canonical URL ─────────────────────────────────
        canonical_tag = soup.find("link", rel="canonical")
        canonical_url = canonical_tag.get("href") if canonical_tag else None

        # ── Paragraphs + word count ───────────────────────
        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 30
        ]
        word_count = len(soup.get_text().split())

        # ── Links ─────────────────────────────────────────
        links = [
            a["href"] for a in soup.find_all("a", href=True)
            if a["href"].startswith("http")
        ]

        # ── Social signals — pure Python, no LLM ──────────
        def get_meta(prop=None, name=None):
            if prop:
                tag = soup.find("meta", {"property": prop})
            else:
                tag = soup.find("meta", {"name": name})
            return tag.get("content", "").strip() if tag else ""

        social_profiles = {}
        social_patterns = {
            "facebook":  "facebook.com/",
            "twitter":   "twitter.com/",
            "linkedin":  "linkedin.com/company",
            "instagram": "instagram.com/",
            "youtube":   "youtube.com/",
        }
        for link in links:
            for platform, pattern in social_patterns.items():
                if pattern in link and platform not in social_profiles:
                    social_profiles[platform] = link

        og_title       = get_meta(prop="og:title")
        og_description = get_meta(prop="og:description")
        og_image       = get_meta(prop="og:image")
        twitter_card   = get_meta(name="twitter:card")
        twitter_site   = get_meta(name="twitter:site")

        social_issues = []
        if not og_title:
            social_issues.append("Missing og:title — social shares show page URL instead of title")
        if not og_description:
            social_issues.append("Missing og:description — social shares have no preview text")
        if not og_image:
            social_issues.append("Missing og:image — no image shown when link is shared")
        if not twitter_card:
            social_issues.append("Missing twitter:card — Twitter/X previews won't render")
        if not social_profiles:
            social_issues.append("No social media profile links found on page")
        elif len(social_profiles) < 2:
            social_issues.append(f"Only {len(social_profiles)} social profile linked — consider adding more")

        social = SocialSignals(
            og_title=og_title or None,
            og_description=og_description or None,
            og_image=og_image or None,
            twitter_card=twitter_card or None,
            twitter_site=twitter_site or None,
            profiles_found=social_profiles,
            issues=social_issues,
        )

        # ── Shopify product JSON (if applicable) ──────────
        product_json_url = url.rstrip("/") + ".json"
        try:
            json_resp = requests.get(product_json_url, headers=SCRAPE_HEADERS, timeout=10)
            if json_resp.status_code == 200:
                pdata = json_resp.json().get("product", {})
                if pdata:
                    shopify_title = pdata.get("title", title)
                    shopify_desc = BeautifulSoup(
                        pdata.get("body_html", ""), "html.parser"
                    ).get_text()
                    tags = pdata.get("tags", [])
                    vendor = pdata.get("vendor", "")
                    product_type = pdata.get("product_type", "")
                    variants = pdata.get("variants", [])
                    price = variants[0].get("price", "") if variants else ""
                    return ProductData(
                        url=url,
                        title=shopify_title,
                        site_name=site_name,
                        meta=meta_analysis,
                        headings=heading_analysis,
                        images_total=len(all_images),
                        images_missing_alt=images_missing_alt[:10],
                        schema_types=schema_types,
                        canonical_url=canonical_url,
                        word_count=word_count,
                        paragraphs=[shopify_desc[:500]] + paragraphs[:9],
                        links=links[:20],
                        is_shopify=True,
                        social=social,
                        tags=tags,
                        vendor=vendor,
                        product_type=product_type,
                        price=price,
                    ).model_dump()
        except Exception:
            pass

        return ProductData(
            url=url,
            title=title,
            site_name=site_name,
            meta=meta_analysis,
            headings=heading_analysis,
            images_total=len(all_images),
            images_missing_alt=images_missing_alt[:10],
            schema_types=schema_types,
            canonical_url=canonical_url,
            word_count=word_count,
            paragraphs=paragraphs[:10],
            links=links[:20],
            is_shopify=False,
            social=social,
        ).model_dump()

    except Exception as e:
        return {"error": str(e), "url": url}


# ============================================================
# TOOL: Scrape Competitor
# ============================================================

def scrape_competitor(url: str) -> dict:
    """Scrapes a competitor website and extracts key SEO signals.

    Args:
        url: The competitor website URL to scrape.

    Returns:
        A CompetitorProfile-compatible dictionary.
    """
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        title = soup.title.text.strip() if soup.title else ""

        meta = soup.find("meta", {"name": "description"})
        desc = meta.get("content", "") if meta else ""

        meta_kw = soup.find("meta", {"name": "keywords"})
        keywords = meta_kw.get("content", "") if meta_kw else ""

        h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
        h2_tags = [h.get_text(strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)]

        schema_types = []
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    t = data.get("@type")
                    if t:
                        schema_types.append(str(t))
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            t = item.get("@type")
                            if t:
                                schema_types.append(str(t))
            except Exception:
                pass

        return CompetitorProfile(
            url=url,
            title=title,
            meta_description=desc or None,
            h1_tags=h1_tags[:3],
            h2_tags=h2_tags[:8],
            schema_types=[s for s in schema_types if s],
            has_schema=len(schema_types) > 0,
            keywords=keywords or None,
            strengths=[],   # LLM fills this in competitor_agent
        ).model_dump()

    except Exception as e:
        return {"url": url, "error": str(e)}


# ============================================================
# TOOL: Discover Competitors
# Chain: SerpAPI → DuckDuckGo scrape → empty (LLM fallback)
# ============================================================

def discover_competitors(keywords: list, target_url: str) -> dict:
    """Finds real competitors from search results.

    Tries SerpAPI first. If it fails for any reason (quota exceeded,
    invalid key, timeout, HTTP error), automatically falls back to
    DuckDuckGo HTML scraping which requires no API key.

    Args:
        keywords: List of primary keywords to search.
        target_url: The target site URL to exclude from results.

    Returns:
        Dict with competitor_urls list, source ("serpapi"|"duckduckgo"|"none"),
        fallback bool, and status fields for diagnostics.
    """
    target_domain = urlparse(target_url).netloc.replace("www.", "")

    def extract_domain(url):
        return urlparse(url).netloc.replace("www.", "")

    def is_valid_competitor(url):
        domain = extract_domain(url)
        return (
            domain
            and domain != target_domain
            and domain not in EXCLUDED_COMPETITOR_DOMAINS
            and len(domain) > 3
        )

    # ── METHOD 1: SerpAPI ─────────────────────────────────
    def try_serpapi(keywords):
        api_key = os.getenv("SERPAPI_KEY")
        if not api_key:
            return {}, "no_key"

        results = {}
        try:
            for keyword in keywords[:3]:
                response = requests.get(
                    "https://serpapi.com/search",
                    params={
                        "q": keyword,
                        "api_key": api_key,
                        "num": 10,
                        "gl": "us",
                        "hl": "en",
                    },
                    timeout=15,
                )
                if response.status_code == 429:
                    return {}, "quota_exceeded"
                if response.status_code == 401:
                    return {}, "invalid_key"
                if response.status_code != 200:
                    return {}, f"http_{response.status_code}"

                data = response.json()
                if "error" in data:
                    return {}, f"serpapi_error: {data['error']}"

                for r in data.get("organic_results", []):
                    link = r.get("link", "")
                    if link and is_valid_competitor(link):
                        results[link] = r.get("title", link)
                    if len(results) >= 5:
                        break
                if len(results) >= 5:
                    break

            return results, "ok"
        except requests.exceptions.Timeout:
            return {}, "timeout"
        except Exception as e:
            return {}, f"exception: {str(e)}"

    # ── METHOD 2: DuckDuckGo HTML scrape ──────────────────
    # No API key. No rate limits in practice. Always available.
    def try_duckduckgo(keywords):
        results = {}
        ddg_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            for keyword in keywords[:3]:
                response = requests.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": keyword, "kl": "us-en"},
                    headers=ddg_headers,
                    timeout=15,
                )
                if response.status_code != 200:
                    continue

                soup = BeautifulSoup(response.text, "html.parser")

                for a in soup.select("a.result__a"):
                    href = a.get("href", "")
                    title = a.get_text(strip=True)

                    # DDG wraps URLs in a redirect — unwrap it
                    if "uddg=" in href:
                        try:
                            href = unquote(href.split("uddg=")[1].split("&")[0])
                        except Exception:
                            continue

                    if href.startswith("http") and is_valid_competitor(href):
                        results[href] = title

                    if len(results) >= 5:
                        break
                if len(results) >= 5:
                    break

            return results, "ok" if results else "no_results"
        except requests.exceptions.Timeout:
            return {}, "timeout"
        except Exception as e:
            return {}, f"exception: {str(e)}"

    # ── EXECUTION ─────────────────────────────────────────
    serpapi_results, serpapi_status = try_serpapi(keywords)

    if serpapi_results:
        return {
            "competitor_urls": [
                {"url": url, "title": title}
                for url, title in list(serpapi_results.items())[:5]
            ],
            "source": "serpapi",
            "fallback": False,
            "serpapi_status": "ok",
        }

    # SerpAPI failed — try DuckDuckGo
    ddg_results, ddg_status = try_duckduckgo(keywords)

    if ddg_results:
        return {
            "competitor_urls": [
                {"url": url, "title": title}
                for url, title in list(ddg_results.items())[:5]
            ],
            "source": "duckduckgo",
            "fallback": True,
            "serpapi_status": serpapi_status,
            "ddg_status": "ok",
        }

    # Both failed — return empty, agent falls back to LLM knowledge
    return {
        "competitor_urls": [],
        "source": "none",
        "fallback": True,
        "serpapi_status": serpapi_status,
        "ddg_status": ddg_status,
    }


# ============================================================
# TOOL: PageSpeed Check
# Full Core Web Vitals + opportunity savings + diagnostics
# ============================================================

def check_pagespeed(url: str) -> dict:
    """Runs PageSpeed Insights (mobile) and returns Core Web Vitals + opportunity details.

    Args:
        url: The website URL to check.

    Returns:
        A TechnicalSEOData-compatible dictionary with scores, CWV values,
        opportunities with ms savings, and diagnostic audits.
    """
    try:
        api_key = os.getenv("PAGESPEED_API_KEY")
        api_url = (
            f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={url}&key={api_key}&strategy=mobile"
            f"&category=performance&category=accessibility"
            f"&category=seo&category=best-practices"
        )
        r = requests.get(api_url, timeout=90)
        data = r.json()

        lighthouse = data.get("lighthouseResult", {})
        categories = lighthouse.get("categories", {})
        audits     = lighthouse.get("audits", {})

        scores = {
            "performance":    round(categories.get("performance",    {}).get("score", 0) * 100),
            "accessibility":  round(categories.get("accessibility",  {}).get("score", 0) * 100),
            "seo":            round(categories.get("seo",            {}).get("score", 0) * 100),
            "best_practices": round(categories.get("best-practices", {}).get("score", 0) * 100),
        }

        def get_cwv(audit_id):
            audit = audits.get(audit_id, {})
            numeric = audit.get("numericValue")
            display = audit.get("displayValue", "")
            if "took" in display:
                import re
                match = re.search(r"[\d.]+ (?:ms|s)", display)
                display = match.group(0) if match else display
            if numeric is None and display:
                try:
                    if " s" in display and "ms" not in display:
                        numeric = float(display.replace(" s", "").strip()) * 1000
                    elif " ms" in display:
                        numeric = float(display.replace(" ms", "").strip())
                except Exception:
                    pass
            return numeric, display

        lcp_ms,    lcp_display  = get_cwv("largest-contentful-paint")
        cls_score, cls_display  = get_cwv("cumulative-layout-shift")
        fid_ms,    fid_display  = get_cwv("max-potential-fid")
        ttfb_ms,   ttfb_display = get_cwv("server-response-time")
        fcp_ms,    fcp_display  = get_cwv("first-contentful-paint")
        tbt_ms,    tbt_display  = get_cwv("total-blocking-time")

        rb_audit = audits.get("render-blocking-resources", {})
        render_blocking = [
            item.get("url", "")
            for item in rb_audit.get("details", {}).get("items", [])
            if item.get("url")
        ]

        opportunities = []
        for audit_id, audit in audits.items():
            score   = audit.get("score")
            details = audit.get("details", {})
            if score is not None and score < 0.9 and details.get("type") == "opportunity":
                savings_ms = details.get("overallSavingsMs", 0) or 0
                opportunities.append(TechnicalIssue(
                    id=audit_id,
                    title=audit.get("title", ""),
                    description=(audit.get("description", ""))[:200],
                    score=score,
                    impact="high" if score < 0.5 else "medium",
                    savings_ms=savings_ms,
                ).model_dump())

        important_diagnostics = [
            "first-contentful-paint", "speed-index",
            "total-blocking-time", "interactive",
            "uses-optimized-images", "uses-responsive-images",
            "unused-css-rules", "unused-javascript",
            "efficient-animated-content", "uses-long-cache-ttl",
        ]
        diagnostics = []
        for audit_id in important_diagnostics:
            audit = audits.get(audit_id, {})
            score = audit.get("score", 1)
            if score is not None and score < 0.9:
                diagnostics.append(TechnicalIssue(
                    id=audit_id,
                    title=audit.get("title", ""),
                    description=(audit.get("description", ""))[:200],
                    score=score,
                    impact="high" if score < 0.5 else "medium",
                ).model_dump())

        avg = (scores["performance"] + scores["seo"] + scores["best_practices"]) / 3
        technical_seo_score = round(avg / 10)

        result = TechnicalSEOData(
            url=url,
            mobile_scores=scores,
            desktop_scores={},
            score_delta={},
            lcp_ms=lcp_ms,
            cls_score=cls_score,
            fid_ms=fid_ms,
            ttfb_ms=ttfb_ms,
            render_blocking_resources=render_blocking,
            opportunities=opportunities[:6],
            diagnostics=diagnostics[:6],
            technical_seo_score=technical_seo_score,
        ).model_dump()

        result["cwv_display"] = {
            "lcp":  lcp_display  or (f"{round(lcp_ms)}ms"     if lcp_ms    else "N/A"),
            "cls":  cls_display  or (f"{round(cls_score, 3)}"  if cls_score else "N/A"),
            "fid":  fid_display  or (f"{round(fid_ms)}ms"     if fid_ms    else "N/A"),
            "ttfb": ttfb_display or (f"{round(ttfb_ms)}ms"    if ttfb_ms   else "N/A"),
            "fcp":  fcp_display  or (f"{round(fcp_ms)}ms"     if fcp_ms    else "N/A"),
            "tbt":  tbt_display  or (f"{round(tbt_ms)}ms"     if tbt_ms    else "N/A"),
        }
        return result

    except Exception as e:
        return {"url": url, "error": str(e)}


# ============================================================
# OUTPUT VALIDATION
# Called after each agent writes to session state.
# Catches malformed JSON before it silently breaks the next agent.
# ============================================================

from .schemas import (
    SEOData, CompetitorData, GEOData, LocalGEOData, GeneratedReport
)

SCHEMA_MAP = {
    "product_data":       ProductData,
    "seo_data":           SEOData,
    "competitor_data":    CompetitorData,
    "technical_seo_data": TechnicalSEOData,
    "geo_data":           GEOData,
    "local_geo_data":     LocalGEOData,
    "generated_content":  GeneratedReport,
}

def validate_agent_output(output_key: str, raw_output: str) -> dict:
    """Validates an agent's JSON output against its expected Pydantic schema.

    Args:
        output_key: The session state key (e.g. "product_data").
        raw_output: The raw string output from the agent.

    Returns:
        Dict with valid (bool), parsed (dict or None), error (str or None),
        output_key (str), truncated (bool).
    """
    schema = SCHEMA_MAP.get(output_key)
    if not schema:
        return {"valid": True, "parsed": None, "error": None,
                "output_key": output_key, "truncated": False}

    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    truncated = not cleaned.rstrip().endswith("}")

    try:
        parsed = json.loads(cleaned)
        schema.model_validate(parsed)
        return {"valid": True, "parsed": parsed, "error": None,
                "output_key": output_key, "truncated": truncated}
    except json.JSONDecodeError as e:
        return {"valid": False, "parsed": None,
                "error": f"Invalid JSON: {str(e)}",
                "output_key": output_key, "truncated": truncated}
    except Exception as e:
        return {"valid": False, "parsed": None,
                "error": f"Schema validation failed: {str(e)}",
                "output_key": output_key, "truncated": truncated}