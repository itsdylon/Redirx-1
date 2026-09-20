"""Read-only, bounded Linux cgroup-v2 observation of an explicitly named worker.

Uses only the standard library. Never imports the application or reads process
arguments/environment. Output is created exclusively with mode 0600.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

MAX_FILE_BYTES = 65536
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
FINAL_RESERVE_BYTES = 65536
MAX_SAMPLES = 12000


class ObservationError(Exception):
    pass


def read(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ObservationError("input_too_large")
    return data.decode("utf-8", errors="strict")


def unified_cgroup(proc, pid):
    matches = [line[3:] for line in read(proc / str(pid) / "cgroup").splitlines()
               if line.startswith("0::")]
    if len(matches) != 1 or not matches[0].startswith("/"):
        raise ObservationError("cgroup_v2_required")
    if ".." in Path(matches[0]).parts:
        raise ObservationError("invalid_cgroup_path")
    return matches[0]


def unescape_mount(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def cgroup_directory(proc, group):
    """Respect cgroup mount roots, including namespace-relative '/' membership."""
    candidates = []
    for line in read(proc / "self" / "mountinfo").splitlines():
        left, separator, right = line.partition(" - ")
        if not separator or right.split()[0] != "cgroup2":
            continue
        fields = left.split()
        root, mount = Path(unescape_mount(fields[3])), Path(unescape_mount(fields[4]))
        try:
            relative = Path(group).relative_to(root)
        except ValueError:
            continue
        candidates.append((len(root.parts), mount / relative))
    if not candidates:
        raise ObservationError("cgroup_mount_not_found")
    return max(candidates, key=lambda item: item[0])[1]


def process_start(proc, pid):
    # comm may contain spaces or ')'; fields after the last ')' start at field 3.
    fields = read(proc / str(pid) / "stat").rsplit(")", 1)[1].split()
    return int(fields[19])  # Linux /proc/PID/stat field 22, starttime.


def counters(path):
    result = {}
    for line in read(path).splitlines():
        key, value = line.split()
        result[key] = int(value)
    return result


def pressure(path):
    if not path.exists():
        return None
    result = {}
    for line in read(path).splitlines():
        kind, *parts = line.split()
        result[kind] = {key: float(value) if key.startswith("avg") else int(value)
                        for key, value in (part.split("=", 1) for part in parts)}
    return result


def optional_number(path):
    return int(read(path).strip()) if path.exists() else None


class Observer:
    def __init__(self, pid, temporary_directory, proc=Path("/proc")):
        self.proc, self.pid = proc, pid
        self.temporary_directory = temporary_directory.resolve(strict=True)
        self.start_ticks = process_start(proc, pid)
        self.group = unified_cgroup(proc, pid)
        if self.group != unified_cgroup(proc, "self"):
            raise ObservationError("worker_and_sampler_cgroup_differ")
        self.directory = cgroup_directory(proc, self.group)
        self.directory_identity = self.directory.stat().st_ino

    def identity_state(self):
        try:
            if process_start(self.proc, self.pid) != self.start_ticks:
                return "worker_restarted"
            if (unified_cgroup(self.proc, self.pid) != self.group
                    or unified_cgroup(self.proc, "self") != self.group):
                return "cgroup_changed"
        except FileNotFoundError:
            return "worker_disappeared"
        try:
            if self.directory.stat().st_ino != self.directory_identity:
                return "cgroup_changed"
        except FileNotFoundError:
            return "cgroup_changed"
        return "running"

    def sample(self):
        state = self.identity_state()
        if state != "running":
            return {"state": state}
        process = {}
        for line in read(self.proc / str(self.pid) / "status").splitlines():
            key, _, value = line.partition(":")
            if key == "State" and value.strip().split()[0] in ("Z", "X"):
                return {"state": "worker_exited"}
            if key in ("VmRSS", "VmHWM"):
                process[key + "_bytes"] = int(value.split()[0]) * 1024
            elif key == "Threads":
                process[key] = int(value.strip())
        directory = self.directory
        usage = os.statvfs(self.temporary_directory)
        result = {
            "state": "running", "worker": process,
            "memory_current_bytes": int(read(directory / "memory.current")),
            "memory_peak_bytes": optional_number(directory / "memory.peak"),
            "memory_max": read(directory / "memory.max").strip(),
            "memory_stat": counters(directory / "memory.stat"),
            "memory_events": counters(directory / "memory.events"),
            "cpu_max": read(directory / "cpu.max").strip(),
            "cpu_stat": counters(directory / "cpu.stat"),
            "pressure": {name: pressure(directory / (name + ".pressure"))
                         for name in ("memory", "cpu", "io")},
            "temporary_available_bytes": usage.f_bavail * usage.f_frsize,
            "temporary_free_bytes": usage.f_bfree * usage.f_frsize,
        }
        # Avoid attributing a sample spanning PID reuse/migration to this worker.
        state = self.identity_state()
        return result if state == "running" else {"state": state}


class PrivateOutput:
    def __init__(self, path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        self.stream = os.fdopen(descriptor, "wb")
        self.written = 0
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            self.stream.close()
            raise ObservationError("output_permissions_invalid")

    def write(self, record, final=False):
        data = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode()
        limit = MAX_OUTPUT_BYTES if final else MAX_OUTPUT_BYTES - FINAL_RESERVE_BYTES
        if self.written + len(data) > limit:
            return False
        self.stream.write(data)
        self.stream.flush()
        self.written += len(data)
        return True

    def close(self):
        self.stream.close()


def delta(current, baseline):
    return {key: value - baseline.get(key, value) for key, value in current.items()}


def pressure_totals(sample):
    return {resource + "." + kind: values["total"]
            for resource, kinds in sample["pressure"].items() if kinds
            for kind, values in kinds.items() if "total" in values}


def observe(observer, output, duration, interval, clock=time.monotonic, sleep=time.sleep):
    start = clock()
    baseline = last = None
    count = 0
    maximum = minimum_tmp = None
    status = "duration_complete"
    output.write({"type": "start", "worker_pid": observer.pid,
                  "worker_start_ticks": observer.start_ticks,
                  "sampler_pid": os.getpid(), "cgroup": observer.group,
                  "cgroup_directory_inode": observer.directory_identity,
                  "temporary_directory": str(observer.temporary_directory),
                  "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                  "started_utc": datetime.now(timezone.utc).isoformat(),
                  "duration_seconds": duration, "interval_seconds": interval,
                  "same_cgroup_verified": True,
                  "limitations": ["cgroup includes sampler, other container processes and filesystem cache",
                                  "memory.peak may predate this observation; never reset",
                                  "PID disappearance stops observation; replacement PID is not followed"]})
    try:
        while count < MAX_SAMPLES:
            try:
                sample = observer.sample()
            except FileNotFoundError:
                status = observer.identity_state()
                if status == "running":
                    status = "observation_file_disappeared"
                break
            if sample["state"] != "running":
                status = sample["state"]
                break
            if baseline is None:
                baseline = sample
            sample = dict(sample, type="sample", elapsed_seconds=round(clock() - start, 3))
            if not output.write(sample):
                status = "output_limit"
                break
            last = sample
            count += 1
            maximum = max(maximum or 0, sample["memory_current_bytes"])
            minimum_tmp = min(minimum_tmp if minimum_tmp is not None else sample["temporary_available_bytes"],
                              sample["temporary_available_bytes"])
            remaining = duration - (clock() - start)
            if remaining <= 0:
                break
            sleep(min(interval, remaining))
        else:
            status = "sample_limit"
    except KeyboardInterrupt:
        status = "interrupted"
    except (OSError, ValueError, KeyError, IndexError, ObservationError):
        status = "observation_failed"
    summary = {"type": "summary", "state": status, "samples": count,
               "elapsed_seconds": round(clock() - start, 3),
               "sampled_max_memory_current_bytes": maximum,
               "minimum_temporary_available_bytes": minimum_tmp,
               "memory_events_delta": delta(last["memory_events"], baseline["memory_events"]) if last else {},
               "cpu_stat_delta": delta(last["cpu_stat"], baseline["cpu_stat"]) if last else {},
               "pressure_total_usec_delta": delta(pressure_totals(last), pressure_totals(baseline)) if last else {}}
    if not output.write(summary, final=True):
        raise ObservationError("summary_output_failed")
    return 0 if status == "duration_complete" else 130 if status == "interrupted" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-pid", type=int, required=True)
    parser.add_argument("--temporary-directory", type=Path, required=True,
                        help="The actual deployed worker temporary directory, verified separately")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=3600)
    parser.add_argument("--interval-seconds", type=float, default=5)
    args = parser.parse_args()
    if (args.worker_pid <= 0 or args.worker_pid == os.getpid()
            or not math.isfinite(args.duration_seconds) or not 1 <= args.duration_seconds <= 86400
            or not math.isfinite(args.interval_seconds) or not 1 <= args.interval_seconds <= 300
            or math.ceil(args.duration_seconds / args.interval_seconds) + 1 > MAX_SAMPLES):
        parser.error("invalid PID, duration, interval or sample budget")
    output = None
    try:
        if not sys.platform.startswith("linux"):
            raise ObservationError("linux_required")
        observer = Observer(args.worker_pid, args.temporary_directory)
        output = PrivateOutput(args.output)
        return observe(observer, output, args.duration_seconds, args.interval_seconds)
    except (OSError, ValueError, KeyError, IndexError, ObservationError):
        # Never echo OS exception text, filesystem content or command arguments.
        print("Worker capacity observation failed; inspect any private summary and verify prerequisites.", file=sys.stderr)
        return 2
    finally:
        if output:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())
