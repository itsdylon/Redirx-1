"""Verify bounded source-order reads of a retained 15k real-vector fixture."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
from uuid import UUID
from run_content_benchmark import LocalClient
from src.redirx.database import WebPageEmbeddingDB


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not os.getenv('CAPACITY_DATABASE_DIR'):
        raise RuntimeError('Requires a retained isolated fixture database')
    child = subprocess.Popen(['node', str(Path(__file__).with_name('vector_fixture_server.mjs'))],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = child.stdout.readline()
        if not line:
            raise RuntimeError(child.stderr.read())
        client = LocalClient(json.loads(line)['port'])
        sessions = client.sql('SELECT id FROM migration_sessions')
        assert len(sessions) == 1
        session = UUID(sessions[0]['id'])
        urls = [row['url'] for row in client.sql('SELECT url FROM webpage_embeddings WHERE session_id=$1 AND site_type=$2 ORDER BY url DESC', [str(session), 'old'])]
        started = time.perf_counter()
        count = 0
        for count, row in enumerate(WebPageEmbeddingDB(client).iter_embeddings_for_urls(session, 'old', urls), 1):
            assert row['url'] == urls[count - 1]
            assert len(row['embedding']) == 1536
        assert count == len(urls) == 15000
        assert client.max_vector_page <= 128
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result = {'rows': count, 'source_order_and_tail_verified': True,
                  'wall_seconds': round(time.perf_counter() - started, 3),
                  'max_page_rows': client.max_vector_page,
                  'python_peak_rss_bytes': peak if sys.platform == 'darwin' else peak * 1024,
                  'database_child': client.http.get(client.url + '/metrics').json(),
                  'limitations': ['real retained PGlite/pgvector read acceptance; excludes fetching, candidate matching, and writes']}
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    finally:
        child.terminate()
        child.wait(timeout=30)


if __name__ == '__main__':
    main()
