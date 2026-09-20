# Public fixture and worker observation readiness

The controlled 500-old/600-new fixture is generated and publicly reachable.
It has not been submitted as a production migration. Only the synthetic `old/`
and `new/` document roots are published; their private sibling reports return 404
and `/.env` returns 403. Both sitemaps contain exactly their advertised count,
including the homepage once. First and last pages independently return 65,536
bytes through HTTPS. Root reproduced all six generator tests.

The fixture requires 1,100 embedding calls before retries, contains 533,487 bytes
of extracted text, and produces 6,758,400 bytes of raw 1536-dimensional float32
vectors before database overhead. At the current
[text-embedding-3-small price](https://developers.openai.com/api/docs/models/text-embedding-3-small)
of $0.02 per million input tokens, the conservative UTF-8 byte-as-token bound is
$0.01067 without retries. Applying the documented 45x retry envelope gives
$0.48014 for that bounded attempt sequence. These are input-token estimates,
not an invoice or a lifetime limit on manually repeated jobs. No provider call
has been made for this fixture. Short semantic text keeps acceptance lean;
discarded HTML comment padding does not test maximum content-spool pressure.

Hosting uses two temporary Cloudflare QuickTunnels backed by an isolated Nginx
process on local ports 55460/55461. No paid hosting service, DNS record, account
setting or system startup service was created. These are disposable acceptance
origins with no uptime promise; preserve their processes through redirect
installation and verification, then retire the task-owned hosts after acceptance.
Sanitized public checks are in `../release-evidence/public-fixture-500-readiness.json`.

## Deployed observer smoke test

The reviewed sampler from 916c703 was transferred to the existing Render worker
instance t49lj and its source SHA256 verified before execution. The process tree
identified daemon PID 40 as the Python child of PID 1; the separate shell observer
used the same cgroup. Eleven samples over 10.001 seconds completed. The existing
0be61d1 revision was idle and had not received the lazy-import/slots changes.

Sampled maximum container memory was 214,392,832 bytes; the limit was 536,870,912.
The first daemon RSS was 180,174,848 bytes. Historical cgroup `memory.peak` was
292,831,232 and may predate this observation. OOM counters and deltas were zero.
CPU quota was 50,000/100,000 microseconds. Minimum available temporary space was
69,969,846,272 bytes; this is not reserved space or a future quota guarantee.
The observer includes its own memory and container filesystem cache. It started
no worker, job, provider call or database process and changed no settings.

This proves the measurement path works on the live Linux container. It is
**not full-job capacity acceptance**. Exact counters and scope are recorded in
`../release-evidence/worker-idle-cgroup-0be61d1.json`. The 512 MiB plan and
`WORKER_MAX_CONCURRENT=2` remain unchanged. The 8 GB/$135 upgrade is unapproved;
the realistic one- and two-job evidence governs the next lean deployment test.
