# Site Mapping Matrix

This document provides the complete URL mapping between the old TechCo Solutions site and the new TechCo site. It specifies the expected match type, content similarity, and which pipeline stage should successfully pair each URL.

## Mapping Legend

**Match Types:**
- `IDENTICAL` - Exact same HTML content (character-for-character)
- `SIMILAR` - High content similarity (85-95%) with minor rewrites
- `MODERATE` - Moderate similarity (70-85%) with significant rewrites
- `RENAMED` - URL changed but content highly similar
- `RESTRUCTURED` - Major URL pattern changes with similar content
- `ORPHANED` - Old page with no equivalent in new site
- `NEW` - New page with no equivalent in old site
- `FILTERED` - Asset file that should be filtered by UrlPruneStage

**Pipeline Stages:**
- `UrlPruneStage` - Filters out non-HTML files (.css, .js, images)
- `HtmlPruneStage` - Matches pages with identical HTML via hash comparison
- `PairingStage` - Matches pages via vector similarity after embedding

## Complete URL Mapping

| # | Old URL | New URL | Match Type | Similarity | Stage | Notes |
|---|---------|---------|------------|------------|-------|-------|
| 1 | /index.html | /index.html | IDENTICAL | 100% | HtmlPruneStage | Exact homepage match |
| 2 | /about.html | /about-us.html | RENAMED | 85% | PairingStage | URL renamed, minor content updates |
| 3 | /services/index.html | /solutions/index.html | RENAMED | 80% | PairingStage | Section renamed, rebranded terminology |
| 4 | /services/consulting.html | /solutions/consulting.html | IDENTICAL | 100% | HtmlPruneStage | Same URL structure, identical content |
| 5 | /services/development.html | /solutions/software-development.html | RENAMED | 90% | PairingStage | URL expanded, minor rewrites |
| 6 | /services/support.html | /solutions/technical-support.html | RENAMED | 88% | PairingStage | URL expanded, similar content |
| 7 | /products/index.html | /products/index.html | SIMILAR | 92% | PairingStage | Same URL, content refreshed |
| 8 | /products/product-a.html | /products/product-alpha.html | RENAMED | 95% | PairingStage | Product renamed alpha |
| 9 | /products/product-b.html | /products/product-beta.html | RENAMED | 95% | PairingStage | Product renamed beta |
| 10 | /blogs/index.html | /news/index.html | RESTRUCTURED | 75% | PairingStage | Major section rename |
| 11 | /blogs/2023-01-industry-trends.html | /news/industry-trends-january.html | RESTRUCTURED | 90% | PairingStage | URL pattern completely changed |
| 12 | /blogs/2023-02-tech-insights.html | /news/tech-insights-february.html | RESTRUCTURED | 88% | PairingStage | URL pattern completely changed |
| 13 | /blogs/2023-03-best-practices.html | /news/best-practices-march.html | RESTRUCTURED | 92% | PairingStage | URL pattern completely changed |
| 14 | /blogs/2024-01-year-review.html | - | ORPHANED | 0% | None | Blog post removed, no new equivalent |
| 15 | /team.html | /our-team.html | RENAMED | 87% | PairingStage | URL renamed, some team updates |
| 16 | /careers.html | /join-us.html | RENAMED | 83% | PairingStage | URL renamed, content refreshed |
| 17 | /contact.html | /get-in-touch.html | IDENTICAL | 100% | HtmlPruneStage | Different URL, identical content |
| 18 | /case-studies/index.html | /success-stories/index.html | RESTRUCTURED | 78% | PairingStage | Section renamed |
| 19 | /case-studies/acme-corp.html | /success-stories/acme-corporation.html | RENAMED | 85% | PairingStage | URL slightly expanded |
| 20 | /case-studies/globex-inc.html | /success-stories/globex-industries.html | RENAMED | 87% | PairingStage | URL slightly changed |
| 21 | /resources/index.html | /resource-center/index.html | RENAMED | 80% | PairingStage | Section renamed |
| 22 | /resources/whitepaper-2023.html | /resource-center/download-whitepaper.html | RENAMED | 92% | PairingStage | URL restructured |
| 23 | /resources/implementation-guide.html | /resource-center/comprehensive-guide.html | RENAMED | 88% | PairingStage | URL renamed |
| 24 | /pricing.html | /plans-and-pricing.html | SIMILAR | 90% | PairingStage | URL expanded, content updated |
| 25 | /faq.html | /help/frequently-asked-questions.html | RESTRUCTURED | 85% | PairingStage | Moved into nested structure |
| 26 | /partners.html | - | ORPHANED | 0% | None | Partners page removed |
| 27 | /testimonials.html | /success-stories/index.html | MODERATE | 60% | PairingStage | Content merged into success stories |
| 28 | /legal/privacy.html | /legal/privacy-policy.html | SIMILAR | 88% | PairingStage | URL expanded, updated policy |
| 29 | /legal/terms.html | /legal/terms-of-service.html | SIMILAR | 90% | PairingStage | URL expanded, updated terms |
| 30 | /sitemap.html | /site-map.html | SIMILAR | 95% | PairingStage | Hyphenated URL, updated links |
| 31 | /404.html | /404.html | SIMILAR | 85% | PairingStage | Same URL, redesigned page |
| 32 | - | /innovations.html | NEW | 0% | None | New page, no old equivalent |
| 33 | - | /customer-portal.html | NEW | 0% | None | New page, no old equivalent |
| 34 | /assets/styles.css | /assets/styles.css | FILTERED | N/A | UrlPruneStage | CSS should be filtered |
| 35 | /assets/main.js | /assets/app.js | FILTERED | N/A | UrlPruneStage | JS should be filtered |
| 36 | /assets/logo-old.png | /assets/logo-new.png | FILTERED | N/A | UrlPruneStage | Images should be filtered |

