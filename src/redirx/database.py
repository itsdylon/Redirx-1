from supabase import create_client, Client
from typing import Optional, List, Dict, Any
import numpy as np
from uuid import UUID

from .config import Config


class SupabaseClient:
    """
    Singleton wrapper for Supabase client.
    Provides high-level methods for interacting with the database.
    """

    _instance: Optional[Client] = None

    @classmethod
    def get_client(cls) -> Client:
        """
        Get or create the Supabase client instance.

        Returns:
            Client: Supabase client instance.

        Raises:
            ValueError: If required configuration is missing.
        """
        if cls._instance is None:
            Config.validate()
            cls._instance = create_client(
                Config.SUPABASE_URL,
                Config.SUPABASE_KEY
            )
        return cls._instance

    @classmethod
    def get_admin_client(cls) -> Client:
        """
        Create a fresh Supabase client with service_role privileges.

        Unlike get_client(), this always returns a NEW client that is never
        contaminated by user auth calls (sign_in_with_password mutates the
        singleton's auth state). Use this for webhook handlers and other
        server-side operations that must bypass RLS reliably.
        """
        Config.validate()
        return create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

    @classmethod
    def reset(cls) -> None:
        """
        Reset the client instance. Useful for testing.
        """
        cls._instance = None


class MigrationSessionDB:
    """
    Database operations for migration sessions.
    """

    def __init__(self, client: Optional[Client] = None):
        self.client = client or SupabaseClient.get_client()

    def create_session(
        self,
        user_id: str = 'default',
        project_name: Optional[str] = None,
        old_urls: Optional[List[str]] = None,
        new_urls: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
        pipeline_type: str = 'content',
    ) -> UUID:
        """
        Create a new migration session.

        Args:
            user_id: User identifier for the session.
            project_name: Optional project/session name.
            old_urls: Optional list of old site URLs (for background processing).
            new_urls: Optional list of new site URLs (for background processing).
            idempotency_key: Optional idempotency key to prevent duplicate job creation.
            pipeline_type: 'content' (default) or 'url_only' (free tier).

        Returns:
            UUID: The created session ID.
        """
        session_data = {
            'user_id': user_id,
            'status': 'pending',
            'pipeline_type': pipeline_type,
        }

        if project_name:
            session_data['project_name'] = project_name

        if old_urls is not None:
            session_data['old_urls'] = old_urls

        if new_urls is not None:
            session_data['new_urls'] = new_urls

        if idempotency_key:
            session_data['idempotency_key'] = idempotency_key

        result = self.client.table('migration_sessions').insert(session_data).execute()

        return UUID(result.data[0]['id'])

    def update_session_status(self, session_id: UUID, status: str) -> None:
        """
        Update the status of a migration session.

        Args:
            session_id: The session ID to update.
            status: New status ('pending', 'processing', 'completed').
        """
        self.client.table('migration_sessions').update({
            'status': status
        }).eq('id', str(session_id)).execute()

    def update_session_progress(self, session_id: UUID, current_stage: int, stage_name: str, total_stages: int) -> None:
        """
        Update the progress fields of a migration session.

        Args:
            session_id: The session ID to update.
            current_stage: Current stage number (1-based).
            stage_name: Friendly name of the current stage.
            total_stages: Total number of stages in the pipeline.
        """
        self.client.table('migration_sessions').update({
            'current_stage': current_stage,
            'stage_name': stage_name,
            'total_stages': total_stages,
        }).eq('id', str(session_id)).execute()

    def get_session(self, session_id: UUID) -> Dict[str, Any]:
        """
        Get session details.

        Args:
            session_id: The session ID to retrieve.

        Returns:
            Dict containing session data.
        """
        result = self.client.table('migration_sessions').select('*').eq(
            'id', str(session_id)
        ).execute()

        if not result.data:
            raise ValueError(f"Session {session_id} not found")

        return result.data[0]

    def find_session_by_idempotency_key(self, user_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Find an existing session by idempotency key.

        Args:
            user_id: User identifier.
            idempotency_key: Idempotency key to search for.

        Returns:
            Dict containing session data if found, None otherwise.
        """
        result = self.client.table('migration_sessions').select('*').eq(
            'user_id', user_id
        ).eq(
            'idempotency_key', idempotency_key
        ).execute()

        if result.data:
            return result.data[0]
        return None

    def update_session_status_with_error(self, session_id: UUID, status: str, error_message: Optional[str] = None) -> None:
        """
        Update the status of a migration session with optional error message.

        Args:
            session_id: The session ID to update.
            status: New status ('pending', 'processing', 'completed', 'permanently_failed').
            error_message: Optional error message (truncated to 5000 chars).
        """
        updates = {'status': status}

        if error_message is not None:
            # Truncate error message to 5000 characters
            updates['last_error'] = error_message[:5000] if len(error_message) > 5000 else error_message

        self.client.table('migration_sessions').update(updates).eq(
            'id', str(session_id)
        ).execute()


class WebPageEmbeddingDB:
    """
    Database operations for webpage embeddings.
    """

    def __init__(self, client: Optional[Client] = None):
        self.client = client or SupabaseClient.get_client()

    def insert_embedding(
        self,
        session_id: UUID,
        url: str,
        site_type: str,
        embedding: np.ndarray,
        extracted_text: str,
        title: str = ''
    ) -> UUID:
        """
        Insert a webpage embedding into the database.

        Args:
            session_id: Migration session ID.
            url: The webpage URL.
            site_type: Either 'old' or 'new'.
            embedding: Vector embedding array.
            extracted_text: Extracted text content.
            title: Page title.

        Returns:
            UUID: The created embedding ID.
        """
        result = self.client.table('webpage_embeddings').insert({
            'session_id': str(session_id),
            'url': url,
            'site_type': site_type,
            'embedding': embedding.tolist(),
            'extracted_text': extracted_text,
            'title': title
        }).execute()

        return UUID(result.data[0]['id'])

    def find_similar_pages(
        self,
        query_embedding: np.ndarray,
        session_id: UUID,
        site_type: str,
        match_count: int = 5,
        match_threshold: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Find similar pages using vector similarity search.

        Args:
            query_embedding: Query vector to search for.
            session_id: Migration session ID.
            site_type: Target site type ('old' or 'new').
            match_count: Maximum number of results to return.
            match_threshold: Minimum similarity threshold.

        Returns:
            List of dictionaries containing matching pages with similarity scores.
        """
        result = self.client.rpc('match_pages', {
            'query_embedding': query_embedding.tolist(),
            'target_site_type': site_type,
            'target_session_id': str(session_id),
            'match_count': match_count,
            'match_threshold': match_threshold
        }).execute()

        return result.data

    def get_embeddings_by_session(
        self,
        session_id: UUID,
        site_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all embeddings for a session.

        Args:
            session_id: Migration session ID.
            site_type: Optional filter by site type.

        Returns:
            List of embedding records with parsed embedding vectors.
        """
        query = self.client.table('webpage_embeddings').select('*').eq(
            'session_id', str(session_id)
        )

        if site_type:
            query = query.eq('site_type', site_type)

        result = query.execute()

        # Parse embedding vectors if they're returned as strings
        import json
        for record in result.data:
            if 'embedding' in record and isinstance(record['embedding'], str):
                record['embedding'] = json.loads(record['embedding'])

        return result.data


class UserQuotaDB:
    """
    Database operations for user credit quotas.
    """

    # Plans that get unlimited Quick Match (no quota check)
    PAID_PLANS = ('starter', 'growth', 'scale', 'enterprise', 'founder')

    def __init__(self, client: Optional[Client] = None):
        self.client = client or SupabaseClient.get_client()

    def _get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch user profile row. Returns None if not found."""
        result = self.client.table('user_profiles').select(
            'plan, credits_limit, credits_used, is_lifetime, '
            'lifetime_credits_total, lifetime_credits_used, '
            'quick_match_limit, quick_match_used, max_concurrent_projects'
        ).eq('id', user_id).execute()
        return result.data[0] if result.data else None

    def get_plan(self, user_id: str) -> str:
        """
        Get the user's current plan.

        Returns:
            Plan string ('launch', 'starter', 'growth', 'scale', 'enterprise', 'founder').
            Defaults to 'launch'.
        """
        result = self.client.table('user_profiles').select(
            'plan'
        ).eq('id', user_id).execute()

        if not result.data:
            return 'launch'

        return result.data[0].get('plan') or 'launch'

    def check_credits(self, user_id: str) -> tuple[bool, int, int]:
        """
        Check if user has remaining Deep Match credits.

        Returns:
            Tuple of (has_credits, credits_used, credits_limit).
        """
        profile = self._get_profile(user_id)

        if not profile:
            return False, 0, 0

        # Founder / lifetime plan
        if profile.get('is_lifetime') and profile.get('lifetime_credits_total') is not None:
            used = profile.get('lifetime_credits_used') or 0
            total = profile['lifetime_credits_total']
            return used < total, used, total

        used = profile.get('credits_used') or 0
        limit = profile.get('credits_limit') or 0
        return used < limit, used, limit

    def check_quick_match_quota(self, user_id: str) -> tuple[bool, int, Optional[int]]:
        """
        Check if user has remaining Quick Match quota.

        Returns:
            Tuple of (has_quota, quick_match_used, quick_match_limit).
            quick_match_limit is None for paid plans (unlimited).
        """
        profile = self._get_profile(user_id)

        if not profile:
            return True, 0, 2500

        plan = profile.get('plan') or 'launch'

        # Paid plans get unlimited Quick Match
        if plan in self.PAID_PLANS:
            return True, 0, None

        used = profile.get('quick_match_used') or 0
        limit = profile.get('quick_match_limit') or 2500
        return used < limit, used, limit

    def increment_credits(self, user_id: str, count: int) -> None:
        """
        Increment the user's Deep Match credit usage.
        Routes to lifetime_credits_used for founder plan.
        """
        profile = self._get_profile(user_id)

        if profile and profile.get('is_lifetime'):
            try:
                self.client.rpc('increment_lifetime_usage', {
                    'target_user_id': user_id,
                    'amount': count
                }).execute()
                return
            except Exception:
                pass

        try:
            self.client.rpc('increment_user_usage', {
                'target_user_id': user_id,
                'amount': count
            }).execute()
        except Exception:
            result = self.client.table('user_profiles').select(
                'credits_used'
            ).eq('id', user_id).execute()

            if result.data:
                current = result.data[0].get('credits_used') or 0
                self.client.table('user_profiles').update({
                    'credits_used': current + count
                }).eq('id', user_id).execute()

    def increment_quick_match_usage(self, user_id: str, count: int) -> None:
        """
        Increment the user's Quick Match usage count.
        Only relevant for free-tier users (paid plans skip quota checks).
        """
        try:
            self.client.rpc('increment_quick_match_usage', {
                'target_user_id': user_id,
                'amount': count
            }).execute()
        except Exception:
            result = self.client.table('user_profiles').select(
                'quick_match_used'
            ).eq('id', user_id).execute()

            if result.data:
                current = result.data[0].get('quick_match_used') or 0
                self.client.table('user_profiles').update({
                    'quick_match_used': current + count
                }).eq('id', user_id).execute()

    def get_remaining_credits(self, user_id: str) -> int:
        """
        Get remaining Deep Match credits for a user.
        """
        has_credits, used, limit = self.check_credits(user_id)
        return max(0, limit - used)

    # Backward-compatible aliases
    def check_quota(self, user_id: str) -> tuple[bool, int, int]:
        """Alias for check_credits (backward compatibility)."""
        return self.check_credits(user_id)

    def increment_usage(self, user_id: str, count: int) -> None:
        """Alias for increment_credits (backward compatibility)."""
        return self.increment_credits(user_id, count)

    def get_subscription_plan(self, user_id: str) -> str:
        """Alias for get_plan (backward compatibility)."""
        return self.get_plan(user_id)

    def get_remaining_quota(self, user_id: str) -> int:
        """Alias for get_remaining_credits (backward compatibility)."""
        return self.get_remaining_credits(user_id)


class URLMappingDB:
    """
    Database operations for URL mappings (redirects).
    """

    def __init__(self, client: Optional[Client] = None):
        self.client = client or SupabaseClient.get_client()

    def insert_mapping(
        self,
        session_id: UUID,
        old_url: str,
        new_url: str,
        confidence_score: float,
        match_type: str,
        needs_review: bool = False
    ) -> UUID:
        """
        Insert a URL mapping.

        Args:
            session_id: Migration session ID.
            old_url: Old site URL.
            new_url: New site URL.
            confidence_score: Similarity/confidence score.
            match_type: Type of match ('exact_url', 'exact_html', 'semantic', 'manual').
            needs_review: Whether the mapping needs human review.

        Returns:
            UUID: The created mapping ID.
        """
        result = self.client.table('url_mappings').insert({
            'session_id': str(session_id),
            'old_url': old_url,
            'new_url': new_url,
            'confidence_score': confidence_score,
            'match_type': match_type,
            'needs_review': needs_review
        }).execute()

        return UUID(result.data[0]['id'])

    def get_mappings_by_session(
        self,
        session_id: UUID,
        needs_review: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all mappings for a session.

        Args:
            session_id: Migration session ID.
            needs_review: Optional filter by review status.

        Returns:
            List of mapping records.
        """
        query = self.client.table('url_mappings').select('*').eq(
            'session_id', str(session_id)
        )

        if needs_review is not None:
            query = query.eq('needs_review', needs_review)

        result = query.execute()
        return result.data

    def get_mapping_by_id(self, mapping_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Fetch a single URL mapping by its ID.

        Args:
            mapping_id: The mapping UUID.

        Returns:
            Mapping record dict, or None if not found.
        """
        result = self.client.table('url_mappings').select('*').eq(
            'id', str(mapping_id)
        ).execute()
        return result.data[0] if result.data else None

    def update_mapping(
        self,
        mapping_id: UUID,
        new_url: Optional[str] = None,
        needs_review: Optional[bool] = None
    ) -> None:
        """
        Update a URL mapping (e.g., after human review).

        Args:
            mapping_id: Mapping ID to update.
            new_url: Optional new URL to update to.
            needs_review: Optional review status update.
        """
        updates = {}
        if new_url is not None:
            updates['new_url'] = new_url
        if needs_review is not None:
            updates['needs_review'] = needs_review

        if updates:
            self.client.table('url_mappings').update(updates).eq(
                'id', str(mapping_id)
            ).execute()
