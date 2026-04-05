import os
import json
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import date
from dotenv import load_dotenv
from google.adk.agents import Agent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

model_name = os.getenv("MODEL", "gemini-2.5-flash")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
AGENTMAIL_API_KEY = os.getenv("AGENTMAIL_API_KEY")
NOTION_PARENT_PAGE_ID = "337a3d74-f8bc-8041-bc9e-f9908aac45e4"
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY")

# -----------------------------
# MCP TOOLSETS
# -----------------------------

agentmail_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=[
                "-y",
                "agentmail-mcp",
                "--tools",
                "list_inboxes,send_message,get_inbox"
            ],
            env={"AGENTMAIL_API_KEY": AGENTMAIL_API_KEY}
        ),
        timeout=30,
    ),
)

# -----------------------------
# NOTION BLOCK HELPERS
# -----------------------------

def notion_h1(text):
    return {
        "type": "heading_1",
        "heading_1": {
            "rich_text": [{"text": {"content": text}}]
        }
    }

def notion_h2(text):
    return {
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"text": {"content": text}}]
        }
    }

def notion_h3(text):
    return {
        "type": "heading_3",
        "heading_3": {
            "rich_text": [{"text": {"content": text}}]
        }
    }

def notion_callout(text, emoji, color="gray_background"):
    return {
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": str(text)[:2000]}}],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color
        }
    }

def notion_bullet(text):
    return {
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"text": {"content": str(text)[:2000]}}]
        }
    }

def notion_quote(text):
    return {
        "type": "quote",
        "quote": {
            "rich_text": [{"text": {"content": str(text)[:2000]}}],
            "color": "gray_background"
        }
    }

def notion_para(text):
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"text": {"content": str(text)[:2000]}}]
        }
    }

def notion_divider():
    return {"type": "divider", "divider": {}}


# -----------------------------
# TOOL: Scrape Website
# -----------------------------

