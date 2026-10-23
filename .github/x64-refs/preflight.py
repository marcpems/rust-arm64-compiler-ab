import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
import tomllib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with Path("bootstrap.toml").open("rb") as stream:
        config = tomllib.load(stream)
    clang_cl = Path(config["llvm"]["clang-cl"])
    if not clang_cl.is_file():
        raise FileNotFoundError(clang_cl)
    command = [
        sys.executable, "x.py", "build", "--dry-run", "--stage", "2", "library/std",
        "--host", "x86_64-pc-windows-msvc", "--target", "x86_64-pc-windows-msvc",
        "--set", f"pgo.llvm.generate={Path('opt-artifacts/llvm-preflight').resolve().as_posix()}",
    ]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start = time.monotonic()
    result = subprocess.run(command, check=False)
    receipt = {
        "command": command,
        "clang_cl": str(clang_cl),
        "started_at": started,
        "wall_seconds": time.monotonic() - start,
        "exit_code": result.returncode,
    }
    (args.output / "bootstrap-preflight.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
