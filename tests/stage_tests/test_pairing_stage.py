import unittest
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch
import numpy as np
from uuid import uuid4

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import PairingStage, WebPage, Mapping
from src.redirx.config import Config


class TestPairingStage(unittest.TestCase):
    """Comprehensive tests for PairingStage."""

    def setUp(self):
        """Set up test fixtures."""
        # Create test pages
        self.old_page_1 = WebPage('http://old.com/page1', '<html><body><h1>Page 1</h1><p>Content about products</p></body></html>')
        self.old_page_2 = WebPage('http://old.com/page2', '<html><body><h1>Page 2</h1><p>Content about services</p></body></html>')
        self.old_page_3 = WebPage('http://old.com/page3', '<html><body><h1>Page 3</h1><p>Content about contact</p></body></html>')
        self.old_page_orphaned = WebPage('http://old.com/orphaned', '<html><body><h1>Old Feature</h1><p>Removed feature</p></body></html>')

        self.new_page_1 = WebPage('http://new.com/products', '<html><body><h1>Products</h1><p>Our product catalog</p></body></html>')
        self.new_page_2 = WebPage('http://new.com/services', '<html><body><h1>Services</h1><p>Our service offerings</p></body></html>')
        self.new_page_3 = WebPage('http://new.com/contact', '<html><body><h1>Contact Us</h1><p>Get in touch</p></body></html>')
        self.new_page_new = WebPage('http://new.com/blog', '<html><body><h1>Blog</h1><p>New blog section</p></body></html>')

        self.session_id = uuid4()

    def test_init_with_session_id(self):
        """Test initialization with provided session ID."""
        stage = PairingStage(session_id=self.session_id)

        self.assertEqual(stage.session_id, self.session_id)
        self.assertIsNotNone(stage.embedding_db)
        self.assertIsNotNone(stage.mapping_db)

    def test_init_without_session_id(self):
        """Test initialization without session ID."""
        stage = PairingStage()

        self.assertIsNone(stage.session_id)
        self.assertIsNotNone(stage.embedding_db)
        self.assertIsNotNone(stage.mapping_db)

    async def test_execute_requires_session_id(self):
        """Test that execute raises ValueError if session_id is not set."""
        stage = PairingStage()  # No session ID

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1]
        mappings = set()

        with self.assertRaises(ValueError) as context:
            await stage.execute((old_pages, new_pages, mappings))

        self.assertIn("session_id must be set", str(context.exception))

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_execute_processes_existing_mappings(self, mock_get_embeddings, mock_insert):
        """Test that existing HTML mappings are stored in database."""
        stage = PairingStage(session_id=self.session_id)

        # Create existing mapping from HtmlPruneStage
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

        # Mock embeddings to avoid processing unmatched pages
        mock_get_embeddings.return_value = []

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify existing mapping was stored
        mock_insert.assert_called_once_with(
            session_id=self.session_id,
            old_url='http://old.com/page1',
            new_url='http://new.com/products',
            confidence_score=1.0,
            match_type='exact_html',
            needs_review=False
        )

        # Verify result includes existing mapping
        self.assertEqual(len(result[2]), 1)
        self.assertIn(existing_mapping, result[2])

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_high_confidence_match(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test high confidence semantic match (score >= 0.9)."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/page1',
            'embedding': [0.1] * 1536
        }]

        # Mock high similarity match
        mock_find_similar.return_value = [{
            'url': 'http://new.com/products',
            'similarity': 0.95
        }]

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify mapping was created with correct attributes
        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        self.assertEqual(call_kwargs['confidence_score'], 0.95)
        self.assertEqual(call_kwargs['match_type'], 'semantic_high')
        self.assertFalse(call_kwargs['needs_review'])

        # Verify result contains the mapping
        self.assertEqual(len(result[2]), 1)

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_medium_confidence_match_no_ambiguity(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test medium confidence match without ambiguity (score 0.8-0.9, gap > 0.1)."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1, self.new_page_2]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/page1',
            'embedding': [0.1] * 1536
        }]

        # Mock medium similarity with clear winner (gap > 0.1)
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.85},
            {'url': 'http://new.com/services', 'similarity': 0.70}  # Gap of 0.15
        ]

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify mapping
        call_kwargs = mock_insert.call_args[1]
        self.assertEqual(call_kwargs['confidence_score'], 0.85)
        self.assertEqual(call_kwargs['match_type'], 'semantic_medium')
        self.assertFalse(call_kwargs['needs_review'])  # No ambiguity

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_medium_confidence_match_with_ambiguity(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test medium confidence match with ambiguity (top 2 scores within 0.1)."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1, self.new_page_2]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/page1',
            'embedding': [0.1] * 1536
        }]

        # Mock ambiguous similarity (gap < 0.1)
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.85},
            {'url': 'http://new.com/services', 'similarity': 0.82}  # Gap of 0.03
        ]

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify mapping needs review due to ambiguity
        call_kwargs = mock_insert.call_args[1]
        self.assertEqual(call_kwargs['confidence_score'], 0.85)
        self.assertEqual(call_kwargs['match_type'], 'semantic_medium')
        self.assertTrue(call_kwargs['needs_review'])  # Ambiguous

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_low_confidence_match(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test low confidence match (score 0.6-0.8, always needs review)."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/page1',
            'embedding': [0.1] * 1536
        }]

        # Mock low similarity
        mock_find_similar.return_value = [{
            'url': 'http://new.com/products',
            'similarity': 0.70
        }]

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify mapping always needs review
        call_kwargs = mock_insert.call_args[1]
        self.assertEqual(call_kwargs['confidence_score'], 0.70)
        self.assertEqual(call_kwargs['match_type'], 'semantic_low')
        self.assertTrue(call_kwargs['needs_review'])

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_orphaned_page_below_threshold(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test that pages below 0.6 threshold are orphaned (no match created)."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_orphaned]
        new_pages = [self.new_page_1]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/orphaned',
            'embedding': [0.1] * 1536
        }]

        # Mock very low similarity (below threshold)
        mock_find_similar.return_value = [{
            'url': 'http://new.com/products',
            'similarity': 0.45  # Below 0.6 threshold
        }]

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify no mapping was created
        mock_insert.assert_not_called()
        self.assertEqual(len(result[2]), 0)

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_orphaned_page_no_similar_pages(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test that pages with no similar results are orphaned."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_orphaned]
        new_pages = [self.new_page_1]
        mappings = set()

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/orphaned',
            'embedding': [0.1] * 1536
        }]

        # Mock no similar pages found
        mock_find_similar.return_value = []

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify no mapping was created
        mock_insert.assert_not_called()
        self.assertEqual(len(result[2]), 0)

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_avoids_rematching_already_matched_pages(self, mock_get_embeddings, mock_find_similar, mock_insert):
        """Test that already matched pages are excluded from new matches."""
        stage = PairingStage(session_id=self.session_id)

        # Create existing mapping
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

        # Mock embeddings
        mock_get_embeddings.return_value = [{
            'url': 'http://old.com/page2',
            'embedding': [0.2] * 1536
        }]

        # Mock that find_similar returns the already matched page first
        mock_find_similar.return_value = [
            {'url': 'http://new.com/products', 'similarity': 0.90},  # Already matched!
            {'url': 'http://new.com/services', 'similarity': 0.85}
        ]

        # Execute
        result = await stage.execute((old_pages, new_pages, mappings))

        # Verify that page2 matched to services (not products, which was already matched)
        # We should have 2 mappings total: 1 existing + 1 new
        self.assertEqual(len(result[2]), 2)

        # Verify insert was called once for the new mapping (not for existing)
        # The call should be for page2 -> services
        self.assertEqual(mock_insert.call_count, 2)  # Once for existing, once for new
        new_mapping_call = mock_insert.call_args_list[1][1]
        self.assertEqual(new_mapping_call['old_url'], 'http://old.com/page2')
        self.assertEqual(new_mapping_call['new_url'], 'http://new.com/services')

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    async def test_integration_with_multiple_pages(self, mock_get_embeddings, mock_insert):
        """Integration test with multiple pages, matches, and orphans."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1, self.old_page_2, self.old_page_orphaned]
        new_pages = [self.new_page_1, self.new_page_2, self.new_page_new]
        mappings = set()

        # Mock embeddings for all old pages
        mock_get_embeddings.return_value = [
            {'url': 'http://old.com/page1', 'embedding': [0.1] * 1536},
            {'url': 'http://old.com/page2', 'embedding': [0.2] * 1536},
            {'url': 'http://old.com/orphaned', 'embedding': [0.3] * 1536}
        ]

        # Mock find_similar to return different results for each page
        def mock_find_similar_side_effect(query_embedding, session_id, site_type, match_count, match_threshold):
            # Determine which old page this is based on embedding
            if np.allclose(query_embedding, [0.1] * 1536):
                # old_page_1 -> new_page_1 (high confidence)
                return [{'url': 'http://new.com/products', 'similarity': 0.92}]
            elif np.allclose(query_embedding, [0.2] * 1536):
                # old_page_2 -> new_page_2 (medium confidence)
                return [{'url': 'http://new.com/services', 'similarity': 0.83}]
            else:
                # old_page_orphaned -> no match
                return [{'url': 'http://new.com/blog', 'similarity': 0.40}]

        with patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages', side_effect=mock_find_similar_side_effect):
            # Execute
            result = await stage.execute((old_pages, new_pages, mappings))

        # Verify we have 2 mappings (page1 and page2, orphaned excluded)
        self.assertEqual(len(result[2]), 2)

        # Verify insert was called twice
        self.assertEqual(mock_insert.call_count, 2)

    def test_find_best_match(self):
        """Test _find_best_match helper method."""
        stage = PairingStage(session_id=self.session_id)

        # Test with valid matches
        similar_pages = [
            {'url': 'http://new.com/page1', 'similarity': 0.85},
            {'url': 'http://new.com/page2', 'similarity': 0.75},
            {'url': 'http://new.com/page3', 'similarity': 0.65}
        ]

        best = stage._find_best_match(similar_pages)
        self.assertEqual(best['url'], 'http://new.com/page1')
        self.assertEqual(best['similarity'], 0.85)

        # Test with all below threshold
        low_similar_pages = [
            {'url': 'http://new.com/page1', 'similarity': 0.55},
            {'url': 'http://new.com/page2', 'similarity': 0.45}
        ]

        best = stage._find_best_match(low_similar_pages)
        self.assertIsNone(best)

        # Test with empty list
        best = stage._find_best_match([])
        self.assertIsNone(best)

    def test_create_mapping_high_confidence(self):
        """Test _create_mapping for high confidence (>=0.9)."""
        stage = PairingStage(session_id=self.session_id)

        similar_pages = [{'url': 'http://new.com/products', 'similarity': 0.95}]

        mapping = stage._create_mapping(
            old_page=self.old_page_1,
            new_page=self.new_page_1,
            similarity_score=0.95,
            similar_pages=similar_pages
        )

        self.assertEqual(mapping.confidence_score, 0.95)
        self.assertEqual(mapping.match_type, 'semantic_high')
        self.assertFalse(mapping.needs_review)

    def test_create_mapping_medium_confidence_clear(self):
        """Test _create_mapping for medium confidence without ambiguity."""
        stage = PairingStage(session_id=self.session_id)

        similar_pages = [
            {'url': 'http://new.com/products', 'similarity': 0.85},
            {'url': 'http://new.com/other', 'similarity': 0.70}  # Gap > 0.1
        ]

        mapping = stage._create_mapping(
            old_page=self.old_page_1,
            new_page=self.new_page_1,
            similarity_score=0.85,
            similar_pages=similar_pages
        )

        self.assertEqual(mapping.match_type, 'semantic_medium')
        self.assertFalse(mapping.needs_review)  # Clear winner

    def test_create_mapping_medium_confidence_ambiguous(self):
        """Test _create_mapping for medium confidence with ambiguity."""
        stage = PairingStage(session_id=self.session_id)

        similar_pages = [
            {'url': 'http://new.com/products', 'similarity': 0.85},
            {'url': 'http://new.com/other', 'similarity': 0.82}  # Gap < 0.1
        ]

        mapping = stage._create_mapping(
            old_page=self.old_page_1,
            new_page=self.new_page_1,
            similarity_score=0.85,
            similar_pages=similar_pages
        )

        self.assertEqual(mapping.match_type, 'semantic_medium')
        self.assertTrue(mapping.needs_review)  # Ambiguous

    def test_create_mapping_low_confidence(self):
        """Test _create_mapping for low confidence (0.6-0.8)."""
        stage = PairingStage(session_id=self.session_id)

        similar_pages = [{'url': 'http://new.com/products', 'similarity': 0.70}]

        mapping = stage._create_mapping(
            old_page=self.old_page_1,
            new_page=self.new_page_1,
            similarity_score=0.70,
            similar_pages=similar_pages
        )

        self.assertEqual(mapping.match_type, 'semantic_low')
        self.assertTrue(mapping.needs_review)

    def test_is_ambiguous(self):
        """Test _is_ambiguous helper method."""
        stage = PairingStage(session_id=self.session_id)

        # Test ambiguous case (gap < 0.1)
        similar_pages = [
            {'url': 'http://new.com/page1', 'similarity': 0.85},
            {'url': 'http://new.com/page2', 'similarity': 0.82}
        ]
        self.assertTrue(stage._is_ambiguous(0.85, similar_pages))

        # Test clear case (gap >= 0.1)
        similar_pages = [
            {'url': 'http://new.com/page1', 'similarity': 0.85},
            {'url': 'http://new.com/page2', 'similarity': 0.70}
        ]
        self.assertFalse(stage._is_ambiguous(0.85, similar_pages))

        # Test with only one page
        similar_pages = [{'url': 'http://new.com/page1', 'similarity': 0.85}]
        self.assertFalse(stage._is_ambiguous(0.85, similar_pages))

        # Test with empty list
        self.assertFalse(stage._is_ambiguous(0.85, []))

    async def test_execute_returns_input_unchanged(self):
        """Test that execute returns the same tuple structure (pass-through)."""
        stage = PairingStage(session_id=self.session_id)

        old_pages = [self.old_page_1]
        new_pages = [self.new_page_1]
        mappings = set()

        # Mock to avoid actual database/embedding operations
        with patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session', return_value=[]):
            result = await stage.execute((old_pages, new_pages, mappings))

        # Verify structure is preserved
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        self.assertIs(result[0], old_pages)
        self.assertIs(result[1], new_pages)
        self.assertIsInstance(result[2], set)


# Helper to run async tests
def async_test(coro):
    """Decorator to run async test methods."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro(*args, **kwargs))
    return wrapper


# Apply async_test decorator to all async test methods
for attr_name in dir(TestPairingStage):
    attr = getattr(TestPairingStage, attr_name)
    if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
        setattr(TestPairingStage, attr_name, async_test(attr))


if __name__ == '__main__':
    unittest.main()
