import json
from typing import Optional

from google.adk.agents import Agent, SequentialAgent, ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from .config import MODEL_NAME, AGENTMAIL_API_KEY
from .schemas import (
    ProductData, SEOData, CompetitorData, TechnicalSEOData,
    GEOData, LocalGEOData, GeneratedReport,
)
from .tools import scrape_website, scrape_competitor, discover_competitors, check_pagespeed
from .notion import create_notion_report


# ============================================================
# RETRY CONFIG
# Applied to every agent. On 429, waits then retries up to 5 times
# with exponential backoff: 30s, 60s, 120s, 240s, 480s.
# This handles transient quota exhaustion without crashing the pipeline.
# ============================================================

RETRY_CONFIG = types.GenerateContentConfig(
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            initial_delay=30,   # wait 30s before first retry
            attempts=5,         # retry up to 5 times
        ),
    )
)


# ============================================================
# PROGRESS CALLBACKS
# Returns an LlmResponse with the status message so it appears
# as a proper agent message in the conversation, then the agent
# continues normally on the next step.
# Returning None from before_agent_callback skips the message.
# Returning an LlmResponse shows the message BUT skips the agent's
# own LLM call — so we use before_agent_callback only for agents
# that don't need to suppress their own run.
#
# ADK behaviour:
#   - before_agent_callback returns None   → agent runs normally
#   - before_agent_callback returns Content → agent is SKIPPED
#
# So we cannot use before_agent_callback to show a message AND
# still run the agent. Instead we use a model_callback approach:
# inject a system message via before_model_callback which fires
# before the LLM call but does not skip the agent.
# ============================================================

def make_progress_callback(message: str):
    """Returns a before_model_callback that emits a status message
    before the LLM call without interrupting the agent's execution."""
    from google.adk.models.llm_request import LlmRequest

    def callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
        # Build a visible response that streams to the user
        from google.adk.models.llm_response import LlmResponse
        from google.genai.types import Content, Part
        # Print to terminal as well for CLI visibility
        print(f"\n{message}", flush=True)
        # Return None so the agent's actual LLM call still runs
        return None
    return callback


def make_agent_start_message(message: str):
    """Returns a before_agent_callback that shows a status message
    by writing directly to session state for display, then returns
    None so the agent still runs."""
    def callback(callback_context: CallbackContext) -> Optional[LlmResponse]:
        print(f"\n{message}", flush=True)
        # Write progress to session state so web UI can read it
        try:
            callback_context.state["progress"] = message
        except Exception:
            pass
        return None
    return callback


# Per-agent status messages
CB_SCRAPING    = make_agent_start_message("⏳ [1/7] Scraping your website and extracting SEO signals...")
CB_SEO         = make_agent_start_message("⏳ [2/7] Analysing keywords and SEO structure...")
CB_COMPETITORS = make_agent_start_message("⏳ [2/7] Discovering and scraping real competitors...")
CB_TECHNICAL   = make_agent_start_message("⏳ [2/7] Running PageSpeed audit — this can take 60-90 seconds...")
CB_GEO         = make_agent_start_message("⏳ [3/7] Analysing AI engine (GEO) visibility...")
CB_LOCAL_GEO   = make_agent_start_message("⏳ [4/7] Analysing local and regional GEO opportunities...")
CB_REPORT      = make_agent_start_message("⏳ [5/7] Compiling full scoring report and 15 action points...")
CB_NOTION      = make_agent_start_message("⏳ [6/7] Saving decorated report to Notion...")
CB_EMAIL       = make_agent_start_message("⏳ [7/7] Sending report link to your email...")


# ============================================================
# JSON SCHEMAS — embedded into agent instructions so the LLM
# knows exactly what structured output to produce.
# ============================================================

