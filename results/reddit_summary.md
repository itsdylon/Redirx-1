# Tested 3 approaches to redirect mapping on a 1,200 URL migration — results

## Dataset

- N=1200
- Freeze date: 2026-03-03 13:38:58 EST
- Source repos and commit pins:
  - aws-amplify/docs @ 23f040666373 (2060 extracted)
  - denoland/docs @ 3bc9f662c58d (153 extracted)
  - hashicorp/terraform-website @ 93ec9cbdc3a1 (611 extracted)
  - fluxcd/website @ fbe28609d4c4 (44 extracted)
  - withastro/docs @ 8e216293f74f (60 extracted)

## Metrics

| Method | Top-1 Accuracy | Top-3 Recall | MRR | Coverage | Runtime (s) | Est. Cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| string_similarity | 0.336667 | 0.436667 | 0.381806 | 1.000000 | 0.0735 | 0.000000 |
| slug_only | 0.126667 | 0.184167 | 0.153611 | 1.000000 | 5.6651 | 0.000000 |
| content_based | 0.162500 | 0.258333 | 0.204306 | 0.746667 | 125.1001 | 0.007475 |

Coverage line: 0.746667 of pairs had usable old-content snapshots for content-based matching.

## Top Failure Modes

- other: 1517
- wildcard_expansion_ambiguity: 572
- no_prediction: 304
- many_to_one_merges: 279
- soft_404_or_thin_pages: 138

## Source Distribution

- aws-amplify/docs: 804
- denoland/docs: 57
- hashicorp/terraform-website: 235
- withastro/docs: 60
- fluxcd/website: 44

## Reproducibility

- Scripts: `python -m scripts.reddit_benchmark.run <subcommand>`
- Key artifacts:
  - `data/normalized/source_lock.json`
  - `data/normalized/pairs.csv`
  - `data/normalized/content_snapshots.csv`
  - `data/normalized/candidate_pool.csv`
  - `results/metrics_summary.csv`
