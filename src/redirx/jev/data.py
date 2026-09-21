"""Load page sets and turn URLs / page text into tokens and readable strings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = {"html", "htm", "index", "php", "the", "a", "an", "and", "of", "to", "in", "q"}


@dataclass
class Page:
    site: str
    url: str
    title: str = ""
    text: str = ""
    id: str | None = None
    true_new_url: str | None = None
    alt_new_urls: list[str] = field(default_factory=list)
    label: str | None = None

    @property
    def key(self) -> str:
        return f"{self.site}|{self.url}"

    @property
    def has_content(self) -> bool:
        return bool(self.title or self.text)


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


def text_tokens(text: str, max_words: int = 300) -> list[str]:
    toks = TOKEN_RE.findall(text.lower())
    return [t for t in toks[:max_words] if t not in STOP and len(t) > 1]


def doc_tokens(page: Page) -> list[str]:
    """Token stream for lexical retrieval: path twice (it is the strongest signal), then title, then text."""
    toks = path_tokens(page.url) * 2
    if page.title:
        toks += text_tokens(page.title, 40)
    if page.text:
        toks += text_tokens(page.text, 300)
    return toks


def humanize_path(url: str) -> str:
    segs = [s for s in clean_url(url).split("/") if s]
    words = [re.sub(r"[-_.]+", " ", s) for s in segs]
    return " / ".join(words) if words else "(site root)"


def excerpt(text: str, max_chars: int) -> str:
    if not text or max_chars <= 0:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    if " " in cut[max_chars // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut + " …"


def embed_text(page: Page, max_chars: int = 1200) -> str:
    parts = [f"path: {humanize_path(page.url)}"]
    if page.title:
        parts.append(f"title: {page.title}")
    if page.text:
        parts.append(f"text: {excerpt(page.text, max_chars)}")
    return " | ".join(parts)


def page_view(page: Page, excerpt_chars: int) -> dict:
    """What Jev sees for one page. Only fields that exist are included, so path-only sets stay tiny."""
    view: dict = {"url": page.url}
    if page.title:
        view["title"] = page.title
    if page.text and excerpt_chars > 0:
        view["excerpt"] = excerpt(page.text, excerpt_chars)
    return view