def scrape_website(url: str) -> dict:
    """Scrapes a website and extracts title, description, meta tags and page structure.

    Args:
        url: The website URL to scrape.

    Returns:
        A dictionary with title, description, headings, images and links.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        title = soup.title.text.strip() if soup.title else ""

        desc = ""
        meta = soup.find("meta", {"name": "description"})
        if meta:
            desc = meta.get("content", "")

        if not desc:
            og_desc = soup.find("meta", {"property": "og:description"})
            if og_desc:
                desc = og_desc.get("content", "")

        site_name = ""
        og_site = soup.find("meta", {"property": "og:site_name"})
        if og_site:
            site_name = og_site.get("content", "")

        headings = []
        for tag in ["h1", "h2", "h3"]:
            for h in soup.find_all(tag):
                text = h.get_text(strip=True)
                if text:
                    headings.append({"tag": tag, "text": text})

        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if text and len(text) > 30:
                paragraphs.append(text)

        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt", "")
            if src:
                if not src.startswith("http"):
                    src = "https:" + src
                images.append({"src": src, "alt": alt})

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http"):
                links.append(href)

        product_json_url = url.rstrip("/") + ".json"
        try:
            json_resp = requests.get(
                product_json_url, headers=headers, timeout=10
            )
            if json_resp.status_code == 200:
                pdata = json_resp.json().get("product", {})
                if pdata:
                    title = pdata.get("title", title)
                    desc = BeautifulSoup(
                        pdata.get("body_html", desc), "html.parser"
                    ).get_text()
                    tags = pdata.get("tags", [])
                    vendor = pdata.get("vendor", "")
                    product_type = pdata.get("product_type", "")
                    variants = pdata.get("variants", [])
                    price = variants[0].get("price", "") if variants else ""
                    image_list = pdata.get("images", [])
                    images = [img.get("src", "") for img in image_list[:5]]
                    return {
                        "title": title,
                        "description": desc,
                        "tags": tags,
                        "vendor": vendor,
                        "product_type": product_type,
                        "price": price,
                        "images": images,
                        "url": url,
                        "is_shopify": True
                    }
        except Exception:
            pass

        return {
            "title": title,
            "site_name": site_name,
            "description": desc,
            "paragraphs": paragraphs[:10],
            "headings": headings[:20],
            "images": images[:10],
            "links": links[:20],
            "url": url,
            "is_shopify": False
        }
    except Exception as e:
        return {"error": str(e), "url": url}


# -----------------------------
# TOOL: Scrape Competitor
# -----------------------------

def scrape_competitor(url: str) -> dict:
    """Scrapes a competitor website and extracts key SEO data.

    Args:
        url: The competitor website URL to scrape.

    Returns:
        A dictionary with title, description, headings and keywords.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        title = soup.title.text.strip() if soup.title else ""

        desc = ""
        meta = soup.find("meta", {"name": "description"})
        if meta:
            desc = meta.get("content", "")

        keywords = ""
        meta_kw = soup.find("meta", {"name": "keywords"})
        if meta_kw:
            keywords = meta_kw.get("content", "")

        headings = []
        for tag in ["h1", "h2"]:
            for h in soup.find_all(tag):
                text = h.get_text(strip=True)
                if text:
                    headings.append(f"{tag}: {text}")

        schema_types = []
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    schema_types.append(data.get("@type", ""))
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            schema_types.append(item.get("@type", ""))
            except Exception:
                pass

        return {
            "url": url,
            "title": title,
            "description": desc,
            "keywords": keywords,
            "headings": headings[:10],
            "schema_types": [s for s in schema_types if s],
            "has_schema": len(schema_types) > 0
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


# -----------------------------
# TOOL: PageSpeed Check
# -----------------------------

def check_pagespeed(url: str) -> dict:
    """Checks PageSpeed Insights for a URL.

    Args:
        url: The website URL to check.

    Returns:
        A dictionary with performance scores and issues.
    """
    try:
        api_key = os.getenv("PAGESPEED_API_KEY")
        api_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&key={api_key}&strategy=mobile"
        r = requests.get(api_url, timeout=30)
        data = r.json()

        categories = data.get("lighthouseResult", {}).get("categories", {})
        audits = data.get("lighthouseResult", {}).get("audits", {})

        performance = categories.get("performance", {}).get("score", 0)
        accessibility = categories.get("accessibility", {}).get("score", 0)
        seo_score = categories.get("seo", {}).get("score", 0)
        best_practices = categories.get("best-practices", {}).get("score", 0)

        issues = []
        important_audits = [
            "first-contentful-paint",
            "largest-contentful-paint",
            "total-blocking-time",
            "cumulative-layout-shift",
            "speed-index",
            "interactive",
            "uses-optimized-images",
            "uses-responsive-images",
            "render-blocking-resources",
            "unused-css-rules",
            "unused-javascript",
        ]

        for audit_id in important_audits:
            audit = audits.get(audit_id, {})
            if audit.get("score", 1) < 0.9:
                issues.append({
                    "id": audit_id,
                    "title": audit.get("title", ""),
                    "description": audit.get("description", "")[:200],
                    "score": audit.get("score", 0)
                })

        return {
            "url": url,
            "scores": {
                "performance": round(performance * 100),
                "accessibility": round(accessibility * 100),
                "seo": round(seo_score * 100),
                "best_practices": round(best_practices * 100)
            },
            "issues": issues[:8]
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


# -----------------------------
# TOOL: Create Notion Report
# -----------------------------

def create_notion_report(
    url: str,
    product_data: str,
    seo_data: str,
    competitor_data: str,
    technical_seo_data: str,
    geo_data: str,
    local_geo_data: str,
    generated_content: str
) -> dict:
    """Creates a decorated Notion page with the full SEO and GEO report.

    Args:
        url: The website URL that was analysed.
        product_data: The scraped website data summary.
        seo_data: The SEO analysis report.
        competitor_data: The competitor analysis report.
        technical_seo_data: The technical SEO audit report.
        geo_data: The GEO analysis report.
        local_geo_data: The local GEO analysis report.
        generated_content: The structured scoring report with action points.

    Returns:
        A dictionary with the Notion page URL or an error message.
    """
    token = os.getenv("NOTION_TOKEN")
    parent_id = NOTION_PARENT_PAGE_ID
    today = date.today().strftime("%b %d %Y")
    domain = url.replace("https://", "").replace("http://", "").split("/")[0]

    # Parse scores from generated_content for callouts
    def extract_score(content, label):
        try:
            for line in content.split("\n"):
                if label in line and "/10" in line:
                    return line.strip()
        except Exception:
            pass
        return f"{label}: N/A"

    seo_score_line = extract_score(generated_content, "SEO Score")
    geo_score_line = extract_score(generated_content, "GEO Score")
    tech_score_line = extract_score(generated_content, "Technical SEO Score")
    competitor_score_line = extract_score(generated_content, "Competitor Position")
    local_score_line = extract_score(generated_content, "Local GEO Score")
    overall_score_line = extract_score(generated_content, "Overall Digital Visibility")

    children = [
        # Header
        notion_h1(f"🔍 SEO + GEO Analysis Report"),
        notion_callout(
            f"Website: {url}  |  Analysed: {today}",
            "📅",
            "blue_background"
        ),
        notion_divider(),

        # Scores
        notion_h2("📊 Overall Scores"),
        notion_callout(seo_score_line, "🔎", "yellow_background"),
        notion_callout(geo_score_line, "🤖", "orange_background"),
        notion_callout(tech_score_line, "⚙️", "red_background"),
        notion_callout(competitor_score_line, "🏆", "purple_background"),
        notion_callout(local_score_line, "📍", "green_background"),
        notion_callout(overall_score_line, "🌐", "blue_background"),
        notion_divider(),

        # Website Overview
        notion_h2("🔍 Website Overview"),
        notion_para(product_data[:2000]),
        notion_divider(),

        # SEO Analysis
        notion_h2("🔎 SEO Analysis"),
        notion_quote("Keywords, target audience, search intent and technical issues"),
        notion_para(seo_data[:2000]),
        notion_divider(),

        # Competitor Analysis
        notion_h2("🏆 Competitor Analysis"),
        notion_quote("Top competitors, keyword gaps and opportunities"),
        notion_para(competitor_data[:2000]),
        notion_divider(),

        # Technical SEO
        notion_h2("⚙️ Technical SEO Audit"),
        notion_quote("PageSpeed scores, technical issues and fixes"),
        notion_para(technical_seo_data[:2000]),
        notion_divider(),

        # GEO Analysis
        notion_h2("🤖 GEO Analysis"),
        notion_quote("AI engine visibility, entity optimisation and recommendations"),
        notion_para(geo_data[:2000]),
        notion_divider(),

        # Local GEO
        notion_h2("📍 Local & Regional GEO"),
        notion_quote("Regional keyword opportunities and localisation gaps"),
        notion_para(local_geo_data[:2000]),
        notion_divider(),

        # SEO Deficiencies
        notion_h2("🔴 SEO Deficiencies"),
    ]

    # Add SEO deficiency bullets
    seo_def_section = False
    action_section = False
    action_items = []
    seo_items = []
    geo_items = []
    tech_items = []

    for line in generated_content.split("\n"):
        if "SEO DEFICIENCIES" in line:
            seo_def_section = True
            action_section = False
        elif "GEO DEFICIENCIES" in line:
            seo_def_section = False
        elif "PRIORITY ACTION POINTS" in line:
            action_section = True
            seo_def_section = False
        elif "OPTIMISED CONTENT" in line:
            action_section = False

        if seo_def_section and line.strip().startswith("-"):
            seo_items.append(line.strip())
        if action_section and line.strip() and line.strip()[0].isdigit():
            action_items.append(line.strip())

    for item in seo_items[:5]:
        children.append(notion_bullet(item))

    children.append(notion_divider())
    children.append(notion_h2("✅ Priority Action Points"))
    children.append(notion_quote("Ranked by impact — tackle top items first"))

    for item in action_items[:15]:
        children.append(notion_bullet(item))

    children.append(notion_divider())

    # Optimised Content section
    children.append(notion_h2("✍️ Optimised Content"))

    opt_section = False
    opt_lines = []
    for line in generated_content.split("\n"):
        if "OPTIMISED CONTENT" in line:
            opt_section = True
        elif "SCHEMA MARKUP" in line:
            opt_section = False
        if opt_section and line.strip().startswith("-"):
            opt_lines.append(line.strip())

    for line in opt_lines[:6]:
        children.append(notion_callout(line, "📝", "gray_background"))

    children.append(notion_divider())
    children.append(notion_h2("🏗️ Schema Markup Needed"))

    schema_section = False
    for line in generated_content.split("\n"):
        if "SCHEMA MARKUP" in line:
            schema_section = True
        if schema_section and line.strip().startswith("-"):
            children.append(notion_bullet(line.strip()))

    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        },
        json={
            "parent": {"page_id": parent_id},
            "properties": {
                "title": {
                    "title": [{"text": {"content": f"{domain} — {today}"}}]
                }
            },
            "children": children
        }
    )

    if response.status_code == 200:
        page = response.json()
        notion_url = page.get("url", "")
        return {"success": True, "notion_url": notion_url}
    else:
        return {"success": False, "error": response.text}


