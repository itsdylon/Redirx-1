"""
Curated tutorial mappings used to seed a first-time sample session.

These rows intentionally include mixed confidence bands and one exact match so
the review UI demonstrates filtering, sorting, and export behavior.
"""

from typing import List, Dict, Any


SAMPLE_TUTORIAL_MAPPINGS: List[Dict[str, Any]] = [
    {
        "old_url": "https://legacy-example.com/about",
        "new_url": "https://new-example.com/about",
        "confidence_score": 1.0,
        "match_type": "exact_url",
        "needs_review": False,
    },
    {
        "old_url": "https://legacy-example.com/services/seo-audit",
        "new_url": "https://new-example.com/services/technical-seo-audit",
        "confidence_score": 0.92,
        "match_type": "semantic",
        "needs_review": False,
    },
    {
        "old_url": "https://legacy-example.com/case-studies/retail-growth",
        "new_url": "https://new-example.com/case-studies/retail-traffic-growth",
        "confidence_score": 0.88,
        "match_type": "semantic",
        "needs_review": False,
    },
    {
        "old_url": "https://legacy-example.com/resources/redirect-guide",
        "new_url": "https://new-example.com/resources/301-redirect-guide",
        "confidence_score": 0.78,
        "match_type": "semantic",
        "needs_review": True,
    },
    {
        "old_url": "https://legacy-example.com/blog/platform-migration-checklist",
        "new_url": "https://new-example.com/blog/website-migration-checklist",
        "confidence_score": 0.71,
        "match_type": "semantic",
        "needs_review": True,
    },
    {
        "old_url": "https://legacy-example.com/pricing/agency",
        "new_url": "https://new-example.com/pricing/partners",
        "confidence_score": 0.67,
        "match_type": "semantic",
        "needs_review": True,
    },
    {
        "old_url": "https://legacy-example.com/integrations/hubspot",
        "new_url": "https://new-example.com/integrations/crm",
        "confidence_score": 0.56,
        "match_type": "semantic",
        "needs_review": True,
    },
    {
        "old_url": "https://legacy-example.com/docs/import",
        "new_url": "https://new-example.com/help/uploading-csvs",
        "confidence_score": 0.44,
        "match_type": "semantic",
        "needs_review": True,
    },
]

