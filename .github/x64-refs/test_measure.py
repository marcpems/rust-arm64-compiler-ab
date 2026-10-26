import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from measure import capture_cache_stats


class MeasurementTests(unittest.TestCase):
    def invoke(self, code, timeout=30, env=None, command=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("measure.py")),
            "--output", str(output), "--timeout", str(timeout), "--",
            *(command if command is not None else [sys.executable, "-c", code]),
        ], capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
        return result, output

    def test_sections_and_failure_propagation(self):
        result, output = self.invoke(
            "print('Section `Stage 5 (final build)` ended: OK (1.25s)'); raise SystemExit(7)"
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        receipt = json.loads((output / "release.json").read_text())
        self.assertEqual(receipt["exit_code"], 7)
        self.assertEqual(receipt["sections"][0]["section"], "Stage 5 (final build)")
        self.assertGreaterEqual(receipt["wall_seconds"], receipt["sections"][0]["elapsed_seconds"])
        self.assertTrue((output / "resources.jsonl").read_text())

    def test_timeout_is_not_success(self):
        result, output = self.invoke("import time; time.sleep(30)", timeout=1)
        self.assertEqual(result.returncode, 124, result.stderr)
        receipt = json.loads((output / "release.json").read_text())
        self.assertTrue(receipt["timed_out"])
        self.assertFalse(receipt["sections"])

    def test_unicode_output_with_legacy_windows_console_encoding(self):
        text = "\u4e2d\u6587 \U0001f680"
        result, output = self.invoke(
            "import sys; sys.stdout.buffer.write(bytes.fromhex('"
            + (text + "\n").encode("utf-8").hex() + "')); sys.stdout.flush()",
            env=dict(os.environ, PYTHONIOENCODING="cp1252"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(text, result.stdout)
        self.assertIn(text, (output / "release.log").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((output / "release.json").read_text())["exit_code"], 0)

    def test_timeout_with_exited_parent_and_live_descendant(self):
        code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)'])"
        )
        result, output = self.invoke(code, timeout=1)
        self.assertEqual(result.returncode, 124, result.stderr)
        receipt = json.loads((output / "release.json").read_text())
        self.assertTrue(receipt["timed_out"])
        self.assertLess(receipt["wall_seconds"], 4)

    def test_timeout_terminates_git_bash_pipeline(self):
        result, output = self.invoke("", timeout=1, command=[
            r"C:\Program Files\Git\bin\bash.exe", "--noprofile", "--norc",
            "-c", "sleep 5 | cat",
        ])
        self.assertEqual(result.returncode, 124, result.stderr)
        receipt = json.loads((output / "release.json").read_text())
        self.assertTrue(receipt["timed_out"])
        self.assertLess(receipt["wall_seconds"], 4)

    def test_success_preserves_detached_service_for_cache_statistics(self):
        result, output = self.invoke(
            "import subprocess,sys; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'], "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            "print(p.pid)"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        pid = int((output / "release.log").read_text().strip())
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x100001, False, pid)
        self.assertTrue(handle, "Detached service was terminated on normal measurement completion")
        try:
            self.assertEqual(api.WaitForSingleObject(handle, 0), 258)
        finally:
            api.TerminateProcess(handle, 0)
            api.CloseHandle(handle)

    def test_preserves_upstream_step_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.txt"
            summary.write_text("upstream timing table")
            result, output = self.invoke("print('done')", env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output / "upstream-step-summary.txt").read_text(), summary.read_text())

    def stop_cache(self, output, cache_function):
        script = Path(__file__).with_name("stop-cache.ps1")
        return subprocess.run([
            "pwsh", "-NoProfile", "-Command",
            cache_function + "; & '" + str(script) + "' -OutputDirectory '" + str(output) + "'",
        ], capture_output=True, text=True, timeout=20)

    def test_timeout_cache_snapshot_does_not_stop_dead_server(self):
        result, output = self.invoke("import time; time.sleep(30)", timeout=1)
        self.assertEqual(result.returncode, 124, result.stderr)
        stopped = self.stop_cache(output, "function sccache { throw 'Server is already dead' }")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("already terminated", stopped.stdout)

    def test_success_cache_snapshot_requires_successful_server_stop(self):
        result, output = self.invoke("print('complete')")
        self.assertEqual(result.returncode, 0, result.stderr)
        stopped = self.stop_cache(output, "function sccache { $global:LASTEXITCODE = 2 }")
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("Cannot stop sccache", stopped.stderr)
        stopped = self.stop_cache(output, "function sccache { $global:LASTEXITCODE = 0; 'cache stopped' }")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("cache stopped", stopped.stdout)

    def test_cache_statistics_preserve_pre_timeout_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            process = Mock(returncode=0)
            process.communicate.return_value = ('{"stats":{"compile_requests":12}}', None)
            job = Mock()
            job.start.return_value = process
            capture_cache_stats(job, "sccache", output, time.perf_counter(), "before-timeout")
            record = json.loads((output / "cache-stats.jsonl").read_text())
            self.assertEqual(record["data"]["stats"]["compile_requests"], 12)
            self.assertEqual(record["reason"], "before-timeout")
            job.start.assert_called_once_with(["sccache", "--show-stats", "--stats-format=json"])

    def test_cache_statistics_errors_are_recorded_not_zeroed(self):
        for result, code in [("not JSON", 0), ("server unavailable", 2)]:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                process = Mock(returncode=code)
                process.communicate.return_value = (result, None)
                job = Mock()
                job.start.return_value = process
                record = capture_cache_stats(job, "sccache", output, time.perf_counter(), "periodic")
                self.assertIn("error", record)
                self.assertNotIn("data", record)

    def test_cache_statistics_timeout_is_bounded_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            process = Mock()
            process.communicate.side_effect = [subprocess.TimeoutExpired("sccache", 5), ("", None)]
            job = Mock()
            job.start.return_value = process
            record = capture_cache_stats(job, "sccache", output, time.perf_counter(), "before-timeout")
            process.kill.assert_called_once()
            self.assertIn("error", record)
            self.assertNotIn("data", record)


if __name__ == "__main__":
    unittest.main()
