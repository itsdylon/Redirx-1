# Redirect Mapping Benchmark Plan (Public Data, No Industry Partner)

## Objective
Produce a reproducible benchmark for a Reddit post titled:

`Tested 3 approaches to redirect mapping on a 1,200 URL migration — results`

With defensible metrics for:
- String similarity accuracy %
- Slug-only heuristics %
- Content-based matching %
- Failure modes
- Edge cases

## Key Nuance (Non-Negotiable)
Live old URLs usually redirect and no longer contain old page content.  
For content-based matching, we must recover **old-page content snapshots** from:
1. Git history at a pre-migration commit (preferred)
2. Wayback snapshots (fallback)

Without this, content-based metrics are not credible.

## Recommended Public Data Sources
Prioritize sources with explicit redirect maps in version control.

### Tier 1 (Use First)
- AWS Amplify docs (`redirects.json`) - large, explicit redirect pairs
- Deno docs (`oldurls.json`) - clean explicit mapping pairs
- Terraform website (`redirects.js`, `redirects.next.js`) - good volume, some pattern-heavy rules

### Tier 2 (Supplemental)
- Flux docs (`static/_redirects`)
- Astro docs (`public/_redirects`, includes placeholders like `:lang`)

### Tier 3 (Optional / Noisy)
- Istio archive redirects (very high volume but heavy template/legacy noise)
- EnterpriseDB redirects (many wildcard/conditional rules; lower utility for literal-pair evaluation)

## Success Criteria
- At least 1,200 redirect pairs with ground truth (`old_url -> new_url`)
- At least 80% of sampled pairs have recoverable old content text
- End-to-end reproducible run from scripts + pinned commits
- Final output includes method metrics + error taxonomy + reproducibility appendix

## Dataset Design

### Record Schema (`pairs.csv`)
- `pair_id`
- `source_repo`
- `repo_commit`
- `redirect_file`
- `old_url_path`
- `new_url_path`
- `status_code`
- `rule_type` (`literal`, `placeholder`, `wildcard`, `conditional`)
- `included_in_core_eval` (`true|false`)

### Snapshot Schema (`content_snapshots.csv`)
- `pair_id`
- `old_content_source` (`git_history|wayback|unavailable`)
- `old_snapshot_ref` (commit SHA or Wayback timestamp URL)
- `new_content_source` (`repo_head|live_fetch`)
- `old_text_chars`
- `new_text_chars`
- `old_content_quality_pass` (`true|false`)
- `new_content_quality_pass` (`true|false`)

## Core Evaluation Slice
For headline metrics, use `included_in_core_eval=true` with these filters:
- Redirect status in `301, 302, 307, 308`
- Exclude rewrites (`200`) and geo/conditional-only rules
- Prefer literal path pairs (no `*`, `:param`, `:splat`) for apples-to-apples accuracy
- Deduplicate exact same `old_url_path`

Keep a secondary analysis slice for wildcard/placeholder rules.

## Implementation Plan

### Phase 1: Ingest Redirect Pairs
1. Build source adapters per repo format:
   - JSON map/object files
   - JS redirect arrays/functions
   - Netlify `_redirects` line format
2. Normalize to common schema.
3. Persist raw + normalized extracts.

Deliverables:
- `data/raw/<source>/*`
- `data/normalized/pairs_raw.csv`

### Phase 2: Classify + Filter
1. Label each rule type (`literal`, `placeholder`, `wildcard`, `conditional`).
2. Compute `included_in_core_eval`.
3. Downsample to 1,200 pairs with source diversity target:
   - 50-70% Tier 1
   - 20-40% Tier 2
   - <=10% Tier 3

Deliverables:
- `data/normalized/pairs.csv`
- `data/normalized/pairs_core_eval.csv`

### Phase 3: Recover Old Content (Critical)
For each pair:
1. Identify migration boundary:
   - locate first commit introducing redirect rule for that pair/rule file.
2. Attempt old content via git history:
   - map `old_url_path` to historical content file path
   - fetch content at commit before redirect introduction
3. If unavailable, fallback to Wayback:
   - query CDX API for `status=200` + `mimetype=text/html`
   - select closest snapshot before migration boundary
