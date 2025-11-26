import unittest
import os
import sys

# Add parent directory to path for imports
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, parent_dir)

from src.redirx.config import Config
from src.redirx.database import (
    SupabaseClient,
    MigrationSessionDB,
    WebPageEmbeddingDB,
    URLMappingDB
)
import numpy as np


class TestDatabaseConnection(unittest.TestCase):
    """
    Test suite for verifying Supabase database connection and basic operations.
    Run this to verify your .env file is configured correctly.
    """

    @classmethod
    def setUpClass(cls):
        """
        Verify configuration before running tests.
        """
        try:
            Config.validate()
            print("\n✓ Configuration validated successfully")
            print(f"  Supabase URL: {Config.SUPABASE_URL}")
            print(f"  OpenAI API Key: {'Set' if Config.OPENAI_API_KEY else 'Not set (optional)'}")
        except ValueError as e:
            raise unittest.SkipTest(f"Configuration error: {e}")

    def setUp(self):
        """
        Set up test fixtures for each test.
        """
        self.session_db = MigrationSessionDB()
        self.embedding_db = WebPageEmbeddingDB()
        self.mapping_db = URLMappingDB()
        self.test_session_id = None

    def tearDown(self):
        """
        Clean up test data after each test.
        """
        # Clean up test session and related data if created
        if self.test_session_id:
            try:
                client = SupabaseClient.get_client()

                # Delete embeddings
                client.table('webpage_embeddings').delete().eq(
                    'session_id', str(self.test_session_id)
                ).execute()

                # Delete mappings
                client.table('url_mappings').delete().eq(
                    'session_id', str(self.test_session_id)
                ).execute()

                # Delete session
                client.table('migration_sessions').delete().eq(
                    'id', str(self.test_session_id)
                ).execute()

            except Exception as e:
                print(f"\nWarning: Cleanup failed: {e}")

    def test_01_client_connection(self):
        """
        Test that we can establish a connection to Supabase.
        """
        try:
            client = SupabaseClient.get_client()
            self.assertIsNotNone(client)
            print("\n✓ Successfully connected to Supabase")
        except Exception as e:
            self.fail(f"Failed to connect to Supabase: {e}")

    def test_02_create_session(self):
        """
        Test creating a migration session.
        """
        try:
            session_id = self.session_db.create_session(user_id='test_user')
            self.test_session_id = session_id
            self.assertIsNotNone(session_id)
            print(f"\n✓ Created migration session: {session_id}")
        except Exception as e:
            self.fail(f"Failed to create session: {e}")

    def test_03_session_crud_operations(self):
        """
        Test full CRUD cycle for migration sessions.
        """
        # Create
        session_id = self.session_db.create_session(user_id='test_user')
        self.test_session_id = session_id
        print(f"\n✓ Created session: {session_id}")

        # Read
        session = self.session_db.get_session(session_id)
        self.assertEqual(session['user_id'], 'test_user')
        self.assertEqual(session['status'], 'pending')
        print(f"✓ Retrieved session: {session['user_id']}")

        # Update
        self.session_db.update_session_status(session_id, 'processing')
        updated_session = self.session_db.get_session(session_id)
        self.assertEqual(updated_session['status'], 'processing')
        print(f"✓ Updated session status to: {updated_session['status']}")

    def test_04_insert_embedding(self):
        """
        Test inserting webpage embeddings.
        """
        # Create session first
        session_id = self.session_db.create_session(user_id='test_user')
        self.test_session_id = session_id

        # Create a fake embedding (1536 dimensions)
        fake_embedding = np.random.randn(1536)

        # Insert embedding
        embedding_id = self.embedding_db.insert_embedding(
            session_id=session_id,
            url='https://old-site.com/test',
            site_type='old',
            embedding=fake_embedding,
            extracted_text='Test page content',
            title='Test Page'
        )

        self.assertIsNotNone(embedding_id)
        print(f"\n✓ Inserted embedding: {embedding_id}")

        # Verify it was stored
        embeddings = self.embedding_db.get_embeddings_by_session(session_id, 'old')
        self.assertEqual(len(embeddings), 1)
        self.assertEqual(embeddings[0]['url'], 'https://old-site.com/test')
        print(f"✓ Retrieved {len(embeddings)} embedding(s)")

    def test_05_vector_similarity_search(self):
        """
        Test vector similarity search functionality.
        """
        # Create session
        session_id = self.session_db.create_session(user_id='test_user')
        self.test_session_id = session_id

        # Insert multiple embeddings
        np.random.seed(42)  # For reproducibility

        # Insert "old" page
        old_embedding = np.random.randn(1536)
        self.embedding_db.insert_embedding(
            session_id=session_id,
            url='https://old-site.com/about',
            site_type='old',
            embedding=old_embedding,
            extracted_text='About our company',
            title='About Us'
        )

        # Insert similar "new" page (add small noise to old embedding)
        similar_embedding = old_embedding + np.random.randn(1536) * 0.1
        self.embedding_db.insert_embedding(
            session_id=session_id,
            url='https://new-site.com/about-us',
            site_type='new',
            embedding=similar_embedding,
            extracted_text='About our company',
            title='About Us - New Site'
        )

        # Insert dissimilar "new" page
        dissimilar_embedding = np.random.randn(1536)
        self.embedding_db.insert_embedding(
            session_id=session_id,
            url='https://new-site.com/products',
            site_type='new',
            embedding=dissimilar_embedding,
            extracted_text='Our products',
            title='Products'
        )

        # Search for similar pages
        results = self.embedding_db.find_similar_pages(
            query_embedding=old_embedding,
            session_id=session_id,
            site_type='new',
            match_count=2
        )

        self.assertGreater(len(results), 0)
        print(f"\n✓ Found {len(results)} similar page(s)")

        # The most similar should be the "about-us" page
        if len(results) > 0:
            top_match = results[0]
            print(f"  Top match: {top_match['url']} (similarity: {top_match['similarity']:.4f})")
            self.assertIn('about-us', top_match['url'])

    def test_06_insert_mapping(self):
        """
        Test inserting URL mappings.
        """
        # Create session
        session_id = self.session_db.create_session(user_id='test_user')
        self.test_session_id = session_id

        # Insert mapping
        mapping_id = self.mapping_db.insert_mapping(
            session_id=session_id,
            old_url='https://old-site.com/page1',
            new_url='https://new-site.com/page1',
            confidence_score=0.95,
            match_type='exact_url',
            needs_review=False
        )

        self.assertIsNotNone(mapping_id)
        print(f"\n✓ Inserted mapping: {mapping_id}")

        # Retrieve mappings
        mappings = self.mapping_db.get_mappings_by_session(session_id)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]['old_url'], 'https://old-site.com/page1')
        self.assertEqual(mappings[0]['confidence_score'], 0.95)
        print(f"✓ Retrieved {len(mappings)} mapping(s)")

    def test_07_mapping_filtering(self):
        """
        Test filtering mappings by review status.
        """
        # Create session
        session_id = self.session_db.create_session(user_id='test_user')
        self.test_session_id = session_id

        # Insert high-confidence mapping (no review needed)
        self.mapping_db.insert_mapping(
            session_id=session_id,
            old_url='https://old-site.com/page1',
            new_url='https://new-site.com/page1',
            confidence_score=0.95,
            match_type='exact_html',
            needs_review=False
        )

        # Insert low-confidence mapping (needs review)
        self.mapping_db.insert_mapping(
            session_id=session_id,
            old_url='https://old-site.com/page2',
            new_url='https://new-site.com/page2',
            confidence_score=0.65,
            match_type='semantic',
            needs_review=True
        )

        # Get all mappings
        all_mappings = self.mapping_db.get_mappings_by_session(session_id)
        self.assertEqual(len(all_mappings), 2)
        print(f"\n✓ Total mappings: {len(all_mappings)}")

        # Get only mappings needing review
        review_mappings = self.mapping_db.get_mappings_by_session(
            session_id,
            needs_review=True
        )
        self.assertEqual(len(review_mappings), 1)
        self.assertEqual(review_mappings[0]['confidence_score'], 0.65)
        print(f"✓ Mappings needing review: {len(review_mappings)}")


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Redirx Database Connection Test Suite")
    print("="*60)
    print("\nThis test verifies your Supabase connection and database setup.")
    print("Make sure you have:")
    print("  1. Created a .env file with SUPABASE_URL and SUPABASE_KEY")
    print("  2. Run the SQL schema setup in Supabase")
    print("="*60)

    # Run tests with verbose output
    unittest.main(verbosity=2)
