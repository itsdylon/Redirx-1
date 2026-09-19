"""Compare legacy ANN and052 against the same retained real vector database."""
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
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not os.getenv('CAPACITY_DATABASE_DIR'):raise RuntimeError('Requires retained isolated fixture database')
    process=subprocess.Popen(['node',str(Path(__file__).with_name('vector_fixture_server.mjs'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=process.stdout.readline()
        if not line:raise RuntimeError(process.stderr.read())
        client=LocalClient(json.loads(line)['port'])
        sessions=client.sql('SELECT id FROM migration_sessions')
        if len(sessions)!=1:raise RuntimeError('Comparison requires one benchmark session')
        session=sessions[0]['id']
        stats={name:{'queries':0,'correct_targets':0,'no_confident_candidate':0,'sql_wall_seconds':0.0} for name in ['match_pages','match_migration_pages']}
        for row in WebPageEmbeddingDB(client).iter_embeddings_by_session(UUID(session),'old'):
            expected=row['url'].replace('/old/','/new/').replace('/source-','/target-')
            params=[json.dumps(row['embedding']),'new',session,5,0]
            for name,result in stats.items():
                start=time.perf_counter()
                candidates=client.sql(f'SELECT * FROM {name}($1::vector,$2,$3::uuid,$4::int,$5::float)',params)
                result['sql_wall_seconds']+=time.perf_counter()-start
                result['queries']+=1
                result['correct_targets']+=bool(candidates and candidates[0]['url']==expected)
                result['no_confident_candidate']+=not any(candidate['similarity']>=.85 for candidate in candidates)
                assert len(candidates)<=5
        for result in stats.values():result['sql_wall_seconds']=round(result['sql_wall_seconds'],3)
        usage=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        output={'comparison':stats,'python_peak_rss_bytes':usage if sys.platform=='darwin' else usage*1024,'database_child':client.http.get(client.url+'/metrics').json(),'limitations':['same retained synthetic vectors, real SQL candidate functions','query-only comparison; excludes scraping/embedding/persistence']}
        args.output.write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output))
        assert stats['match_migration_pages']['correct_targets']==stats['match_migration_pages']['queries']
    finally:process.terminate();process.wait(timeout=30)

if __name__=='__main__':main()
