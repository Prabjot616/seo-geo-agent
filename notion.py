import json
import requests
from datetime import date

from .config import NOTION_TOKEN, NOTION_PARENT_PAGE_ID
from .firestore import save_analysis_to_firestore, get_previous_analysis, build_score_diff


# ============================================================
# NOTION BLOCK HELPERS
# ============================================================

def notion_h1(text):
    return {"type": "heading_1", "heading_1": {"rich_text": [{"text": {"content": text}}]}}

def notion_h2(text):
    return {"type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": text}}]}}

def notion_h3(text):
    return {"type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": text}}]}}

def notion_callout(text, emoji, color="gray_background"):
    return {
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": str(text)[:2000]}}],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        }
    }

def notion_bullet(text):
    return {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content": str(text)[:2000]}}]},
    }

def notion_quote(text):
    return {
        "type": "quote",
        "quote": {
            "rich_text": [{"text": {"content": str(text)[:2000]}}],
            "color": "gray_background",
        }
    }

def notion_para(text):
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": str(text)[:2000]}}]},
    }

def notion_divider():
    return {"type": "divider", "divider": {}}


# ============================================================
# TOOL: Create Notion Report
# Consumes structured JSON from all agents.
# ============================================================

def create_notion_report(
    url: str,
    product_data: str,
    seo_data: str,
    competitor_data: str,
    technical_seo_data: str,
    geo_data: str,
    local_geo_data: str,
    generated_content: str,
) -> dict:
    """Creates a decorated Notion page with the full SEO and GEO report.

    Args:
        url: The website URL that was analysed.
        product_data: JSON string from product_researcher.
        seo_data: JSON string from seo_researcher.
        competitor_data: JSON string from competitor_agent.
        technical_seo_data: JSON string from technical_seo_agent.
        geo_data: JSON string from geo_researcher.
        local_geo_data: JSON string from local_geo_agent.
        generated_content: JSON string from copywriter_agent (GeneratedReport schema).

    Returns:
        A dictionary with the Notion page URL or an error message.
    """
    token = NOTION_TOKEN
    parent_id = NOTION_PARENT_PAGE_ID
    today = date.today().strftime("%b %d %Y")
    domain = url.replace("https://", "").replace("http://", "").split("/")[0]

    # ── Parse structured JSON ─────────────────────────────
    try:
        report = json.loads(generated_content)
    except Exception:
        report = {}

    try:
        tech = json.loads(technical_seo_data)
        # technical_seo_agent sometimes wraps tool output in {"check_pagespeed_response": {...}}
        if "check_pagespeed_response" in tech:
            tech = tech["check_pagespeed_response"]
    except Exception:
        tech = {}

    scores               = report.get("scores", {})
    priority_actions     = report.get("priority_actions", [])
    seo_defs             = report.get("seo_deficiencies", [])
    geo_defs             = report.get("geo_deficiencies", [])
    tech_defs            = report.get("technical_deficiencies", [])
    social_defs          = report.get("social_deficiencies", [])
    competitor_gaps      = report.get("competitor_gaps", [])
    optimised            = report.get("optimised_content", {})
    schema_needed        = report.get("schema_markup_needed", [])

    try:
        product = json.loads(product_data)
        social  = product.get("social", {}) or {}
    except Exception:
        product = {}
        social  = {}

    # ── Score diff vs previous run ────────────────────────
    previous    = get_previous_analysis(url)
    score_diffs = build_score_diff(scores, previous) if previous else {}

    def score_label(key, label):
        val  = scores.get(key, "N/A")
        diff = score_diffs.get(key, {})
        trend = diff.get("trend", "")
        delta = diff.get("delta")
        if delta is not None:
            sign = "+" if delta > 0 else ""
            return f"{label}: {val}/10  {trend} ({sign}{delta} vs last run)"
        return f"{label}: {val}/10"

    score_map = [
        ("seo",                 "🔎", "SEO Score",                "yellow_background"),
        ("geo",                 "🤖", "GEO Score",                "orange_background"),
        ("technical_seo",       "⚙️", "Technical SEO Score",      "red_background"),
        ("competitor_position", "🏆", "Competitor Position",      "purple_background"),
        ("local_geo",           "📍", "Local GEO Score",          "green_background"),
        ("overall",             "🌐", "Overall Digital Visibility","blue_background"),
    ]

    # ── Build Notion blocks ───────────────────────────────
    children = [
        notion_h1("🔍 SEO + GEO Analysis Report"),
        notion_callout(f"Website: {url}  |  Analysed: {today}", "📅", "blue_background"),
        notion_divider(),
        notion_h2("📊 Overall Scores"),
    ]

    for key, emoji, label, color in score_map:
        children.append(notion_callout(score_label(key, label), emoji, color))

    if previous:
        prev_date = previous.get("timestamp", "")
        if hasattr(prev_date, "strftime"):
            prev_date = prev_date.strftime("%b %d %Y")
        children.append(notion_callout(
            f"Compared to previous run: {prev_date}", "🔄", "gray_background"
        ))

    # Core Web Vitals block — use display values, fall back to numeric, then "N/A"
    cwv = tech.get("cwv_display", {})

    def fmt_cwv(display_key, numeric_val, unit="ms", decimal=0):
        """Use display string from API if available, else format numeric, else N/A."""
        display = cwv.get(display_key, "")
        if display and display != "N/A":
            return display
        if numeric_val:
            return f"{round(numeric_val, decimal)}{unit}"
        return "N/A"

    lcp_str  = fmt_cwv("lcp",  tech.get("lcp_ms"))
    cls_str  = fmt_cwv("cls",  tech.get("cls_score"), unit="", decimal=3)
    fid_str  = fmt_cwv("fid",  tech.get("fid_ms"))
    ttfb_str = fmt_cwv("ttfb", tech.get("ttfb_ms"))
    fcp_str  = fmt_cwv("fcp",  None)   # display only
    tbt_str  = fmt_cwv("tbt",  None)   # display only

    children += [
        notion_divider(),
        notion_h2("⚡ Core Web Vitals"),
        notion_callout(
            f"LCP: {lcp_str}  |  CLS: {cls_str}  |  FID: {fid_str}  |  TTFB: {ttfb_str}",
            "📐", "gray_background"
        ),
        notion_callout(
            f"FCP: {fcp_str}  |  TBT: {tbt_str}",
            "⏱️", "gray_background"
        ),
    ]

    # Mobile vs Desktop scores
    mobile_scores  = tech.get("mobile_scores", tech.get("scores", {}))
    desktop_scores = tech.get("desktop_scores", {})
    score_delta    = tech.get("score_delta", {})
    if desktop_scores:
        children.append(notion_h3("📱 Mobile vs 🖥️ Desktop"))
        for cat in ["performance", "accessibility", "seo", "best_practices"]:
            mob = mobile_scores.get(cat, "N/A")
            desk = desktop_scores.get(cat, "N/A")
            delta = score_delta.get(cat, 0)
            trend = "▲" if delta > 0 else ("▼" if delta < 0 else "→")
            label = cat.replace("_", " ").title()
            children.append(notion_bullet(
                f"{label}: Mobile {mob} | Desktop {desk} | Gap {trend}{abs(delta)}"
            ))

    # Opportunities with savings
    opportunities = tech.get("opportunities", [])
    if opportunities:
        children.append(notion_h3("🔧 Top Opportunities"))
        for opp in opportunities[:4]:
            if isinstance(opp, dict):
                savings = opp.get("savings_ms", 0) or 0
                children.append(notion_bullet(
                    f"{opp.get('title', '')} — saves ~{round(savings)}ms"
                    f" | Impact: {opp.get('impact', '').upper()}"
                ))

    # Deficiencies
    def deficiency_section(heading, emoji, items, color):
        blocks = [notion_divider(), notion_h2(f"{emoji} {heading}")]
        for item in items[:5]:
            if isinstance(item, dict):
                blocks.append(notion_bullet(
                    f"❌ {item.get('issue', '')} "
                    f"| Impact: {item.get('impact', '')} "
                    f"| Fix: {item.get('fix', '')}"
                ))
        return blocks

    children += deficiency_section("SEO Deficiencies",          "🔴", seo_defs,        "red_background")
    children += deficiency_section("GEO Deficiencies",          "🤖", geo_defs,        "orange_background")
    children += deficiency_section("Technical SEO Deficiencies","⚙️", tech_defs,       "yellow_background")

    # Social signals section
    children += [notion_divider(), notion_h2("🔗 Social Signals")]
    profiles = social.get("profiles_found", {})
    if profiles:
        children.append(notion_callout(
            "Profiles found: " + ", ".join(f"{k}: {v}" for k, v in profiles.items()),
            "✅", "green_background"
        ))
    og_items = [
        ("og:title",       social.get("og_title")),
        ("og:description", social.get("og_description")),
        ("og:image",       social.get("og_image")),
        ("twitter:card",   social.get("twitter_card")),
    ]
    for label, value in og_items:
        if value:
            children.append(notion_bullet(f"✅ {label}: {str(value)[:100]}"))
        else:
            children.append(notion_bullet(f"❌ {label}: Missing"))
    if social_defs:
        children += deficiency_section("Social Deficiencies", "📣", social_defs, "pink_background")

    children += [notion_divider(), notion_h2("🏆 Competitor Gaps")]
    for item in competitor_gaps[:3]:
        if isinstance(item, dict):
            children.append(notion_bullet(
                f"📌 {item.get('issue', '')} | Fix: {item.get('fix', '')}"
            ))

    children += [
        notion_divider(),
        notion_h2("✅ Priority Action Points"),
        notion_quote("Ranked by impact — effort estimate in brackets"),
    ]
    for item in priority_actions[:15]:
        if isinstance(item, dict):
            effort = item.get("effort", "")
            effort_str = f" [{effort}]" if effort else ""
            children.append(notion_bullet(
                f"{item.get('rank', '')}. {item.get('action', '')}{effort_str} — {item.get('expected_outcome', '')}"
            ))

    # Meta description variants
    meta_variants = optimised.get("meta_description_variants", [])
    children += [
        notion_divider(),
        notion_h2("✍️ Optimised Content"),
        notion_callout(f"SEO Title: {optimised.get('seo_title', '')}", "📝", "gray_background"),
        notion_callout(f"Meta Description: {optimised.get('meta_description', '')}", "📝", "gray_background"),
    ]
    if meta_variants:
        children.append(notion_h3("Meta Description Variants"))
        for i, variant in enumerate(meta_variants[:3], 1):
            children.append(notion_callout(
                f"Variant {i} ({len(variant)} chars): {variant}", "📏", "gray_background"
            ))
    children += [
        notion_callout(f"Primary Keywords: {', '.join(optimised.get('primary_keywords', []))}", "🔑", "gray_background"),
        notion_callout(f"Long Tail Keywords: {', '.join(optimised.get('long_tail_keywords', []))}", "🔑", "gray_background"),
        notion_divider(),
        notion_h2("🏗️ Schema Markup — Ready to Paste"),
    ]
    for s in schema_needed:
        if isinstance(s, dict):
            children.append(notion_h3(f"{s.get('type', '')} Schema"))
            children.append(notion_bullet(s.get("description", "")))
            json_ld = s.get("json_ld", "")
            if json_ld:
                # Truncate to Notion's 2000 char limit
                children.append(notion_quote(str(json_ld)[:1990]))

    # ── Save to Firestore before posting ─────────────────
    save_analysis_to_firestore(url, {
        "scores": scores,
        "notion_url": "",
        "report": report,
        "tech": tech,
    })

    # ── Post to Notion ────────────────────────────────────
    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
        json={
            "parent": {"page_id": parent_id},
            "properties": {
                "title": {"title": [{"text": {"content": f"{domain} — {today}"}}]}
            },
            "children": children,
        }
    )

    if response.status_code == 200:
        page = response.json()
        notion_url = page.get("url", "")
        # Update Firestore with the actual Notion URL
        save_analysis_to_firestore(url, {
            "scores": scores,
            "notion_url": notion_url,
            "report": report,
            "tech": tech,
        })
        return {"success": True, "notion_url": notion_url}
    else:
        return {"success": False, "error": response.text}