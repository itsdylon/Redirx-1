# Observe the deployed worker without starting another worker

`sample_worker_cgroup.py` uses only Python's standard library. It reads Linux
cgroup v2 and `/proc` counters, observes an explicitly identified worker PID,
and writes a private JSONL file. It never imports the application, opens a
database, starts jobs, reads process arguments/environment, or changes process,
cgroup, deployment or account settings.

First establish the actual daemon PID from the running instance, its process
tree and deployment startup evidence. `ps -eo pid,ppid,comm` lists process names
without command arguments. Do not guess the daemon PID from the first Python
process. Verify the actual worker temporary directory separately; passing a path
does not prove the daemon uses it. Supply a new output filename on every run.

For example, replacing `ACTUAL_WORKER_PID` with the verified numeric PID:

```sh
python scripts/capacity/sample_worker_cgroup.py \
  --worker-pid ACTUAL_WORKER_PID \
  --temporary-directory /tmp \
  --duration-seconds 14400 --interval-seconds 5 \
  --output /tmp/worker-capacity-unique-run.jsonl
```

Do not run this example until the intended instance and PID are identified.
Worker and sampler must have identical cgroup-v2 membership. The sampler also
checks PID start ticks and cgroup identity before and after each observation.
PID disappearance, reuse, or cgroup change stops the run with a final summary;
it never follows a replacement process automatically. Exit 0 means the requested
observation duration completed, **not that capacity acceptance passed**. Exit 2
means a failure or limit stopped observation; 130 means operator interruption.

Output is exclusively created with mode 0600, refuses existing paths/symlinks,
and is limited to 16 MiB including reserved summary space. Duration is 1–86,400
seconds, interval 1–300 seconds, and the requested budget cannot exceed 12,000
samples. Filesystem exhaustion can still prevent the final summary. Keep the
output private and export it before a deploy replaces ephemeral storage.

Each sample includes daemon RSS/high-water RSS, memory current/peak/limit,
memory accounting (including anonymous memory, file cache and shared memory),
memory events, CPU quota and counters, optional memory/CPU/I/O pressure, and
temporary-filesystem available/free bytes. Summary counters include OOM-event,
CPU-throttling and pressure-total deltas relative to the first successful sample.
No counters are reset. `memory.peak` can predate the observation; the independent
sampled maximum can miss peaks between samples. Missing pressure/peak files are
reported as null. The cgroup includes the observer, other container processes
and filesystem cache; worker RSS is a different measurement. Temporary free
space is neither a reservation nor a future quota guarantee.

Use this observer alongside the **existing** daemon while separately submitting
ordinary account-isolated acceptance jobs. Verify one job, then actual overlap
of two claimed jobs from leases/logs, and correlate timestamps, semantic results,
attempt counts and idle recovery. Exercise the lazy preview import before
claiming repeated-job headroom. Retain customer-data preservation checks and
explicit background-workload limitations. Do not disable host pacing or change
concurrency/compute settings as part of observation.

Do **not** run `run_worker_concurrency_benchmark.py` in the production shell: that
local harness constructs another worker and spawns large PGlite database children
which would count against the same container, while bypassing production dispatch
and network controls. A full one-job plus two-job provider run must first have
fixture token, retry, request and persisted-text/vector storage budgets. The
nominal 105,000 embedding calls alone are not a spending bound. Shorter extracted
fixture text reduces those costs but does not establish maximum spool pressure.

Local verification:

```sh
python3 -m unittest backend.tests.test_worker_cgroup_sampler -v
```

These filesystem fixtures test the shipped sampler, PID reuse/disappearance,
namespace mounts, cgroup equality, deltas, private output and bounds. They do
not constitute a live Render/Linux workload measurement.
