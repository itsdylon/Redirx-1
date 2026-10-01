"""URL inventory values and retrieval tokenization; raw URL identity is preserved."""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = {"html", "htm", "index", "php", "the", "a", "an", "and", "of", "to", "in", "q"}


@dataclass
class Page:
    site: str
    url: str
    id: str | None = None
    # Populated only by audited confirmations; never by model confidence.
    true_new_url: str | None = None


def clean_url(url: str) -> str:
    """Strip redirect-file artefacts and file extensions; keep the path readable."""
    u = url.strip()
    u = u.replace("(/index.html)", "").replace("(/index)", "")
    u = re.sub(r"/index\.html?$", "", u)
    u = re.sub(r"\.html?$", "", u)
    u = re.sub(r"[()]", "", u)
    if len(u) > 1:
        u = u.rstrip("/")
    return u or "/"


def path_tokens(url: str) -> list[str]:
    toks = TOKEN_RE.findall(clean_url(url).lower())
    return [t for t in toks if t not in STOP]


def humanize_path(url: str) -> str:
    segs = [s for s in clean_url(url).split("/") if s]
    words = [re.sub(r"[-_.]+", " ", s) for s in segs]
    return " / ".join(words) if words else "(site root)"


def page_view(page: Page) -> dict:
    """The URL-only evidence sent to the judge. Confirmed targets are never included."""
    return {"url": page.url}
