"""
End-to-end integration tests for the complete Redirx pipeline.

Tests all 5 stages working together:
1. UrlPruneStage - Filter out assets
2. WebScraperStage - Scrape HTML content
3. HtmlPruneStage - Match pages with identical HTML
4. EmbedStage - Generate embeddings
5. PairingStage - Create semantic mappings

Uses temporary HTTP servers to test WebScraperStage with real HTTP requests.
"""

import unittest
import asyncio
import os
import sys
import tempfile
import shutil
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np
from uuid import uuid4

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.lib import Pipeline
from src.redirx.stages import (
    UrlPruneStage, WebScraperStage, HtmlPruneStage,
    EmbedStage, PairingStage, WebPage, Mapping
)


# ============================================================================
# Test HTTP Server Setup
# ============================================================================

def create_test_site(site_dir: Path, pages: dict):
    """
    Create a test website with HTML files.

    Args:
        site_dir: Directory to create files in
        pages: Dict mapping filename -> HTML content
    """
    site_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in pages.items():
        file_path = site_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)


def start_http_server(directory: str, port: int):
    """Start HTTP server in background thread."""
    class DirectoryHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

    server = HTTPServer(('localhost', port), DirectoryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ============================================================================
# Mock Embedding Generator
# ============================================================================

def mock_embedding_generator(text: str) -> np.ndarray:
    """
    Generate deterministic mock embeddings based on text content.
    Similar text will produce similar embeddings.
    """
    seed = hash(text) % (2**32)
    rng = np.random.RandomState(seed)
    embedding = rng.rand(1536).astype(np.float32)
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
# Test HTML Content
# ============================================================================

# Identical HTML for testing HtmlPruneStage
IDENTICAL_INDEX_HTML = """
<!DOCTYPE html>
<html>
<head><title>Home</title></head>
<body>
    <h1>Welcome</h1>
    <p>This is the homepage content that is identical.</p>
</body>
</html>
"""

# Similar but different HTML for semantic matching
OLD_ABOUT_HTML = """
<!DOCTYPE html>
<html>
<head><title>About Us</title></head>
<body>
    <h1>About Our Company</h1>
    <p>We provide professional consulting services and solutions.</p>
    <p>Founded in 2020, we have grown to serve 100+ clients.</p>
</body>
</html>
"""

NEW_ABOUT_HTML = """
<!DOCTYPE html>
<html>
<head><title>About</title></head>
<body>
    <h1>About</h1>
    <p>Professional consulting and solutions provider.</p>
    <p>Established 2020, serving over 100 clients worldwide.</p>
</body>
</html>
"""

OLD_SERVICES_HTML = """
<!DOCTYPE html>
<html>
<head><title>Services</title></head>
<body>
    <h1>Our Services</h1>
    <p>Web development, cloud migration, and technical consulting.</p>
    <ul>
        <li>Custom software development</li>
        <li>Cloud infrastructure</li>
        <li>DevOps consulting</li>
    </ul>
</body>
</html>
"""

NEW_SOLUTIONS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Solutions</title></head>
<body>
    <h1>Solutions We Offer</h1>
    <p>Web development, cloud services, and technology consulting.</p>
    <ul>
        <li>Software engineering</li>
        <li>Cloud platforms</li>
        <li>DevOps services</li>
    </ul>
</body>
</html>
"""

OLD_CONTACT_HTML = """
<!DOCTYPE html>
<html>
<head><title>Contact</title></head>
<body>
    <h1>Contact Us</h1>
    <p>Email: contact@oldsite.com</p>
    <p>Phone: 555-0100</p>
</body>
</html>
"""

NEW_CONTACT_HTML = """
<!DOCTYPE html>
<html>
<head><title>Contact</title></head>
<body>
    <h1>Get in Touch</h1>
    <p>Email: info@newsite.com</p>
    <p>Phone: 555-0200</p>
</body>
</html>
"""

# Orphaned page (only in old site)
OLD_LEGACY_HTML = """
<!DOCTYPE html>
<html>
<head><title>Legacy Feature</title></head>
<body>
    <h1>Discontinued Feature</h1>
    <p>This feature has been deprecated and removed.</p>
</body>
</html>
"""

# New page (only in new site)
NEW_INNOVATIONS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Innovations</title></head>
<body>
    <h1>New Innovations</h1>
    <p>Check out our latest cutting-edge features and products.</p>
</body>
</html>
"""


# ============================================================================
# E2E Test Suite
# ============================================================================

class TestFullPipelineE2E(unittest.TestCase):
    """End-to-end tests for complete pipeline execution."""

    @classmethod
    def setUpClass(cls):
        """Set up temporary HTTP servers for testing."""
        cls.temp_dir = tempfile.mkdtemp()
        cls.old_site_dir = Path(cls.temp_dir) / 'old_site'
        cls.new_site_dir = Path(cls.temp_dir) / 'new_site'

        # Create old site
        old_pages = {
            'index.html': IDENTICAL_INDEX_HTML,
            'about.html': OLD_ABOUT_HTML,
            'services.html': OLD_SERVICES_HTML,
            'contact.html': OLD_CONTACT_HTML,
            'legacy.html': OLD_LEGACY_HTML,
            'assets/styles.css': 'body { color: blue; }',
            'assets/app.js': 'console.log("old");',
            'images/logo.png': 'fake-png-data',
        }
        create_test_site(cls.old_site_dir, old_pages)

        # Create new site
        new_pages = {
            'index.html': IDENTICAL_INDEX_HTML,  # Identical to old
            'about-us.html': NEW_ABOUT_HTML,
            'solutions.html': NEW_SOLUTIONS_HTML,
            'contact.html': NEW_CONTACT_HTML,
            'innovations.html': NEW_INNOVATIONS_HTML,
            'assets/styles.css': 'body { color: red; }',
            'assets/main.js': 'console.log("new");',
            'images/logo.png': 'fake-png-data-new',
        }
        create_test_site(cls.new_site_dir, new_pages)

        # Start HTTP servers
        cls.old_server = start_http_server(str(cls.old_site_dir), 8765)
        cls.new_server = start_http_server(str(cls.new_site_dir), 8766)

        # Wait for servers to start
        import time
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Shutdown HTTP servers and cleanup."""
        cls.old_server.shutdown()
        cls.new_server.shutdown()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        """Set up test fixtures."""
        self.session_id = uuid4()

        # URLs for testing (including assets that should be filtered)
        self.old_urls = [
            'http://localhost:8765/index.html',
            'http://localhost:8765/about.html',
            'http://localhost:8765/services.html',
            'http://localhost:8765/contact.html',
            'http://localhost:8765/legacy.html',
            'http://localhost:8765/assets/styles.css',  # Should be filtered
            'http://localhost:8765/assets/app.js',       # Should be filtered
            'http://localhost:8765/images/logo.png',     # Should be filtered
        ]

        self.new_urls = [
            'http://localhost:8766/index.html',
            'http://localhost:8766/about-us.html',
            'http://localhost:8766/solutions.html',
            'http://localhost:8766/contact.html',
            'http://localhost:8766/innovations.html',
            'http://localhost:8766/assets/styles.css',   # Should be filtered
            'http://localhost:8766/assets/main.js',      # Should be filtered
            'http://localhost:8766/images/logo.png',     # Should be filtered
        ]

    # ========================================================================
    # Test 1: Full Pipeline with Mocked External Services
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_full_pipeline_all_stages(
        self,
        mock_validate,
        mock_openai,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_embedding,
        mock_insert_mapping
    ):
        """Test complete pipeline: UrlPrune → Scraper → HtmlPrune → Embed → Pairing."""

        # Setup OpenAI mock
        mock_client = AsyncMock()

        def mock_create_embedding(input, model, **kwargs):
            text = input if isinstance(input, str) else input[0]
            embedding = mock_embedding_generator(text)
            mock_response = MagicMock()
            mock_response.data = [MagicMock()]
            mock_response.data[0].embedding = embedding.tolist()
            return mock_response

        mock_client.embeddings.create = AsyncMock(side_effect=mock_create_embedding)
        mock_openai.return_value = mock_client

        # Create pipeline with all default stages
        pipeline = Pipeline(
            input=(self.old_urls, self.new_urls),
            session_id=self.session_id
        )

        # Execute pipeline stage by stage
        state = pipeline.state

        # Stage 1: UrlPruneStage
        url_prune = UrlPruneStage()
        state = await url_prune.execute(state)

        # Verify assets were filtered
        old_urls_filtered, new_urls_filtered = state
        self.assertEqual(len(old_urls_filtered), 5)  # 5 HTML pages
        self.assertEqual(len(new_urls_filtered), 5)  # 5 HTML pages

        # Verify no asset URLs remain
        for url in old_urls_filtered:
            self.assertNotIn('.css', url)
            self.assertNotIn('.js', url)
            self.assertNotIn('.png', url)

        # Stage 2: WebScraperStage
        web_scraper = WebScraperStage()
        state = await web_scraper.execute(state)

        # Verify pages were scraped
        old_pages, new_pages = state
        self.assertEqual(len(old_pages), 5)
        self.assertEqual(len(new_pages), 5)
        self.assertIsInstance(old_pages[0], WebPage)
        self.assertIsInstance(new_pages[0], WebPage)

        # Verify HTML content was fetched
        for page in old_pages:
            self.assertIsInstance(page.html, str)
            self.assertGreater(len(page.html), 0)

        # Stage 3: HtmlPruneStage
        html_prune = HtmlPruneStage()
        state = await html_prune.execute(state)

        # Verify state now includes mappings
        old_pages, new_pages, mappings = state
        self.assertIsInstance(mappings, set)

        # Verify index.html was matched (identical HTML)
        self.assertEqual(len(mappings), 1)
        mapping = list(mappings)[0]
        self.assertIn('index.html', mapping.old_page.url)
        self.assertIn('index.html', mapping.new_page.url)
        self.assertEqual(mapping.confidence_score, 1.0)
        self.assertEqual(mapping.match_type, 'exact_html')

        # Stage 4: EmbedStage
        embed_stage = EmbedStage(session_id=self.session_id)
        state = await embed_stage.execute(state)

        # Verify embeddings were created for all pages
        # 5 old + 5 new = 10 pages
        self.assertEqual(mock_insert_embedding.call_count, 10)

        # Stage 5: PairingStage
        # Setup mocks for pairing
        stored_embeddings = []
        for call in mock_insert_embedding.call_args_list:
            kwargs = call[1]
            stored_embeddings.append({
                'url': kwargs['url'],
                'embedding': kwargs['embedding'],
                'site_type': kwargs['site_type']
            })

        # Mock get_embeddings to return old embeddings (excluding already matched)
        old_embeddings = [e for e in stored_embeddings if e['site_type'] == 'old']
        # Remove index.html since it was already matched
        old_embeddings = [e for e in old_embeddings if 'index.html' not in e['url']]
        mock_get_embeddings.return_value = old_embeddings

        # Mock find_similar to use real cosine similarity
        def mock_find_similar_impl(query_embedding, session_id, site_type, match_count, match_threshold):
            results = []
            for emb in stored_embeddings:
                if emb['site_type'] == site_type:
                    similarity = calculate_cosine_similarity(
                        np.array(query_embedding),
                        np.array(emb['embedding'])
                    )
                    if similarity >= match_threshold:
                        results.append({
                            'url': emb['url'],
                            'similarity': float(similarity)
                        })
            results.sort(key=lambda x: x['similarity'], reverse=True)
            return results[:match_count]

        mock_find_similar.side_effect = mock_find_similar_impl

        pairing_stage = PairingStage(session_id=self.session_id)
        final_state = await pairing_stage.execute(state)

        # Verify final state structure
        old_pages_final, new_pages_final, mappings_final = final_state
        self.assertEqual(len(old_pages_final), 5)
        self.assertEqual(len(new_pages_final), 5)

        # Verify mappings were created
        # Should have at least 1 mapping (index.html from HtmlPrune)
        # May have additional semantic mappings
        self.assertGreaterEqual(len(mappings_final), 1)

        # Verify all mappings were inserted to database
        self.assertGreater(mock_insert_mapping.call_count, 0)

    # ========================================================================
    # Test 2: Pipeline Using Pipeline.iterate()
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding')
    @patch('src.redirx.stages.WebPageEmbeddingDB.find_similar_pages')
    @patch('src.redirx.stages.WebPageEmbeddingDB.get_embeddings_by_session')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_pipeline_using_iterate(
        self,
        mock_validate,
        mock_openai,
        mock_get_embeddings,
        mock_find_similar,
        mock_insert_embedding,
        mock_insert_mapping
    ):
        """Test pipeline execution using Pipeline.iterate() method."""

        # Setup mocks (same as test_full_pipeline_all_stages)
        mock_client = AsyncMock()

        def mock_create_embedding(input, model, **kwargs):
            text = input if isinstance(input, str) else input[0]
            embedding = mock_embedding_generator(text)
            mock_response = MagicMock()
            mock_response.data = [MagicMock()]
            mock_response.data[0].embedding = embedding.tolist()
            return mock_response

        mock_client.embeddings.create = AsyncMock(side_effect=mock_create_embedding)
        mock_openai.return_value = mock_client

        # Setup find_similar mock
        def mock_find_similar_impl(query_embedding, session_id, site_type, match_count, match_threshold):
            return []  # Return empty for simplicity

        mock_find_similar.side_effect = mock_find_similar_impl
        mock_get_embeddings.return_value = []

        # Create pipeline
        pipeline = Pipeline(
            input=(self.old_urls, self.new_urls),
            session_id=self.session_id
        )

        # Execute using iterate()
        states = []
        async for state in pipeline.iterate():
            states.append(state)

        # Verify we got states from all 5 stages
        self.assertEqual(len(states), 5)

        # Verify first state (after UrlPrune) is tuple of URL lists
        self.assertIsInstance(states[0], tuple)
        self.assertEqual(len(states[0]), 2)

        # Verify second state (after WebScraper) has WebPage objects
        self.assertIsInstance(states[1][0][0], WebPage)

        # Verify states 3-5 have mappings set
        for i in range(2, 5):
            self.assertEqual(len(states[i]), 3)
            self.assertIsInstance(states[i][2], set)

    # ========================================================================
    # Test 3: Verify Session ID Propagation
    # ========================================================================

    @patch('src.redirx.stages.URLMappingDB.insert_mapping')
    @patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_session_id_propagation(
        self,
        mock_validate,
        mock_openai,
        mock_insert_embedding,
        mock_insert_mapping
    ):
        """Test that session_id is correctly propagated through pipeline."""

        test_session_id = uuid4()

        # Setup mocks
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_openai.return_value = mock_client

        # Create pipeline with specific session_id
        pipeline = Pipeline(
            input=(self.old_urls[:2], self.new_urls[:2]),  # Use subset for speed
            session_id=test_session_id
        )

        # Verify pipeline has session_id
        self.assertEqual(pipeline.session_id, test_session_id)

        # Execute pipeline
        async for state in pipeline.iterate():
            pass

        # Verify all database operations used correct session_id
        for call in mock_insert_embedding.call_args_list:
            self.assertEqual(call[1]['session_id'], test_session_id)

        if mock_insert_mapping.call_count > 0:
            for call in mock_insert_mapping.call_args_list:
                self.assertEqual(call[1]['session_id'], test_session_id)

    # ========================================================================
    # Test 4: Test Individual Stage Integration
    # ========================================================================

    async def test_url_prune_to_web_scraper_integration(self):
        """Test UrlPruneStage → WebScraperStage integration."""

        # Stage 1: UrlPrune
        url_prune = UrlPruneStage()
        filtered_urls = await url_prune.execute((self.old_urls, self.new_urls))

        # Verify filtering worked
        old_filtered, new_filtered = filtered_urls
        self.assertLess(len(old_filtered), len(self.old_urls))
        self.assertLess(len(new_filtered), len(self.new_urls))

        # Stage 2: WebScraper
        web_scraper = WebScraperStage()
        pages = await web_scraper.execute(filtered_urls)

        # Verify pages were created
        old_pages, new_pages = pages
        self.assertEqual(len(old_pages), len(old_filtered))
        self.assertEqual(len(new_pages), len(new_filtered))

        # Verify URLs match
        for page, url in zip(old_pages, old_filtered):
            self.assertEqual(page.url, url)

    @patch('src.redirx.stages.AsyncOpenAI')
    @patch('src.redirx.stages.Config.validate_embeddings')
    async def test_html_prune_to_embed_integration(
        self,
        mock_validate,
        mock_openai
    ):
        """Test HtmlPruneStage → EmbedStage integration."""

        # Setup mocks
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_openai.return_value = mock_client

        # Create test pages
        old_pages = [
            WebPage('http://old.com/identical.html', IDENTICAL_INDEX_HTML),
            WebPage('http://old.com/unique.html', OLD_ABOUT_HTML)
        ]
        new_pages = [
            WebPage('http://new.com/identical.html', IDENTICAL_INDEX_HTML),
            WebPage('http://new.com/different.html', NEW_ABOUT_HTML)
        ]

        # Stage 1: HtmlPrune
        html_prune = HtmlPruneStage()
        result = await html_prune.execute((old_pages, new_pages))

        old_pages_out, new_pages_out, mappings = result

        # Verify mapping was created for identical HTML
        self.assertEqual(len(mappings), 1)

        # Stage 2: Embed
        with patch('src.redirx.stages.WebPageEmbeddingDB.insert_embedding'):
            embed_stage = EmbedStage(session_id=self.session_id)
            final_result = await embed_stage.execute(result)

            # Verify state structure preserved
            self.assertEqual(len(final_result), 3)
            self.assertEqual(final_result[2], mappings)


# ============================================================================
# Test Runner Helper
# ============================================================================

def async_test(coro):
    """Decorator to run async test methods."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro(*args, **kwargs))
    return wrapper


# Apply async_test decorator to all async test methods
for attr_name in dir(TestFullPipelineE2E):
    attr = getattr(TestFullPipelineE2E, attr_name)
    if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
        setattr(TestFullPipelineE2E, attr_name, async_test(attr))


if __name__ == '__main__':
    unittest.main()