PRODUCT_DATA_SCHEMA   = json.dumps(ProductData.model_json_schema(),    indent=2)
SEO_DATA_SCHEMA       = json.dumps(SEOData.model_json_schema(),         indent=2)
COMPETITOR_DATA_SCHEMA= json.dumps(CompetitorData.model_json_schema(),  indent=2)
TECHNICAL_SEO_SCHEMA  = json.dumps(TechnicalSEOData.model_json_schema(),indent=2)
GEO_DATA_SCHEMA       = json.dumps(GEOData.model_json_schema(),         indent=2)
LOCAL_GEO_SCHEMA      = json.dumps(LocalGEOData.model_json_schema(),    indent=2)
REPORT_SCHEMA         = json.dumps(GeneratedReport.model_json_schema(),  indent=2)

# Compact field-only schema for product_researcher — avoids sending
# the full verbose Pydantic schema which adds unnecessary tokens.
PRODUCT_DATA_COMPACT = """
{
  "url": "string",
  "title": "string",
  "site_name": "string or null",
  "meta": {
    "present": true/false,
    "content": "string or null",
    "length": "integer or null",
    "issues": ["list of issue strings"]
  },
  "headings": {
    "h1_count": "integer",
    "h1_texts": ["list"],
    "h2_texts": ["list"],
    "h3_texts": ["list"],
    "issues": ["list of issue strings"]
  },
  "images_total": "integer",
  "images_missing_alt": ["list of src urls"],
  "schema_types": ["list of strings"],
  "canonical_url": "string or null",
  "word_count": "integer",
  "paragraphs": ["list of strings"],
  "links": ["list of urls"],
  "is_shopify": true/false,
  "social": {
    "og_title": "string or null",
    "og_description": "string or null",
    "og_image": "string or null",
    "twitter_card": "string or null",
    "twitter_site": "string or null",
    "profiles_found": {"platform": "url"},
    "issues": ["list of missing signal strings"]
  },
  "tags": ["list or null"],
  "vendor": "string or null",
  "product_type": "string or null",
  "price": "string or null"
}
"""


# ============================================================
# VALIDATION CALLBACKS
# Runs after each agent completes. Logs validation result.
# Does not block the pipeline — alerts only.
# ============================================================


# ============================================================
# MCP TOOLSETS
# ============================================================

agentmail_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=["-y", "agentmail-mcp", "--tools", "list_inboxes,send_message,get_inbox"],
            env={"AGENTMAIL_API_KEY": AGENTMAIL_API_KEY},
        ),
        timeout=30,
    ),
)


# ============================================================
# AGENT 1: Product Researcher
# ============================================================

product_researcher = Agent(
    name="product_researcher",
    model=MODEL_NAME,
    description="Scrapes and extracts structured data from any website or Shopify product page.",
    before_agent_callback=CB_SCRAPING,
    generate_content_config=RETRY_CONFIG,
    instruction="""
You are a website researcher.

Find the URL in the conversation context.
IMMEDIATELY use the scrape_website tool on that URL.

The tool returns a complete structured JSON object with all SEO signals
already computed. Do NOT reformat, summarise, or restructure the output.

Your ONLY job:
1. Call scrape_website with the URL
2. Output the tool result EXACTLY as returned — raw JSON, nothing else
   No preamble, no markdown fences, no explanation, no changes.

If the tool returns an error field, output the error JSON as-is.
Never ask the user for more information.
""",
    tools=[scrape_website],
    output_key="product_data",
)


# ============================================================
# AGENT 2: SEO Researcher
# ============================================================

seo_researcher = Agent(
    name="seo_researcher",
    model=MODEL_NAME,
    description="Analyses scraped website data and generates a structured SEO keyword report.",
    before_agent_callback=CB_SEO,
    generate_content_config=RETRY_CONFIG,
    instruction=f"""
You are an expert SEO strategist specialising in ecommerce and service businesses.

Read product_data from session state. It is a JSON object — parse it directly.

IMPORTANT: The following are already computed in product_data — copy them
into your output, do NOT recompute them:
- meta_issues → copy from product_data.meta.issues
- heading_issues → copy from product_data.headings.issues
- image_alt_issues → list the first 5 items from product_data.images_missing_alt

Your job: generate keyword strategy, search intent analysis, and content recommendations.

content_score: integer 0-100 representing overall content quality based on
word count, heading structure, meta quality, and paragraph depth.

You MUST respond with ONLY valid JSON matching this exact schema.
No preamble, no markdown fences, no explanation — raw JSON only.

Schema:
{SEO_DATA_SCHEMA}

Do NOT ask for clarification. Generate from product_data.
""",
    output_key="seo_data",
)


