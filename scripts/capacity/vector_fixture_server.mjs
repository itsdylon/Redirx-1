// Local-only real PostgreSQL/pgvector SQL fixture. Never accepts a remote bind.
import http from 'node:http';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
const modules = process.env.CAPACITY_NODE_MODULES;
if (!modules) throw new Error('CAPACITY_NODE_MODULES must point to isolated test dependencies');
const { PGlite } = await import(pathToFileURL(join(modules, '@electric-sql/pglite/dist/index.js')));
const { vector } = await import(pathToFileURL(join(modules, '@electric-sql/pglite-pgvector/dist/index.js')));
const pg = await PGlite.create({ dataDir: process.env.CAPACITY_DATABASE_DIR, extensions: { vector } });
if (!(await pg.query("SELECT to_regclass('public.webpage_embeddings') AS existing")).rows[0].existing) {
  await pg.exec(await readFile(new URL('./vector_fixture.sql', import.meta.url), 'utf8'));
  await pg.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;');
}
await pg.exec(await readFile(new URL('../../database/migrations/052_pivot_vector_candidate_fallback.sql', import.meta.url), 'utf8'));
let queries = 0, peakRss = process.memoryUsage().rss;
const server = http.createServer(async (req, res) => {
  try {
    peakRss = Math.max(peakRss, process.memoryUsage().rss);
    if (req.method === 'GET' && req.url === '/metrics') {
      res.end(JSON.stringify({ queries, sampled_peak_rss_bytes: peakRss, os_peak_rss_bytes: process.resourceUsage().maxRSS * 1024, current_rss_bytes: process.memoryUsage().rss })); return;
    }
    if (req.method !== 'POST' || req.url !== '/query') { res.writeHead(404); res.end(); return; }
    const chunks = []; let size = 0;
    for await (const chunk of req) { size += chunk.length; if (size > 2*1024*1024) throw new Error('fixture request too large'); chunks.push(chunk); }
    const { sql, params=[] } = JSON.parse(Buffer.concat(chunks));
    const result = await pg.query(sql, params); queries++;
    res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(result.rows));
  } catch (error) { res.writeHead(400); res.end(JSON.stringify({ error: String(error.message) })); }
});
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({ port: server.address().port })));
process.on('SIGTERM', async () => { server.close(); await pg.close(); process.exit(0); });