# -----------------------------
# AGENT 1: Product Researcher
# -----------------------------

product_researcher = Agent(
    name="product_researcher",
    model=model_name,
    description="Scrapes and extracts data from any website or Shopify product page.",
    instruction="""
You are a website researcher.

Find the URL in the conversation context.
IMMEDIATELY use the scrape_website tool on that URL without asking anything.

From the scraped data extract and summarise:
- Business name and what they do
- Key services or products offered
- Target audience if mentioned
- Website title and meta description
- Headings structure
- Page content from paragraphs
- Images and alt texts
- Internal and external links

If scraping returns partial data or an error, use whatever is available
and note the limitation. Construct the best possible business summary
from whatever data was returned.

Do NOT ask the user for more information under any circumstances.
""",
    tools=[scrape_website],
    output_key="product_data"
)


# -----------------------------
# AGENT 2: SEO Researcher
# -----------------------------

seo_researcher = Agent(
    name="seo_researcher",
    model=model_name,
    description="Analyses website data and generates structured SEO keyword report.",
    instruction="""
You are an expert SEO strategist specialising in ecommerce and service businesses.

Use the product_data from the previous agent to generate a structured SEO report.
Do NOT ask the user for more information. Work with what has been scraped.

Generate:
1. **Primary Keywords** (2-3) - highest volume, most relevant
2. **Long Tail Keywords** (5-7) - specific phrases buyers search for
3. **Informational Keywords** - what, how, why queries
4. **Commercial Keywords** - buy, best, hire, review queries
5. **Target Audience** - who is searching for this
6. **Technical SEO Issues** - missing meta tags, poor heading structure, missing alt texts
7. **Search Intent Summary** - what is the visitor looking for

Do NOT ask for clarification. Generate the best possible report from available data.
""",
    output_key="seo_data"
)


