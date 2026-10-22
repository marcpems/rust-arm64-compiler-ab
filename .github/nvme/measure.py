import argparse
import ctypes
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def cpu_times():
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        raise ctypes.WinError()
    return (kernel.value + user.value - idle.value) / 10_000_000


parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command
if command and command[0] == "--":
    command = command[1:]
if not command:
    parser.error("A command is required")
args.output.mkdir(parents=True, exist_ok=True)
start_cpu = cpu_times()
started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
started = time.perf_counter()
code = None
try:
    code = subprocess.run(command, check=False).returncode
finally:
    record = {
        "scope": args.name,
        "command": command,
        "started_at": started_at,
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "wall_seconds": time.perf_counter() - started,
        "machine_busy_cpu_seconds": cpu_times() - start_cpu,
        "cpu_scope": "Whole VM, not process-tree CPU; includes background services.",
        "exit_code": code,
        "cwd": os.getcwd(),
        "free_bytes_after": shutil.disk_usage(Path.cwd()).free,
    }
    (args.output / f"{args.name}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    metrics = Path("build/metrics.json")
    if metrics.exists():
        shutil.copy2(metrics, args.output / f"{args.name}-bootstrap-metrics.json")
raise SystemExit(code)