# ============================================================
# AGENT 3: Competitor Agent
# ============================================================

competitor_agent = Agent(
    name="competitor_agent",
    model=MODEL_NAME,
    description="Discovers and analyses real SERP competitors.",
    before_agent_callback=CB_COMPETITORS,
    generate_content_config=RETRY_CONFIG,
    instruction=f"""
You are a competitive intelligence specialist.

Read product_data and seo_data from session state (both JSON objects).

STEP 1: Call discover_competitors with:
  - keywords: the primary keywords array from seo_data.keywords.primary
  - target_url: the url field from product_data

STEP 2: Check the result:
  - source = "serpapi" → best quality, use the competitor_urls list
  - source = "duckduckgo" → good quality, use the competitor_urls list.
    Note "Results sourced via DuckDuckGo" in your analysis.
  - source = "none" OR competitor_urls is empty → both sources failed.
    Use your own knowledge to identify 5 real competitors for this
    business type and niche. Populate them with what you know.
    Set has_schema based on what is typical for this industry.
    Set strengths based on what these competitors are known for.
    Note "Competitor discovery unavailable — using knowledge base" in your analysis.

STEP 3: For each competitor URL from Step 2, call scrape_competitor.
Skip this step only if source was "none".

STEP 4: For each scraped competitor, assess their strengths based on their
title, headings, schema types, and meta description. Fill the strengths list
with 2-3 specific observations.

STEP 5: Compare all competitors against product_data and seo_data to identify:
keyword_gaps, content_gaps, schema_gaps, what_competitors_do_better, opportunities.

You MUST respond with ONLY valid JSON matching this exact schema.
No preamble, no markdown fences, no explanation — raw JSON only.

Schema:
{COMPETITOR_DATA_SCHEMA}

Do NOT ask for clarification.
""",
    tools=[discover_competitors, scrape_competitor],
    output_key="competitor_data",
)


# ============================================================
# AGENT 4: Technical SEO Agent
# ============================================================

technical_seo_agent = Agent(
    name="technical_seo_agent",
    model=MODEL_NAME,
    description="Performs technical SEO audit including full Core Web Vitals analysis.",
    before_agent_callback=CB_TECHNICAL,
    generate_content_config=RETRY_CONFIG,
    instruction="""
You are a technical SEO specialist.

Read product_data from session state and extract the url field.

STEP 1: Call check_pagespeed with that URL.

STEP 2: The tool returns a result. Output the result as raw JSON.

CRITICAL: If the tool result is wrapped in a key like "check_pagespeed_response",
extract and output ONLY the inner object — not the wrapper.

Example: if tool returns {"check_pagespeed_response": {"url": "...", "scores": {...}}}
you output: {"url": "...", "scores": {...}}

If the result contains an "error" field → output this JSON exactly:
{
  "url": "[the url]",
  "mobile_scores": {"performance": 0, "accessibility": 0, "seo": 0, "best_practices": 0},
  "desktop_scores": {"performance": 0, "accessibility": 0, "seo": 0, "best_practices": 0},
  "score_delta": {"performance": 0, "accessibility": 0, "seo": 0, "best_practices": 0},
  "lcp_ms": null, "cls_score": null, "fid_ms": null, "ttfb_ms": null,
  "render_blocking_resources": [],
  "opportunities": [],
  "diagnostics": [{"id": "api_error", "title": "PageSpeed API unavailable",
    "description": "Could not retrieve scores", "score": 0, "impact": "high",
    "savings_ms": null}],
  "technical_seo_score": 0,
  "cwv_display": {"lcp": "N/A", "cls": "N/A", "fid": "N/A",
    "ttfb": "N/A", "fcp": "N/A", "tbt": "N/A"}
}

Raw JSON only. No preamble, no markdown fences.
Do NOT ask for clarification. Run the tool immediately.
""",
    tools=[check_pagespeed],
    output_key="technical_seo_data",
)


