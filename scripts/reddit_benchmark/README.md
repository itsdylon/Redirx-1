# Reddit Redirect Benchmark

This module implements a reproducible benchmark pipeline for redirect mapping comparisons.

## CLI

Run from repo root:

```bash
./.venv/bin/python -m scripts.reddit_benchmark.run <subcommand>
```

Subcommands:
- `extract`
- `classify`
- `sample`
- `recover-content`
- `build-candidates`
- `match`
- `evaluate`
- `analyze-errors`
- `report`
- `run-all`

## Common Flow

Dry run (200 pairs):

```bash
./.venv/bin/python -m scripts.reddit_benchmark.run run-all \
  --size 200 \
  --seed 20260303 \
  --max-workers 8 \
  --methods string_similarity,slug_only,content_based
```

Full run (1200 pairs):

```bash
./.venv/bin/python -m scripts.reddit_benchmark.run run-all \
  --size 1200 \
  --seed 20260303 \
  --max-workers 8 \
  --methods string_similarity,slug_only,content_based
```

## Output Artifacts

- `data/normalized/source_lock.json`
- `data/normalized/pairs_raw.csv`
- `data/normalized/pairs_classified.csv`
- `data/normalized/pairs.csv`
- `data/normalized/pairs_core_eval.csv`
- `data/normalized/content_snapshots.csv`
- `data/normalized/candidate_pool.csv`
- `results/predictions_<method>.csv`
- `results/metrics_summary.csv`
- `results/metrics_by_source.csv`
- `results/error_analysis.csv`
- `results/edge_cases.md`
- `results/reddit_summary.md`
- `results/run_manifest.json`

## Notes

- OpenAI embeddings are used for `content_based` when `OPENAI_API_KEY` is set.
- Set `REDDIT_BENCHMARK_DISABLE_OPENAI=true` to force deterministic local embeddings.
- Tier-2 source share is capped by available literal data in configured sources.