# -----------------------------
# AGENT 3: Competitor Agent
# -----------------------------

competitor_agent = Agent(
    name="competitor_agent",
    model=model_name,
    description="Identifies and analyses top competitors based on primary keywords.",
    instruction="""
You are a competitive intelligence specialist.

Using the product_data and seo_data from previous agents:

STEP 1: Based on the business type and primary keywords, identify 5 likely
competitor websites. Use your knowledge to identify real competitors in this
industry/niche. Think about who ranks for these primary keywords.

STEP 2: Use the scrape_competitor tool on each of the 5 competitor URLs.
Call it 5 times, once per competitor.

STEP 3: Analyse and compare all competitors against the target site:

## COMPETITOR ANALYSIS

### Competitors Identified:
List each competitor with their URL and what they do.

### Keyword Gaps:
Keywords competitors are targeting that the target site is missing.

### Content Gaps:
Types of content competitors have that the target site lacks.

### Schema Markup Comparison:
What structured data competitors use vs target site.

### Heading Structure Comparison:
How competitors structure their H1/H2s vs target site.

### What Competitors Do Better:
Top 5 things competitors do that the target site should adopt.

### Opportunities:
Top 3 areas where the target site can outperform competitors.

Do NOT ask for clarification. Use your knowledge to identify real competitors.
""",
    tools=[scrape_competitor],
    output_key="competitor_data"
)


# -----------------------------
# AGENT 4: Technical SEO Agent
# -----------------------------

technical_seo_agent = Agent(
    name="technical_seo_agent",
    model=model_name,
    description="Performs technical SEO audit including PageSpeed analysis.",
    instruction="""
You are a technical SEO specialist.

Using the product_data from the product_researcher and the URL from the conversation:

STEP 1: Use the check_pagespeed tool on the website URL to get performance scores.

STEP 2: Analyse the technical data from product_data combined with PageSpeed results.

Generate a complete technical SEO audit:

## TECHNICAL SEO AUDIT

### PageSpeed Scores:
- Performance: [X/100]
- Accessibility: [X/100]
- SEO: [X/100]
- Best Practices: [X/100]

### Critical Issues (fix immediately):
List issues with High impact label and exact fix.

### H1 Tag Analysis:
Number of H1 tags found, what they say, what they should say.

### Meta Tags Analysis:
Meta description — present/missing, length, quality score.

### Image Optimisation:
Number of images missing alt text, examples, impact.

### URL Structure Issues:
Any problematic URLs found with fix recommendations.

### Page Speed Issues:
Top issues from PageSpeed with fix recommendations.

### Schema Markup Status:
What schema is present, what is missing.

### Technical SEO Score: [X/10]
Overall assessment with reasoning.

Do NOT ask for clarification. Run the tool immediately.
""",
    tools=[check_pagespeed],
    output_key="technical_seo_data"
)


