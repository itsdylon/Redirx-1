#!/usr/bin/env python3
"""
Demo script to show pairing results from embedding + nearest neighbor search.

This script:
1. Loads all HTML files from mock_sites/old_site and mock_sites/new_site
2. Generates embeddings using OpenAI API (real embeddings)
3. Performs vector similarity search to find matches
4. Outputs detailed results showing which pages match

Usage:
    python demo_pairing_results.py

Requirements:
    - OPENAI_API_KEY in .env file
    - SUPABASE_URL and SUPABASE_KEY in .env file
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Dict
from uuid import uuid4

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.redirx.stages import EmbedStage, PairingStage, WebPage
from src.redirx.database import MigrationSessionDB, URLMappingDB
from src.redirx.config import Config


def load_mock_site_pages(site_dir: str) -> List[WebPage]:
    """
    Load all HTML files from a mock site directory.

    Args:
        site_dir: Path to site directory (e.g., 'tests/mock_sites/old_site')

    Returns:
        List of WebPage objects with proper URLs
    """
    site_path = Path(site_dir)
    pages = []

    # Determine base URL based on site type
    if 'old_site' in site_dir:
        base_url = 'http://localhost:8000'
    else:
        base_url = 'http://localhost:8001'

    # Find all HTML files
    html_files = sorted(site_path.rglob('*.html'))

    for html_file in html_files:
        # Create relative URL path
        rel_path = html_file.relative_to(site_path)
        url = f"{base_url}/{rel_path}"

        # Read HTML content
        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()

        pages.append(WebPage(url, html))

    return pages


def load_expected_mappings() -> Dict[str, Dict]:
    """
    Load expected mappings from SITE_MAPPING.md for comparison.

    Returns:
        Dictionary mapping old URL -> expected new URL with metadata
    """
    mapping_file = Path('tests/mock_sites/SITE_MAPPING.md')

    if not mapping_file.exists():
        return {}

    expected = {}

    with open(mapping_file, 'r') as f:
        lines = f.readlines()

    # Parse the markdown table
    in_table = False
    for line in lines:
        if line.startswith('| # |'):
            in_table = True
            continue
        if in_table and line.startswith('|---'):
            continue
        if in_table and line.startswith('|'):
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 7 and parts[1].isdigit():
                old_url = parts[2]
                new_url = parts[3]
                match_type = parts[4]
                similarity = parts[5]
                stage = parts[6]

                # Skip orphaned and new pages
                if new_url != '-' and old_url != '-':
                    expected[old_url] = {
                        'new_url': new_url,
                        'match_type': match_type,
                        'similarity': similarity,
                        'stage': stage
                    }

    return expected


async def run_demo():
    """Run the demo and output results."""

    print("=" * 80)
    print("REDIRX PAIRING DEMO - Mock Sites with Real Embeddings")
    print("=" * 80)
    print()

    # Validate configuration
    try:
        Config.validate()
        Config.validate_embeddings()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        print("\nPlease ensure your .env file contains:")
        print("  - OPENAI_API_KEY")
        print("  - SUPABASE_URL")
        print("  - SUPABASE_KEY")
        return

    print("✅ Configuration validated")
    print()

    # Load mock site pages
    print("📂 Loading mock site pages...")
    old_site_path = 'tests/mock_sites/old_site'
    new_site_path = 'tests/mock_sites/new_site'

    old_pages = load_mock_site_pages(old_site_path)
    new_pages = load_mock_site_pages(new_site_path)

    print(f"   Loaded {len(old_pages)} old site pages")
    print(f"   Loaded {len(new_pages)} new site pages")
    print()

    # Load expected mappings for comparison
    expected_mappings = load_expected_mappings()
    print(f"📋 Loaded {len(expected_mappings)} expected mappings from SITE_MAPPING.md")
    print()

    # Create a test session
    session_db = MigrationSessionDB()
    session_id = session_db.create_session(user_id='demo_user')
    print(f"🔑 Created migration session: {session_id}")
    print()

    try:
        # Run EmbedStage
        print("🧠 Generating embeddings with OpenAI...")
        print("   (This may take 30-60 seconds for all pages)")
        embed_stage = EmbedStage(session_id=session_id)
        result_after_embed = await embed_stage.execute((old_pages, new_pages, set()))
        print("   ✅ Embeddings generated successfully")
        print()

        # Run PairingStage
        print("🔗 Finding matches with vector similarity search...")
        pairing_stage = PairingStage(session_id=session_id)
        result_after_pairing = await pairing_stage.execute(result_after_embed)
        print()

        # Get mappings from database
        mapping_db = URLMappingDB()
        mappings = mapping_db.get_mappings_by_session(session_id=session_id)

        # Organize results
        print("=" * 80)
        print("PAIRING RESULTS")
        print("=" * 80)
        print()

        # Sort mappings by confidence score (descending)
        mappings.sort(key=lambda m: m['confidence_score'], reverse=True)

        # Group by confidence level
        high_confidence = [m for m in mappings if m['confidence_score'] >= 0.9]
        medium_confidence = [m for m in mappings if 0.8 <= m['confidence_score'] < 0.9]
        low_confidence = [m for m in mappings if 0.6 <= m['confidence_score'] < 0.8]

        # Display high confidence matches
        if high_confidence:
            print(f"🟢 HIGH CONFIDENCE MATCHES ({len(high_confidence)})")
            print("   Score ≥ 0.9 - Highly likely to be correct")
            print("-" * 80)
            for mapping in high_confidence:
                old_url = mapping['old_url'].replace('http://localhost:8000/', '')
                new_url = mapping['new_url'].replace('http://localhost:8001/', '')
                score = mapping['confidence_score']
                needs_review = "⚠️  REVIEW" if mapping['needs_review'] else "✓"

                # Check against expected
                expected_match = ""
                expected_key = f"/{old_url}"
                if expected_key in expected_mappings:
                    expected_new = expected_mappings[expected_key]['new_url']
                    if expected_new == f"/{new_url}":
                        expected_match = " ✅ MATCHES EXPECTED"
                    else:
                        expected_match = f" ⚠️  EXPECTED: {expected_new}"

                print(f"   {score:.3f}  {old_url:45} → {new_url:45} {needs_review}{expected_match}")
            print()

        # Display medium confidence matches
        if medium_confidence:
            print(f"🟡 MEDIUM CONFIDENCE MATCHES ({len(medium_confidence)})")
            print("   Score 0.8-0.9 - Likely correct, may need review if ambiguous")
            print("-" * 80)
            for mapping in medium_confidence:
                old_url = mapping['old_url'].replace('http://localhost:8000/', '')
                new_url = mapping['new_url'].replace('http://localhost:8001/', '')
                score = mapping['confidence_score']
                needs_review = "⚠️  REVIEW" if mapping['needs_review'] else "✓"

                # Check against expected
                expected_match = ""
                expected_key = f"/{old_url}"
                if expected_key in expected_mappings:
                    expected_new = expected_mappings[expected_key]['new_url']
                    if expected_new == f"/{new_url}":
                        expected_match = " ✅ MATCHES EXPECTED"
                    else:
                        expected_match = f" ⚠️  EXPECTED: {expected_new}"

                print(f"   {score:.3f}  {old_url:45} → {new_url:45} {needs_review}{expected_match}")
            print()

        # Display low confidence matches
        if low_confidence:
            print(f"🟠 LOW CONFIDENCE MATCHES ({len(low_confidence)})")
            print("   Score 0.6-0.8 - May be correct, should review")
            print("-" * 80)
            for mapping in low_confidence:
                old_url = mapping['old_url'].replace('http://localhost:8000/', '')
                new_url = mapping['new_url'].replace('http://localhost:8001/', '')
                score = mapping['confidence_score']
                needs_review = "⚠️  REVIEW" if mapping['needs_review'] else "✓"

                # Check against expected
                expected_match = ""
                expected_key = f"/{old_url}"
                if expected_key in expected_mappings:
                    expected_new = expected_mappings[expected_key]['new_url']
                    if expected_new == f"/{new_url}":
                        expected_match = " ✅ MATCHES EXPECTED"
                    else:
                        expected_match = f" ⚠️  EXPECTED: {expected_new}"

                print(f"   {score:.3f}  {old_url:45} → {new_url:45} {needs_review}{expected_match}")
            print()

        # Find orphaned and new pages
        old_urls = {m['old_url'] for m in mappings}
        new_urls = {m['new_url'] for m in mappings}

        orphaned_pages = [p for p in old_pages if p.url not in old_urls]
        new_only_pages = [p for p in new_pages if p.url not in new_urls]

        # Display orphaned pages
        if orphaned_pages:
            print(f"🔴 ORPHANED PAGES ({len(orphaned_pages)})")
            print("   Old pages with no suitable match (similarity < 0.6)")
            print("-" * 80)
            for page in orphaned_pages:
                url = page.url.replace('http://localhost:8000/', '')
                print(f"   {url}")
            print()

        # Display new pages
        if new_only_pages:
            print(f"🆕 NEW PAGES ({len(new_only_pages)})")
            print("   New pages with no old equivalent")
            print("-" * 80)
            for page in new_only_pages:
                url = page.url.replace('http://localhost:8001/', '')
                print(f"   {url}")
            print()

        # Summary statistics
        print("=" * 80)
        print("SUMMARY STATISTICS")
        print("=" * 80)
        print(f"Total old pages:           {len(old_pages)}")
        print(f"Total new pages:           {len(new_pages)}")
        print(f"Successful matches:        {len(mappings)}")
        print(f"  - High confidence:       {len(high_confidence)}")
        print(f"  - Medium confidence:     {len(medium_confidence)}")
        print(f"  - Low confidence:        {len(low_confidence)}")
        print(f"Orphaned pages:            {len(orphaned_pages)}")
        print(f"New pages:                 {len(new_only_pages)}")
        print(f"Needs review:              {len([m for m in mappings if m['needs_review']])}")
        print()

        # Accuracy check against expected mappings
        if expected_mappings:
            correct_matches = 0
            incorrect_matches = 0

            for mapping in mappings:
                old_url = mapping['old_url'].replace('http://localhost:8000', '')
                new_url = mapping['new_url'].replace('http://localhost:8001', '')

                if old_url in expected_mappings:
                    expected_new = expected_mappings[old_url]['new_url']
                    if expected_new == new_url:
                        correct_matches += 1
                    else:
                        incorrect_matches += 1

            accuracy = (correct_matches / len(mappings) * 100) if mappings else 0

            print("VALIDATION AGAINST SITE_MAPPING.md")
            print("=" * 80)
            print(f"Correct matches:           {correct_matches}")
            print(f"Incorrect matches:         {incorrect_matches}")
            print(f"Accuracy:                  {accuracy:.1f}%")
            print()

        print("✅ Demo complete!")
        print()
        print(f"💾 All data saved to session: {session_id}")
        print("   You can query this session from the database to inspect the results.")

    except Exception as e:
        print(f"❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(run_demo())
