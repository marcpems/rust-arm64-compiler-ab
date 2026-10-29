import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import benchmark


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def executable(self, machine):
        path = self.root / f"{machine}.exe"
        header = bytearray(128)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 0x3C, 64)
        header[64:68] = b"PE\0\0"
        struct.pack_into("<H", header, 68, machine)
        path.write_bytes(header)
        return path

    def artifact_log(self, executable, fresh=False):
        log = self.root / "artifacts.jsonl"
        log.write_text(json.dumps({
            "reason": "compiler-artifact", "fresh": fresh,
            "executable": str(executable), "profile": {"test": True},
            "manifest_path": str(self.root / "crate" / "Cargo.toml"),
        }) + "\n", encoding="utf-8")
        return log

    def test_requires_fresh_compilation_and_expected_test_architecture(self):
        executable = self.executable(benchmark.MACHINES["x64"])
        log = self.artifact_log(executable)
        self.assertEqual(
            benchmark.cargo_artifacts(log, "x64", tests=True),
            [(executable, self.root / "crate")],
        )
        with self.assertRaisesRegex(ValueError, "unexpected-test-architecture"):
            benchmark.cargo_artifacts(log, "arm64", tests=True)
        log = self.artifact_log(executable, fresh=True)
        with self.assertRaisesRegex(ValueError, "no-compilation-observed"):
            benchmark.cargo_artifacts(log, "x64", tests=True)

    def test_rejects_non_pe_executable(self):
        path = self.root / "invalid.exe"
        path.write_bytes(b"not-a-pe")
        with self.assertRaises(ValueError):
            benchmark.pe_machine(path)

    def test_keeps_failure_evidence_and_does_not_report_success(self):
        with self.assertRaises(subprocess.CalledProcessError):
            benchmark.measure(
                [sys.executable, "-c", "import sys; sys.exit(7)"],
                cwd=self.root, env=None, output=self.root, label="failure",
                metadata={"case": "test"},
            )
        record = json.loads((self.root / "measurements.jsonl").read_text())
        self.assertEqual(record["exit_code"], 7)
        self.assertGreater(record["seconds"], 0)

    def test_collects_test_counts_and_rejects_missing_or_failed_suites(self):
        log = self.root / "tests.log"
        log.write_text("test result: ok. 8 passed; 0 failed; 2 ignored; 0 measured;\n")
        self.assertEqual(benchmark.test_counts(log), {"passed": 8, "ignored": 2})
        log.write_text("test result: ok. 8 passed; 1 failed; 2 ignored;\n")
        with self.assertRaisesRegex(ValueError, "invalid-test-result"):
            benchmark.test_counts(log)
        log.write_text("test result: FAILED\n")
        with self.assertRaisesRegex(ValueError, "invalid-test-result"):
            benchmark.test_counts(log)

    def test_rotates_arm_cases_across_independent_vms(self):
        orders = [benchmark.case_order("arm64", replicate) for replicate in [1, 2, 3]]
        for position in range(3):
            self.assertEqual(
                {order[position] for order in orders},
                {"arm-native", "arm-cross-x64", "x64-emulated"},
            )
        self.assertEqual(benchmark.case_order("x64", 1), ["x64-native"])


if __name__ == "__main__":
    unittest.main()
