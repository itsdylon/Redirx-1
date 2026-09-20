"""Which inventory shapes can actually reach a worker through the import gates.

Count and per-URL maxima are independent limits. A full-size inventory of
maximum-length original URLs satisfies both and still cannot be imported: the
explicit-import request and the policy JSON it produces each have their own
byte bound. This script measures those bounds against the real policy code and
the real serialization, so a memory tier is not sized from a URL array that no
import can deliver.

Deliberately NOT claimed here: that the worker can never hold long URLs.
Network discovery accumulates rows 500 at a time through
checkpoint_inventory_discovery, so it is bounded by the count limit and the
per-checkpoint state, not by these single-request byte caps.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from backend.services.inventory_policy import MAX_URL_LENGTH, preflight_inventory

# backend/app.py: MAX_CONTENT_LENGTH defaults to 25 MiB; the whole
# POST /migrations/<id>/inventories body must fit under it.
FLASK_REQUEST_LIMIT_BYTES = 25 * 1024 * 1024
# database/migrations/034 and 056: octet_length(p_inventory::text) > 33554432
# raises capacity_exceeded before any row is stored.
POLICY_JSON_LIMIT_BYTES = 32 * 1024 * 1024
# checkpoint_inventory_discovery accepts at most this many rows per call.
DISCOVERY_ROWS_PER_CHECKPOINT = 500

ORIGIN = 'https://www.example.com'
# The committed pivot caps: independent count limits, per side.
FULL_SIDE_ROWS = {'old': 15000, 'new': 20000}


def realistic_url(index: int, characters: int) -> str:
    """A deterministic catalogue-shaped URL of exactly `characters` length."""
    base = f'{ORIGIN}/collections/spring-2026/guides/article-{index:06d}'
    if len(base) >= characters:
        return base[:characters]
    tail = f'?ref=catalog&variant=default&session_pad='
    prefix = base + tail
    assert len(prefix) < characters, (len(prefix), characters)
    return prefix.ljust(characters, 'a')


def request_bytes(rows: list[str], side: str) -> int:
    """Exactly the JSON body backend/routes/v2_routes.import_inventory parses."""
    body = {'side': side, 'rows': rows, 'idempotency_key': '0' * 64, 'host_aliases': []}
    return len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def policy_bytes(rows: list[str], side: str) -> int:
    """Exactly the p_inventory text the RPC measures with octet_length()."""
    result = preflight_inventory(rows, declared_origins=[ORIGIN], side=side)
    assert not result['exclusions'], result['exclusions'][:1]
    assert len(result['items']) == len(rows)
    return len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def linear_model(characters: int, side: str, samples=(200, 400, 800)) -> dict:
    """Rows are uniform, so both sizes are exactly linear in the row count.

    Fit on two sample sizes and verify on a third rather than materialising a
    165 MB array just to watch it fail a 25 MiB check.
    """
    measured = {}
    for count in samples:
        rows = [realistic_url(index, characters) for index in range(count)]
        measured[count] = {'request': request_bytes(rows, side), 'policy': policy_bytes(rows, side)}
    low, mid, high = samples
    model = {}
    for key in ('request', 'policy'):
        per_row = (measured[mid][key] - measured[low][key]) / (mid - low)
        base = measured[low][key] - per_row * low
        predicted = base + per_row * high
        model[key] = {
            'bytes_per_row': per_row,
            'base_bytes': base,
            'verified_at_rows': high,
            'predicted_bytes': round(predicted),
            'measured_bytes': measured[high][key],
        }
        assert abs(predicted - measured[high][key]) <= 2, model[key]
    return model


def evaluate(characters: int, side: str, rows_wanted: int, exact: bool) -> dict:
    model = linear_model(characters, side)
    if exact:
        rows = [realistic_url(index, characters) for index in range(rows_wanted)]
        sizes = {'request': request_bytes(rows, side), 'policy': policy_bytes(rows, side)}
        del rows
        method = 'materialised the full inventory and measured it'
    else:
        sizes = {
            key: round(model[key]['base_bytes'] + model[key]['bytes_per_row'] * rows_wanted)
            for key in model
        }
        method = 'linear model fitted and verified on smaller samples'
    limits = {'request': FLASK_REQUEST_LIMIT_BYTES, 'policy': POLICY_JSON_LIMIT_BYTES}
    return {
        'url_characters': characters,
        'side': side,
        'rows': rows_wanted,
        'method': method,
        'request_body_bytes': sizes['request'],
        'request_body_limit_bytes': FLASK_REQUEST_LIMIT_BYTES,
        'request_body_fits': sizes['request'] <= FLASK_REQUEST_LIMIT_BYTES,
        'policy_json_bytes': sizes['policy'],
        'policy_json_limit_bytes': POLICY_JSON_LIMIT_BYTES,
        'policy_json_fits': sizes['policy'] <= POLICY_JSON_LIMIT_BYTES,
        'importable_in_one_request': all(sizes[key] <= limits[key] for key in limits),
        'max_rows_per_request': min(
            int((limits[key] - model[key]['base_bytes']) // model[key]['bytes_per_row'])
            for key in limits
        ),
        'binding_gate': min(limits, key=lambda key: (limits[key] - model[key]['base_bytes']) / model[key]['bytes_per_row']),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--characters', type=int, nargs='+', default=[128, MAX_URL_LENGTH])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    shapes = []
    for characters in args.characters:
        for side, rows_wanted in FULL_SIDE_ROWS.items():
            # Only materialise shapes small enough that doing so is cheap and
            # the exact number is the interesting one.
            exact = characters * rows_wanted <= 8_000_000
            shapes.append(evaluate(characters, side, rows_wanted, exact))

    realistic = [shape for shape in shapes if shape['url_characters'] == 128]
    maximal = [shape for shape in shapes if shape['url_characters'] == MAX_URL_LENGTH]
    result = {
        'policy_max_url_characters': MAX_URL_LENGTH,
        'full_side_row_counts': FULL_SIDE_ROWS,
        'shapes': shapes,
        'realistic_full_inventory_importable': all(shape['importable_in_one_request'] for shape in realistic),
        'maximum_length_full_inventory_importable': all(shape['importable_in_one_request'] for shape in maximal),
        'discovery_rows_per_checkpoint': DISCOVERY_ROWS_PER_CHECKPOINT,
        'notes': [
            'request body limit is backend/app.py MAX_CONTENT_LENGTH (25 MiB default)',
            'policy JSON limit is octet_length(p_inventory::text) in migrations 034 and 056 (32 MiB)',
            'each side is imported by its own request, so the caps apply per side, not to the combined 35,000',
            'network discovery is NOT bounded by these caps: checkpoint_inventory_discovery accepts 500 rows per call and accumulates up to the count limit, so a long-URL inventory can still reach a worker that way',
            'these are input-gate byte bounds only; they are not a memory measurement',
        ],
    }
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
