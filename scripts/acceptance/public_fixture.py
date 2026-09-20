"""Generate synthetic static sites for controlled public migration acceptance.

No provider, database, browser, or hosting calls. Publish only old/ and new/.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend.services.inventory_policy import preflight_inventory

WORDS = (
    'orchard glacier telescope pottery coral violin copper meadow lantern turbine '
    'saffron harbor canvas quartz garden satellite bamboo canyon library compass '
    'silk rainfall furnace mosaic cedar microscope feather bridge velvet lagoon '
    'marble bicycle safflower thunder maple workshop reef clockwork porcelain '
    'walnut geyser prism paper windmill tapestry fern sculpture sesame granite '
    'honey observatory willow ceramic island fountain ruby timber prairie nectar '
    'cobalt blossom sandstone cabin pearl ocean'.split()
)
MAX_TEXT_BYTES = 1024
VECTOR_DIMENSIONS = 1536


def public_origin(value):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value) or '\\' in value:
        raise ValueError('Use a public HTTPS origin without whitespace or credentials')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('Use a public HTTPS origin without a path, query, or nonstandard port')
    host = parsed.hostname.encode('idna').decode('ascii').lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split('.')
        if (len(host) > 253 or len(labels) < 2 or host.endswith(('.localhost', '.local', '.internal'))
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
            raise ValueError('Use a public DNS hostname')
    else:
        if not address.is_global:
            raise ValueError('Private and loopback addresses are not public fixture origins')
        if address.version == 6:
            host = '[' + host + ']'
    return 'https://' + host


def page_path(side, index, origin):
    if index == 0:
        return '/'
    prefix = '/before-' if side == 'old' else '/after-'
    seed = hashlib.sha256(f'{side}/{index}'.encode()).hexdigest()
    stem = prefix + f'{index:06d}-'
    padding = max(12, 128 - len(origin) - len(stem) - len('.html'))
    return stem + (seed * ((padding + 63) // 64))[:padding] + '.html'


def semantic_text(index):
    digest = hashlib.sha256(f'redirx-public-fixture-v1/{index}'.encode()).digest()
    words = [WORDS[value % len(WORDS)] for value in digest[:18]]
    text = (
        f'Synthetic acceptance specimen {index:06d}. '
        f'This fictional collection studies {words[0]}, {words[1]}, and {words[2]}. '
        f'The field notebook connects {words[3]} with {words[4]} beside {words[5]}. '
        f'Its separate exhibition compares {words[6]}, {words[7]}, and {words[8]}. '
        f'Workshop observations describe {words[9]} around {words[10]} and {words[11]}. '
        f'The catalog includes {words[12]}, {words[13]}, and {words[14]}, '
        f'while the closing study considers {words[15]}, {words[16]}, and {words[17]}. '
        f'This is generated test content, not a real customer document. Record {index:06d}.'
    )
    if len(text.encode()) > MAX_TEXT_BYTES:
        raise ValueError('Synthetic semantic text exceeds its fixed bound')
    return text


def render_page(side, index, html_bytes):
    text = semantic_text(index)
    content = (f'<main><p>{text}</p></main>' if side == 'old'
               else f'<article><section><p>{text}</p></section></article>')
    before = (f'<!doctype html><html><head><meta charset="utf-8">'
              f'<meta name="robots" content="noindex"><title>Synthetic specimen {index:06d}</title>'
              f'</head><body>{content}<!-- {side} fixture padding ')
    after = ' --></body></html>\n'
    remaining = html_bytes - len((before + after).encode())
    if remaining < 0:
        raise ValueError('HTML byte budget is smaller than the synthetic document')
    return (before + 'x' * remaining + after).encode()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2) + '\n').encode()


def build_plan(old_pages, new_pages, old_origin, new_origin, html_bytes=65536):
    if type(old_pages) is not int or not 1 <= old_pages <= 15000:
        raise ValueError('old-pages must be between 1 and 15000, including the homepage')
    if type(new_pages) is not int or not old_pages <= new_pages <= 20000:
        raise ValueError('new-pages must be at least old-pages and at most 20000')
    if type(html_bytes) is not int or not 2048 <= html_bytes <= 2 * 1024 * 1024:
        raise ValueError('html-bytes must be between 2048 and 2097152')
    origins = {'old': public_origin(old_origin), 'new': public_origin(new_origin)}
    if origins['old'] == origins['new']:
        raise ValueError('Old and new origins must differ')
    counts = {'old': old_pages, 'new': new_pages}
    payloads, inventories, lengths, urls = {}, {}, {}, {}
    text_bytes = 0
    maximum_text = 0
    for side, count in counts.items():
        urls[side] = [origins[side] + page_path(side, index, origins[side]) for index in range(count)]
        payload = {'side': side, 'rows': urls[side], 'idempotency_key': 'public-fixture-' + side + '-' +
                   hashlib.sha256('\n'.join(urls[side]).encode()).hexdigest()[:24]}
        policy = preflight_inventory(urls[side], declared_origins=[origins[side]], side=side)
        if policy['status'] != 'complete' or policy['coverage']['unique_count'] != count:
            raise ValueError('Fixture failed the actual inventory policy')
        # ASCII input: ordinary JSON spacing matches jsonb's size convention;
        # keep a margin instead of calling this an executed SQL admission proof.
        policy_size = len(json.dumps(policy).encode())
        request_size = len(json_bytes(payload))
        if request_size > 25 * 1024 * 1024 or policy_size * 1.1 > 32 * 1024 * 1024:
            raise ValueError('Fixture exceeds import request or estimated SQL policy budget')
        payloads[side] = payload
        inventories[side] = {'pages': count, 'request_body_bytes': request_size,
                             'policy_json_bytes_estimate': policy_size, 'policy_budget_bytes': 32 * 1024 * 1024}
        lengths[side] = {'minimum': min(map(len, urls[side])), 'maximum': max(map(len, urls[side]))}
        for index in range(count):
            size = len(semantic_text(index).encode())
            text_bytes += size
            maximum_text = max(maximum_text, size)
    calls = old_pages + new_pages
    estimates = {
        'assumptions': ['All URLs fetched successfully; no identical full URL or raw HTML pruning.',
                        'Fresh session per job; model text-embedding-3-small, 1536 float32 dimensions.',
                        'No provider requests made by this generator; costs require current provider pricing.',
                        'Comment padding is discarded: this does NOT prove maximum 32000-byte spool capacity.',
                        'Vector payload excludes PostgreSQL rows, indexes, WAL, replicas, and backups.',
                        'Worker retries/resumes may reuse stored vectors; manual retries have no finite lifetime bound.'],
        'model': 'text-embedding-3-small', 'vector_dimensions': VECTOR_DIMENSIONS,
        'html_bytes_per_page': html_bytes, 'html_bytes_total': calls * html_bytes,
        'maximum_extracted_text_bytes': maximum_text,
        'scenarios': {},
    }
    for jobs, name in ((1, 'one_job'), (2, 'two_jobs'), (3, 'one_plus_two_jobs')):
        logical = jobs * calls
        estimates['scenarios'][name] = {
            'jobs': jobs, 'embedding_calls_without_retries': logical,
            'sdk_create_calls_per_worker_attempt_upper': logical * 3,
            'http_attempts_with_sdk_default_two_retries_upper': logical * 9,
            'http_attempts_with_five_worker_attempts_upper': logical * 45,
            'input_text_utf8_bytes_without_retries': jobs * text_bytes,
            'input_tokens_byte_bound_without_retries': jobs * text_bytes,
            'input_tokens_rough_estimate_without_retries': (jobs * text_bytes + 3) // 4,
            'raw_float32_vector_bytes': logical * VECTOR_DIMENSIONS * 4,
            'persisted_text_bytes': jobs * text_bytes,
        }
    manifest = {
        'fixture_version': 1, 'synthetic_only': True, 'origins': origins, 'counts': counts,
        'homepage_is_item_zero': True, 'url_lengths': lengths,
        'expected_pair_basis': 'Identical extracted text under the same embedding model; ranking still needs live verification.',
        'mappings': [{'index': index, 'old_url': urls['old'][index], 'new_url': urls['new'][index],
                      'semantic_sha256': hashlib.sha256(semantic_text(index).encode()).hexdigest()}
                     for index in range(old_pages)],
        'unmatched_new_pages': new_pages - old_pages,
    }
    return {'manifest': manifest, 'payloads': payloads, 'admission': inventories, 'estimates': estimates}


def write_new(path, data, mode):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), 'wb') as stream:
        stream.write(data)


def generate(output, old_pages, new_pages, old_origin, new_origin, html_bytes=65536):
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError('Output must be a new directory; existing fixtures are never overwritten')
    plan = build_plan(old_pages, new_pages, old_origin, new_origin, html_bytes)
    required = plan['estimates']['html_bytes_total'] + 64 * 1024 * 1024
    if shutil.disk_usage(output.parent).free < required:
        raise ValueError('Insufficient local disk for fixture HTML plus metadata margin')
    output.mkdir(mode=0o700)
    for side, payload in plan['payloads'].items():
        root = output / side
        root.mkdir(mode=0o700)
        origin = plan['manifest']['origins'][side]
        for index, url in enumerate(payload['rows']):
            path = urlsplit(url).path
            filename = 'index.html' if path == '/' else path.removeprefix('/')
            write_new(root / filename, render_page(side, index, html_bytes), 0o644)
        sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
                   ''.join(f'<url><loc>{escape(url)}</loc></url>\n' for url in payload['rows']) + '</urlset>\n')
        write_new(root / 'sitemap.xml', sitemap.encode(), 0o644)
        write_new(root / 'robots.txt', f'User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n'.encode(), 0o644)
        write_new(output / f'import-{side}.json', json_bytes(payload), 0o600)
    for key, name in (('manifest', 'expected-mappings.json'), ('estimates', 'estimates.json'), ('admission', 'import-admission.json')):
        write_new(output / name, json_bytes(plan[key]), 0o600)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-pages', type=int, default=500)
    parser.add_argument('--new-pages', type=int, default=600)
    parser.add_argument('--old-origin', required=True)
    parser.add_argument('--new-origin', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--html-bytes', type=int, default=65536)
    args = parser.parse_args()
    generate(args.output, args.old_pages, args.new_pages, args.old_origin, args.new_origin, args.html_bytes)
    print(json.dumps({'generated': True, 'old_pages': args.old_pages, 'new_pages': args.new_pages,
                      'publish_subdirectories_only': ['old', 'new']}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print(json.dumps({'generated': False, 'error_type': type(exc).__name__,
                          'message': 'Generation stopped. Check arguments, output existence, and available disk.'}))
        raise SystemExit(1)
