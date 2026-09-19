// A fresh local cluster only; never reads application .env or production credentials.
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
const root=process.env.QUOTE_PG_MODULE_ROOT;
const {default:EmbeddedPostgres}=await import(root?pathToFileURL(join(root,'embedded-postgres/dist/index.js')).href:'embedded-postgres');
const probe=createServer(); await new Promise(r=>probe.listen(0,'127.0.0.1',r));
const port=probe.address().port; await new Promise(r=>probe.close(r));
const cluster=new EmbeddedPostgres({databaseDir:await mkdtemp(join(tmpdir(),'redirx-studio-http-')),
 user:'postgres',password:'local-fixture-only',port,persistent:false,postgresFlags:['-h','127.0.0.1'],
 onLog:msg=>{if(/FATAL|could not/.test(msg))console.error(msg);},onError:console.error});
try {
 await cluster.initialise(); await cluster.start();
 const child=spawn(process.env.SUBSCRIPTION_TEST_PYTHON??'python3',['-B','-m','unittest','backend.tests.test_studio_run_http','backend.tests.test_migration_run_service','-v'],
  {stdio:'inherit',env:{...process.env,PREFLIGHT_TEST_DATABASE_URL:`postgresql://postgres:local-fixture-only@127.0.0.1:${port}/postgres`}});
 process.exitCode=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',code=>resolve(code??1));});
} finally {await cluster.stop();}
