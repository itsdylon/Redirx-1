"""Per-page WebPage footprint, measured rather than estimated.

A full pivot job holds 35,000 compacted WebPage objects at once, so the
per-instance overhead is a real line item. Run this against two tree states to
compare; it reports whether the class it actually imported is slotted.
"""
import argparse
import gc
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from src.redirx.stages import WebPage


def rss() -> int:
    return int(subprocess.check_output(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())], text=True).strip()) * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pages', type=int, default=35000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    url = 'https://www.example.com/collections/spring-2026/guides/article-'
    probe = WebPage(url + '000000', '')
    slotted = not hasattr(probe, '__dict__')
    object_bytes = sys.getsizeof(probe)
    dict_bytes = 0 if slotted else sys.getsizeof(probe.__dict__)
    del probe

    gc.collect()
    before = rss()
    pages = [WebPage(f'{url}{index:06d}', '') for index in range(args.pages)]
    after = rss()
    assert len(pages) == args.pages

    result = {
        'slotted': slotted,
        'pages': args.pages,
        'sizeof_instance_bytes': object_bytes,
        'sizeof_instance_dict_bytes': dict_bytes,
        'sizeof_total_per_page_bytes': object_bytes + dict_bytes,
        'rss_before_bytes': before,
        'rss_after_bytes': after,
        'rss_delta_bytes': after - before,
        'rss_bytes_per_page': round((after - before) / args.pages, 2),
        'limitations': [
            'macOS CPython 3.13 process, not a Linux cgroup measurement',
            'empty html: this isolates object overhead and the URL string, not page content',
            'RSS delta includes the list and the URL strings, which both tree states share',
        ],
    }
    del pages
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
