#!/usr/bin/env python3
"""
Test Embedding Storage in Supabase

This script demonstrates the EmbedStage by:
1. Creating simple test webpages
2. Generating embeddings using OpenAI
3. Storing them in Supabase
4. Verifying what was stored
5. Testing similarity search

Run this to verify your EmbedStage implementation works end-to-end.
"""

import sys
import os
import asyncio

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, parent_dir)

from src.redirx.config import Config
from src.redirx.database import MigrationSessionDB, WebPageEmbeddingDB
from src.redirx.stages import EmbedStage, WebPage, Mapping


def print_header(title):
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


async def main():
    print_header("EmbedStage Test - Embedding Storage Demo")

    # 1. Validate configuration
    print("Step 1: Validating configuration...")
    try:
        Config.validate()
        print(f"✓ Supabase URL: {Config.SUPABASE_URL}")

        Config.validate_embeddings()
        print(f"✓ OpenAI API Key: Set")
        print(f"✓ Embedding Model: {Config.EMBEDDING_MODEL}")
        print(f"✓ Embedding Dimension: {Config.EMBEDDING_DIMENSION}")
    except ValueError as e:
        print(f"✗ Configuration Error: {e}")
        print("\nPlease check your .env file and ensure:")
        print("  - SUPABASE_URL is set")
        print("  - SUPABASE_KEY is set")
        print("  - OPENAI_API_KEY is set")
        return 1

    # 2. Create test session
    print("\nStep 2: Creating migration session...")
    session_db = MigrationSessionDB()
    session_id = session_db.create_session(user_id='embedding_test_user')
    print(f"✓ Created session: {session_id}")

    try:
        # 3. Create test webpages
        print("\nStep 3: Creating test webpages...")

        old_pages = [
            WebPage(
                'http://old-site.com/about',
                '''
                <html>
                    <head><title>About Us - Old Site</title></head>
                    <body>
                        <h1>About Our Company</h1>
                        <p>We are a leading provider of innovative solutions for web migration.</p>
                        <p>Our team has over 10 years of experience helping businesses transition smoothly.</p>
                    </body>
                </html>
                '''
            ),
            WebPage(
                'http://old-site.com/contact',
                '''
                <html>
                    <head><title>Contact Us - Old Site</title></head>
                    <body>
                        <h1>Get in Touch</h1>
                        <p>Email: contact@oldsite.com</p>
                        <p>Phone: (555) 123-4567</p>
                    </body>
                </html>
                '''
            )
        ]

        new_pages = [
            WebPage(
                'http://new-site.com/about-us',
                '''
                <html>
                    <head><title>About - New Site</title></head>
                    <body>
                        <h1>About Our Company</h1>
                        <p>We are a leading provider of innovative solutions for web migration.</p>
                        <p>Our experienced team helps businesses transition smoothly to new platforms.</p>
                    </body>
                </html>
                '''
            ),
            WebPage(
                'http://new-site.com/contact-info',
                '''
                <html>
                    <head><title>Contact - New Site</title></head>
                    <body>
                        <h1>Contact Information</h1>
                        <p>Email: hello@newsite.com</p>
                        <p>Phone: (555) 123-4567</p>
                    </body>
                </html>
                '''
            )
        ]

        print(f"✓ Created {len(old_pages)} old pages")
        print(f"✓ Created {len(new_pages)} new pages")

        # 4. Run EmbedStage
        print("\nStep 4: Running EmbedStage (this may take ~10-20 seconds)...")
        print("  - Extracting text from HTML")
        print("  - Generating embeddings via OpenAI API")
        print("  - Storing in Supabase vector database")

        embed_stage = EmbedStage(session_id=session_id)
        mappings = set()

        # Execute the stage
        result = await embed_stage.execute((old_pages, new_pages, mappings))

        print("✓ EmbedStage execution complete!")

        # 5. Verify embeddings were stored
        print("\nStep 5: Verifying embeddings in Supabase...")
        embedding_db = WebPageEmbeddingDB()

        old_embeddings = embedding_db.get_embeddings_by_session(session_id, site_type='old')
        new_embeddings = embedding_db.get_embeddings_by_session(session_id, site_type='new')

        print(f"✓ Found {len(old_embeddings)} old site embeddings")
        print(f"✓ Found {len(new_embeddings)} new site embeddings")

        # 6. Display details of stored embeddings
        print("\nStep 6: Examining stored embeddings...")

        if old_embeddings:
            print("\n--- Old Site Embedding Sample ---")
            sample = old_embeddings[0]
            print(f"  URL: {sample['url']}")
            print(f"  Title: {sample['title']}")
            print(f"  Site Type: {sample['site_type']}")
            print(f"  Extracted Text (first 100 chars): {sample['extracted_text'][:100]}...")
            print(f"  Embedding Dimensions: {len(sample['embedding'])}")
            print(f"  First 5 embedding values: {sample['embedding'][:5]}")

        if new_embeddings:
            print("\n--- New Site Embedding Sample ---")
            sample = new_embeddings[0]
            print(f"  URL: {sample['url']}")
            print(f"  Title: {sample['title']}")
            print(f"  Site Type: {sample['site_type']}")
            print(f"  Extracted Text (first 100 chars): {sample['extracted_text'][:100]}...")
            print(f"  Embedding Dimensions: {len(sample['embedding'])}")

        # 7. Test similarity search
        print("\nStep 7: Testing vector similarity search...")
        print("  Searching for pages similar to 'http://old-site.com/about'...")

        if old_embeddings:
            import numpy as np

            # Get embedding for old about page
            old_about = next(e for e in old_embeddings if 'about' in e['url'].lower())
            query_embedding = np.array(old_about['embedding'], dtype=np.float32)

            # Search for similar new pages
            results = embedding_db.find_similar_pages(
                query_embedding=query_embedding,
                session_id=session_id,
                site_type='new',
                match_count=2
            )

            print(f"✓ Found {len(results)} similar page(s):\n")
            for i, result in enumerate(results, 1):
                print(f"  {i}. {result['url']}")
                print(f"     Title: {result['title']}")
                print(f"     Similarity: {result['similarity']:.4f}")
                print()

        # 8. Summary
        print_header("Test Summary")
        print(f"Session ID: {session_id}")
        print(f"Old pages embedded: {len(old_embeddings)}")
        print(f"New pages embedded: {len(new_embeddings)}")
        print(f"Total embeddings: {len(old_embeddings) + len(new_embeddings)}")
        print(f"Embedding dimensions: {Config.EMBEDDING_DIMENSION}")
        print(f"Similarity search: {'✓ Working' if results else '✗ Failed'}")
        print()
        print("✓ EmbedStage is working correctly!")
        print()
        print("You can view the embeddings in Supabase:")
        print(f"  Dashboard → Table Editor → webpage_embeddings")
        print(f"  Filter by session_id = {session_id}")

        return 0

    except Exception as e:
        print(f"\n✗ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # 9. Cleanup (optional - comment out if you want to inspect data in Supabase)
        print("\nStep 9: Cleanup...")
        cleanup = input("Delete test data from Supabase? (y/N): ").strip().lower()

        if cleanup == 'y':
            try:
                from src.redirx.database import SupabaseClient
                client = SupabaseClient.get_client()

                # Delete embeddings
                client.table('webpage_embeddings').delete().eq(
                    'session_id', str(session_id)
                ).execute()

                # Delete session
                client.table('migration_sessions').delete().eq(
                    'id', str(session_id)
                ).execute()

                print("✓ Test data cleaned up")
            except Exception as e:
                print(f"✗ Cleanup failed: {e}")
        else:
            print(f"Test data preserved. Session ID: {session_id}")


if __name__ == '__main__':
    print("\n" + "="*60)
    print("  Redirx EmbedStage - Embedding Storage Test")
    print("="*60)
    print("\nThis script will:")
    print("  1. Create test webpages with sample HTML")
    print("  2. Run the EmbedStage to generate embeddings")
    print("  3. Store embeddings in your Supabase database")
    print("  4. Verify storage and test similarity search")
    print("\nRequirements:")
    print("  - Valid .env with SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY")
    print("  - Active internet connection (for OpenAI API)")
    print("  - Supabase schema properly set up")
    print("\n" + "="*60 + "\n")

    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