# -----------------------------
# AGENT 5: GEO Researcher
# -----------------------------

geo_researcher = Agent(
    name="geo_researcher",
    model=model_name,
    description="Analyses GEO visibility for AI-generated search answers.",
    instruction="""
You are an expert in Generative Engine Optimisation (GEO).

Use the product_data and seo_data from previous agents.
Do NOT ask the user for more information. Work with what has been provided.

Analyse:
1. **AI Visibility Score** - estimate 1-10 how likely this site appears in AI answers
2. **Entity Optimisation** - is the brand clearly defined as an entity?
3. **FAQ Opportunities** - 5 questions AI engines commonly answer about this business
4. **Content Gaps** - what authoritative content is missing that AI would cite?
5. **GEO Recommendations** - specific actions to improve AI engine visibility
6. **Schema Markup Suggestions** - what structured data should be added

Do NOT ask for clarification. Generate the best possible analysis from available data.
""",
    output_key="geo_data"
)


# -----------------------------
# AGENT 6: Local GEO Agent
# -----------------------------

local_geo_agent = Agent(
    name="local_geo_agent",
    model=model_name,
    description="Analyses local and regional GEO keyword opportunities.",
    instruction="""
You are an expert in local and regional SEO and GEO optimisation.

Use product_data, seo_data, and geo_data from previous agents.
Do NOT ask the user for more information.

Generate a local and regional GEO analysis:

## LOCAL GEO ANALYSIS

### Global Market Opportunities:
Top 5 global markets this business should target based on their services.
For each market:
- Region/Country
- Local keyword variations
- Search volume potential (High/Medium/Low)
- Localisation recommendations

### Language & Localisation Gaps:
Is the site available in multiple languages? Should it be?
What languages would drive the most traffic?

### Regional Keyword Opportunities:
10 region-specific long tail keywords not currently targeted.
Format: [keyword] — [region] — [intent]

### Local Schema Markup:
Should LocalBusiness schema be added? What fields are needed?

### AI Visibility by Region:
Which regions is this business most/least visible in AI answers?
What content would improve regional AI visibility?

### Local GEO Score: [X/10]
Overall local/regional optimisation assessment.

Do NOT ask for clarification. Generate from available data.
""",
    output_key="local_geo_data"
)


# -----------------------------
# AGENT 7: Report Generator
# -----------------------------

copywriter_agent = Agent(
    name="copywriter_agent",
    model=model_name,
    description="Generates structured SEO and GEO scoring report with action points.",
    instruction="""
You are an expert SEO and GEO analyst.

Use ALL data from previous agents:
- product_data, seo_data, competitor_data
- technical_seo_data, geo_data, local_geo_data

Do NOT generate HTML. Generate a clean structured report.

Generate in this EXACT format:

## OVERALL SCORES
- SEO Score: [X/10] — [one line reasoning]
- GEO Score: [X/10] — [one line reasoning]
- Technical SEO Score: [X/10] — [one line reasoning]
- Competitor Position: [X/10] — [how well positioned vs competitors]
- Local GEO Score: [X/10] — [one line reasoning]
- Overall Digital Visibility: [X/10]

## SEO DEFICIENCIES
Top 5 specific issues:
- Issue: [what is wrong] | Impact: [High/Medium/Low] | Fix: [exact action]

## GEO DEFICIENCIES
Top 5 specific issues:
- Issue: [what is wrong] | Impact: [High/Medium/Low] | Fix: [exact action]

## TECHNICAL SEO DEFICIENCIES
Top 5 specific issues:
- Issue: [what is wrong] | Impact: [High/Medium/Low] | Fix: [exact action]

## COMPETITOR GAPS
Top 3 things competitors do better:
- Gap: [what competitors do] | Fix: [how to match or exceed them]

## PRIORITY ACTION POINTS
15 actions ranked by priority:
1. [Action] — [Expected outcome]
2. [Action] — [Expected outcome]
3. [Action] — [Expected outcome]
4. [Action] — [Expected outcome]
5. [Action] — [Expected outcome]
6. [Action] — [Expected outcome]
7. [Action] — [Expected outcome]
8. [Action] — [Expected outcome]
9. [Action] — [Expected outcome]
10. [Action] — [Expected outcome]
11. [Action] — [Expected outcome]
12. [Action] — [Expected outcome]
13. [Action] — [Expected outcome]
14. [Action] — [Expected outcome]
15. [Action] — [Expected outcome]

## OPTIMISED CONTENT
- SEO Title: [optimised title]
- Meta Description: [under 160 characters]
- Primary Keywords: [list]
- Long Tail Keywords: [list]

## SCHEMA MARKUP NEEDED
- [Schema type]: [one line description]

Do NOT ask for clarification. Generate the complete report.
""",
    output_key="generated_content"
)


