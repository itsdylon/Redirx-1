from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID

import numpy as np
from rapidfuzz import fuzz, process as rf_process
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from backend.services.results_formatter import calculate_path_similarity
from src.redirx.config import Config
from src.redirx.database import (
    DeepMatchPreviewDB,
    MigrationSessionDB,
    URLMappingDB,
    UserQuotaDB,
    WebPageEmbeddingDB,
)


INTENT_KEYWORDS = (
    'pricing',
    'product',
    'products',
    'services',
    'service',
    'contact',
    'checkout',
    'plans',
)


@dataclass
class CandidateRow:
    old_url: str
    quick_new_url: str
    quick_confidence: float
    needs_review: bool
    path_similarity_normalized: float
    risk_score: float


class DeepPreviewService:
    """
    Handles Deep Match conversion preview lifecycle for free quick-match sessions.
    """

    def __init__(self):
        self.preview_db = DeepMatchPreviewDB()
        self.session_db = MigrationSessionDB()
        self.mapping_db = URLMappingDB()
        self.quota_db = UserQuotaDB()
        self.embedding_db = WebPageEmbeddingDB()

    def maybe_queue_preview(
        self,
        source_session_id: UUID,
        user_id: str,
        old_urls: list[str],
        new_urls: list[str],
        opted_in: bool = False,
    ) -> dict[str, Any]:
        """
        Initialize or queue a preview content job when eligible.
        Returns a summary dict with status and details.
        """
        if not Config.ENABLE_DEEP_MATCH_PREVIEW:
            return {'status': 'not_applicable', 'reason': 'feature_disabled'}

        session = self.session_db.get_session(source_session_id)
        if session.get('pipeline_type') != 'url_only' or session.get('is_preview'):
            return {'status': 'not_applicable', 'reason': 'unsupported_source_session'}

        if self.quota_db.get_plan(user_id) != 'free':
            return {'status': 'not_applicable', 'reason': 'plan_not_free'}

        billable_pages = max(len(old_urls), len(new_urls))
        if billable_pages < max(1, Config.DEEP_MATCH_BACKGROUND_MIN_PAGES):
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='skipped',
                total_convincing_fixes=0,
                error_message='Deep Match preview is only available for larger projects.',
            )
            return {
                'status': 'skipped',
                'reason': 'below_background_threshold',
                'billable_pages': billable_pages,
            }

        if not Config.OPENAI_API_KEY:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='skipped',
                error_message='Deep Match preview is temporarily unavailable.',
            )
            return {'status': 'skipped', 'reason': 'missing_embeddings_config'}

        existing = self.preview_db.get_by_source_session(source_session_id)
        if existing:
            existing_status = str(existing.get('status') or '').lower()
            if existing_status in ('queued', 'processing', 'completed'):
                return {'status': existing_status, 'reason': 'already_exists'}
            if existing_status == 'awaiting_opt_in' and not opted_in:
                return {'status': 'awaiting_opt_in', 'reason': 'awaiting_opt_in'}

        if self.preview_db.count_recent_for_user(user_id, within_hours=24) >= Config.PREVIEW_MAX_JOBS_PER_USER_PER_DAY:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='skipped',
                error_message='Daily preview limit reached. Try again tomorrow.',
            )
            return {'status': 'skipped', 'reason': 'daily_limit'}

        source_mappings = self.mapping_db.get_mappings_by_session(source_session_id)
        candidates = self._score_candidates(source_mappings)

        if len(candidates) < 4:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='skipped',
                candidate_old_urls=[c.old_url for c in candidates],
                total_convincing_fixes=0,
                error_message='Not enough risky matches to generate a trustworthy preview.',
            )
            return {'status': 'skipped', 'reason': 'not_enough_candidates'}

        max_candidates = max(1, Config.PREVIEW_MAX_OLD_CANDIDATES)
        candidates = candidates[:max_candidates]
        old_url_set = set(old_urls)
        candidate_old_urls = [c.old_url for c in candidates if c.old_url in old_url_set]
        if len(candidate_old_urls) < 4:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='skipped',
                candidate_old_urls=candidate_old_urls,
                total_convincing_fixes=0,
                error_message='Not enough eligible source URLs remained for preview.',
            )
            return {'status': 'skipped', 'reason': 'not_enough_source_urls'}

        candidate_old_url_set = set(candidate_old_urls)
        candidates = [c for c in candidates if c.old_url in candidate_old_url_set]
        preview_new_urls = self._select_new_url_subset(new_urls, candidates)

        if len(preview_new_urls) < 2:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='skipped',
                candidate_old_urls=candidate_old_urls,
                total_convincing_fixes=0,
                error_message='Unable to build enough target URL context for preview run.',
            )
            return {'status': 'skipped', 'reason': 'insufficient_new_context'}

        if not opted_in:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=user_id,
                status='awaiting_opt_in',
                free_unlock_count=max(1, Config.PREVIEW_FREE_ROWS),
                candidate_old_urls=candidate_old_urls,
            )
            return {
                'status': 'awaiting_opt_in',
                'reason': 'awaiting_opt_in',
                'candidate_count': len(candidate_old_urls),
            }

        source_name = session.get('project_name') or 'Project'
        preview_session_id = self.session_db.create_session(
            user_id=user_id,
            project_name=f"{source_name} (Deep Match Preview)",
            old_urls=candidate_old_urls,
            new_urls=preview_new_urls,
            pipeline_type='content',
            is_preview=True,
            source_session_id=source_session_id,
        )

        self.preview_db.upsert_source_preview(
            source_session_id=source_session_id,
            user_id=user_id,
            status='queued',
            preview_session_id=preview_session_id,
            free_unlock_count=max(1, Config.PREVIEW_FREE_ROWS),
            candidate_old_urls=candidate_old_urls,
            opt_in_confirmed_at=datetime.now(timezone.utc).isoformat(),
        )

        return {
            'status': 'queued',
            'preview_session_id': str(preview_session_id),
            'candidate_count': len(candidate_old_urls),
        }

    def mark_processing(self, preview_session_id: UUID) -> None:
        self.preview_db.mark_processing_by_preview_session(preview_session_id)

    def mark_failed(self, preview_session_id: UUID, error_message: str) -> None:
        self.preview_db.mark_failed_by_preview_session(preview_session_id, error_message)

    def finalize_preview(self, preview_session_id: UUID) -> dict[str, Any]:
        """
        Evaluate preview job output and persist conversion-safe snapshot rows.
        """
        preview_row = self.preview_db.get_by_preview_session(preview_session_id)
        if not preview_row:
            return {'status': 'not_applicable', 'reason': 'preview_row_not_found'}

        source_session_id = UUID(preview_row['source_session_id'])
        free_rows = max(1, int(preview_row.get('free_unlock_count') or Config.PREVIEW_FREE_ROWS))

        convincing = self._build_convincing_fixes(
            source_session_id=source_session_id,
            preview_session_id=preview_session_id,
        )

        if len(convincing) < 3:
            self.preview_db.upsert_source_preview(
                source_session_id=source_session_id,
                user_id=preview_row['user_id'],
                status='skipped',
                preview_session_id=preview_session_id,
                free_unlock_count=free_rows,
                total_convincing_fixes=len(convincing),
                visible_items=[],
                locked_teasers=[],
                error_message='No persuasive Deep Match deltas met accuracy thresholds.',
                completed=True,
            )
            return {'status': 'skipped', 'total_convincing_fixes': len(convincing)}

        visible_items = convincing[:free_rows]
        locked_teasers = [
            {
                'old_url': item['old_url'],
                'current_target_url': item['current_target_url'],
                'confidence_gain_points': item['confidence_gain_points'],
                'deep_confidence': item['deep_confidence'],
                'teaser': 'Higher-confidence destination found',
            }
            for item in convincing[free_rows:]
        ]

        self.preview_db.mark_completed_by_preview_session(
            preview_session_id=preview_session_id,
            visible_items=visible_items,
            locked_teasers=locked_teasers,
            total_convincing_fixes=len(convincing),
            free_unlock_count=free_rows,
        )

        return {
            'status': 'completed',
            'total_convincing_fixes': len(convincing),
            'visible_items': visible_items,
            'locked_teasers': locked_teasers,
        }

    def _score_candidates(self, source_mappings: list[dict[str, Any]]) -> list[CandidateRow]:
        filtered = [
            m for m in source_mappings
            if (m.get('match_type') != 'exact_url') and float(m.get('confidence_score') or 0.0) <= 0.90
        ]

        target_counts: dict[str, int] = defaultdict(int)
        for row in filtered:
            target = row.get('new_url') or ''
            if target:
                target_counts[target] += 1

        candidates: list[CandidateRow] = []
        for row in filtered:
            old_url = row.get('old_url') or ''
            new_url = row.get('new_url') or ''
            if not old_url or not new_url:
                continue

            quick_conf = float(row.get('confidence_score') or 0.0)
            needs_review = bool(row.get('needs_review'))
            path_sim = calculate_path_similarity(old_url, new_url) / 100.0
            risk = (1 - quick_conf)

            if needs_review:
                risk += 0.20
            if 0.65 <= quick_conf < 0.85:
                risk += 0.15
            if quick_conf < 0.65:
                risk += 0.25

            risk += 0.10 * (1 - path_sim)

            if target_counts.get(new_url, 0) > 1:
                risk += 0.08

            if self._is_high_intent_url(old_url):
                risk += 0.06

            candidates.append(CandidateRow(
                old_url=old_url,
                quick_new_url=new_url,
                quick_confidence=quick_conf,
                needs_review=needs_review,
                path_similarity_normalized=path_sim,
                risk_score=risk,
            ))

        candidates.sort(key=lambda c: c.risk_score, reverse=True)

        seen_old: set[str] = set()
        deduped: list[CandidateRow] = []
        for candidate in candidates:
            if candidate.old_url in seen_old:
                continue
            seen_old.add(candidate.old_url)
            deduped.append(candidate)
        return deduped

    def _select_new_url_subset(self, new_urls: list[str], candidates: list[CandidateRow]) -> list[str]:
        full_scan_cutoff = max(1, Config.PREVIEW_MAX_NEW_URLS_FULL_SCAN)
        capped = max(2, Config.PREVIEW_MAX_NEW_URLS_CAPPED)

        if len(new_urls) <= full_scan_cutoff:
            return new_urls

        new_url_set = set(new_urls)
        new_paths = [self._normalize_path(url) for url in new_urls]
        url_scores: dict[str, float] = defaultdict(float)

        for candidate in candidates:
            if candidate.quick_new_url in new_url_set:
                url_scores[candidate.quick_new_url] += 8.0

            old_path = self._normalize_path(candidate.old_url)
            old_tokens = set(old_path.split())

            # Fuzzy path candidates
            for match_path, score, idx in rf_process.extract(old_path, new_paths, scorer=fuzz.WRatio, limit=4):
                _ = match_path
                url_scores[new_urls[idx]] += (score / 100.0) * 2.0

            # Token overlap candidates
            if old_tokens:
                for idx, new_path in enumerate(new_paths):
                    new_tokens = set(new_path.split())
                    if not new_tokens:
                        continue
                    overlap = len(old_tokens & new_tokens) / len(old_tokens | new_tokens)
                    if overlap >= 0.40:
                        url_scores[new_urls[idx]] += overlap

            # TF-IDF path similarity candidates
            docs = [old_path] + new_paths
            try:
                matrix = TfidfVectorizer(ngram_range=(1, 2)).fit_transform(docs)
                sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
                top_idx = np.argsort(sims)[::-1][:4]
                for idx in top_idx:
                    url_scores[new_urls[idx]] += float(sims[idx]) * 2.0
            except Exception:
                # Fallback safety: if vectorization fails, skip this heuristic.
                pass

        ranked = sorted(url_scores.items(), key=lambda kv: kv[1], reverse=True)

        # Preserve each candidate's current Quick Match target first.
        selected: list[str] = []
        selected_set: set[str] = set()
        for candidate in candidates:
            quick_url = candidate.quick_new_url
            if quick_url in new_url_set and quick_url not in selected_set:
                selected.append(quick_url)
                selected_set.add(quick_url)
                if len(selected) >= capped:
                    break

        # Fill remaining capacity with strongest ranked candidates.
        if len(selected) < capped:
            for url, _score in ranked:
                if url in selected_set:
                    continue
                selected.append(url)
                selected_set.add(url)
                if len(selected) >= capped:
                    break

        # Final fallback: always return at least 2 targets.
        if len(selected) < 2:
            minimum = min(capped, max(2, len(new_urls)))
            for url in new_urls:
                if url in selected_set:
                    continue
                selected.append(url)
                selected_set.add(url)
                if len(selected) >= minimum:
                    break

        if len(selected) > capped:
            selected = selected[:capped]

        return selected

    def _build_convincing_fixes(self, source_session_id: UUID, preview_session_id: UUID) -> list[dict[str, Any]]:
        source_mappings = self.mapping_db.get_mappings_by_session(source_session_id)
        preview_mappings = self.mapping_db.get_mappings_by_session(preview_session_id)

        source_by_old = {m['old_url']: m for m in source_mappings if m.get('old_url')}
        preview_by_old = {m['old_url']: m for m in preview_mappings if m.get('old_url')}

        deep_gaps = self._compute_deep_gaps(preview_session_id)

        convincing: list[dict[str, Any]] = []
        for old_url, quick_row in source_by_old.items():
            deep_row = preview_by_old.get(old_url)
            if not deep_row:
                continue

            quick_target = quick_row.get('new_url') or ''
            deep_target = deep_row.get('new_url') or ''
            if not quick_target or not deep_target or deep_target == quick_target:
                continue

            quick_conf = float(quick_row.get('confidence_score') or 0.0)
            deep_conf = float(deep_row.get('confidence_score') or 0.0)
            if deep_conf < 0.86:
                continue
            if bool(deep_row.get('needs_review')):
                continue

            margin = deep_conf - quick_conf
            if margin < 0.12:
                continue

            gap = deep_gaps.get(old_url, 0.0)
            if gap < 0.08:
                continue

            conviction = (margin * 0.55) + (deep_conf * 0.25) + (gap * 0.20)
            convincing.append({
                'old_url': old_url,
                'current_target_url': quick_target,
                'deep_target_url': deep_target,
                'quick_confidence': round(quick_conf, 4),
                'deep_confidence': round(deep_conf, 4),
                'confidence_gain': round(margin, 4),
                'confidence_gain_points': int(round(margin * 100)),
                'deep_gap': round(gap, 4),
                'quick_match_type': quick_row.get('match_type', ''),
                'deep_match_type': deep_row.get('match_type', ''),
                'conviction_score': round(conviction, 4),
            })

        convincing.sort(key=lambda row: row['conviction_score'], reverse=True)
        return convincing

    def _compute_deep_gaps(self, preview_session_id: UUID) -> dict[str, float]:
        old_embeddings = self.embedding_db.get_embeddings_by_session(preview_session_id, site_type='old')
        if not old_embeddings:
            return {}

        gaps: dict[str, float] = {}
        for emb in old_embeddings:
            old_url = emb.get('url')
            vector = emb.get('embedding')
            if not old_url or not vector:
                continue

            try:
                similar = self.embedding_db.find_similar_pages(
                    query_embedding=np.array(vector, dtype=np.float32),
                    session_id=preview_session_id,
                    site_type='new',
                    match_count=2,
                    match_threshold=0.0,
                )
            except Exception:
                continue

            if not similar:
                gaps[old_url] = 0.0
                continue

            top = float(similar[0].get('similarity') or 0.0)
            second = float(similar[1].get('similarity') or 0.0) if len(similar) > 1 else 0.0
            gaps[old_url] = max(0.0, top - second)

        return gaps

    @staticmethod
    def _normalize_path(url: str) -> str:
        path = urlparse(url).path.lower()
        normalized = path.replace('-', ' ').replace('_', ' ').replace('/', ' ').strip()
        return normalized or '/'

    @staticmethod
    def _is_high_intent_url(url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(token in path for token in INTENT_KEYWORDS)
