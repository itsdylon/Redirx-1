"""
Integration tests for EmbedStage and PairingStage.

Tests the complete workflow from webpage content through embedding generation
to final URL pairing with confidence scores.
"""

import unittest
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import numpy as np
from uuid import uuid4, UUID
from typing import List

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import EmbedStage, PairingStage, WebPage, Mapping
from src.redirx.config import Config
from src.redirx.database import WebPageEmbeddingDB, MigrationSessionDB, URLMappingDB


# ============================================================================
# Helper Functions
# ============================================================================

def load_mock_site_pages(site_dir: str, limit: int = None) -> List[WebPage]:
    """
    Load HTML files from mock site directory.

    Args:
        site_dir: Path to mock site directory (e.g., 'tests/mock_sites/old_site')
        limit: Optional limit on number of pages to load

    Returns:
        List of WebPage objects
    """
    site_path = Path(site_dir)
    pages = []

    # Find all HTML files
    html_files = sorted(site_path.rglob('*.html'))

    if limit:
        html_files = html_files[:limit]

    for html_file in html_files:
        # Create relative URL path
        rel_path = html_file.relative_to(site_path)
        url = f"http://example.com/{rel_path}"

        # Read HTML content
        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()

        pages.append(WebPage(url, html))

    return pages


def mock_embedding_generator(text: str) -> np.ndarray:
    """
    Generate deterministic mock embeddings based on text content.

    Similar text will produce similar embeddings.

    Args:
        text: Text content to embed

    Returns:
        1536-dimensional numpy array (float32)
    """
    # Use text hash as seed for reproducibility
    seed = hash(text) % (2**32)
    rng = np.random.RandomState(seed)

    # Generate embedding
    embedding = rng.rand(1536).astype(np.float32)

    # Normalize to unit length (like real embeddings)
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm

    return embedding