# ============================================================
# AGENT 5: GEO Researcher
# ============================================================

geo_researcher = Agent(
    name="geo_researcher",
    model=MODEL_NAME,
    description="Analyses GEO visibility for AI-generated search answers.",
    before_agent_callback=CB_GEO,
    generate_content_config=RETRY_CONFIG,
    instruction=f"""
You are an expert in Generative Engine Optimisation (GEO).

Read product_data and seo_data from session state (both JSON objects).

Produce:
- ai_visibility_score: 1-10 estimate of how likely this site appears in AI answers.
  Base this on: entity clarity, schema presence, content authority signals,
  FAQ-style content, and brand mentions.
- entity_optimisation: is the brand clearly defined as a named entity with
  consistent NAP (Name, Address, Phone) and schema?
- faq_opportunities: 5 questions AI engines commonly answer about this
  business type — questions the site should explicitly answer.
- content_gaps: authoritative content that is missing and that AI would cite
  if it existed (e.g. "About Us page lacks founder story", "No pricing page").
- geo_recommendations: specific, actionable steps to improve AI engine visibility.
- schema_suggestions: structured data types that would improve AI discoverability.

You MUST respond with ONLY valid JSON matching this exact schema.
No preamble, no markdown fences, no explanation — raw JSON only.

Schema:
{GEO_DATA_SCHEMA}

Do NOT ask for clarification.
""",
    output_key="geo_data",
)


# ============================================================
# AGENT 6: Local GEO Agent
# ============================================================

local_geo_agent = Agent(
    name="local_geo_agent",
    model=MODEL_NAME,
    description="Analyses local and regional GEO keyword opportunities.",
    before_agent_callback=CB_LOCAL_GEO,
    generate_content_config=RETRY_CONFIG,
    instruction=f"""
You are an expert in local and regional SEO and GEO optimisation.

Read product_data, seo_data, and geo_data from session state (all JSON objects).

Produce:
- global_market_opportunities: top 5 markets as
  {{region, keywords: [list], volume: "High|Medium|Low", recommendations: [list]}}
- language_gaps: languages that would meaningfully increase traffic
- regional_keywords: 10 keywords as {{keyword, region, intent}}
- local_schema_needed: true/false
- local_schema_fields: if true, list the LocalBusiness fields needed
- ai_visibility_by_region: for 3-5 regions:
  {{region, visibility: "High|Medium|Low", content_needed: [list]}}
- local_geo_score: 0-10 overall local/regional optimisation score

You MUST respond with ONLY valid JSON matching this exact schema.
No preamble, no markdown fences, no explanation — raw JSON only.

Schema:
{LOCAL_GEO_SCHEMA}

Do NOT ask for clarification.
""",
    output_key="local_geo_data",
)


# ============================================================
# AGENT 7: Report Generator
# ============================================================

