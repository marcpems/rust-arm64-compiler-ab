import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MeasurementTests(unittest.TestCase):
    def invoke(self, code, timeout=30, env=None, command=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("measure.py")),
            "--output", str(output), "--timeout", str(timeout), "--",
            *(command if command is not None else [sys.executable, "-c", code]),
        ], capture_output=True, text=True, env=env, timeout=20)
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


if __name__ == "__main__":
    unittest.main()
