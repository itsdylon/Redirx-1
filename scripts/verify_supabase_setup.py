#!/usr/bin/env python3
"""
Verify Supabase Vector Store Setup

This script checks that your Supabase database is properly configured
for the EmbedStage to work correctly.

Run this BEFORE testing the EmbedStage to ensure everything is set up.
"""

import sys
import os

# Add parent directory to path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, parent_dir)

from src.redirx.config import Config
from src.redirx.database import SupabaseClient, MigrationSessionDB, WebPageEmbeddingDB
import numpy as np


def print_header(title):
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_check(message, passed):
    """Print a check result."""
    symbol = "✓" if passed else "✗"
    status = "PASS" if passed else "FAIL"
    print(f"{symbol} {message}: {status}")


def main():
    print_header("Supabase Vector Store Verification")

    all_checks_passed = True

    # 1. Configuration Check
    print_header("1. Configuration Check")
    try:
        Config.validate()
        print_check("Supabase URL configured", True)
        print(f"  URL: {Config.SUPABASE_URL}")
        print_check("Supabase Key configured", True)
    except ValueError as e:
        print_check("Configuration", False)
        print(f"  Error: {e}")
        all_checks_passed = False
        return 1

    try:
        Config.validate_embeddings()
        print_check("OpenAI API Key configured", True)
        print(f"  Model: {Config.EMBEDDING_MODEL}")
        print(f"  Dimension: {Config.EMBEDDING_DIMENSION}")
    except ValueError as e:
        print_check("OpenAI configuration", False)
        print(f"  Error: {e}")
        all_checks_passed = False

    # 2. Database Connection
    print_header("2. Database Connection")
    try:
        client = SupabaseClient.get_client()
        print_check("Supabase connection established", True)
    except Exception as e:
        print_check("Supabase connection", False)
        print(f"  Error: {e}")
        all_checks_passed = False
        return 1

    # 3. Table Structure Check
    print_header("3. Table Structure Check")

    # Check migration_sessions table
    try:
        result = client.table('migration_sessions').select('*').limit(0).execute()
        print_check("migration_sessions table exists", True)
    except Exception as e:
        print_check("migration_sessions table exists", False)
        print(f"  Error: {e}")
        all_checks_passed = False

    # Check webpage_embeddings table
    try:
        result = client.table('webpage_embeddings').select('*').limit(0).execute()
        print_check("webpage_embeddings table exists", True)
    except Exception as e:
        print_check("webpage_embeddings table exists", False)
        print(f"  Error: {e}")
        all_checks_passed = False

    # Check url_mappings table
    try:
        result = client.table('url_mappings').select('*').limit(0).execute()
        print_check("url_mappings table exists", True)
    except Exception as e:
        print_check("url_mappings table exists", False)
        print(f"  Error: {e}")
        all_checks_passed = False

    # 4. Vector Column Check
    print_header("4. Vector Column Check")
    try:
        # Try to insert a test embedding
        session_db = MigrationSessionDB()
        embedding_db = WebPageEmbeddingDB()

        # Create test session
        test_session_id = session_db.create_session(user_id='test_verify')
        print_check("Can create migration session", True)
        print(f"  Test session ID: {test_session_id}")

        # Create 1536-dimensional test embedding
        test_embedding = np.random.randn(1536).astype(np.float32)
        print_check(f"Generated test embedding ({test_embedding.shape[0]} dims)", True)

        # Try to insert it
        try:
            embedding_id = embedding_db.insert_embedding(
                session_id=test_session_id,
                url='https://test.com/verify',
                site_type='old',
                embedding=test_embedding,
                extracted_text='Test verification content',
                title='Test Verification'
            )
            print_check("Can insert 1536-dim embedding", True)
            print(f"  Embedding ID: {embedding_id}")

            # Verify it was stored correctly
            embeddings = embedding_db.get_embeddings_by_session(test_session_id)
            if len(embeddings) == 1:
                stored_embedding = embeddings[0]['embedding']
                if len(stored_embedding) == 1536:
                    print_check("Embedding stored with correct dimensions", True)
                else:
                    print_check("Embedding stored with correct dimensions", False)
                    print(f"  Expected: 1536, Got: {len(stored_embedding)}")
                    all_checks_passed = False
            else:
                print_check("Embedding retrieval", False)
                all_checks_passed = False

        except Exception as e:
            print_check("Can insert embedding", False)
            print(f"  Error: {e}")
            print("\n  HINT: Your vector column might not be configured for 1536 dimensions.")
            print("  Run this SQL in Supabase:")
            print("  ALTER TABLE webpage_embeddings ALTER COLUMN embedding TYPE vector(1536);")
            all_checks_passed = False

        # Clean up test data
        try:
            client.table('webpage_embeddings').delete().eq('session_id', str(test_session_id)).execute()
            client.table('migration_sessions').delete().eq('id', str(test_session_id)).execute()
            print_check("Test data cleanup", True)
        except Exception as e:
            print(f"  Warning: Could not clean up test data: {e}")

    except Exception as e:
        print_check("Vector operations", False)
        print(f"  Error: {e}")
        all_checks_passed = False

    # 5. RPC Function Check
    print_header("5. RPC Function Check (match_pages)")
    try:
        # Create test data for similarity search
        session_db = MigrationSessionDB()
        embedding_db = WebPageEmbeddingDB()

        test_session_id = session_db.create_session(user_id='test_rpc')

        # Insert a test embedding
        test_embedding = np.random.randn(1536).astype(np.float32)
        embedding_db.insert_embedding(
            session_id=test_session_id,
            url='https://test.com/page1',
            site_type='new',
            embedding=test_embedding,
            extracted_text='Test content',
            title='Test Page'
        )

        # Try to call match_pages RPC
        try:
            results = embedding_db.find_similar_pages(
                query_embedding=test_embedding,
                session_id=test_session_id,
                site_type='new',
                match_count=1
            )
            print_check("match_pages RPC function exists", True)
            print_check("Similarity search works", True)
            if len(results) > 0:
                print(f"  Found {len(results)} match(es)")
                print(f"  Similarity score: {results[0].get('similarity', 'N/A')}")

        except Exception as e:
            print_check("match_pages RPC function", False)
            print(f"  Error: {e}")
            print("\n  HINT: You need to create the match_pages() function in Supabase.")
            print("  See docs/SUPABASE_SETUP.md for the SQL code.")
            all_checks_passed = False

        # Clean up
        try:
            client.table('webpage_embeddings').delete().eq('session_id', str(test_session_id)).execute()
            client.table('migration_sessions').delete().eq('id', str(test_session_id)).execute()
        except:
            pass

    except Exception as e:
        print_check("RPC function test", False)
        print(f"  Error: {e}")
        all_checks_passed = False

    # Final Summary
    print_header("Verification Summary")
    if all_checks_passed:
        print("✓ ALL CHECKS PASSED!")
        print("\nYour Supabase setup is correct and ready for EmbedStage.")
        print("\nNext steps:")
        print("  1. Run: python tests/test_database_connection.py")
        print("  2. Run: python scripts/test_embedding_storage.py")
        return 0
    else:
        print("✗ SOME CHECKS FAILED")
        print("\nPlease fix the issues above before using EmbedStage.")
        print("See docs/SUPABASE_SETUP.md for setup instructions.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
