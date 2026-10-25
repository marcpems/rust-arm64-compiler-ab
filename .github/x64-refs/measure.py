import argparse
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
import time

from process_job import ProcessJob


SECTION = re.compile(r"Section `([^`]+)` ended: (OK|FAIL) \(([\d.]+)s\)")


def cpu_seconds():
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        raise ctypes.WinError()
    return (kernel.value + user.value - idle.value) / 10_000_000


def measure(command, output, timeout):
    with ProcessJob() as job:
        return measure_job(command, output, timeout, job)


def measure_job(command, output, timeout, job):
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    start_cpu = cpu_seconds()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    events = []
    lines = queue.Queue()
    process = job.start(command)

    def reader():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    next_sample = 0
    timed_out = False
    with (output / "release.log").open("w", encoding="utf-8") as log, (
        output / "resources.jsonl"
    ).open("w", encoding="utf-8") as resources:
        finished_output = False
        while not finished_output:
            elapsed = time.perf_counter() - started
            if elapsed >= timeout and not timed_out:
                timed_out = True
                job.terminate()
            if elapsed >= next_sample:
                resources.write(json.dumps({
                    "elapsed_seconds": elapsed, "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "machine_busy_cpu_seconds": cpu_seconds() - start_cpu,
                    "free_bytes": shutil.disk_usage(Path.cwd()).free,
                }) + "\n")
                resources.flush()
                next_sample = elapsed + 60
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                finished_output = True
                continue
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
            match = SECTION.search(line)
            if match:
                events.append({
                    "section": match[1], "status": match[2],
                    "duration_seconds": float(match[3]),
                    "elapsed_seconds": time.perf_counter() - started,
                    "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
                (output / "sections.json").write_text(json.dumps(events, indent=2))
    code = process.wait()
    thread.join()
    record = {
        "command": command, "started_at": started_at,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "wall_seconds": time.perf_counter() - started,
        "machine_busy_cpu_seconds": cpu_seconds() - start_cpu,
        "cpu_scope": "Whole VM busy CPU, not process-tree CPU",
        "exit_code": 124 if timed_out else code, "timed_out": timed_out,
        "sections": events,
    }
    (output / "release.json").write_text(json.dumps(record, indent=2))
    if Path("build/metrics.json").is_file():
        shutil.copy2("build/metrics.json", output / "bootstrap-metrics.json")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and Path(summary).is_file():
        shutil.copy2(summary, output / "upstream-step-summary.txt")
    return record["exit_code"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=19800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required")
    raise SystemExit(measure(command, args.output, args.timeout))
