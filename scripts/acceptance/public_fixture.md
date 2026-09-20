# Controlled public migration fixtures

Generate these static sites only after two temporary public HTTPS origins exist.
The command creates a new private output directory and refuses any existing path,
including a dangling symlink. Its parent directory must already exist.

```sh
python scripts/acceptance/public_fixture.py \
  --old-pages 500 --new-pages 600 \
  --old-origin https://OLD-PUBLIC-HOST \
  --new-origin https://NEW-PUBLIC-HOST \
  --output /private/tmp/redirx-operation-20260919/public-fixtures-500
```

The CLI makes no network/provider/database calls. Only generated `old/` and `new/`
are public document roots. Nginx/tunnels, access logs, ports, process lifecycle,
artifact installation and credential handling are separate operator work. Never
serve the output parent or the source checkout. Directory permissions are 0700;
the task's Nginx worker must run as the same user. Public files are 0644, private
JSON artifacts 0600. Use ordinary static HTML handling, no directory listing or
fallback that turns missing paths into successful pages.

Each site has exactly the requested number of HTML pages, counting `/` once.
`robots.txt` advertises a sitemap with the same exact total. Other page paths
differ between origins and target 128-character absolute URLs when the hostname
allows it. Every old page has one intended new counterpart; extra new pages are
distinct distractors. Pages contain only synthetic text, with no forms, scripts,
credentials, controls or real customer information. A noindex meta tag reduces
search indexing; it is not access control.

Paired HTML differs in markup and padding comments, while the actual `WebPage`
extractor sees identical text. Each specimen's text is unique. This prevents
literal URL and identical HTML shortcuts in the pivot pipeline, including for
the homepages. Identical model inputs establish the expected semantic pair;
model output, candidate ranking, ambiguity flags and final mappings still need
actual deployed verification. Do not substitute this synthetic corpus for
matching-quality acceptance on varied real documents.

Private sibling artifacts:

- `expected-mappings.json`: intended URL pairs and extracted-text hashes.
- `import-old.json` and `import-new.json`: complete bodies for
  `POST /api/v2/migrations/{migration_id}/inventories`. Create an owned migration
  using the exact fixture origins first; the generator creates no account or run.
- `import-admission.json`: actual pure inventory-policy counts, request byte
  sizes and JSON policy size estimates. SQL admission itself is not executed.
- `estimates.json`: review before dispatching provider-backed work. Records HTML,
  extracted text, model/dimension assumptions, calls, retry amplification and
  raw vector payload for one job, two jobs, and both stages together.

`--old-pages 501 --new-pages 600` exercises the paid boundary with a fresh output
directory and fresh owned migration. Limits are 15000 old/20000 new; all old pages
must have a new counterpart. At those full limits, one job plus two simultaneous
jobs needs 105000 successful per-page embedding calls before retries. The code
has up to three embedding attempts; the current SDK defaults to two additional
HTTP retries, and the worker defaults to five attempts. Estimates show those
separately: 105000 is not a total retry/cost ceiling. Persisted embeddings may
avoid repeated calls on resume, but manual reruns have no finite lifetime cap.
Token count divided by four is a rough estimate; UTF-8 input byte count is a
conservative text-token bound for the assumed byte-pair tokenizer. No current
price is asserted; check the configured model and provider price before spending.

Default raw HTML is 65536 bytes per page; `--html-bytes` accepts 2048 through 2097152.
Semantic text is capped at 1024 bytes and is mostly much shorter. Comment padding
is discarded by extraction. **This fixture does not prove the maximum 32000-byte
text spool or worst-case container memory requirement.** Raw vector byte counts
exclude PostgreSQL row/index overhead, WAL, replicas and backups; they are not
a database disk-allocation bound. Generation checks free local disk for HTML plus
a 64 MiB metadata margin; a partial output after an IO failure is never reused.

Offline checks (app virtualenv, no provider credentials required):

```sh
python -m unittest scripts.acceptance.test_public_fixture -v
```
