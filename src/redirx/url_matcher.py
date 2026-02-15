"""
URL-based redirect matching engine.

Provides zero-API-cost URL matching through a cascading 3-pass approach:
1. Slug Match — exact slug comparison via hash lookup (O(n))
2. TF-IDF Token Match — cosine similarity on tokenized URL paths
3. RapidFuzz Fallback — fuzzy string matching on remaining unmatched URLs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, unquote


@dataclass
class UrlMatchConfig:
    """Configuration for the URL matching engine. Adjustable per user tier."""

    # Rate limits
    max_old_urls: int = 5000
    max_new_urls: int = 5000

    # Slug match settings
    slug_match_confidence: float = 0.90

    # TF-IDF settings
    tfidf_ngram_range: tuple = (1, 2)
    tfidf_top_n: int = 5
    tfidf_min_score: float = 0.40

    # RapidFuzz settings
    rapidfuzz_min_score: float = 0.45
    rapidfuzz_scorer: str = 'token_sort_ratio'

    # Confidence bands
    high_threshold: float = 0.75
    medium_threshold: float = 0.55
    low_threshold: float = 0.40

    # General
    skip_root_paths: bool = True


@dataclass
class MatchResult:
    """A single URL match result."""

    old_url: str
    new_url: str
    confidence_score: float
    match_type: str
    needs_review: bool


# Extensions to strip during normalization
_STRIP_EXTENSIONS = {'.html', '.htm', '.php', '.asp', '.aspx', '.jsp', '.shtml'}

# Default index filenames to remove
_INDEX_FILES = {'index', 'default'}

# camelCase boundary regex
_CAMEL_RE = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')


def _normalize_url(url: str) -> dict:
    """
    Decompose and normalize a URL for comparison.

    Returns a dict with: path, segments, slug, slug_tokens, all_tokens, depth, directory
    """
    parsed = urlparse(url)
    path = unquote(parsed.path).lower().rstrip('/')

    # Remove query params and fragments (already handled by urlparse)
    # Remove file extensions
    for ext in _STRIP_EXTENSIONS:
        if path.endswith(ext):
            path = path[:-len(ext)]
            break

    # Remove trailing /index or /default (after extension strip)
    parts = path.split('/')
    if parts and parts[-1] in _INDEX_FILES:
        parts = parts[:-1]

    path = '/'.join(parts) or '/'

    # Segments (non-empty path components)
    segments = [s for s in path.split('/') if s]

    # Slug is the last segment
    slug = segments[-1] if segments else ''

    # Tokenize slug: split on -, _, and camelCase boundaries
    slug_tokens = _tokenize(slug) if slug else []

    # Tokenize entire path
    all_tokens = []
    for seg in segments:
        all_tokens.extend(_tokenize(seg))

    return {
        'path': path,
        'segments': segments,
        'slug': slug,
        'slug_tokens': slug_tokens,
        'all_tokens': all_tokens,
        'depth': len(segments),
        'directory': '/'.join(segments[:-1]) if len(segments) > 1 else '',
    }


def _tokenize(text: str) -> list[str]:
    """Split text on -, _, ., and camelCase boundaries. Returns lowercase tokens."""
    # First split on camelCase
    expanded = _CAMEL_RE.sub('-', text)
    # Then split on separators
    tokens = re.split(r'[-_./]+', expanded)
    return [t.lower() for t in tokens if t and len(t) > 1]


class UrlMatcher:
    """
    Cascading URL matching engine.

    Usage:
        matcher = UrlMatcher(config=UrlMatchConfig())
        results = matcher.match(old_urls, new_urls)
    """

    def __init__(self, config: Optional[UrlMatchConfig] = None):
        self.config = config or UrlMatchConfig()

    def match(self, old_urls: list[str], new_urls: list[str]) -> list[MatchResult]:
        """
        Run all matching passes and return combined results.

        Args:
            old_urls: URLs from the old site (unmatched after exact URL match).
            new_urls: URLs from the new site (unmatched after exact URL match).

        Returns:
            List of MatchResult objects sorted by confidence descending.
        """
        cfg = self.config

        # Enforce rate limits
        old_urls = old_urls[:cfg.max_old_urls]
        new_urls = new_urls[:cfg.max_new_urls]

        # Pre-normalize all URLs
        old_norm = [(url, _normalize_url(url)) for url in old_urls]
        new_norm = [(url, _normalize_url(url)) for url in new_urls]

        # Filter root paths if configured
        if cfg.skip_root_paths:
            old_norm = [(u, n) for u, n in old_norm if n['path'] != '/']
            new_norm = [(u, n) for u, n in new_norm if n['path'] != '/']

        results: list[MatchResult] = []
        matched_old: set[str] = set()
        matched_new: set[str] = set()

        # Pass 1: Slug Match
        slug_results = self._pass_slug_match(old_norm, new_norm)
        for r in slug_results:
            results.append(r)
            matched_old.add(r.old_url)
            matched_new.add(r.new_url)

        print(f"UrlMatcher Pass 1 (Slug): {len(slug_results)} matches")

        # Filter remaining
        remaining_old = [(u, n) for u, n in old_norm if u not in matched_old]
        remaining_new = [(u, n) for u, n in new_norm if u not in matched_new]

        # Pass 2: TF-IDF
        if remaining_old and remaining_new:
            tfidf_results = self._pass_tfidf_match(remaining_old, remaining_new)
            for r in tfidf_results:
                results.append(r)
                matched_old.add(r.old_url)
                matched_new.add(r.new_url)

            print(f"UrlMatcher Pass 2 (TF-IDF): {len(tfidf_results)} matches")

            # Filter remaining again
            remaining_old = [(u, n) for u, n in remaining_old if u not in matched_old]
            remaining_new = [(u, n) for u, n in remaining_new if u not in matched_new]

        # Pass 3: RapidFuzz fallback
        if remaining_old and remaining_new:
            fuzz_results = self._pass_rapidfuzz_match(remaining_old, remaining_new)
            for r in fuzz_results:
                results.append(r)
                matched_old.add(r.old_url)
                matched_new.add(r.new_url)

            print(f"UrlMatcher Pass 3 (RapidFuzz): {len(fuzz_results)} matches")

        # Summary
        unmatched_old_count = len([u for u, _ in old_norm if u not in matched_old])
        unmatched_new_count = len([u for u, _ in new_norm if u not in matched_new])
        print(f"UrlMatcher Summary: {len(results)} total matches, "
              f"{unmatched_old_count} orphaned old, {unmatched_new_count} unmatched new")

        # Sort by confidence descending
        results.sort(key=lambda r: r.confidence_score, reverse=True)
        return results

    def _classify_match(self, score: float) -> tuple[str, bool]:
        """Classify a score into match_type and needs_review."""
        cfg = self.config
        if score >= cfg.high_threshold:
            return 'url_high', False
        elif score >= cfg.medium_threshold:
            return 'url_medium', True
        elif score >= cfg.low_threshold:
            return 'url_low', True
        else:
            return 'url_low', True

    # ========================================
    # Pass 1: Slug Match
    # ========================================

    def _pass_slug_match(
        self,
        old_norm: list[tuple[str, dict]],
        new_norm: list[tuple[str, dict]],
    ) -> list[MatchResult]:
        """Match URLs with identical slugs via hash-map lookup."""
        cfg = self.config
        results: list[MatchResult] = []

        # Build map: slug -> list of (url, norm) for new URLs
        slug_map: dict[str, list[tuple[str, dict]]] = {}
        for url, norm in new_norm:
            slug = norm['slug']
            if not slug:
                continue
            slug_map.setdefault(slug, []).append((url, norm))

        matched_new_urls: set[str] = set()

        for old_url, old_norm_data in old_norm:
            old_slug = old_norm_data['slug']
            if not old_slug:
                continue

            candidates = slug_map.get(old_slug)
            if not candidates:
                continue

            # Pick the best candidate (prefer same directory depth, then first available)
            best_url = None
            best_norm = None
            for new_url, new_norm_data in candidates:
                if new_url in matched_new_urls:
                    continue
                if best_url is None:
                    best_url = new_url
                    best_norm = new_norm_data
                # Prefer same depth
                elif new_norm_data['depth'] == old_norm_data['depth'] and best_norm['depth'] != old_norm_data['depth']:
                    best_url = new_url
                    best_norm = new_norm_data

            if best_url is not None:
                results.append(MatchResult(
                    old_url=old_url,
                    new_url=best_url,
                    confidence_score=cfg.slug_match_confidence,
                    match_type='slug_match',
                    needs_review=False,
                ))
                matched_new_urls.add(best_url)

        return results

    # ========================================
    # Pass 2: TF-IDF Token Match
    # ========================================

    def _pass_tfidf_match(
        self,
        old_norm: list[tuple[str, dict]],
        new_norm: list[tuple[str, dict]],
    ) -> list[MatchResult]:
        """Match URLs using TF-IDF cosine similarity on tokenized paths."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        cfg = self.config
        results: list[MatchResult] = []

        # Convert tokens to strings for TfidfVectorizer
        old_docs = [' '.join(n['all_tokens']) for _, n in old_norm]
        new_docs = [' '.join(n['all_tokens']) for _, n in new_norm]

        # Filter out empty docs
        valid_old = [(i, doc) for i, doc in enumerate(old_docs) if doc.strip()]
        valid_new = [(i, doc) for i, doc in enumerate(new_docs) if doc.strip()]

        if not valid_old or not valid_new:
            return results

        old_indices = [i for i, _ in valid_old]
        new_indices = [i for i, _ in valid_new]
        old_texts = [doc for _, doc in valid_old]
        new_texts = [doc for _, doc in valid_new]

        # Fit TF-IDF on combined corpus
        vectorizer = TfidfVectorizer(
            ngram_range=cfg.tfidf_ngram_range,
            analyzer='word',
            token_pattern=r'[a-z0-9]+',
        )

        all_texts = old_texts + new_texts
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        old_matrix = tfidf_matrix[:len(old_texts)]
        new_matrix = tfidf_matrix[len(old_texts):]

        # Compute cosine similarity
        sim_matrix = cosine_similarity(old_matrix, new_matrix)

        matched_new: set[int] = set()

        # For each old URL, find the best new URL above threshold
        # Process in order of highest similarity first for greedy assignment
        scored_pairs = []
        for old_idx_local in range(len(old_texts)):
            for new_idx_local in range(len(new_texts)):
                score = float(sim_matrix[old_idx_local, new_idx_local])
                if score >= cfg.tfidf_min_score:
                    scored_pairs.append((score, old_idx_local, new_idx_local))

        # Sort by score descending for greedy best-first matching
        scored_pairs.sort(key=lambda x: x[0], reverse=True)

        matched_old_local: set[int] = set()

        for score, old_idx_local, new_idx_local in scored_pairs:
            if old_idx_local in matched_old_local or new_idx_local in matched_new:
                continue

            old_orig_idx = old_indices[old_idx_local]
            new_orig_idx = new_indices[new_idx_local]

            old_url = old_norm[old_orig_idx][0]
            new_url = new_norm[new_orig_idx][0]

            match_type, needs_review = self._classify_match(score)

            results.append(MatchResult(
                old_url=old_url,
                new_url=new_url,
                confidence_score=round(score, 4),
                match_type=match_type,
                needs_review=needs_review,
            ))

            matched_old_local.add(old_idx_local)
            matched_new.add(new_idx_local)

        return results

    # ========================================
    # Pass 3: RapidFuzz Fallback
    # ========================================

    def _pass_rapidfuzz_match(
        self,
        old_norm: list[tuple[str, dict]],
        new_norm: list[tuple[str, dict]],
    ) -> list[MatchResult]:
        """Match remaining URLs using RapidFuzz fuzzy string matching on paths."""
        from rapidfuzz import process as rfprocess
        from rapidfuzz import fuzz

        cfg = self.config
        results: list[MatchResult] = []

        # Select scorer
        scorer_map = {
            'token_sort_ratio': fuzz.token_sort_ratio,
            'token_set_ratio': fuzz.token_set_ratio,
            'ratio': fuzz.ratio,
            'partial_ratio': fuzz.partial_ratio,
        }
        scorer = scorer_map.get(cfg.rapidfuzz_scorer, fuzz.token_sort_ratio)

        # Use normalized paths for comparison
        new_paths = [n['path'] for _, n in new_norm]
        new_urls = [u for u, _ in new_norm]

        matched_new: set[int] = set()

        # Score all pairs and do greedy assignment
        scored_pairs = []
        for old_idx, (old_url, old_norm_data) in enumerate(old_norm):
            old_path = old_norm_data['path']
            # Extract top candidates
            matches = rfprocess.extract(
                old_path,
                new_paths,
                scorer=scorer,
                limit=cfg.tfidf_top_n,
            )
            for match_path, raw_score, new_idx in matches:
                score = raw_score / 100.0  # Normalize 0-100 to 0.0-1.0
                if score >= cfg.rapidfuzz_min_score:
                    scored_pairs.append((score, old_idx, new_idx))

        # Sort by score descending for greedy matching
        scored_pairs.sort(key=lambda x: x[0], reverse=True)

        matched_old: set[int] = set()

        for score, old_idx, new_idx in scored_pairs:
            if old_idx in matched_old or new_idx in matched_new:
                continue

            old_url = old_norm[old_idx][0]
            new_url = new_urls[new_idx]

            match_type, needs_review = self._classify_match(score)

            results.append(MatchResult(
                old_url=old_url,
                new_url=new_url,
                confidence_score=round(score, 4),
                match_type=match_type,
                needs_review=needs_review,
            ))

            matched_old.add(old_idx)
            matched_new.add(new_idx)

        return results
