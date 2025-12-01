#!/usr/bin/env python3
"""
Demo script comparing results WITH and WITHOUT HtmlPruneStage.

This script shows how HtmlPruneStage improves accuracy by catching
identical HTML pages before semantic matching.

Usage:
    python demo_with_htmlprune.py
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Dict
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.redirx.stages import EmbedStage, PairingStage, HtmlPruneStage, WebPage
from src.redirx.database import MigrationSessionDB, URLMappingDB
from src.redirx.config import Config


def load_mock_site_pages(site_dir: str) -> List[WebPage]:
    """Load all HTML files from a mock site directory."""
    site_path = Path(site_dir)
    pages = []

    if 'old_site' in site_dir:
        base_url = 'http://localhost:8000'
    else:
        base_url = 'http://localhost:8001'

    for html_file in sorted(site_path.rglob('*.html')):
        rel_path = html_file.relative_to(site_path)
        url = f"{base_url}/{rel_path}"

        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()

        pages.append(WebPage(url, html))

    return pages


async def run_demo():
    """Run the demo comparing with and without HtmlPruneStage."""

    print("=" * 80)
    print("REDIRX PAIRING DEMO - HtmlPruneStage Impact Analysis")
    print("=" * 80)
    print()

    # Validate configuration
    try:
        Config.validate()
        Config.validate_embeddings()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        return

    # Load pages
    print("📂 Loading mock site pages...")
    old_pages = load_mock_site_pages('tests/mock_sites/old_site')
    new_pages = load_mock_site_pages('tests/mock_sites/new_site')
    print(f"   Loaded {len(old_pages)} old site pages")
    print(f"   Loaded {len(new_pages)} new site pages")
    print()

    # ========================================================================
    # Scenario 1: WITH HtmlPruneStage (Recommended Pipeline)
    # ========================================================================

    print("=" * 80)
    print("SCENARIO 1: WITH HtmlPruneStage (Recommended)")
    print("=" * 80)
    print()

    session1 = MigrationSessionDB().create_session(user_id='demo_with_prune')

    try:
        # Run HtmlPruneStage first
        print("🔍 Running HtmlPruneStage (exact HTML matching)...")
        html_prune = HtmlPruneStage()
        old_pages_1, new_pages_1, html_mappings = await html_prune.execute((old_pages, new_pages))
        print(f"   ✅ Found {len(html_mappings)} exact HTML matches")
        print()

        # Show what HtmlPruneStage matched
        if html_mappings:
            print("   Exact matches found:")
            for mapping in html_mappings:
                old_url = mapping.old_page.url.replace('http://localhost:8000/', '')
                new_url = mapping.new_page.url.replace('http://localhost:8001/', '')
                print(f"      • {old_url:50} → {new_url}")
            print()

        # Run EmbedStage
        print("🧠 Generating embeddings...")
        embed_stage1 = EmbedStage(session_id=session1)
        result1 = await embed_stage1.execute((old_pages_1, new_pages_1, html_mappings))
        print("   ✅ Embeddings generated")
        print()

        # Run PairingStage
        print("🔗 Finding semantic matches (excluding HTML-matched pages)...")
        pairing_stage1 = PairingStage(session_id=session1)
        await pairing_stage1.execute(result1)
        print()

        # Get results
        mapping_db = URLMappingDB()
        mappings1 = mapping_db.get_mappings_by_session(session_id=session1)

        html_match_count = len([m for m in mappings1 if m['match_type'] == 'exact_html'])
        semantic_match_count = len(mappings1) - html_match_count

        print(f"📊 Results: {len(mappings1)} total matches")
        print(f"   • {html_match_count} exact HTML matches")
        print(f"   • {semantic_match_count} semantic matches")
        print()

        # Check the consulting.html match
        consulting_match1 = next(
            (m for m in mappings1 if 'services/consulting.html' in m['old_url']),
            None
        )

        if consulting_match1:
            print("✅ services/consulting.html result:")
            new_url = consulting_match1['new_url'].replace('http://localhost:8001/', '')
            print(f"   → {new_url}")
            print(f"   Match type: {consulting_match1['match_type']}")
            print(f"   Confidence: {consulting_match1['confidence_score']:.3f}")
            print(f"   Needs review: {consulting_match1['needs_review']}")
            print()

    except Exception as e:
        print(f"❌ Error in Scenario 1: {e}")
        import traceback
        traceback.print_exc()

    # ========================================================================
    # Scenario 2: WITHOUT HtmlPruneStage (Semantic Only)
    # ========================================================================

    print("=" * 80)
    print("SCENARIO 2: WITHOUT HtmlPruneStage (Semantic Matching Only)")
    print("=" * 80)
    print()

    session2 = MigrationSessionDB().create_session(user_id='demo_without_prune')

    try:
        print("⚠️  Skipping HtmlPruneStage - using semantic matching only")
        print()

        # Run EmbedStage
        print("🧠 Generating embeddings...")
        embed_stage2 = EmbedStage(session_id=session2)
        result2 = await embed_stage2.execute((old_pages, new_pages, set()))
        print("   ✅ Embeddings generated")
        print()

        # Run PairingStage
        print("🔗 Finding matches with pure semantic search...")
        pairing_stage2 = PairingStage(session_id=session2)
        await pairing_stage2.execute(result2)
        print()

        # Get results
        mappings2 = mapping_db.get_mappings_by_session(session_id=session2)

        print(f"📊 Results: {len(mappings2)} total matches")
        print(f"   • 0 exact HTML matches (stage skipped)")
        print(f"   • {len(mappings2)} semantic matches")
        print()

        # Check the consulting.html match
        consulting_match2 = next(
            (m for m in mappings2 if 'services/consulting.html' in m['old_url']),
            None
        )

        if consulting_match2:
            print("⚠️  services/consulting.html result:")
            new_url = consulting_match2['new_url'].replace('http://localhost:8001/', '')
            print(f"   → {new_url}")
            print(f"   Match type: {consulting_match2['match_type']}")
            print(f"   Confidence: {consulting_match2['confidence_score']:.3f}")
            print(f"   Needs review: {consulting_match2['needs_review']}")

            expected = 'solutions/consulting.html'
            actual = new_url
            if expected != actual:
                print(f"   ❌ INCORRECT - Expected: {expected}")
            else:
                print(f"   ✅ CORRECT")
            print()

    except Exception as e:
        print(f"❌ Error in Scenario 2: {e}")
        import traceback
        traceback.print_exc()

    # ========================================================================
    # Comparison Summary
    # ========================================================================

    print("=" * 80)
    print("COMPARISON SUMMARY")
    print("=" * 80)
    print()

    # Calculate accuracy for both scenarios
    from pathlib import Path
    expected_file = Path('tests/mock_sites/SITE_MAPPING.md')
    expected_mappings = {}

    if expected_file.exists():
        with open(expected_file, 'r') as f:
            lines = f.readlines()

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
                    if new_url != '-' and old_url != '-':
                        expected_mappings[old_url] = new_url

    def calculate_accuracy(mappings):
        correct = 0
        incorrect = 0
        for m in mappings:
            old = m['old_url'].replace('http://localhost:8000', '')
            new = m['new_url'].replace('http://localhost:8001', '')
            if old in expected_mappings:
                if expected_mappings[old] == new:
                    correct += 1
                else:
                    incorrect += 1
        return correct, incorrect

    correct1, incorrect1 = calculate_accuracy(mappings1)
    correct2, incorrect2 = calculate_accuracy(mappings2)

    print(f"{'Metric':<35} {'WITH HtmlPrune':>20} {'WITHOUT HtmlPrune':>20}")
    print("-" * 80)
    print(f"{'Total matches':<35} {len(mappings1):>20} {len(mappings2):>20}")
    print(f"{'Exact HTML matches':<35} {html_match_count:>20} {0:>20}")
    print(f"{'Semantic matches':<35} {semantic_match_count:>20} {len(mappings2):>20}")
    print(f"{'Correct matches':<35} {correct1:>20} {correct2:>20}")
    print(f"{'Incorrect matches':<35} {incorrect1:>20} {incorrect2:>20}")
    acc1 = (correct1 / len(mappings1) * 100) if mappings1 else 0
    acc2 = (correct2 / len(mappings2) * 100) if mappings2 else 0
    print(f"{'Accuracy':<35} {f'{acc1:.1f}%':>20} {f'{acc2:.1f}%':>20}")
    print()

    improvement = acc1 - acc2
    print(f"🎯 Accuracy Improvement: +{improvement:.1f}% with HtmlPruneStage")
    print()

    print("=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print()
    print("HtmlPruneStage is CRITICAL for accuracy because:")
    print("  1. Catches identical HTML pages BEFORE semantic matching")
    print("  2. Prevents semantic confusion between similar topics")
    print("  3. Reserves semantic matching for truly different pages")
    print("  4. Improves overall accuracy and reduces manual review")
    print()
    print("✅ Always use the full pipeline: HtmlPrune → Embed → Pairing")
    print()


if __name__ == '__main__':
    asyncio.run(run_demo())