# -----------------------------
# AGENT 8: Notion Report Agent
# -----------------------------

notion_report_agent = Agent(
    name="notion_report_agent",
    model=model_name,
    description="Saves the complete decorated SEO and GEO report to Notion.",
    instruction="""
You are responsible for saving the complete analysis report to Notion.

Call create_notion_report IMMEDIATELY with ALL of these parameters:
- url: the website URL from the conversation
- product_data: complete output from product_researcher
- seo_data: complete output from seo_researcher
- competitor_data: complete output from competitor_agent
- technical_seo_data: complete output from technical_seo_agent
- geo_data: complete output from geo_researcher
- local_geo_data: complete output from local_geo_agent
- generated_content: complete output from copywriter_agent

Do NOT summarise or shorten any data.
Do NOT ask for confirmation.
Call the tool as your very first action.

After the tool returns:
- If success is True output exactly:
  NOTION_URL: [the notion_url value from the response]
- If success is False output exactly:
  NOTION_ERROR: [the error value from the response]
""",
    tools=[create_notion_report],
    output_key="notion_page_url"
)


# -----------------------------
# AGENT 9: AgentMail Delivery
# -----------------------------

agentmail_delivery_agent = Agent(
    name="agentmail_delivery_agent",
    model=model_name,
    description="Delivers the Notion report link to the user via email.",
    instruction="""
You are responsible for delivering the completed report to the user via email.

IMPORTANT - use these exact tool names:
- list_inboxes — to list available inboxes
- send_message — to send an email

Steps:
1. Use list_inboxes to get the inbox ID for seoagent@agentmail.to
2. Look for a line starting with "NOTION_URL:" in the previous agent output
3. Find the user's email address from the conversation
4. Use send_message with:
   - inbox_id: ID from step 1
   - to: user's email address
   - subject: "Your SEO + GEO Report is Ready — [website domain]"
   - body:
     Hi,

     Your SEO and GEO analysis for [website URL] is complete.

     SCORES:
     [extract all scores from generated_content]

     TOP 3 PRIORITY ACTIONS:
     [extract top 3 action points from generated_content]

     View your full decorated report with competitor analysis,
     technical audit, GEO scores and action points here:
     [NOTION_URL]

     Best regards,
     SEO GEO Assistant

If send_message fails output exactly:
EMAIL_FAILED: true
NOTION_URL: [notion url]
""",
    tools=[agentmail_toolset],
    output_key="email_status"
)


# -----------------------------
# DELIVERY WORKFLOW (Sequential)
# -----------------------------

delivery_workflow = SequentialAgent(
    name="delivery_workflow",
    description="Saves report to Notion then delivers link via AgentMail.",
    sub_agents=[
        notion_report_agent,
        agentmail_delivery_agent
    ]
)


# -----------------------------
# MAIN SEO GEO WORKFLOW (Sequential)
# -----------------------------

seo_geo_workflow = SequentialAgent(
    name="seo_geo_workflow",
    description="Full SEO and GEO analysis pipeline.",
    sub_agents=[
        product_researcher,
        seo_researcher,
        competitor_agent,
        technical_seo_agent,
        geo_researcher,
        local_geo_agent,
        copywriter_agent,
        delivery_workflow
    ]
)


# -----------------------------
# ROOT AGENT
# -----------------------------

root_agent = Agent(
    name="seo_geo_assistant",
    model=model_name,
    description="SEO and GEO optimisation assistant for any website or Shopify store.",
    instruction="""
You are a helpful SEO and GEO optimisation assistant.

Ask the user for only two things:
1. Their website URL
2. Their email address

Tell them: "I will extract your business information automatically from your website."

Once you have both, output exactly:
URL: [url]
EMAIL: [email]

Then immediately transfer control to seo_geo_workflow.
Do not ask for anything else.
Do not continue the conversation after transferring.
Do not summarize or comment on the workflow output.
If email delivery fails, share the NOTION_URL directly with the user.
""",
    sub_agents=[seo_geo_workflow]
)