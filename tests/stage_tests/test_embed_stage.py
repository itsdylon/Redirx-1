import unittest
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np
from uuid import uuid4

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import EmbedStage, WebPage, Mapping
from src.redirx.config import Config
from src.redirx.database import WebPageEmbeddingDB, MigrationSessionDB


class TestEmbedStage(unittest.TestCase):
    """Integration tests for EmbedStage."""

    def setUp(self):
        """Set up test fixtures."""
        self.old_pages = [
            WebPage('http://old.com/page1', '<html><body><h1>Page 1</h1><p>Content 1</p></body></html>'),
            WebPage('http://old.com/page2', '<html><body><h1>Page 2</h1><p>Content 2</p></body></html>'),
        ]
        self.new_pages = [
            WebPage('http://new.com/page1', '<html><body><h1>New Page 1</h1><p>New Content 1</p></body></html>'),
            WebPage('http://new.com/page2', '<html><body><h1>New Page 2</h1><p>New Content 2</p></body></html>'),
        ]
        self.mappings = set()

    def test_init_with_session_id(self):
        """Test initialization with provided session ID."""
        session_id = uuid4()
        stage = EmbedStage(session_id=session_id)

        self.assertEqual(stage.session_id, session_id)
        self.assertIsNotNone(stage.embedding_db)
        self.assertIsNotNone(stage.session_db)
        self.assertIsNone(stage.openai_client)

    def test_init_without_session_id(self):
        """Test initialization without session ID."""
        stage = EmbedStage()

        self.assertIsNone(stage.session_id)
        self.assertIsNotNone(stage.embedding_db)
        self.assertIsNotNone(stage.session_db)

    @patch('src.redirx.stages.Config.validate_embeddings')
    @patch('src.redirx.stages.AsyncOpenAI')
    async def test_execute_validates_config(self, mock_openai, mock_validate):
        """Test that execute validates OpenAI configuration."""
        # Setup
        mock_validate.side_effect = ValueError("Missing OPENAI_API_KEY")
        stage = EmbedStage(session_id=uuid4())

        # Execute and verify
        with self.assertRaises(ValueError) as context:
            await stage.execute((self.old_pages, self.new_pages, self.mappings))

        self.assertIn("OPENAI_API_KEY", str(context.exception))
        mock_validate.assert_called_once()

    @patch('src.redirx.stages.MigrationSessionDB.create_session')
    @patch('src.redirx.stages.Config.validate_embeddings')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch.object(EmbedStage, '_process_pages')
    async def test_execute_creates_session_if_needed(self, mock_process, mock_openai, mock_validate, mock_create_session):
        """Test that execute creates session if not provided."""
        # Setup
        new_session_id = uuid4()
        mock_create_session.return_value = new_session_id
        mock_process.return_value = None

        mock_client = AsyncMock()
        mock_openai.return_value = mock_client

        stage = EmbedStage()  # No session ID provided

        # Execute
        result = await stage.execute((self.old_pages, self.new_pages, self.mappings))

        # Verify session was created
        mock_create_session.assert_called_once_with(user_id='default')
        self.assertEqual(stage.session_id, new_session_id)

        # Verify result is unchanged
        self.assertEqual(result, (self.old_pages, self.new_pages, self.mappings))

    @patch('src.redirx.stages.Config.validate_embeddings')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch.object(EmbedStage, '_process_pages')
    async def test_execute_processes_both_sites(self, mock_process, mock_openai, mock_validate):
        """Test that execute processes both old and new pages."""
        # Setup
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_process.return_value = None

        stage = EmbedStage(session_id=uuid4())

        # Execute
        await stage.execute((self.old_pages, self.new_pages, self.mappings))

        # Verify both old and new pages were processed
        self.assertEqual(mock_process.call_count, 2)
        calls = mock_process.call_args_list
        self.assertEqual(calls[0][0], (self.old_pages, 'old'))
        self.assertEqual(calls[1][0], (self.new_pages, 'new'))

    @patch('src.redirx.stages.Config.validate_embeddings')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch.object(EmbedStage, '_process_pages')
    async def test_execute_returns_input_unchanged(self, mock_process, mock_openai, mock_validate):
        """Test that execute returns input unchanged."""
        # Setup
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_process.return_value = None

        stage = EmbedStage(session_id=uuid4())
        input_tuple = (self.old_pages, self.new_pages, self.mappings)

        # Execute
        result = await stage.execute(input_tuple)

        # Verify
        self.assertEqual(result, input_tuple)
        self.assertIs(result[0], self.old_pages)
        self.assertIs(result[1], self.new_pages)
        self.assertIs(result[2], self.mappings)

    @patch('src.redirx.stages.Config.validate_embeddings')
    @patch('src.redirx.stages.AsyncOpenAI')
    @patch.object(EmbedStage, '_generate_and_store_embedding')
    async def test_process_batch_concurrent_execution(self, mock_generate, mock_openai, mock_validate):
        """Test that batches are processed concurrently."""
        # Setup
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_generate.return_value = None

        stage = EmbedStage(session_id=uuid4())
        stage.openai_client = mock_client

        # Execute batch processing
        await stage._process_batch(self.old_pages, 'old')

        # Verify all pages were processed
        self.assertEqual(mock_generate.call_count, len(self.old_pages))

    @patch('src.redirx.stages.Config.EMBEDDING_MODEL', 'text-embedding-3-small')
    async def test_generate_embedding_with_retry_mock(self):
        """Test embedding generation with mocked API."""
        # Setup
        stage = EmbedStage(session_id=uuid4())

        # Mock OpenAI client
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        stage.openai_client = mock_client

        # Execute
        embedding = await stage._generate_embedding_with_retry("Test text")

        # Verify
        self.assertIsInstance(embedding, np.ndarray)
        self.assertEqual(embedding.shape, (1536,))
        self.assertEqual(embedding.dtype, np.float32)
        mock_client.embeddings.create.assert_called_once()

    @patch('src.redirx.stages.Config.EMBEDDING_MODEL', 'text-embedding-3-small')
    async def test_generate_embedding_retry_logic(self):
        """Test retry logic on API failures."""
        # Setup
        stage = EmbedStage(session_id=uuid4())

        # Mock OpenAI client to fail twice, then succeed
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(
            side_effect=[
                Exception("API Error 1"),
                Exception("API Error 2"),
                mock_response
            ]
        )
        stage.openai_client = mock_client

        # Execute
        embedding = await stage._generate_embedding_with_retry("Test text", max_retries=3)

        # Verify it eventually succeeded
        self.assertIsInstance(embedding, np.ndarray)
        self.assertEqual(mock_client.embeddings.create.call_count, 3)

    @patch('src.redirx.stages.Config.EMBEDDING_MODEL', 'text-embedding-3-small')
    async def test_generate_embedding_exhausted_retries(self):
        """Test that exhausted retries raise exception."""
        # Setup
        stage = EmbedStage(session_id=uuid4())

        # Mock OpenAI client to always fail
        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(side_effect=Exception("API Error"))
        stage.openai_client = mock_client

        # Execute and verify
        with self.assertRaises(Exception) as context:
            await stage._generate_embedding_with_retry("Test text", max_retries=3)

        self.assertIn("API Error", str(context.exception))
        self.assertEqual(mock_client.embeddings.create.call_count, 3)

    def test_text_extraction_integration(self):
        """Test that WebPage text extraction works with EmbedStage."""
        page = self.old_pages[0]

        # Extract text
        text = page.extract_text()
        title = page.extract_title()

        # Verify extraction worked
        self.assertIn('Page 1', text)
        self.assertIn('Content 1', text)
        self.assertEqual(title, 'Page 1')

        # Verify caching
        self.assertIsNotNone(page._extracted_text)
        self.assertIsNotNone(page._title)


# Helper to run async tests
def async_test(coro):
    """Decorator to run async test methods."""
    def wrapper(*args, **kwargs):
        return asyncio.run(coro(*args, **kwargs))
    return wrapper


# Apply async_test decorator to all async test methods
for attr_name in dir(TestEmbedStage):
    attr = getattr(TestEmbedStage, attr_name)
    if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
        setattr(TestEmbedStage, attr_name, async_test(attr))


if __name__ == '__main__':
    unittest.main()
