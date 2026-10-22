import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MeasurementTests(unittest.TestCase):
    def invoke(self, code, timeout=30, env=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("measure.py")),
            "--output", str(output), "--timeout", str(timeout), "--",
            sys.executable, "-c", code,
        ], capture_output=True, text=True, env=env)
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

    def test_preserves_upstream_step_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.txt"
            summary.write_text("upstream timing table")
            result, output = self.invoke("print('done')", env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output / "upstream-step-summary.txt").read_text(), summary.read_text())


if __name__ == "__main__":
    unittest.main()
