import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Models ────────────────────────────────────────────────
MODEL_NAME = os.getenv("MODEL", "gemini-2.5-flash")

# ── GCP ───────────────────────────────────────────────────
GCP_PROJECT_ID = os.getenv("PROJECT_ID", "shopify-agent-491911")
GCP_REGION     = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

# ── API Keys ──────────────────────────────────────────────
NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
AGENTMAIL_API_KEY  = os.getenv("AGENTMAIL_API_KEY")
PAGESPEED_API_KEY  = os.getenv("PAGESPEED_API_KEY")
SERPAPI_KEY        = os.getenv("SERPAPI_KEY")

# ── Notion ────────────────────────────────────────────────
NOTION_PARENT_PAGE_ID = "337a3d74-f8bc-8041-bc9e-f9908aac45e4"

# ── Session State Keys ────────────────────────────────────
# These keys are written/read explicitly — never parsed from LLM text.
SESSION_KEY_EMAIL       = "user_email"
SESSION_KEY_URL         = "target_url"
SESSION_KEY_NOTION_URL  = "notion_page_url"

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Domains excluded from competitor results
EXCLUDED_COMPETITOR_DOMAINS = {
    "google.com", "youtube.com", "facebook.com",
    "wikipedia.org", "amazon.com", "reddit.com",
    "twitter.com", "instagram.com", "linkedin.com",
    "pinterest.com", "yelp.com", "tripadvisor.com",
}