## Summary Statistics

### By Match Type
- **IDENTICAL:** 3 pages (100% HTML match)
- **RENAMED:** 13 pages (URL changed, content highly similar)
- **SIMILAR:** 6 pages (Same/similar URL, content refreshed)
- **RESTRUCTURED:** 6 pages (Major URL pattern changes)
- **MODERATE:** 1 page (Merged content)
- **ORPHANED:** 3 pages (Old site only)
- **NEW:** 2 pages (New site only)
- **FILTERED:** 6 files (Assets to be filtered)

### By Pipeline Stage
- **UrlPruneStage:** 6 filtered files
- **HtmlPruneStage:** 3 exact HTML matches
- **PairingStage:** 25 semantic matches
- **No Match:** 5 pages (3 orphaned, 2 new)

### By Content Similarity
- **100%:** 3 pages
- **90-99%:** 10 pages
- **80-89%:** 11 pages
- **70-79%:** 3 pages
- **60-69%:** 1 page
- **0% (No match):** 5 pages

## Testing Guidelines

### HtmlPruneStage Testing
These pages should match via hash comparison:
1. `/index.html` → `/index.html`
2. `/services/consulting.html` → `/solutions/consulting.html`
3. `/contact.html` → `/get-in-touch.html`

**Expected Behavior:**
- Stage should create 3 `Mapping` objects
- Matched pages should be removed from further processing
- Hash comparison should be O(n) time complexity

### PairingStage Testing
All remaining pages (25 total) should be matched via vector similarity:

**High Confidence (>90% similarity):** 10 pages
- Should generate mappings with `confidence_score >= 0.90`
- Should set `needs_review = False`
- Match type should be `exact_html` or `semantic_high`

**Medium Confidence (80-90% similarity):** 11 pages
- Should generate mappings with `confidence_score >= 0.80`
- Should set `needs_review = False` or `True` based on threshold
- Match type should be `semantic_medium`

**Low Confidence (60-80% similarity):** 3 pages
- Should generate mappings with `confidence_score >= 0.60`
- Should set `needs_review = True`
- Match type should be `semantic_low`

