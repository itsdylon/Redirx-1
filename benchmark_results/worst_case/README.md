# Worst-Case Benchmark Baseline

This folder keeps one canonical benchmark baseline for repository history.

- Baseline date: `2026-03-02`
- Scenario: content pipeline, unrelated old/new URL sets (`quotes.toscrape.com` vs `books.toscrape.com`)
- Matrix: tiers `500,1000,2500,5000` with `3` runs per tier
- Output files:
  - `baseline_2026-03-02.json`
  - `baseline_2026-03-02.csv`

Regenerate benchmark runs with:

```bash
./.venv/bin/python benchmark_worst_case.py --tiers 500,1000,2500,5000 --runs-per-tier 3 --output-dir benchmark_results/worst_case
```

Freeze fixture URLs (if needed) with:

```bash
./.venv/bin/python benchmark_worst_case.py --freeze-fixtures
```

Notes:
- `.gitignore` is configured to keep this baseline and ignore future timestamped benchmark outputs by default.
- Frozen URL fixtures are under `tests/fixtures/worst_case_benchmark/`.