copywriter_agent = Agent(
    name="copywriter_agent",
    model=MODEL_NAME,
    description="Compiles all agent outputs into the final structured scoring report.",
    before_agent_callback=CB_REPORT,
    generate_content_config=RETRY_CONFIG,
    instruction=f"""
You are an expert SEO and GEO analyst.

Read ALL of these from session state (all JSON objects):
  product_data, seo_data, competitor_data,
  technical_seo_data, geo_data, local_geo_data

Compile the complete analysis. All scores are integers 0-10.

Score derivation rules:
  scores.technical_seo    → use technical_seo_data.technical_seo_score directly
  scores.seo              → derive from seo_data.content_score (divide by 10, round)
  scores.geo              → use geo_data.ai_visibility_score directly
  scores.local_geo        → use local_geo_data.local_geo_score directly
  scores.competitor_position → assess 0-10 how well target competes vs competitor_data findings
  scores.overall          → average of all five scores, rounded

priority_actions must have exactly 15 items.
Each item: {{rank: 1-15, action: "...", expected_outcome: "...", effort: "Hours|Days|Weeks"}}
Ranked highest impact first. effort = realistic time to implement the action.

social_deficiencies: derive from product_data.social.issues — convert each issue
into a DeficiencyItem with appropriate impact and fix.

schema_markup_needed: for each schema type recommended, generate an object with
EXACTLY these three fields (no other field names):
  - "type": string — the schema type name e.g. "Organization"
  - "description": string — one line on why it's needed
  - "json_ld": string — the COMPLETE ready-to-paste JSON-LD as a JSON string
    Use real data from product_data (site_name for name, url, meta.content for description,
    social.profiles_found for sameAs).

Example of ONE correct schema_markup_needed item:
{{
  "type": "Organization",
  "description": "Defines the business entity for search engines and AI.",
  "json_ld": "{{\\n  \\"@context\\": \\"https://schema.org\\",\\n  \\"@type\\": \\"Organization\\",\\n  \\"name\\": \\"Tech Wishes Solutions\\",\\n  \\"url\\": \\"https://techwishes.com/\\"\\n}}"
}}

DO NOT use "@type" as the field name — use "type".
DO NOT use "@context" as the field name — use "type".
The json_ld value must be a valid JSON string (escaped properly).

optimised_content.meta_description_variants: generate 3 variants:
  - variant 1: ~120 chars (for tight spaces)
  - variant 2: ~140 chars (standard)
  - variant 3: ~155 chars (maximum, most descriptive)

You MUST respond with ONLY valid JSON matching this exact schema.
No preamble, no markdown fences, no explanation — raw JSON only.

Schema:
{REPORT_SCHEMA}
""",
    output_key="generated_content",
)


# ============================================================
# AGENT 8: Notion Report Agent
# ============================================================

notion_report_agent = Agent(
    name="notion_report_agent",
    model=MODEL_NAME,
    description="Saves the complete decorated SEO and GEO report to Notion.",
    before_agent_callback=CB_NOTION,
    generate_content_config=RETRY_CONFIG,
    instruction="""
You are responsible for saving the complete analysis report to Notion.

Read product_data from session state and use product_data.url as the target URL.
Read the user's email from the conversation history — it was the email address
the user provided at the very start of the conversation.

Call create_notion_report IMMEDIATELY with ALL of these parameters:
- url: product_data.url
- product_data: complete JSON string output from product_researcher
- seo_data: complete JSON string output from seo_researcher
- competitor_data: complete JSON string output from competitor_agent
- technical_seo_data: complete JSON string output from technical_seo_agent
- geo_data: complete JSON string output from geo_researcher
- local_geo_data: complete JSON string output from local_geo_agent
- generated_content: complete JSON string output from copywriter_agent

Do NOT summarise, shorten, or reformat any data.
Do NOT ask for confirmation.
Call the tool as your very first action.

After the tool returns output EXACTLY these two lines and nothing else:
NOTION_URL: [notion_url from response]
USER_EMAIL: [the email address the user gave at the start]

If the tool fails output:
NOTION_ERROR: [error message]
USER_EMAIL: [the email address the user gave at the start]

Always output USER_EMAIL on the second line no matter what.
""",
    tools=[create_notion_report],
    output_key="notion_page_url",
)


# ============================================================
# AGENT 9: AgentMail Delivery
# ============================================================