**Very Low Confidence (<60%):** 1 page
- Testimonials merged into success stories
- May or may not generate mapping depending on threshold
- Should set `needs_review = True` if matched

**No Match:** 5 pages
- 3 orphaned pages (should identify as unmapped)
- 2 new pages (should identify as new content)

### UrlPruneStage Testing
These files should be filtered out:
1. `/assets/styles.css`
2. `/assets/main.js` (old site)
3. `/assets/app.js` (new site)
4. `/assets/logo-old.png`
5. `/assets/logo-new.png`
6. Any other asset files with extensions: `.css`, `.js`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.ico`, `.woff`, `.woff2`, `.ttf`

**Expected Behavior:**
- Should remove 6 URLs total (3 from old site, 3 from new site)
- Should preserve all 28 old HTML pages and 29 new HTML pages
- Should use URL extension or pattern matching

## Content Variation Guidelines

To achieve the specified similarity percentages, use these techniques:

### For IDENTICAL (100%)
- Copy HTML exactly, including whitespace
- Only change: URLs in links, if any
- No paraphrasing or reordering

### For SIMILAR (90-95%)
- Keep same structure and headings
- Paraphrase 1-2 paragraphs
- Update dates or statistics
- Keep semantic meaning identical

### For RENAMED (80-90%)
- Moderate paraphrasing (3-5 paragraphs)
- Reorder some sections
- Update terminology (e.g., "solutions" instead of "services")
- Add or remove minor details
- Keep overall message the same

### For RESTRUCTURED (70-85%)
- Significant rewriting (50% of content)
- Different paragraph structure
- Some sections added or removed
- Same topic and key points
- Different presentation style

### For MODERATE (60-70%)
- Major rewrite (70% different)
- Content from multiple sources
- Same general topic
- Different focus or angle
- Some overlapping information

## Expected Test Assertions

### Unit Tests (Per-Stage)

```python
# UrlPruneStage
assert len(old_urls_after_prune) == 28  # HTML pages only
assert len(new_urls_after_prune) == 29  # HTML pages only
assert not any(url.endswith('.css') for url in old_urls_after_prune)
assert not any(url.endswith('.js') for url in new_urls_after_prune)

# HtmlPruneStage
assert len(mappings_after_html_prune) == 3
assert all(mapping.confidence == 1.0 for mapping in mappings_after_html_prune)

# PairingStage
assert len(mappings_after_pairing) >= 23  # 3 from HTML + ~20 from semantic
assert len(mappings_after_pairing) <= 26  # Depends on confidence threshold
```

### Integration Tests (Multi-Stage)

```python
# Full pipeline
old_urls = get_all_old_site_urls()  # 31 URLs (28 HTML + 3 assets)
new_urls = get_all_new_site_urls()  # 32 URLs (29 HTML + 3 assets)

pipeline = Pipeline((old_urls, new_urls))
final_state = await pipeline.run()

old_pages, new_pages, mappings = final_state

# Should have filtered out assets
assert len(old_pages) == 28
assert len(new_pages) == 29

# Should have created mappings
assert len(mappings) >= 23
assert len(mappings) <= 26

# Should identify orphaned pages
orphaned = [p for p in old_pages if p not in any_mapping]
assert len(orphaned) == 3

# Should identify new pages
new_only = [p for p in new_pages if p not in any_mapping]
assert len(new_only) == 2
```

## Maintenance Notes

When updating this document:
1. Ensure row numbers remain sequential
2. Update summary statistics when mappings change
3. Verify similarity percentages match actual content
4. Update test assertions if expected counts change
5. Document any new match types or edge cases

## See Also

- [README.md](README.md) - Technical overview and quick start
- [CONTENT_GUIDELINES.md](CONTENT_GUIDELINES.md) - How to create matching content
- [TEST_SCENARIOS.md](TEST_SCENARIOS.md) - Expected pipeline behavior details
