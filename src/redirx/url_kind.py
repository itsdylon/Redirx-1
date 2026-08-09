"""
URL classification.

Replaces BlogPruneStage, which dropped individual blog posts on the theory
that they should not be redirected. Measured against a real customer site,
those posts were 77% of organic clicks and 98% of impressions — pruning them
would have deleted most of what the migration needed to protect.

Path shape is a weak signal for importance and a decent one for *treatment*:
an individual article usually wants a 1:1 redirect, whereas pagination and
date archives are better served by a pattern rule. So the kind is advisory,
shown next to traffic in review, and never removes a URL on its own.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

KIND_HOMEPAGE = "homepage"
KIND_POST = "post"
KIND_PAGE = "page"
KIND_PAGINATION = "pagination"
KIND_DATE_ARCHIVE = "date_archive"
KIND_TAXONOMY = "taxonomy"
KIND_FEED = "feed"

# Archive-shaped URLs: rarely need a 1:1 redirect, usually better handled by a
# pattern rule. Still mapped, just not prioritised.
LOW_PRIORITY_KINDS = frozenset({KIND_PAGINATION, KIND_DATE_ARCHIVE, KIND_TAXONOMY, KIND_FEED})

_PAGINATION = re.compile(r"/page/\d+/?$", re.I)
_DATE_ARCHIVE = re.compile(r"^/\d{4}(/\d{1,2}){0,2}/?$")
_DATED_POST = re.compile(r"^/\d{4}/\d{1,2}/\d{1,2}/.+")
_TAXONOMY = re.compile(r"^/(category|tag|author|topics?)/", re.I)
_FEED = re.compile(r"/(feed|rss|atom)/?$", re.I)
# /blog/my-post or /news/2024-05-thing — the shapes the old pruner looked for.
_SECTION_POST = re.compile(r"^/(blogs?|news|articles?|insights|resources)/.+", re.I)


def classify_url_kind(url: str) -> str:
    """Best-effort classification from the URL path alone."""
    try:
        path = urlparse(url).path or "/"
    except Exception:
        return KIND_PAGE

    if path in ("", "/"):
        return KIND_HOMEPAGE
    if _FEED.search(path):
        return KIND_FEED
    if _PAGINATION.search(path):
        return KIND_PAGINATION
    if _DATE_ARCHIVE.match(path):
        return KIND_DATE_ARCHIVE
    if _TAXONOMY.match(path):
        return KIND_TAXONOMY
    # WordPress date permalinks (/2024/05/12/slug/) are a standard format the
    # old pruner never recognised, which is why it silently did nothing on
    # most WordPress sites.
    if _DATED_POST.match(path):
        return KIND_POST
    if _SECTION_POST.match(path):
        return KIND_POST
    return KIND_PAGE


def is_low_priority(url: str, clicks: int = 0, impressions: int = 0) -> bool:
    """
    Whether a URL can be deprioritised in review.

    Recorded traffic always wins over path shape: a paginated archive that
    actually gets clicks is not noise.
    """
    if clicks > 0 or impressions > 0:
        return False
    return classify_url_kind(url) in LOW_PRIORITY_KINDS