agentmail_delivery_agent = Agent(
    name="agentmail_delivery_agent",
    model=MODEL_NAME,
    description="Delivers the Notion report link to the user via email.",
    before_agent_callback=CB_EMAIL,
    generate_content_config=RETRY_CONFIG,
    instruction="""
You are responsible for delivering the completed report to the user via email.

Read notion_page_url from session state. Parse it line by line:
  - Line containing "NOTION_URL:": extract the URL after the colon and space
  - Line containing "USER_EMAIL:": extract the email after the colon and space

Also read product_data.url from session state for the website domain.
Also parse generated_content (JSON) for scores and priority_actions.

Tool names:
  list_inboxes  — lists available inboxes
  send_message  — sends an email

Steps:
1. Call list_inboxes — find inbox where address = "seoagent@agentmail.to", note its id.

2. Call send_message with:
   - inbox_id: id from step 1
   - to: the USER_EMAIL value you extracted above (exact, no changes)
   - subject: "Your SEO + GEO Report is Ready — [domain from product_data.url]"
   - body:
       Hi,

       Your SEO and GEO analysis is complete.

       SCORES:
       SEO: [scores.seo]/10
       GEO: [scores.geo]/10
       Technical SEO: [scores.technical_seo]/10
       Competitor Position: [scores.competitor_position]/10
       Local GEO: [scores.local_geo]/10
       Overall: [scores.overall]/10

       TOP 3 PRIORITY ACTIONS:
       1. [priority_actions[0].action] — [priority_actions[0].expected_outcome]
       2. [priority_actions[1].action] — [priority_actions[1].expected_outcome]
       3. [priority_actions[2].action] — [priority_actions[2].expected_outcome]

       View your full report here:
       [NOTION_URL]

       Best regards,
       SEO GEO Assistant

3. After send_message:
   - Success → output: EMAIL_SENT: true to [USER_EMAIL]
   - Failure → output:
       EMAIL_FAILED: true
       Your Notion report is here: [NOTION_URL]

CRITICAL: Always call send_message. Never skip it.
""",
    tools=[agentmail_toolset],
    output_key="email_status",
)


# ============================================================
# PARALLEL GROUPS
# Agents 2, 3, 4 run simultaneously after product_researcher.
# Agents 5, 6 run simultaneously after seo_data is ready.
# ============================================================

# ============================================================
# PARALLEL GROUP — analysis only (SEO + competitors + technical)
# GEO agents run sequentially to avoid Vertex AI 429 quota errors.
# Parallelising 2 lightweight agents saves ~20s but causes crashes
# on standard quota — not worth the tradeoff.
# ============================================================

parallel_analysis = ParallelAgent(
    name="parallel_analysis",
    description="Runs SEO research, competitor discovery, and technical audit simultaneously.",
    sub_agents=[seo_researcher, competitor_agent, technical_seo_agent],
)


# ============================================================
# DELIVERY WORKFLOW
# ============================================================

delivery_workflow = SequentialAgent(
    name="delivery_workflow",
    description="Saves report to Notion then delivers link via AgentMail.",
    sub_agents=[notion_report_agent, agentmail_delivery_agent],
)


# ============================================================
# MAIN SEO GEO WORKFLOW
# ============================================================

seo_geo_workflow = SequentialAgent(
    name="seo_geo_workflow",
    description="Full SEO and GEO analysis pipeline.",
    sub_agents=[
        product_researcher,   # Step 1: scrape target
        parallel_analysis,    # Step 2: SEO + competitors + technical (parallel)
        geo_researcher,       # Step 3: GEO analysis (sequential — quota safety)
        local_geo_agent,      # Step 4: local GEO analysis (sequential)
        copywriter_agent,     # Step 5: compile report
        delivery_workflow,    # Step 6: Notion + email
    ],
)


# ============================================================
# ROOT AGENT
# ============================================================

root_agent = Agent(
    name="seo_geo_assistant",
    model=MODEL_NAME,
    description="SEO and GEO optimisation assistant for any website or Shopify store.",
    generate_content_config=RETRY_CONFIG,
    instruction="""
You are a helpful SEO and GEO optimisation assistant.

Ask the user for only two things:
1. Their website URL
2. Their email address

Tell them: "I will extract your business information automatically from your website."

Once you have BOTH the URL and email, reply with EXACTLY this message:
"✅ Got it! Starting your full SEO + GEO analysis for [URL].

Here's what's happening:
⏳ [1/7] Scraping your website...
⏳ [2/7] Running SEO research, competitor discovery and PageSpeed audit in parallel...
⏳ [3/7] GEO analysis...
⏳ [4/7] Local and regional GEO analysis...
⏳ [5/7] Compiling your scored report...
⏳ [6/7] Saving to Notion...
⏳ [7/7] Emailing your report...

⚠️ PageSpeed audit takes 60-90 seconds — total analysis is typically 4-6 minutes.
Progress updates will appear below as each stage completes."

Then immediately transfer control to seo_geo_workflow.
Do not call any tools.
Do not ask for anything else.
Do not add any extra commentary.
""",
    sub_agents=[seo_geo_workflow],
)