def calculate_cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Calculate cosine similarity between two vectors."""
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(dot_product / (norm1 * norm2))


# ============================================================================
# Integration Test Suite
# ============================================================================

class TestEmbedPairingIntegration(unittest.TestCase):
    """Integration tests for EmbedStage + PairingStage workflow."""

    def setUp(self):
        """Set up test fixtures."""
        self.session_id = uuid4()

        # Create simple test pages
        self.old_page_1 = WebPage(
            'http://old.com/products',
            '<html><body><h1>Products</h1><p>Our product catalog and offerings.</p></body></html>'
        )
        self.old_page_2 = WebPage(
            'http://old.com/services',
            '<html><body><h1>Services</h1><p>Professional services and consulting.</p></body></html>'
        )
        self.old_page_orphaned = WebPage(
            'http://old.com/legacy-feature',
            '<html><body><h1>Legacy Feature</h1><p>Discontinued feature.</p></body></html>'
        )

        self.new_page_1 = WebPage(
            'http://new.com/products',
            '<html><body><h1>Product Catalog</h1><p>Browse our complete product offerings.</p></body></html>'
        )
        self.new_page_2 = WebPage(
            'http://new.com/solutions',
            '<html><body><h1>Solutions</h1><p>Expert consulting and professional services.</p></body></html>'
        )
        self.new_page_new = WebPage(
            'http://new.com/innovations',
            '<html><body><h1>Innovations</h1><p>New innovative features and products.</p></body></html>'
        )

    # ========================================================================
    # Test 1: Full Workflow with Mocked Services
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_full_workflow_with_mocked_services(
        self,
        mock_validate,
        mock_openai,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_embedding,
        mock_insert_mapping
    ):
        """Test complete EmbedStage → PairingStage workflow with mocks."""
        # Setup OpenAI mock
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_openai.return_value = mock_client

        # Setup test data
        old_pages = [self.old_page_1, self.old_page_2]
        new_pages = [self.new_page_1, self.new_page_2]
        mappings = set()

        # Mock embeddings database responses
        mock_get_embeddings.return_value = [
            {'url': 'http://old.com/products', 'embedding': mock_embedding_generator('products').tolist()},
            {'url': 'http://old.com/services', 'embedding': mock_embedding_generator('services').tolist()}
        ]

        # Mock similarity search
        def mock_find_similar_impl(query_embedding, session_id, site_type, match_count, match_threshold):
            # Determine which old page based on similarity to products/services
            products_emb = mock_embedding_generator('products')
            if calculate_cosine_similarity(query_embedding, products_emb) > 0.9:
                return [{'url': 'http://new.com/products', 'similarity': 0.92}]
            else:
                return [{'url': 'http://new.com/solutions', 'similarity': 0.88}]

        mock_find_similar.side_effect = mock_find_similar_impl

        # Execute EmbedStage
        embed_stage = EmbedStage(session_id=self.session_id)
        result_after_embed = await embed_stage.execute((old_pages, new_pages, mappings))

        # Verify EmbedStage results
        self.assertEqual(len(result_after_embed), 3)
        self.assertEqual(result_after_embed[0], old_pages)
        self.assertEqual(result_after_embed[1], new_pages)

        # Verify embeddings were inserted (4 total: 2 old + 2 new)
        self.assertEqual(mock_insert_embedding.call_count, 4)

        # Execute PairingStage
        pairing_stage = PairingStage(session_id=self.session_id)
        result_after_pairing = await pairing_stage.execute(result_after_embed)

        # Verify PairingStage results
        self.assertEqual(len(result_after_pairing), 3)
        self.assertEqual(len(result_after_pairing[2]), 2)  # 2 mappings created

        # Verify mappings were inserted
        self.assertEqual(mock_insert_mapping.call_count, 2)

    # ========================================================================
    # Test 2: Confidence Scoring Accuracy
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_confidence_scoring_accuracy(
        self,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_mapping
    ):
        """Test that confidence scores produce correct match types and review flags."""
        pairing_stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1, self.new_page_2]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/products',
            'embedding': [0.1] * 1536
        }]

        # Test high confidence (0.95)
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.95}
        ]

        await pairing_stage.execute((old_pages, new_pages, mappings))

        call_kwargs = mock_insert_mapping.call_args[1]
        self.assertEqual(call_kwargs['confidence_score'], 0.95)
        self.assertEqual(call_kwargs['match_type'], 'semantic_high')
        self.assertFalse(call_kwargs['needs_review'])

        # Reset for next test
        mock_insert_mapping.reset_mock()

        # Test medium confidence with clear winner (0.85 vs 0.70)
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.85},
            {'url': 'http://new.com/solutions', 'similarity': 0.70}
        ]

        await pairing_stage.execute((old_pages, new_pages, mappings))

        call_kwargs = mock_insert_mapping.call_args[1]
        self.assertEqual(call_kwargs['match_type'], 'semantic_medium')
        self.assertFalse(call_kwargs['needs_review'])  # Clear winner

        # Reset for next test
        mock_insert_mapping.reset_mock()

        # Test medium confidence with ambiguity (0.85 vs 0.82)
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.85},
            {'url': 'http://new.com/solutions', 'similarity': 0.82}  # Gap < 0.1
        ]

        await pairing_stage.execute((old_pages, new_pages, mappings))

        call_kwargs = mock_insert_mapping.call_args[1]
        self.assertEqual(call_kwargs['match_type'], 'semantic_medium')
        self.assertTrue(call_kwargs['needs_review'])  # Ambiguous

        # Reset for next test
        mock_insert_mapping.reset_mock()

        # Test low confidence (0.65)
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.65}
        ]

        await pairing_stage.execute((old_pages, new_pages, mappings))

        call_kwargs = mock_insert_mapping.call_args[1]
        self.assertEqual(call_kwargs['match_type'], 'semantic_low')
        self.assertTrue(call_kwargs['needs_review'])

        # Reset for next test
        mock_insert_mapping.reset_mock()

        # Test below threshold (0.50) - should not create mapping
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.50}
        ]

        await pairing_stage.execute((old_pages, new_pages, mappings))

        mock_insert_mapping.assert_not_called()  # No mapping created

    # ========================================================================
    # Test 3: Orphaned and New Page Identification
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_orphaned_and_new_page_identification(
        self,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_mapping
    ):
        """Test that orphaned and new pages are correctly identified."""
        pairing_stage = PairingStage(session_id=self.session_id)

        # Setup: 3 old pages, 3 new pages
        # old_page_1 -> new_page_1 (match)
        # old_page_orphaned -> no match (orphaned)
        # new_page_new -> no old equivalent (new)

        old_pages = [self.old_page_1, self.old_page_orphaned]
        new_pages = [self.new_page_1, self.new_page_new]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [
            {'url': 'http://old.com/products', 'embedding': [0.1] * 1536},
            {'url': 'http://old.com/legacy-feature', 'embedding': [0.9] * 1536}
        ]

        # Mock similarity: products match, legacy does not
        def mock_find_impl(query_embedding, session_id, site_type, match_count, match_threshold):
            if np.allclose(query_embedding, [0.1] * 1536):
                return [{'url': 'http://new.com/products', 'similarity': 0.90}]
            else:
                # Legacy page has no good match
                return [{'url': 'http://new.com/innovations', 'similarity': 0.45}]

        mock_find_similar.side_effect = mock_find_impl

        # Execute
        result = await pairing_stage.execute((old_pages, new_pages, mappings))

        # Verify only 1 mapping created (products)
        self.assertEqual(mock_insert_mapping.call_count, 1)
        self.assertEqual(len(result[2]), 1)

        # Verify correct mapping was created
        call_kwargs = mock_insert_mapping.call_args[1]
        self.assertEqual(call_kwargs['old_url'], 'http://old.com/products')
        self.assertEqual(call_kwargs['new_url'], 'http://new.com/products')

        # old_page_orphaned should be orphaned (no mapping)
        # new_page_new should be identified as new (no old equivalent)

    # ========================================================================
    # Test 4: HtmlPrune Mappings Integration
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_htmlprune_mappings_integration(
        self,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_mapping
    ):
        """Test that existing HtmlPruneStage mappings are handled correctly."""
        pairing_stage = PairingStage(session_id=self.session_id)

        # Create existing HTML mapping
        existing_mapping = Mapping(
            old_page=self.old_page_1,
            new_page=self.new_page_1,
            confidence_score=1.0,
            match_type='exact_html',
            needs_review=False
        )

        old_pages = [self.old_page_1, self.old_page_2]
        new_pages = [self.new_page_1, self.new_page_2]
        mappings = {existing_mapping}

        # Mock embeddings for unmatched page (old_page_2)
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/services',
            'embedding': [0.2] * 1536
        }]

        # Mock similarity for services
        mock_find_similar.return_value = [{
            'url': 'http://new.com/solutions',
            'similarity': 0.88
        }]

        # Execute
        result = await pairing_stage.execute((old_pages, new_pages, mappings))

        # Verify 2 mappings total: 1 existing + 1 new
        self.assertEqual(len(result[2]), 2)

        # Verify 2 inserts: existing mapping + new mapping
        self.assertEqual(mock_insert_mapping.call_count, 2)

        # First call should be for existing mapping
        first_call = mock_insert_mapping.call_args_list[0][1]
        self.assertEqual(first_call['match_type'], 'exact_html')
        self.assertEqual(first_call['confidence_score'], 1.0)

        # Second call should be for new semantic mapping
        second_call = mock_insert_mapping.call_args_list[1][1]
        self.assertEqual(second_call['old_url'], 'http://old.com/services')
        self.assertEqual(second_call['new_url'], 'http://new.com/solutions')

    # ========================================================================
    # Test 5: Full Mock Site Workflow
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_full_mock_site_workflow(
        self,
        mock_validate,
        mock_openai,
        mock_insert_embedding,
        mock_insert_mapping
    ):
        """Test with real mock site HTML files (limited subset for speed)."""
        # Setup OpenAI mock
        mock_client = AsyncMock()

        def mock_create_embedding(input, model, **kwargs):
            # Generate deterministic embeddings based on input text
            # Accept **kwargs to handle encoding_format and other params
            text = input if isinstance(input, str) else input[0]
            embedding = mock_embedding_generator(text)
            mock_response = MagicMock()
            mock_response.data = [MagicMock()]
            mock_response.data[0].embedding = embedding.tolist()
            return mock_response

        mock_client.embeddings.create = AsyncMock(side_effect=mock_create_embedding)
        mock_openai.return_value = mock_client

        # Load subset of mock site pages (5 each for speed)
        old_site_path = os.path.join(parent_dir, 'tests', 'mock_sites', 'old_site')
        new_site_path = os.path.join(parent_dir, 'tests', 'mock_sites', 'new_site')

        old_pages = load_mock_site_pages(old_site_path, limit=5)
        new_pages = load_mock_site_pages(new_site_path, limit=5)

        # Verify pages loaded
        self.assertGreater(len(old_pages), 0)
        self.assertGreater(len(new_pages), 0)

        # Execute EmbedStage
        embed_stage = EmbedStage(session_id=self.session_id)
        result_after_embed = await embed_stage.execute((old_pages, new_pages, set()))

        # Verify embeddings were created
        expected_embeddings = len(old_pages) + len(new_pages)
        self.assertEqual(mock_insert_embedding.call_count, expected_embeddings)

        # Setup mocks for PairingStage
        # Mock get_embeddings to return stored embeddings
        stored_embeddings = []
        for call in mock_insert_embedding.call_args_list:
            kwargs = call[1]
            stored_embeddings.append({
                'url': kwargs['url'],
                'embedding': kwargs['embedding'],
                'site_type': kwargs['site_type']
            })

        # Execute PairingStage with mock database
        with patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session') as mock_get_emb, \
             patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages') as mock_find_sim:

            # Return old embeddings when queried
            mock_get_emb.return_value = [e for e in stored_embeddings if e['site_type'] == 'old']

            # Mock find_similar to use actual cosine similarity
            def mock_find_similar_impl(query_embedding, session_id, site_type, match_count, match_threshold):
                results = []
                for emb in stored_embeddings:
                    if emb['site_type'] == site_type:
                        similarity = calculate_cosine_similarity(
                            np.array(query_embedding),
                            np.array(emb['embedding'])
                        )
                        results.append({
                            'url': emb['url'],
                            'similarity': float(similarity)
                        })
                # Sort by similarity and return top matches
                results.sort(key=lambda x: x['similarity'], reverse=True)
                return results[:match_count]

            mock_find_sim.side_effect = mock_find_similar_impl

            pairing_stage = PairingStage(session_id=self.session_id)
            result = await pairing_stage.execute(result_after_embed)

            # Verify mappings were created
            self.assertGreater(mock_insert_mapping.call_count, 0)

            # Verify result structure
            self.assertEqual(len(result), 3)
            self.assertIsInstance(result[2], set)

    # ========================================================================
    # Test 6: Real OpenAI Embeddings (Optional)
    # ========================================================================

    @unittest.skipUnless(Config.OPENAI_API_KEY, "OpenAI API key required")
    async def test_real_openai_embeddings(self):
        """Test with real OpenAI API (requires API key)."""
        embed_stage = EmbedStage(session_id=self.session_id)

        # Use small subset for real API test
        old_pages = [self.old_page_1, self.old_page_2]
        new_pages = [self.new_page_1]

        # Mock database to avoid actual storage
        with patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding') as mock_insert:
            result = await embed_stage.execute((old_pages, new_pages, set()))

            # Verify embeddings were generated
            self.assertEqual(mock_insert.call_count, 3)

            # Verify embedding format
            for call in mock_insert.call_args_list:
                embedding = call[1]['embedding']
                self.assertIsInstance(embedding, (list, np.ndarray))
                if isinstance(embedding, list):
                    self.assertEqual(len(embedding), 1536)
                else:
                    self.assertEqual(embedding.shape, (1536,))

    # ========================================================================
    # Test 7: Real Database Persistence (Optional)
    # ========================================================================

    @unittest.skipUnless(Config.SUPABASE_URL and Config.SUPABASE_KEY,
                         "Supabase credentials required")
    async def test_real_database_persistence(self):
        """Test with real Supabase database (requires credentials)."""
        # Create test session
        session_db = MigrationSessionDB()
        test_session_id = session_db.create_session(user_id='test_integration')

        try:
            # Use mocked embeddings to avoid OpenAI costs
            with patch('src.redirx.stages.AsyncOpenAI') as mock_openai, \
                 patch('src.redirx.stages.Config.validate_embeddings'):

                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.data = [MagicMock()]
                mock_response.data[0].embedding = mock_embedding_generator('test').tolist()
                mock_client.embeddings.create = AsyncMock(return_value=mock_response)
                mock_openai.return_value = mock_client

                # Execute stages
                embed_stage = EmbedStage(session_id=test_session_id)
                old_pages = [self.old_page_1]
                new_pages = [self.new_page_1]

                result = await embed_stage.execute((old_pages, new_pages, set()))

                # Verify embeddings in database
                embedding_db = WebPageEmbeddingDB()
                old_embeddings = embedding_db.get_embeddings_by_session(
                    session_id=test_session_id,
                    site_type='old'
                )

                self.assertEqual(len(old_embeddings), 1)
                self.assertEqual(old_embeddings[0]['url'], 'http://old.com/products')

        finally:
            # Cleanup test data
            # Note: Would need to add cleanup methods to database classes
            pass

    # ========================================================================
    # Test 8: Missing Embeddings Handling
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_missing_embeddings_handling(
        self,
        mock_get_embeddings,
        mock_insert_mapping
    ):
        """Test graceful handling when embeddings are missing."""
        pairing_stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1, self.old_page_2]
        new_pages = [self.new_page_1]

        # Mock: only first page has embedding
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/products',
            'embedding': [0.1] * 1536
        }]
        # old_page_2 has no embedding

        # Should not crash, just skip pages without embeddings
        result = await pairing_stage.execute((old_pages, new_pages, set()))

        # Should complete without error
        self.assertEqual(len(result), 3)

    # ========================================================================
    # Test 9: Empty Input
    # ========================================================================

    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_empty_input(self, mock_validate, mock_openai):
        """Test handling of empty page lists."""
        embed_stage = EmbedStage(session_id=self.session_id)
        pairing_stage = PairingStage(session_id=self.session_id)

        mock_client = AsyncMock()
        mock_openai.return_value = mock_client

        # Test with empty input
        result = await embed_stage.execute(([], [], set()))
        self.assertEqual(result, ([], [], set()))

        result = await pairing_stage.execute(([], [], set()))
        self.assertEqual(result, ([], [], set()))

    # ========================================================================
    # Test 10: Session ID Propagation
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_session_id_propagation(
        self,
        mock_validate,
        mock_openai,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_embedding,
        mock_insert_mapping
    ):
        """Test that session_id is correctly propagated through stages."""
        test_session_id = uuid4()

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_openai.return_value = mock_client

        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/products',
            'embedding': [0.1] * 1536
        }]

        mock_find_similar.return_value = [{
            'url': 'http://new.com/products',
            'similarity': 0.90
        }]

        # Execute with specific session ID
        embed_stage = EmbedStage(session_id=test_session_id)
        pairing_stage = PairingStage(session_id=test_session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1]

        result = await embed_stage.execute((old_pages, new_pages, set()))
        result = await pairing_stage.execute(result)

        # Verify all database operations used correct session_id
        for call in mock_insert_embedding.call_args_list:
            self.assertEqual(call[1]['session_id'], test_session_id)

        for call in mock_insert_mapping.call_args_list:
            self.assertEqual(call[1]['session_id'], test_session_id)


# ============================================================================
# Test Runner Helper
# ============================================================================

def async_test(coro):
    """Decorator to run async test methods."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro(*args, **kwargs))
    return wrapper


# Apply async_test decorator to all async test methods
for attr_name in dir(TestEmbedPairingIntegration):
    attr = getattr(TestEmbedPairingIntegration, attr_name)
    if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
        setattr(TestEmbedPairingIntegration, attr_name, async_test(attr))


if __name__ == '__main__':
    unittest.main()