4. Store provenance + quality metrics.

Deliverables:
- `data/content/old/<pair_id>.txt`
- `data/content/new/<pair_id>.txt`
- `data/normalized/content_snapshots.csv`

### Phase 4: Build Candidate Pools
Per source site, build candidate destination set from:
- current docs routes (repo content paths + known destination URLs)
- normalized into canonical URLs/paths

Deliverable:
- `data/normalized/candidate_pool.csv`

### Phase 5: Run 3 Matching Approaches
1. String similarity:
   - path + title token overlap
   - edit distance / Jaccard / TF-IDF on URL tokens
2. Slug-only heuristics:
   - basename/slug overlap
   - segment prefix/suffix rules
3. Content-based:
   - embed old content vs candidate new content
   - cosine similarity ranking

Each method should output top-k predictions with score.

Deliverables:
- `results/predictions_<method>.csv`

### Phase 6: Evaluate
Compute for each method:
- Top-1 Accuracy %
- Top-3 Recall %
- MRR
- Coverage (% pairs with usable old content)
- Runtime and cost (for content-based)

Deliverables:
- `results/metrics_summary.csv`
- `results/metrics_by_source.csv`

### Phase 7: Failure Modes + Edge Cases
Create labeled taxonomy for misses:
- No lexical overlap after IA rewrite
- Many-to-one merges
- Locale remaps (`/en/` to `/fr/`)
- Wildcard expansion ambiguity
- Version collapse (`/v1/...` -> `/latest/...`)
- Anchor/hash-only destination
- Soft-404 or thin pages

Deliverables:
- `results/error_analysis.csv`
- `results/edge_cases.md`

## Old Content Retrieval Details for Dev

### A) Git History Path (Preferred)
1. Find commit that introduced redirect rule:
   - `git log --reverse -- <redirect_file>`
2. Choose parent commit as pre-migration snapshot.
3. Resolve old URL -> historical content file.
4. Extract text from that file version.

If URL-to-file mapping is ambiguous:
- attempt route conventions
- fallback to repo search at that commit
- record confidence score

### B) Wayback Path (Fallback)
1. Query CDX:
   - `https://web.archive.org/cdx/search/cdx?url=<url>&output=json&fl=timestamp,original,statuscode,mimetype&filter=statuscode:200&filter=mimetype:text/html`
2. Pick best snapshot before migration date.
3. Fetch archived body:
   - `https://web.archive.org/web/<timestamp>id_/<url>`
4. Extract main text; discard nav-heavy/low-text pages.

### C) Quality Gates
Mark snapshot unusable if:
- extracted text < 300 chars
- boilerplate ratio too high
- fetch errors/timeouts
- language mismatch for pair

## Repo Layout for Handoff
- `scripts/extract_redirects.py`
- `scripts/classify_rules.py`
- `scripts/recover_old_content.py`
- `scripts/build_candidate_pool.py`
- `scripts/run_matchers.py`
- `scripts/evaluate.py`
- `data/raw/`
- `data/normalized/`
- `data/content/`
- `results/`

## Execution Checklist
1. Extract + normalize redirect rules
2. Filter and freeze 1,200-pair benchmark set
3. Recover old/new content with provenance
4. Generate candidate pools
5. Run three methods
6. Evaluate metrics
7. Label failures/edge cases
8. Export Reddit-ready summary table and examples

## Reddit Output Template (for final writeup)
- Dataset: `N=1,200`, source repos, date, commit pins
- Metrics table: String vs Slug vs Content
- Coverage line: `% with old content recovered`
- Top 5 failure modes with examples
- Edge-case section with concrete URLs
- Repro section: scripts, schema, assumptions

## Risks and Mitigations
- Risk: old content not recoverable for many URLs  
  Mitigation: broaden sources, use Wayback fallback, report coverage explicitly.

- Risk: wildcard-heavy sources skew fairness  
  Mitigation: separate core literal benchmark from wildcard benchmark.

- Risk: source-specific routing conventions create bias  
  Mitigation: stratify metrics by source and report both macro and micro averages.

- Risk: irreproducible future changes  
  Mitigation: pin commits and store extracted artifacts with hashes.

