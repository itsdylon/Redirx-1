"""Filesystem fixtures for the actual read-only sampler, without a worker or DB."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "scripts/capacity/sample_worker_cgroup.py"
SPEC = importlib.util.spec_from_file_location("worker_cgroup_sampler", SOURCE)
sampler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sampler)


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / "proc"
        self.cg = self.root / "cgroup"
        self.cg.mkdir()
        for pid in ("self", "123"):
            (self.proc / pid).mkdir(parents=True)
            (self.proc / pid / "cgroup").write_text("0::/\n")
        (self.proc / "self" / "mountinfo").write_text(
            f"30 20 0:28 / {self.cg} rw - cgroup2 cgroup rw\n")
        self.write_start(456)
        (self.proc / "123" / "status").write_text(
            "Name:\tignored-name\nVmRSS:\t123 kB\nVmHWM:\t234 kB\nThreads:\t7\n")
        for name, content in {
            "memory.current": "1000", "memory.peak": "9000", "memory.max": "536870912",
            "memory.stat": "anon 600\nfile 300\nshmem 10\ninactive_file 200\nslab 20\n",
            "memory.events": "low 0\nhigh 1\nmax 2\noom 0\noom_kill 0\n",
            "cpu.max": "50000 100000", "cpu.stat": "usage_usec 500\nnr_throttled 2\nthrottled_usec 30\n",
            "memory.pressure": "some avg10=1.00 avg60=0.00 avg300=0.00 total=50\n",
        }.items():
            (self.cg / name).write_text(content)

    def write_start(self, ticks):
        # Include ')' inside comm: splitting stat by whitespace is incorrect.
        (self.proc / "123" / "stat").write_text(
            "123 (worker ) odd name) " + " ".join(["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 30))

    def observer(self):
        return sampler.Observer(123, self.root, proc=self.proc)

    def output(self, name="output.jsonl"):
        output = sampler.PrivateOutput(self.root / name)
        self.addCleanup(output.close)
        return output

    def test_reads_real_fixture_files_and_never_reads_env_or_cmdline(self):
        allowed = {"stat", "status", "cgroup", "mountinfo", "memory.current", "memory.peak", "memory.max",
                   "memory.stat", "memory.events", "cpu.max", "cpu.stat", "memory.pressure", "cpu.pressure", "io.pressure"}
        original = sampler.read
        def guarded(path):
            self.assertIn(path.name, allowed)
            return original(path)
        with patch.object(sampler, "read", side_effect=guarded):
            observer = self.observer()
            sample = observer.sample()
        self.assertEqual(observer.start_ticks, 456)
        self.assertEqual(sample["worker"], {"VmRSS_bytes": 123*1024, "VmHWM_bytes": 234*1024, "Threads": 7})
        self.assertEqual(sample["memory_stat"]["file"], 300)
        self.assertEqual(sample["pressure"]["memory"]["some"]["total"], 50)
        self.assertIsNone(sample["pressure"]["cpu"])
        self.assertGreater(sample["temporary_available_bytes"], 0)

    def test_requires_same_cgroup_and_v2(self):
        (self.proc / "123" / "cgroup").write_text("0::/other\n")
        with self.assertRaisesRegex(sampler.ObservationError, "differ"):
            self.observer()
        (self.proc / "123" / "cgroup").write_text("2:memory:/\n")
        with self.assertRaisesRegex(sampler.ObservationError, "v2"):
            self.observer()

    def test_namespace_mount_root_and_escaped_mountpoint(self):
        mount = self.root / "mounted space"
        mount.mkdir()
        escaped = str(mount).replace(" ", r"\040")
        (self.proc / "self" / "mountinfo").write_text(f"30 20 0:28 /container {escaped} rw - cgroup2 cgroup rw\n")
        self.assertEqual(sampler.cgroup_directory(self.proc, "/container/job"), mount / "job")
        with self.assertRaises(sampler.ObservationError):
            sampler.cgroup_directory(self.proc, "/outside")

    def test_reused_pid_disappearance_and_cgroup_move(self):
        observer = self.observer()
        self.write_start(457)
        self.assertEqual(observer.sample(), {"state": "worker_restarted"})
        self.write_start(456)
        (self.proc / "123" / "cgroup").write_text("0::/other\n")
        self.assertEqual(observer.sample(), {"state": "cgroup_changed"})
        (self.proc / "123" / "stat").unlink()
        self.assertEqual(observer.sample(), {"state": "worker_disappeared"})

    def test_final_identity_check_rejects_pid_reuse_during_sample(self):
        observer = self.observer()
        with patch.object(observer, "identity_state", side_effect=["running", "worker_restarted"]):
            self.assertEqual(observer.sample(), {"state": "worker_restarted"})

    def test_zombie_worker_is_terminal(self):
        observer = self.observer()
        (self.proc / "123" / "status").write_text("State:\tZ (zombie)\n")
        self.assertEqual(observer.sample(), {"state": "worker_exited"})

    def test_cli_rejects_unbounded_sampling_before_observation(self):
        base = [str(SOURCE), "--worker-pid", "123", "--temporary-directory", str(self.root),
                "--output", str(self.root / "never-created")]
        for extra in (["--duration-seconds", "nan"], ["--duration-seconds", "inf"],
                      ["--interval-seconds", "0"],
                      ["--duration-seconds", "86400", "--interval-seconds", "1"]):
            with self.subTest(extra=extra), patch.object(sampler.sys, "argv", base + extra), \
                    patch.object(sampler, "Observer") as observer, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    sampler.main()
                self.assertEqual(result.exception.code, 2)
                observer.assert_not_called()
        self.assertFalse((self.root / "never-created").exists())

    def test_output_is_private_exclusive_and_refuses_symlink(self):
        output = self.output()
        self.assertEqual((self.root / "output.jsonl").stat().st_mode & 0o777, 0o600)
        self.assertTrue(output.write({"positive": True}))
        with self.assertRaises(FileExistsError):
            sampler.PrivateOutput(self.root / "output.jsonl")
        link = self.root / "link"
        link.symlink_to(self.root / "output.jsonl")
        with self.assertRaises(OSError):
            sampler.PrivateOutput(link)
        self.assertEqual((self.root / "output.jsonl").read_text(), '{"positive":true}\n')

    def test_bounded_run_positive_deltas_and_peak_is_not_subtracted(self):
        observer = self.observer()
        output = self.output()
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
            (self.cg / "memory.current").write_text("1700")
            (self.cg / "memory.events").write_text("low 0\nhigh 2\nmax 4\noom 1\noom_kill 1\n")
            (self.cg / "cpu.stat").write_text("usage_usec 800\nnr_throttled 4\nthrottled_usec 80\n")
            (self.cg / "memory.pressure").write_text("some avg10=1.00 avg60=0.00 avg300=0.00 total=75\n")
        code = sampler.observe(observer, output, 2, 1, clock=lambda: elapsed[0], sleep=sleep)
        records = [json.loads(line) for line in (self.root / "output.jsonl").read_text().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(len(records), 5)
        self.assertEqual(records[-1]["samples"], 3)
        self.assertEqual(records[-1]["sampled_max_memory_current_bytes"], 1700)
        self.assertEqual(records[-1]["memory_events_delta"]["oom_kill"], 1)
        self.assertEqual(records[-1]["cpu_stat_delta"]["throttled_usec"], 50)
        self.assertEqual(records[-1]["pressure_total_usec_delta"]["memory.some"], 25)
        self.assertEqual(records[1]["memory_peak_bytes"], 9000)

    def test_worker_disappearance_stops_without_following_replacement(self):
        observer = self.observer()
        output = self.output()
        def sleep(seconds):
            (self.proc / "123" / "stat").unlink()
        code = sampler.observe(observer, output, 5, 1, clock=lambda: 0, sleep=sleep)
        summary = json.loads((self.root / "output.jsonl").read_text().splitlines()[-1])
        self.assertEqual(code, 2)
        self.assertEqual(summary["state"], "worker_disappeared")
        self.assertEqual(summary["samples"], 1)

    def test_output_and_sample_limits_leave_summary(self):
        with patch.object(sampler, "MAX_OUTPUT_BYTES", 4096), patch.object(sampler, "FINAL_RESERVE_BYTES", 1024):
            code = sampler.observe(self.observer(), self.output(), 100, 1, clock=lambda: 0, sleep=lambda _: None)
        path = self.root / "output.jsonl"
        self.assertEqual(code, 2)
        self.assertLessEqual(path.stat().st_size, 4096)
        self.assertEqual(json.loads(path.read_text().splitlines()[-1])["state"], "output_limit")
        with patch.object(sampler, "MAX_SAMPLES", 2):
            code = sampler.observe(self.observer(), self.output("limited.jsonl"), 100, 1, clock=lambda: 0, sleep=lambda _: None)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads((self.root / "limited.jsonl").read_text().splitlines()[-1])["state"], "sample_limit")

    def test_failure_does_not_echo_input_and_files_remain_unchanged(self):
        original = {path: path.read_bytes() for path in self.cg.iterdir()}
        with patch.object(sampler, "read", side_effect=ValueError("sensitive-fixture-value")):
            code = sampler.observe(self.observer_without_read(), self.output(), 5, 1)
        self.assertEqual(code, 2)
        self.assertNotIn("sensitive-fixture-value", (self.root / "output.jsonl").read_text())
        self.assertEqual(original, {path: path.read_bytes() for path in self.cg.iterdir()})

    def observer_without_read(self):
        observer = object.__new__(sampler.Observer)
        observer.proc, observer.pid, observer.start_ticks = self.proc, 123, 456
        observer.group, observer.directory_identity, observer.temporary_directory = "/", 1, self.root
        return observer


if __name__ == "__main__":
    unittest.main()
