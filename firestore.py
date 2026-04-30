from datetime import datetime
from typing import Optional
from .config import GCP_PROJECT_ID


def save_analysis_to_firestore(url: str, report_data: dict) -> str:
    """Saves a completed analysis run to Firestore. Returns the document ID."""
    try:
        from google.cloud import firestore
        db = firestore.Client(project=GCP_PROJECT_ID)
        doc_ref = db.collection("seo_analyses").document()
        doc_ref.set({
            "url": url,
            "timestamp": datetime.utcnow(),
            "scores": report_data.get("scores", {}),
            "notion_url": report_data.get("notion_url", ""),
            "full_report": report_data,
        })
        return doc_ref.id
    except Exception as e:
        return f"firestore_error: {str(e)}"


def get_previous_analysis(url: str) -> Optional[dict]:
    """Retrieves the most recent previous analysis for a URL from Firestore."""
    try:
        from google.cloud import firestore
        db = firestore.Client(project=GCP_PROJECT_ID)
        docs = (
            db.collection("seo_analyses")
            .where("url", "==", url)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc.to_dict()
        return None
    except Exception:
        return None


def build_score_diff(current: dict, previous: dict) -> dict:
    """Returns score deltas between current and previous run.

    Returns a dict of {score_key: {current, previous, delta, trend}}
    where trend is ▲, ▼, or →.
    """
    if not previous or "scores" not in previous:
        return {}
    diffs = {}
    for key, value in current.items():
        prev_value = previous["scores"].get(key)
        if prev_value is not None and isinstance(value, (int, float)):
            delta = value - prev_value
            diffs[key] = {
                "current": value,
                "previous": prev_value,
                "delta": delta,
                "trend": "▲" if delta > 0 else ("▼" if delta < 0 else "→"),
            }
    return diffs